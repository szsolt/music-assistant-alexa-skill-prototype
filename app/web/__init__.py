"""The Flask app: the skill's endpoint for Alexa, MA's routes, the player page and the web UI."""

import json
import logging
import os
import threading
import time

from flask import Flask, current_app, g, request
from flask_ask_sdk.skill_adapter import SkillAdapter
from werkzeug.middleware.proxy_fix import ProxyFix

from skill import echo, store, watch
from skill.lambda_function import sb
from skill.ma import client as ma_client
from skill.page.html.routes import page_bp

from . import auth
from .devices import devices_bp
from .ma_routes import ma_bp
from .setup import setup_bp
from .simulator import simulator_bp
from .status import status_bp

logger = logging.getLogger(__name__)


def create_app():
    """The app, with every route. Starts nothing: see start_background."""
    app = Flask(__name__)
    app.config['INTENT_LOGS'] = []     # every Alexa request, for the devices page
    app.config['INTENT_LOGS_MAXLEN'] = 500
    # Without a skill ID ask-sdk skips the check that a request is meant for this skill.
    sb.skill_id = os.environ.get('SKILL_ID')
    app.config['ALEXA_SKILL'] = sb.create()   # the simulator page runs requests on it
    alexa = SkillAdapter(skill=app.config['ALEXA_SKILL'], skill_id=sb.skill_id, app=app)
    app.add_url_rule('/', 'invoke_skill', alexa.dispatch_request, methods=['POST'])

    app.before_request(auth.check)
    app.before_request(_capture_incoming_intent)
    app.after_request(_record_incoming_intent)
    # A request that failed before the skill's reply must not leave its MA deadline to the thread's next one.
    app.teardown_request(lambda error: ma_client.end_request())

    app.register_blueprint(ma_bp, url_prefix='/ma')
    for blueprint in (setup_bp, status_bp, simulator_bp, devices_bp, page_bp):
        app.register_blueprint(blueprint)

    # Behind a reverse proxy, request.host_url and request.scheme are the client's.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    return app


_started = False
_start_lock = threading.Lock()


def start_background():
    """Bring back the open pages, then start the background work. Once per process: later calls do nothing."""
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    # Voice commands: keep the slot types from MA's library names up to date.
    try:
        from skill.voice import lists as voice_lists
        voice_lists.start()
    except Exception:
        logger.exception('Could not start the voice names job')
    # MA's latest streams and the pages that were open before this start (see skill/echo.py).
    store.load()
    echo.load()
    # Pause MA when a player page goes quiet (see skill/watch.py).
    watch.start_sweeping()
    # The skill's one MA connection; the pages' "Up next" follows its queue changes.
    try:
        from skill.ma import events as ma_events
        ma_events.start()
        ma_client.start()
    except Exception:
        logger.exception("Could not start the connection to MA")


def _capture_incoming_intent():
    """Keep the Alexa request's payload, for the devices page."""
    if request.path == '/' and request.method == 'POST':
        payload = None
        try:
            payload = request.get_json(silent=True)
        except Exception:
            payload = None
        if not payload:
            try:
                raw = request.get_data(as_text=True)
                if raw:
                    try:
                        payload = json.loads(raw)
                    except Exception:
                        payload = None
            except Exception:
                payload = None
        if not payload:
            try:
                if request.form:
                    intent = request.form.get('intent')
                    raw_slots = request.form.get('slots')
                    if intent:
                        payload = {"version": "1.0", "request": {"type": "IntentRequest", "intent": {"name": intent}}}
                        if raw_slots:
                            try:
                                payload['request']['intent']['slots'] = json.loads(raw_slots)
                            except Exception:
                                pass
            except Exception:
                pass

        g._incoming_alexa_payload = payload or {}
        g._incoming_alexa_ts = time.time()


def _record_incoming_intent(response):
    if getattr(g, '_incoming_alexa_payload', None) is not None:
        try:
            logs = current_app.config.setdefault('INTENT_LOGS', [])
            entry = {
                'incoming': g._incoming_alexa_payload,
                'response_status': response.status_code,
                'response_body': response.get_data(as_text=True),
                'ts': getattr(g, '_incoming_alexa_ts', None)
            }
            logs.append(entry)
            maxlen = current_app.config.get('INTENT_LOGS_MAXLEN', 500)
            if len(logs) > maxlen:
                del logs[0:len(logs)-maxlen]
        except Exception:
            pass
    return response
