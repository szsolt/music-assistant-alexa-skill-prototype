"""The setup page: creates the Alexa skill with the ASK CLI, after its no-browser login if needed."""

import json
import os
import pty
import re
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, request

from .setup_helpers import sanitize_log, enqueue_setup_log, setup_reader_thread as _helpers_setup_reader_thread, read_master_loop as _helpers_read_master_loop
from .setup_helpers import has_functional_cli_config, prepare_cli_config_for_configure
from .signal_helpers import register_signal_handlers

setup_bp = Blueprint('setup_bp', __name__)

_setup_proc = None
_setup_logs = deque(maxlen=500)
_setup_lock = threading.Lock()

# Auth (ask configure --no-browser) process state
_setup_auth_proc = None
_setup_auth_lock = threading.Lock()
_pending_endpoint = None
_setup_auth_master_fd = None
_PENDING_FILE = Path(os.environ.get('TMPDIR', '/tmp')) / 'ask_pending_endpoint.txt'


def _enqueue_setup_log(line: str):
    # Thin wrapper to keep module-level _setup_logs while delegating logic
    enqueue_setup_log(_setup_logs, line)



def register_signals():
    """Forward Ctrl+C and SIGTERM to the running ask/create processes. From the main thread."""
    register_signal_handlers(lambda: {'_setup_auth_proc': _setup_auth_proc, '_setup_proc': _setup_proc, 'master_fd': _setup_auth_master_fd})


def _setup_reader_thread(proc, prefix=None):
    # Delegate implementation to helpers while binding enqueue function
    return _helpers_setup_reader_thread(proc, _enqueue_setup_log, prefix=prefix)


def _read_master_loop(master_fd, prefix=None):
    # Delegate implementation to helpers while binding enqueue function
    return _helpers_read_master_loop(master_fd, _enqueue_setup_log, prefix=prefix)



def _create_script():
    """The create-skill script: in the container at /app/scripts (see the Dockerfile), else the repo's."""
    candidate = '/app/scripts/ask_create_skill.sh'
    if os.path.exists(candidate):
        return candidate
    return str(Path(__file__).parent.parent.parent / 'scripts' / 'ask_create_skill.sh')


def _create_command(endpoint):
    return ['/bin/bash', _create_script(), '--endpoint', endpoint, '--profile', 'default',
            '--locale', os.environ.get('LOCALE', 'en-US'), '--stage', 'development']


