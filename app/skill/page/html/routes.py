"""The HTML player page's routes: the page, its events, its reports, its buttons and its logo."""

import json
import re
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, send_from_directory

from . import channel, commands

page_bp = Blueprint('page_bp', __name__)

PAGE_DIR = Path(__file__).parent
LOGO_PATH = PAGE_DIR.parent.parent / 'ma_logo.png'


def page_html():
    """page.html with each <!--include:name--> replaced by that file, so the browser gets one page."""
    return re.sub(r'<!--include:([\w.]+)-->', lambda m: (PAGE_DIR / m.group(1)).read_text(),
                  (PAGE_DIR / 'page.html').read_text())


@page_bp.route('/bell/logo.png', methods=['GET'])
def bell_logo():
    # Music Assistant's logo in the HTML page's header.
    with open(LOGO_PATH, 'rb') as f:
        resp = Response(f.read(), mimetype='image/png')
    resp.headers['Cache-Control'] = 'max-age=86400'
    return resp


@page_bp.route('/bell/html/lib/<name>', methods=['GET'])
def html_page_lib(name):
    # MilkDrop for the visualiser, as MA's app runs it.
    return send_from_directory(PAGE_DIR / 'lib', name, mimetype='text/javascript', max_age=86400)


@page_bp.route('/bell/html/<page>', methods=['GET'])
def html_player_page(page):
    # The HTML player page, with its start state inline.
    start = channel.start_state(page)
    if start is None:
        return Response('Unknown page', 404)
    start = json.dumps(start).replace('</', '<\\/')
    resp = Response(page_html().replace('/*START*/null', start), mimetype='text/html')
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@page_bp.route('/bell/html/<page>/events', methods=['GET'])
def html_player_events(page):
    if not channel.held(page):
        return Response('Unknown page', 404)
    resp = Response(channel.stream(page), mimetype='text/event-stream')
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@page_bp.route('/bell/html/<page>/state', methods=['POST'])
def html_player_state(page):
    body = request.get_json(silent=True) or {}
    try:
        seen, at, playing = int(body['seen']), max(int(body['at']), 0), bool(body['playing'])
    except (KeyError, TypeError, ValueError):
        return Response('Bad state', 400)
    answer = channel.report(page, seen, at, playing)
    if answer is None:
        return Response('Unknown page', 404)
    return jsonify(answer) if answer else Response(status=204)


@page_bp.route('/bell/html/<page>/action', methods=['POST'])
def html_player_action(page):
    try:
        done = commands.run(page, request.get_json(silent=True))
    except ValueError:
        return Response('Bad action', 400)
    if not done:
        return Response('Unknown page', 404)
    return Response(status=204)
