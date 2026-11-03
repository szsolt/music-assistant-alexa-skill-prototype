"""The web UI's one login check: HTTP Basic auth against APP_USERNAME and APP_PASSWORD."""

import hmac

from flask import Response, request

from env_secrets import get_env_secret


def _same(given, expected):
    """True if given is expected, compared in constant time. Never for a missing value."""
    return given is not None and expected is not None and hmac.compare_digest(given.encode(), expected.encode())


def logged_in(authorization):
    """True if authorization (the request's) has the app's user and password, or none is set."""
    user, password = get_env_secret('APP_USERNAME'), get_env_secret('APP_PASSWORD')
    if not user and not password:
        return True
    return bool(authorization) and _same(authorization.username, user) and _same(authorization.password, password)


def check():
    """Before each request: 401 without the login, except for Alexa's requests and the Echo's player page."""
    # Alexa's requests: the skill endpoint checks Amazon's signature instead
    if request.path == '/' and request.method == 'POST':
        return None
    # The HTML player page and its logo are loaded by the Show, which has no credentials
    if request.path.startswith('/bell/') and request.method == 'GET':
        return None
    # The HTML player page's events: its page id is its only credential (see skill/page/html/routes.py)
    if request.path.startswith('/bell/html/') and request.method == 'POST':
        return None
    if logged_in(request.authorization):
        return None
    # MA's routes always answered their 401 in plain text
    mimetype = 'text/plain' if request.path.startswith('/ma/') else 'text/html'
    return Response('Access denied', 401, {'WWW-Authenticate': 'Basic realm="music-assistant-skill"'}, mimetype=mimetype)
