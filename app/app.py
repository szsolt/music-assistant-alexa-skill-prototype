"""The skill's web server: python app.py."""

import json
import logging
import os
import sys
import threading


def _load_addon_options_into_env():
    """Load Home Assistant add-on options from /data/options.json."""
    options_path = '/data/options.json'
    try:
        if not os.path.exists(options_path):
            return {}
        with open(options_path, 'r', encoding='utf-8') as f:
            options = json.load(f)
        if not isinstance(options, dict):
            return {}

        loaded = {}
        for key, value in options.items():
            if value is None:
                continue
            os.environ[str(key)] = str(value)
            loaded[str(key)] = str(value)
        return loaded
    except Exception:
        return {}


def _safe_options_for_log(options):
    redacted = {}
    secret_keys = {'APP_USERNAME', 'APP_PASSWORD'}
    for key, value in options.items():
        if key in secret_keys:
            redacted[key] = 'set' if value else ''
        else:
            redacted[key] = value
    return redacted


def _one_at_a_time(check):
    # The certificate check reads the shared trust roots, which asn1crypto
    # parses lazily and not thread-safely: two requests right after a start
    # (a page's pull and a button press) failed with KeyErrors.
    lock = threading.Lock()

    def locked(self, cert_chain):
        with lock:
            return check(self, cert_chain)
    return locked


def main():
    loaded_options = _load_addon_options_into_env()
    # Ensure boto3 has a default region in container/dev environments to avoid
    # NoRegionError during imports that create AWS clients at module import time.
    os.environ.setdefault('AWS_REGION', os.environ.get('AWS_DEFAULT_REGION', 'us-east-1'))
    os.environ.setdefault('AWS_DEFAULT_REGION', os.environ.get('AWS_DEFAULT_REGION', 'us-east-1'))
    from skill import log_setup
    log_setup.setup()
    if not os.environ.get('SKILL_ID'):
        sys.exit('SKILL_ID is not set')

    from ask_sdk_webservice_support.verifier import RequestVerifier
    from web.setup_helpers import ask_home_from_credentials_dir
    import web
    from web import setup

    RequestVerifier._validate_cert_chain = _one_at_a_time(RequestVerifier._validate_cert_chain)
    app = web.create_app()
    if loaded_options:
        app.logger.info('Loaded add-on options from /data/options.json: %s', _safe_options_for_log(loaded_options))
    # Optionally silence HTTP request logs (werkzeug/urllib3) when running
    # in container or debugger. Set QUIET_HTTP=0 to keep request logging.
    if os.environ.get('QUIET_HTTP', '1').lower() in ('1', 'true', 'yes', 'on'):
        logging.getLogger('werkzeug').setLevel(logging.WARNING)
        logging.getLogger('urllib3').setLevel(logging.WARNING)
        # Also reduce Flask's internal request logging
        logging.getLogger('flask.app').setLevel(logging.WARNING)
    # Allow overriding where ASK CLI stores credentials so they persist across containers.
    # If ASK_CREDENTIALS_DIR is set (e.g. /root/.ask), set HOME to its parent so
    # tools that rely on ~/.ask (ASK CLI) use the mounted location.
    try:
        ask_home = ask_home_from_credentials_dir()
        if ask_home:
            os.environ['HOME'] = ask_home
            app.logger.info('Using ASK credentials under HOME=%s', ask_home)
    except Exception:
        pass
    setup.register_signals()

    port = int(os.environ.get('PORT', '5000'))
    host = os.environ.get('HOST', '0.0.0.0')
    # Respect FLASK_DEBUG (1 enables debug mode) and FLASK_RELOADER (1 enables reloader)
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    use_reloader = debug and os.environ.get('FLASK_RELOADER', '0') == '1'
    # The reloader's watching process serves nothing: only the process it runs starts the background work.
    if not use_reloader or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        web.start_background()
    app.run(debug=debug, use_reloader=use_reloader, host=host, port=port)


if __name__ == "__main__":
    main()
