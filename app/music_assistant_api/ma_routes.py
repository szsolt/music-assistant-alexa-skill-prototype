"""Route definitions for music_assistant_api (ma_routes)."""

from flask import jsonify, request
import os
import time
from urllib.parse import urlparse, urlunparse
import logging
import shared_store
from skill import device_mapping, live_page, ma_control

logger = logging.getLogger(__name__)


def _rewrite_url(url: str) -> str:
    """Rewrite internal Music Assistant URLs to public hostname."""
    if not url:
        return url
    ma_hostname = os.environ.get('MA_HOSTNAME', '').strip()
    if not ma_hostname:
        return url
    try:
        parsed = urlparse(url)
        if not parsed.hostname:
            return url
        rewritten = urlunparse((
            'https', ma_hostname, parsed.path,
            parsed.params, parsed.query, parsed.fragment
        ))
        return rewritten
    except Exception:
        return url


def _fall_back_to_speech(player_id):
    """For a hand-off the page missed: have MA do it the spoken way."""
    def missed(device_id, command):
        logger.warning("Open page on %s missed the %s hand-off: asking MA to do it the spoken way",
                       player_id, command)
        if command in ("stream", "resume"):
            if ma_control.resume_at(player_id) is None:
                logger.warning("MA did not resend the stream for %s", player_id)
        elif command == "pause":
            # MA is paused already and the Echo still plays: MA pauses again,
            # now speaking (the page counts as closed); its echo is ours.
            ma_control.mark_ma_triggered(device_id, "pause")
            if not ma_control.send_player_command(player_id, "pause"):
                logger.warning("MA did not pause %s again", player_id)
    return missed


def _offer_to_open_page(data, command, value=None):
    """True if an open page of MA's player takes command, so MA needn't speak.

    Only when MA says it can leave its utterance out: an MA that speaks
    anyway brings up a new page, and the open one must not switch as well.
    """
    player_id = data.get('playerId')
    if not data.get('canSkipSpeech') or not player_id:
        return False
    for device_id in device_mapping.get_devices_for_player(player_id):
        if live_page.offer(device_id, command, value, on_missed=_fall_back_to_speech(player_id)):
            logger.info("Handing %s to the open page on %s", command, player_id)
            return True
    return False


def register_routes(bp):
    @bp.route('/push-url', methods=['POST'])
    def push_url():
        data = request.get_json(silent=True) or {}
        stream_url = data.get('streamUrl')
        if not stream_url:
            return jsonify({'error': 'Missing required fields'}), 400

        stream_url = _rewrite_url(stream_url)
        image_url = _rewrite_url(data.get('imageUrl'))

        shared_store._version += 1
        shared_store._store = {
            'streamUrl': stream_url,
            'title': data.get('title'),
            'artist': data.get('artist'),
            'album': data.get('album'),
            'imageUrl': image_url,
            'playerId': data.get('playerId'),
            'version': shared_store._version,
            'timestamp': time.time()
        }
        page_live = _offer_to_open_page(data, 'stream', stream_url)
        if not page_live:
            device_mapping.wait_for_pairing(data.get('playerId'))
        return jsonify({'status': 'ok', 'version': shared_store._version, 'pageLive': page_live})

    @bp.route('/control', methods=['POST'])
    def control():
        """MA's pause/resume: {playerId, command, canSkipSpeech}; pageLive: leave it to us."""
        data = request.get_json(silent=True) or {}
        command = data.get('command')
        if command not in ('pause', 'resume'):
            return jsonify({'error': 'Unsupported command'}), 400
        return jsonify({'status': 'ok', 'pageLive': _offer_to_open_page(data, command)})

    @bp.route('/latest-url', methods=['GET'])
    def latest_url():
        if not shared_store._store:
            return jsonify({'error': 'No URL available'}), 404
        return jsonify(shared_store._store)
