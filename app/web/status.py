from flask import Blueprint, request, jsonify, Response
from markupsafe import escape
import json
import os
import re
import shutil
import subprocess
import urllib.parse

from pathlib import Path
from .setup_helpers import has_functional_cli_config
from skill import store
from skill.ma.errors import safe_error

status_bp = Blueprint('status_bp', __name__)


def _build_status_json():
    skill_html = '<span class="led green"></span> Skill running'

    skill_ask_html = '<span class="muted">ASK CLI check unavailable</span>'
    try:
        skill_host = os.environ.get('SKILL_HOSTNAME', '').strip()
        if shutil.which('ask') and skill_host:
            if not has_functional_cli_config(profile='default'):
                skill_ask_html = '<span class="led yellow"></span> ASK CLI credentials are not configured for profile default'
                try:
                    skill_ask_html += ' <button onclick="window.location=\'/setup\'" style="margin-left:8px">Open Setup</button>'
                except Exception:
                    pass
            else:
                ls = subprocess.run(['ask', 'smapi', 'list-skills-for-vendor', '--profile', 'default'], capture_output=True, text=True)
                out = ls.stdout or ls.stderr or ''
                m = re.search(r'amzn1\.ask\.skill\.[0-9a-fA-F\-]+', out)
                if not m:
                    skill_ask_html = '<span class="led red"></span> Music Assistant Skill interaction model not found via ASK CLI'
                else:
                    sid = m.group(0)
                    mf = subprocess.run(['ask', 'smapi', 'get-skill-manifest', '--skill-id', sid, '--profile', 'default'], capture_output=True, text=True)
                    mf_out = mf.stdout or mf.stderr or ''
                    mm = re.search(r'https?://[^"\s\)\]]+', mf_out)
                    try:
                        if skill_host.startswith('http://') or skill_host.startswith('https://'):
                            cfg_host = urllib.parse.urlparse(skill_host).netloc
                        else:
                            cfg_host = skill_host
                    except Exception:
                        cfg_host = skill_host

                    testing_enabled = False
                    try:
                        en = subprocess.run(['ask', 'smapi', 'get-skill-enablement-status', '--skill-id', sid, '--stage', 'development', '--profile', 'default'], capture_output=True, text=True)
                        en_out = en.stdout or en.stderr or ''
                        if en.returncode == 0 or 'Command executed successfully' in en_out:
                            testing_enabled = True
                        else:
                            if re.search(r'\[Error\]:\s*\{', en_out) or re.search(r'404', en_out):
                                testing_enabled = False
                            elif re.search(r'"isEnabled"\s*:\s*true', en_out, re.IGNORECASE) or re.search(r'"enabled"\s*:\s*true', en_out, re.IGNORECASE):
                                testing_enabled = True
                    except Exception:
                        testing_enabled = False

                    is_green = False
                    if not mm:
                        testing_msg = 'testing enabled' if testing_enabled else 'testing not enabled'
                        skill_ask_html = f'<span class="led yellow"></span> Music Assistant Skill interaction model {escape(sid)} found; endpoint not set ({testing_msg})'
                    else:
                        uri = mm.group(0)
                        try:
                            parsed = urllib.parse.urlparse(uri)
                            manifest_host = parsed.netloc
                            locale_list = []
                            try:
                                mf_json = None
                                try:
                                    mf_json = json.loads(mf_out)
                                except Exception:
                                    idx = mf_out.find('{')
                                    if idx != -1:
                                        try:
                                            mf_json = json.loads(mf_out[idx:])
                                        except Exception:
                                            mf_json = None
                                if mf_json:
                                    locales_obj = mf_json.get('manifest', {}).get('publishingInformation', {}).get('locales', {})
                                    if isinstance(locales_obj, dict):
                                        locale_list = list(locales_obj.keys())
                            except Exception:
                                locale_list = []

                            locale_display = ','.join(locale_list) if locale_list else 'unknown'

                            if manifest_host == cfg_host:
                                if testing_enabled:
                                    skill_ask_html = f'<span class="led green"></span> Music Assistant Skill interaction model found; endpoint matches ({escape(manifest_host)}); testing enabled; locale: {escape(locale_display)}'
                                    is_green = True
                                else:
                                    skill_ask_html = f'<span class="led yellow"></span> Music Assistant Skill interaction model found and endpoint matches ({escape(manifest_host)}); testing NOT enabled'
                            else:
                                testing_note = 'testing enabled' if testing_enabled else 'testing not enabled'
                                skill_ask_html = f'<span class="led red"></span> Music Assistant Skill interaction model endpoint mismatch (manifest: {escape(manifest_host)} vs configured: {escape(cfg_host)}); {testing_note}'
                        except Exception:
                            testing_msg = 'testing enabled' if testing_enabled else 'testing not enabled'
                            skill_ask_html = f'<span class="led yellow"></span> Music Assistant Skill interaction model found; endpoint parse failed ({testing_msg})'

                    try:
                        if not is_green:
                            skill_ask_html += ' <button onclick="window.location=\'/setup\'" style="margin-left:8px">Open Setup</button>'
                    except Exception:
                        pass
        else:
            if not shutil.which('ask'):
                skill_ask_html = '<span class="muted">ask CLI not available in container</span>'
            else:
                skill_ask_html = '<span class="muted">SKILL_HOSTNAME not configured</span>'
    except Exception as e:
        skill_ask_html = f'<span class="muted">ASK check error: {escape(str(e))}</span>'

    ma_api_html = _compute_ma_api_html()
    metadata_html = _compute_metadata_html()

    return {'skill_html': skill_html, 'skill_ask_html': skill_ask_html, 'ma_api_html': ma_api_html, 'metadata_html': metadata_html, 'created': False}