def _start_create(endpoint):
    """Run the create-skill script for endpoint; its output goes to the setup log."""
    global _setup_proc
    proc = subprocess.Popen(_create_command(endpoint), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    _setup_proc = proc
    threading.Thread(target=_setup_reader_thread, args=(proc, 'CREATE'), daemon=True).start()
    return proc


@setup_bp.route('/setup', methods=['GET'])
def setup_ui():
    # Embed current logs and detected auth URL so the page shows state immediately
    initial_logs = list(_setup_logs)
    auth_url = None
    try:
        for ln in initial_logs:
            try:
                m = re.search(r"(https?://[^\s'\"]+)", ln)
                if m:
                    auth_url = m.group(1)
                    break
            except Exception:
                try:
                    if isinstance(ln, str) and ln.strip().startswith('['):
                        arr = json.loads(ln)
                        if isinstance(arr, list):
                            for item in arr:
                                mm = re.search(r"(https?://[^\s'\"]+)", str(item))
                                if mm:
                                    auth_url = mm.group(1)
                                    break
                            if auth_url:
                                break
                except Exception:
                    pass
                continue
    except Exception:
        auth_url = None

    # Suppress auth URL when functional credentials already exist.
    try:
        if has_functional_cli_config(profile='default'):
            current_app.logger.info('Functional ASK credentials found; suppressing auth UI')
            auth_url = None
    except Exception:
        pass

    # If the client requested JSON (polling), return logs + auth_url + active flag
    want_json = request.args.get('format') == 'json' or 'application/json' in (request.headers.get('Accept') or '')
    if want_json:
        active = False
        try:
            active = (_setup_proc and _setup_proc.poll() is None) or (_setup_auth_proc and _setup_auth_proc.poll() is None)
        except Exception:
            active = False
        # Return sanitized logs for the UI polling loop
        try:
            safe_logs = [sanitize_log(ln) for ln in list(_setup_logs)]
        except Exception:
            safe_logs = list(_setup_logs)
        # Detect whether the setup process has completed creation (Done. Skill ID)
        created = False
        try:
            for ln in safe_logs:
                if re.search(r'Done\.\s*Skill ID', str(ln), re.IGNORECASE):
                    created = True
                    break
        except Exception:
            created = False
        return jsonify({'logs': safe_logs, 'auth_url': auth_url, 'active': bool(active), 'created': bool(created)})

    # Load setup HTML template from disk to keep this file readable
    try:
        tpl_path = Path(__file__).parent.parent / 'templates' / 'setup.html'
        page = tpl_path.read_text()
    except Exception:
        page = None

    # If valid ASK CLI credentials are present, show a notice on the setup page.
    try:
        creds_html = ''
        if has_functional_cli_config(profile='default'):
            creds_html = '<div style="margin-top:8px;color:green;font-weight:600">Persistent ASK credentials detected — setup will use existing credentials.</div>'
    except Exception:
        creds_html = ''

    # Compute initial created flag for first render (Done. Skill ID)
    try:
        initial_created = False
        for ln in initial_logs:
            try:
                if re.search(r'Done\.\s*Skill ID', str(ln), re.IGNORECASE):
                    initial_created = True
                    break
            except Exception:
                continue
    except Exception:
        initial_created = False

    if page:
        page = page.replace('__INITIAL_LOGS__', json.dumps(initial_logs))
        page = page.replace('__INITIAL_AUTH__', json.dumps(auth_url))
        page = page.replace('__INITIAL_CREATED__', json.dumps(bool(initial_created)))
        page = page.replace('__CREDENTIALS_HTML__', creds_html)
        return Response(page, mimetype='text/html')

    # Fallback: simple inline page if template is missing
    page = """<!doctype html><html><head><meta charset='utf-8'><title>Skill Setup</title></head><body><h1>Skill Setup</h1><pre>%s</pre></body></html>""" % json.dumps(initial_logs)
    return Response(page, mimetype='text/html')


@setup_bp.route('/setup/logs/download', methods=['GET'])
def setup_logs_download():
    try:
        content = '\n'.join(sanitize_log(line) for line in list(_setup_logs))
    except Exception:
        content = '\n'.join(str(line) for line in list(_setup_logs))
    resp = Response(content, mimetype='text/plain')
    resp.headers['Content-Disposition'] = 'attachment; filename="setup_logs.txt"'
    return resp


@setup_bp.route('/setup/start', methods=['POST'])
def setup_start():
    # Endpoint is provided via environment (SKILL_HOSTNAME) in container deployments
    data = request.get_json(silent=True) or {}
    endpoint = os.environ.get('SKILL_HOSTNAME', '').strip()
    # Allow override for local testing if provided in request body (kept for compatibility)
    if not endpoint:
        endpoint = data.get('endpoint')
    # Fixed options (user-not-editable). `LOCALE` may be set in the environment.
    profile = 'default'
    locale = os.environ.get('LOCALE', 'en-US')
    stage = 'development'

    # Immediate trace so UI shows activity when button is clicked
    try:
        _enqueue_setup_log(f"Received /setup/start request; resolved endpoint={endpoint!r}")
    except Exception:
        _setup_logs.append('Received /setup/start request')

    current_app.logger.info('setup_start called; resolved endpoint=%s', endpoint)

    if not endpoint:
        _setup_logs.append('Error: SKILL_HOSTNAME environment variable is not set and no endpoint provided')
        return jsonify({'error':'SKILL_HOSTNAME not set; set SKILL_HOSTNAME in container environment'}), 400

    # Normalize endpoint: allow ARN, full URLs, or hostnames (prefix https://)
    def _normalize(ep: str):
        ep = ep.strip()
        if ep.startswith('arn:'):
            return ep
        if ep.startswith('http://') or ep.startswith('https://'):
            return ep
        return 'https://' + ep

    endpoint = _normalize(endpoint)

    with _setup_lock:
        # If a setup script is already running, report it
        if _setup_proc and _setup_proc.poll() is None:
            return jsonify({'status':'running'})

        # Ensure ask CLI exists
        try:
            which = subprocess.run(['which','ask'], capture_output=True, text=True)
            if which.returncode != 0:
                _setup_logs.append('Error: ask CLI not installed')
                current_app.logger.error('ask CLI not installed')
                return jsonify({'error':'ask CLI not installed'}), 500
        except Exception as e:
            _setup_logs.append(f'Error checking ask CLI: {e}')
            current_app.logger.exception('check failed')
            return jsonify({'error':'check failed'}), 500

        # If ASK CLI is not configured with functional credentials, remove any
        # non-functional cli_config and start the no-browser auth flow.
        if not has_functional_cli_config(profile=profile):
            ok, prep_msg = prepare_cli_config_for_configure(profile=profile)
            if prep_msg:
                _enqueue_setup_log(prep_msg)
            if not ok:
                current_app.logger.error('Unable to prepare ASK cli_config for auth: %s', prep_msg)
                return jsonify({'error': 'failed preparing ASK cli_config for auth'}), 500
            with _setup_auth_lock:
                global _setup_auth_proc
                if _setup_auth_proc and _setup_auth_proc.poll() is None:
                    return jsonify({'status':'auth_started'})
                try:
                    current_app.logger.info('Starting ASK CLI no-browser configure')
                    _setup_logs.append('Starting ASK CLI no-browser configure. Follow the auth URL printed in logs.')
                    # Spawn ask configure inside a pseudo-tty so it prints the auth URL.
                    master_fd, slave_fd = pty.openpty()
                    auth_cmd = ['ask','configure','--no-browser']
                    _setup_auth_proc = subprocess.Popen(auth_cmd, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, close_fds=True)
                    os.close(slave_fd)
                    # remember the endpoint requested so we can start creation after auth
                    global _pending_endpoint, _setup_auth_master_fd
                    _pending_endpoint = endpoint
                    try:
                        _PENDING_FILE.write_text(endpoint)
                    except Exception:
                        pass
                    _setup_auth_master_fd = master_fd
                    t = threading.Thread(target=_read_master_loop, args=(master_fd,'ASK'), daemon=True)
                    t.start()
                    # Try to capture any immediate output that may have been written
                    try:
                        time.sleep(0.1)
                        try:
                            initial = os.read(master_fd, 4096)
                        except OSError:
                            initial = b''
                        if initial:
                            try:
                                s = initial.decode('utf-8', errors='replace')
                            except Exception:
                                s = str(initial)
                            for ln in s.splitlines():
                                _enqueue_setup_log(f'[ASK] {ln}')
                    except Exception:
                        pass
                    current_app.logger.info('ask configure started, pid=%s master_fd=%s', getattr(_setup_auth_proc, 'pid', None), master_fd)
                    return jsonify({'status':'auth_started'})
                except Exception as e:
                    _setup_logs.append(f'Failed to start auth: {e}')
                    current_app.logger.exception('failed starting ask configure')
                    return jsonify({'error':'failed starting auth'}), 500

        # ASK already configured: start the create script directly
        try:
            current_app.logger.info('Launching setup script: %s', _create_script())
            _setup_logs.append(f'Starting setup: endpoint={endpoint} profile={profile} locale={locale} stage={stage}')
            proc = _start_create(endpoint)
            current_app.logger.info('setup script started, pid=%s', getattr(proc, 'pid', None))
            return jsonify({'status': 'started'})
        except Exception as e:
            _setup_logs.append(f'Failed to start setup script: {e}')
            current_app.logger.exception('failed starting setup script')
            return jsonify({'error': 'failed starting setup script'}), 500


@setup_bp.route('/setup/code', methods=['POST'])
def setup_code():
    """Accept the auth code from the user and forward it to the running `ask configure --no-browser` process.
    After auth completes successfully, start the create-skill script.
    """
    global _setup_auth_proc, _setup_proc
    data = request.get_json(silent=True) or {}
    code = data.get('code')
    if not code:
        return jsonify({'error':'missing code'}), 400

    with _setup_auth_lock:
        if not _setup_auth_proc or _setup_auth_proc.poll() is not None:
            return jsonify({'error':'auth not running'}), 400
        try:
            # Write the code into the pty master so the ask process receives it
            global _setup_auth_master_fd
            if _setup_auth_master_fd is None:
                raise RuntimeError('auth master fd not available')
            os.write(_setup_auth_master_fd, (code + '\n').encode('utf-8'))
            # Attempt to auto-respond 'Y' to the AWS linking prompt that follows
            # the authorization code exchange. Send a short delay then write 'Y\n'
            # if the auth process is still running and the master fd is available.
            try:
                time.sleep(0.2)
                if _setup_auth_master_fd is not None and _setup_auth_proc and _setup_auth_proc.poll() is None:
                    try:
                        os.write(_setup_auth_master_fd, b'n\n')
                        _enqueue_setup_log('[ASK] Auto-responded N to AWS linking prompt')
                    except Exception as _e:
                        _enqueue_setup_log(f'[ASK] Auto-respond N failed: {_e}')
            except Exception:
                pass
        except Exception as e:
            _setup_logs.append(f'Failed to submit code: {e}')
            return jsonify({'error': str(e)}), 500

    # Wait for auth process to exit (short timeout)
    timeout = 120
    waited = 0
    while waited < timeout:
        if _setup_auth_proc.poll() is not None:
            break
        time.sleep(1)
        waited += 1

    rc = _setup_auth_proc.poll()
    if rc is None:
        _setup_logs.append('Auth process did not complete within timeout')
        return jsonify({'error':'auth timeout'}), 500
    if rc != 0:
        _setup_logs.append(f'Auth process exited with code {rc}')
        return jsonify({'error':f'auth failed (rc {rc})'}), 500

    if not has_functional_cli_config(profile='default'):
        _setup_logs.append('Auth completed but cli_config is still non-functional; run setup again and verify ASK login')
        return jsonify({'error': 'auth completed but cli_config is non-functional'}), 500

    _setup_logs.append('Auth completed successfully; starting skill creation')

    # Now start the create-skill script (use fixed options)
    # Use the pending endpoint saved when auth was started
    global _pending_endpoint
    endpoint_val = _pending_endpoint
    if not endpoint_val:
        # try to recover from tmp file in case the app restarted or state was lost
        try:
            if _PENDING_FILE.exists():
                endpoint_val = _PENDING_FILE.read_text().strip()
                if endpoint_val:
                    _enqueue_setup_log(f'Recovered endpoint from {_PENDING_FILE}')
        except Exception:
            pass
    if not endpoint_val:
        _setup_logs.append('Error: missing endpoint context; call /setup/start first')
        return jsonify({'error':'missing endpoint context; call /setup/start first'}), 400

    # Start the create script now
    try:
        _enqueue_setup_log(f'Starting create script: {_create_command(endpoint_val)}')
        try:
            proc = _start_create(endpoint_val)
        except Exception as e:
            _enqueue_setup_log(f'Failed to spawn create script: {e}')
            raise
        _enqueue_setup_log(f'Create script pid={getattr(proc, "pid", None)}')
        # Quick check: if the process exits immediately, capture and log its output and rc
        time.sleep(0.25)
        try:
            rc = proc.poll()
            if rc is not None:
                _enqueue_setup_log(f'Create script exited immediately with rc={rc}')
                try:
                    if proc.stdout:
                        out = proc.stdout.read()
                        if out:
                            for ln in out.splitlines():
                                _enqueue_setup_log(f'[CREATE-OUT] {ln}')
                except Exception:
                    pass
        except Exception as e:
            _enqueue_setup_log(f'Error while checking create script immediate status: {e}')
        # clear pending endpoint after starting
        _pending_endpoint = None
        try:
            if _PENDING_FILE.exists():
                _PENDING_FILE.unlink()
        except Exception:
            pass
        return jsonify({'status':'started'})
    except Exception as e:
        _setup_logs.append(f'Failed to start setup script after auth: {e}')
        return jsonify({'error':'failed to start setup script after auth'}), 500


@setup_bp.route('/setup/stop', methods=['POST'])
def setup_stop():
    global _setup_proc
    with _setup_lock:
        if not _setup_proc:
            return jsonify({'status':'no-process'})
        try:
            _setup_proc.terminate()
        except Exception:
            pass
        _setup_proc = None
    return jsonify({'status':'stopped'})

