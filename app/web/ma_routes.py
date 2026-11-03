"""MA's requests to the skill, under /ma: its new streams and its pause and resume."""

from flask import Blueprint, jsonify, request
import time
import logging
from skill import actions, clock, devices, handoff, store, view, watch
from skill.ma import player
from skill.ma.errors import or_none
from skill.page.html import channel

logger = logging.getLogger(__name__)

ma_bp = Blueprint('ma', __name__)


def _fall_back_to_speech(player_id):
    """For a hand-off the page missed: have MA do it the spoken way."""
    def missed(device_id, command):
        logger.warning("Open page on %s missed the %s hand-off: asking MA to do it the spoken way",
                       player_id, command)
        if command == "resume":
            if not actions.run(device_id, actions.Play()).ok:
                logger.warning("MA did not resend the stream for %s", player_id)
        elif command == "stream":
            # MA's new stream may be a seek in its own app: it plays from MA's place, not the page's pause.
            start = or_none("resume", player.resume_at, player_id)
            if start is None:
                logger.warning("MA did not resend the stream for %s", player_id)
            else:
                actions.playing(device_id)
        elif command == "pause":
            # Not actions.Pause: the echo mark of the pause just sent would swallow this one.
            actions.mark_ma_triggered(device_id, "pause")
            if not or_none("send pause", player.send_player_command, player_id, "pause"):
                logger.warning("MA did not pause %s again", player_id)
    return missed


def _offer_to_open_page(data, command, value=None):
    """True if the open pages of MA's player take command, so MA needn't speak.

    Only when MA says it can leave its utterance out: an MA that speaks
    anyway brings up a new page, and the open one must not switch as well.
    """
    player_id = data.get('playerId')
    if not data.get('canSkipSpeech') or not player_id:
        return False
    taken = False
    for device_id in devices.get_devices_for_player(player_id):
        # MA speaks a missed hand-off once, for the first page that took it.
        on_missed = None if taken else _fall_back_to_speech(player_id)
        if handoff.offer(device_id, command, value, on_missed=on_missed):
            logger.info("Handing %s to the open page on %s", command, player_id)
            clock.spawn(channel.deliver, device_id, name="handoff")
            taken = True
            if command == "resume":
                break   # the page asks MA to play: once is enough
    return taken


def _silent_pause(data):
    """True if MA's pause is one the skill asked for after the page closed (see
    handoff.expect_silent_pause), so MA needn't speak."""
    player_id = data.get('playerId')
    if not data.get('canSkipSpeech') or not player_id:
        return False
    for device_id in devices.get_devices_for_player(player_id):
        if handoff.takes_silent_pause(device_id):
            logger.info("Pause for %s needs no speech: its page has closed", player_id)
            return True
    return False


@ma_bp.route('/push-url', methods=['POST'])
def push_url():
    data = request.get_json(silent=True) or {}
    stream_url = data.get('streamUrl')
    if not stream_url:
        return jsonify({'error': 'Missing required fields'}), 400

    stream_url = view.public_url(stream_url)
    image_url = view.public_url(data.get('imageUrl'))

    player_id = store.save({
        'streamUrl': stream_url,
        'title': data.get('title'),
        'artist': data.get('artist'),
        'album': data.get('album'),
        'imageUrl': image_url,
        'playerId': data.get('playerId'),
        'timestamp': time.time()
    })
    if not data.get('playerId'):
        # MA's new metadata for its stream (a track change in its flow): to the player's HTML pages.
        clock.spawn(channel.news, devices.get_devices_for_player(player_id) if player_id else None,
                    name="news")
    page_live = _offer_to_open_page(data, 'stream', stream_url)
    if data.get('playerId') and not page_live:
        watch.stream_sent(devices.get_devices_for_player(player_id))
    if not page_live:
        devices.wait_for_pairing(data.get('playerId'))
    return jsonify({'status': 'ok', 'version': store.version(), 'pageLive': page_live})


@ma_bp.route('/control', methods=['POST'])
def control():
    """MA's pause/resume: {playerId, command, canSkipSpeech}; pageLive: leave it to us."""
    data = request.get_json(silent=True) or {}
    command = data.get('command')
    if command not in ('pause', 'resume'):
        return jsonify({'error': 'Unsupported command'}), 400
    page_live = (_offer_to_open_page(data, command)
                 or (command == 'pause' and _silent_pause(data)))
    return jsonify({'status': 'ok', 'pageLive': page_live})


@ma_bp.route('/latest-url', methods=['GET'])
def latest_url():
    latest = store.for_player(None)
    if not latest:
        return jsonify({'error': 'No URL available'}), 404
    return jsonify(latest)