def _compute_ma_api_html():
    """MA's latest push, as /ma/latest-url answers it."""
    latest = store.for_player(None)
    code, body = (200, latest) if latest else (404, {'error': 'No URL available'})
    content_preview = escape(json.dumps(body, indent=2, ensure_ascii=False, sort_keys=True))
    if latest:
        return (
            f'<span class="led green"></span> Music Assistant API reachable ({code}) — /ma/latest-url'
            f"<pre class='status-box' tabindex='0' style='white-space:pre-wrap;background:#f6f6f6;padding:8px;border-radius:4px;max-height:200px;overflow:auto;user-select:text'>"
            f"{content_preview}</pre>"
        )
    return (
        f'<span class="led red"></span> Music Assistant API responded {code} for /ma/latest-url'
        f"<pre class='status-box' tabindex='0' style='white-space:pre-wrap;background:#fdf2f2;padding:8px;border-radius:4px;max-height:200px;overflow:auto;user-select:text'>"
        f"{content_preview}</pre>"
    )


@status_bp.route('/status/ma', methods=['GET'])
def status_ma():
    return jsonify({'ma_api_html': _compute_ma_api_html()})


def _compute_metadata_html():
    """Compute HTML showing the current APL metadata being sent in refreshes."""
    try:
        from skill import view
        current = view.build(None)
        # only what MA's last push says: the rest comes from MA's queue per page
        metadata_info = {k: getattr(current, k) for k in ("title", "artist", "album", "cover")} if current else {}
        pretty_metadata = json.dumps(metadata_info, indent=2, ensure_ascii=False)
        content_preview = escape(pretty_metadata)
        
        if current:
            return (
                f'<span class="led green"></span> APL Metadata Refresh (current data)'
                f"<pre class='status-box' tabindex='0' style='white-space:pre-wrap;background:#f6f6f6;padding:8px;border-radius:4px;max-height:200px;overflow:auto;user-select:text'>"
                f"{content_preview}</pre>"
            )
        else:
            return (
                f'<span class="led yellow"></span> APL Metadata Refresh (no data loaded yet)'
                f"<pre class='status-box' tabindex='0' style='white-space:pre-wrap;background:#fff9e6;padding:8px;border-radius:4px;max-height:200px;overflow:auto;user-select:text'>"
                f"{content_preview}</pre>"
            )
    except Exception as e:
        return f'<span class="led red"></span> Error loading metadata: {escape(safe_error(e))}'


@status_bp.route('/status/metadata', methods=['GET'])
def status_metadata():
    return jsonify({'metadata_html': _compute_metadata_html()})

@status_bp.route('/status', methods=['GET'])
def status():
    # If client requested JSON, return the aggregated checks
    want_json = request.args.get('format') == 'json' or 'application/json' in (request.headers.get('Accept') or '')
    if want_json:
        return jsonify(_build_status_json())

    # Non-JSON: render status template
    try:
        tpl_path = Path(__file__).parent.parent / 'templates' / 'status.html'
        tpl = tpl_path.read_text()
        tpl = tpl.replace('__SKILL_HTML__', '<span class="led green"></span> Skill running')
        tpl = tpl.replace('__SKILL_ASK_HTML__', '<span class="muted">Checking ASK CLI status...</span>')
        tpl = tpl.replace('__MA_API_HTML__', '<span class="muted">Checking Music Assistant API...</span>')
        tpl = tpl.replace('__METADATA_HTML__', '<span class="muted">Loading APL metadata...</span>')
        tpl = tpl.replace('__VOICE_HTML__', _voice_html())
        return Response(tpl, status=200, mimetype='text/html')
    except Exception:
        html = """<!doctype html>
            <html>
            <head><meta charset="utf-8"><title>Service Status</title></head>
            <body>
                <h1>Service Status</h1>
                <div><span class=\"led green\"></span> Skill running</div>
                <div><span class=\"muted\">Checking ASK CLI status...</span></div>
                <div><span class=\"muted\">Checking Music Assistant API...</span></div>
            </body>
            </html>"""
        return Response(html, status=200, mimetype='text/html')


VOICE_BASE_DIR = Path(__file__).parent.parent / 'models'


def _voice_html():
    from skill import model_upload
    from skill.voice import lists as voice_lists, model as voice_model
    voice = voice_lists.load()
    if not voice:
        return '<span class="muted">Voice commands: library names not read yet</span>'
    counts = ', '.join(f'{n} {kind}s' for kind, n in voice.get('counts', {}).items())
    if model_upload.configured():
        state = 'uploaded automatically'
    else:
        locales = sorted(p.stem for p in VOICE_BASE_DIR.glob('*.json') if voice_model.template_for(p.stem))
        links = ' '.join(f'<a href="/status/voice-model/{l}.json">{l}</a>' for l in locales)
        state = ('not uploaded (no LWA credentials). Download the model and paste it into the '
                 'Alexa developer console JSON editor: ' + links)
    return f'Voice commands: {escape(counts)}; {state}'


@status_bp.route('/status/voice-model/<locale>.json', methods=['GET'])
def status_voice_model(locale):
    """The repo's model for locale with the voice commands and library names, for the developer console."""
    from skill.voice import lists as voice_lists
    base_path = VOICE_BASE_DIR / f'{locale}.json'
    voice = voice_lists.load()
    if not re.fullmatch(r'[a-z]{2}-[A-Z]{2}', locale) or not base_path.exists() or not voice:
        return jsonify({'error': 'unknown locale or library names not read yet'}), 404
    model = voice_lists.build_model(json.loads(base_path.read_text(encoding='utf-8')), locale, voice['types'])
    if model is None:
        return jsonify({'error': f'no voice commands for {locale} yet'}), 404
    body = json.dumps(model, ensure_ascii=False, indent=2)
    return Response(body, mimetype='application/json',
                    headers={'Content-Disposition': f'attachment; filename={locale}.json'})


@status_bp.route('/status/api', methods=['GET'])
def status_api():
    """Lightweight API used by the status UI to fetch aggregated checks."""
    return jsonify(_build_status_json())


@status_bp.route('/status/ask', methods=['GET'])
def status_ask():
    """Return only the ASK CLI check fragment used by the client UI."""
    data = _build_status_json()
    return jsonify({'skill_ask_html': data.get('skill_ask_html')})

