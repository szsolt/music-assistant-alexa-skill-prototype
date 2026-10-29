# -*- coding: utf-8 -*-
"""Send player commands to Music Assistant via music_assistant_client.

Next/Previous/StartOver are safe to send unconditionally: MA handles them
at the queue level and reacts by pushing new content via /ma/push-url,
not by speaking a command-like phrase.

Pause/Stop/Resume are different: the `alexa` MA player provider
implements them by speaking the corresponding phrase back into the
device via alexapy (Alexa.TextCommand - a direct API call, not TTS
through the speaker, so it reliably re-triggers a real intent request).
That would loop back into this same skill. Callers MUST use
mark_ma_triggered()/is_echo_of_ma_command() to suppress exactly the
one echoed intent this causes, rather than calling send_player_command
for these unconditionally.
"""

import asyncio
import logging
import threading
import time

import aiohttp
from music_assistant_client import MusicAssistantClient
from music_assistant_client.exceptions import (
    CannotConnect,
    ConnectionFailed,
    InvalidServerVersion,
)
from music_assistant_models.enums import ContentType, PlaybackState, QueueOption, RepeatMode
from music_assistant_models.errors import MusicAssistantError

from env_secrets import get_env_secret
from . import ma_events, track_time

logger = logging.getLogger(__name__)
# The client logs every connect.
logging.getLogger("music_assistant_client").setLevel(logging.WARNING)

SUPPORTED_COMMANDS = ("next", "previous", "start_over", "pause", "stop", "resume")

# How long to suppress the echoed intent caused by pause/stop/resume.
# Alexa.TextCommand is a direct API call (not acoustic), so the round trip
# is consistently fast; this just needs to comfortably cover it.
_ECHO_SUPPRESS_SECONDS = 8
_recent_ma_triggered = {}
_recent_lock = threading.Lock()


def mark_ma_triggered(device_id, command):
    """Record that we're about to send `command` to MA for device_id.

    Call this immediately before send_player_command() for pause/stop/resume,
    so the intent MA's alexa provider echoes back gets recognized and not
    re-forwarded to MA again.
    """
    if not device_id:
        return
    with _recent_lock:
        _recent_ma_triggered[(device_id, command)] = time.time() + _ECHO_SUPPRESS_SECONDS


def is_echo_of_ma_command(device_id, command):
    """Check (and consume) whether this looks like the echo of our own MA command."""
    if not device_id:
        return False
    key = (device_id, command)
    with _recent_lock:
        expires_at = _recent_ma_triggered.pop(key, None)
    return expires_at is not None and expires_at >= time.time()


async def _play_relative_index(client, player_id, offset):
    """Jump the active queue to current_index + offset (clamped to 0).

    MA's own queue/cmd/previous restarts the current track instead of
    going to the prior one once more than 5s have elapsed - always true
    for voice-triggered commands. Fetching the queue and calling
    play_index directly bypasses that heuristic. offset=0 restarts the
    current track (used for AMAZON.StartOverIntent).
    """
    queue = await client.player_queues.get_active_queue(player_id)
    if queue is None or queue.current_index is None:
        raise ValueError(f"No active queue/current track for player {player_id}")
    target_index = max(queue.current_index + offset, 0)
    await client.player_queues.play_index(queue.queue_id, target_index)


async def _send_command(server_url, token, player_id, command):
    async with aiohttp.ClientSession() as session:
        async with MusicAssistantClient(server_url, session, token=token) as client:
            if command == "next":
                await client.players.next_track(player_id)
            elif command == "previous":
                await _play_relative_index(client, player_id, -1)
            elif command == "start_over":
                await _play_relative_index(client, player_id, 0)
            elif command == "pause":
                await client.players.pause(player_id)
            elif command == "stop":
                await client.players.stop(player_id)
            elif command == "resume":
                # Not players.resume(): the alexa provider only overrides
                # play(), which is what speaks the AMAZON.ResumeIntent
                # utterance back into the device.
                await client.players.play(player_id)
            else:
                raise ValueError(f"Unsupported MA command: {command}")


def send_player_command(player_id, command):
    """Send a command to a Music Assistant player. Returns True on success.

    This opens a short-lived connection per call; commands are infrequent
    (voice-triggered), so the connection overhead is not a concern here.
    """
    if command not in SUPPORTED_COMMANDS:
        raise ValueError(f"Unsupported MA command: {command}")

    server_url = get_env_secret("MA_API_URL")
    token = get_env_secret("MA_API_TOKEN")

    if not server_url:
        logger.error("MA_API_URL is not set; cannot send %s command to MA", command)
        return False

    try:
        asyncio.run(_send_command(server_url, token, player_id, command))
        return True
    except (CannotConnect, ConnectionFailed, InvalidServerVersion) as e:
        logger.error("Could not connect to Music Assistant at %s: %s", server_url, e)
        return False
    except MusicAssistantError as e:
        logger.error("Music Assistant rejected %s command for player %s: %s", command, player_id, e)
        return False
    except Exception:
        logger.exception("Unexpected error sending %s command to MA player %s", command, player_id)
        return False


async def _get_track_time(server_url, token, player_id):
    async with aiohttp.ClientSession() as session:
        async with MusicAssistantClient(server_url, session, token=token) as client:
            queue = await client.player_queues.get_active_queue(player_id)
            if queue is None or queue.current_item is None:
                return None
            duration = queue.current_item.duration
            return (int(duration * 1000) if duration else None,
                    int(queue.corrected_elapsed_time * 1000),
                    queue.state == PlaybackState.PAUSED,
                    queue.next_item is None,
                    _upcoming(client, queue.next_item),
                    quality(queue.current_item))


# What the page calls a format, where MA's own name isn't the usual one.
_FORMAT_NAMES = {"mpeg": "MP3", "mp4": "AAC", "mp4a": "AAC", "m4a": "AAC", "m4b": "AAC",
                 "vorbis": "OGG", "wavpack": "WV"}


def quality(item):
    """The quality of a queue item's file, as the page shows it, or "" if unknown.

    "FLAC 16/44.1" for lossless (bit depth / kHz), "MP3 192k" for lossy.
    The file's, not what the Echo gets: MA streams it on as PCM.
    """
    media = getattr(item, "media_item", None)
    formats = [m.audio_format for m in getattr(media, "provider_mappings", None) or ()
               if m.available and m.audio_format]
    details = getattr(item, "streamdetails", None)
    fmt = formats[0] if formats else getattr(details, "audio_format", None)
    if fmt is None:
        return ""
    kind = fmt.codec_type if fmt.codec_type != ContentType.UNKNOWN else fmt.content_type
    if kind == ContentType.UNKNOWN:
        return ""
    name = _FORMAT_NAMES.get(kind.value, kind.value.upper())
    if kind.is_lossless():
        return f"{name} {fmt.bit_depth}/{fmt.sample_rate / 1000:g}"
    return f"{name} {fmt.bit_rate}k" if fmt.bit_rate else name


def _upcoming(client, item):
    """What the page shows for the queue's next item: title, artist, album, image, length, quality."""
    if item is None:
        return None
    media = item.media_item
    title = (media.name if media else None) or item.name or ""
    version = getattr(media, "version", None)
    if version:
        title = f"{title} ({version})"
    artist = getattr(media, "artist_str", "") or ""
    album = getattr(getattr(media, "album", None), "name", "") or ""
    try:
        image = client.get_media_item_image_url(item, size=512) or ""
    except Exception:
        image = ""
    duration = getattr(media, "duration", None) or item.duration
    return {"item_id": item.queue_item_id,
            "title": title,
            "secondary": artist,
            "album": album,
            "image": image,
            "duration_ms": int(duration * 1000) if duration else 0,
            "quality": quality(item)}


def get_current_track_time(player_id):
    """(duration_ms, elapsed_ms, paused, last, upcoming, quality) of MA's current track on player_id, or None.

    duration_ms is None when MA doesn't know the length (e.g. radio); last
    is True when nothing follows it in the queue. upcoming: the next
    track's title, secondary (artist), album, image, duration_ms (0 if
    unknown) and quality, or None: the page switches to it at the track's
    end. quality: the current track's (see quality()).
    Called once per track change from the APL refresh, not per refresh.
    """
    server_url = get_env_secret("MA_API_URL")
    token = get_env_secret("MA_API_TOKEN")
    if not server_url:
        logger.error("MA_API_URL is not set; cannot read track time from MA")
        return None
    try:
        return asyncio.run(_get_track_time(server_url, token, player_id))
    except (CannotConnect, ConnectionFailed, InvalidServerVersion) as e:
        logger.warning("Could not connect to Music Assistant at %s: %s", server_url, e)
    except MusicAssistantError as e:
        logger.warning("Music Assistant could not return the queue of player %s: %s", player_id, e)
    except Exception:
        logger.exception("Unexpected error reading track time of MA player %s", player_id)
    return None


def get_queue_state(player_id):
    """(state, elapsed_ms) of MA's queue on player_id, or None.

    state is MA's playback state: "playing", "paused" or "idle". For the
    player page's check every few seconds (bell.py): read from the event
    listener's copy, so it needs no connection of its own.
    """
    queue = ma_events.active_queue(player_id)
    if queue is None or queue.current_item is None:
        return None
    return queue.state.value, int(queue.corrected_elapsed_time * 1000)


def is_paused(player_id):
    """True if MA's queue on player_id is paused (False if unknown)."""
    result = get_current_track_time(player_id)
    return bool(result and result[2])


async def _resume_at(server_url, token, player_id, position_ms, item_id=None):
    async with aiohttp.ClientSession() as session:
        async with MusicAssistantClient(server_url, session, token=token) as client:
            queue = await client.player_queues.get_active_queue(player_id)
            if queue is None or queue.current_item is None:
                raise ValueError(f"No active queue/current track for player {player_id}")
            if item_id:
                await client.player_queues.play_index(queue.queue_id, item_id)
                return 0
            # corrected: where a playing queue is now, not at its last update
            position = track_time.resume_position_s(position_ms, queue.corrected_elapsed_time,
                                                    queue.current_item.duration)
            await client.player_queues.seek(queue.queue_id, position)
            return position * 1000


def resume_at(player_id, position_ms=None, item_id=None):
    """Resume MA's current track at position_ms (default: where MA is), or queue item item_id at its start.

    Returns the position used, in ms, or None on failure.

    Not players.play(): for this provider that speaks AMAZON.ResumeIntent
    back into the Echo, which reopens the old flow URL at the start of the
    track. A queue seek makes MA build a new stream from the position and
    push it like any track change, with no echoed intent.
    """
    server_url = get_env_secret("MA_API_URL")
    token = get_env_secret("MA_API_TOKEN")
    if not server_url:
        logger.error("MA_API_URL is not set; cannot resume MA player %s", player_id)
        return None
    try:
        return asyncio.run(_resume_at(server_url, token, player_id, position_ms, item_id))
    except (CannotConnect, ConnectionFailed, InvalidServerVersion) as e:
        logger.error("Could not connect to Music Assistant at %s: %s", server_url, e)
    except MusicAssistantError as e:
        logger.error("Music Assistant rejected resume for player %s: %s", player_id, e)
    except Exception:
        logger.exception("Unexpected error resuming MA player %s", player_id)
    return None


# The playlist opens this many songs before the playing one.
_WINDOW_BEFORE = 10


async def _all_items(client, queue):
    items = []
    while len(items) < queue.items:
        more = await client.player_queues.get_queue_items(queue.queue_id, limit=500, offset=len(items))
        if not more:
            break
        items += more
    return items


async def _queue_window(server_url, token, player_id, offset, limit):
    async with aiohttp.ClientSession() as session:
        async with MusicAssistantClient(server_url, session, token=token) as client:
            queue = await client.player_queues.get_active_queue(player_id)
            if queue is None:
                return None
            items = await _all_items(client, queue)
            index = queue.current_index or 0
            if offset is None:
                offset = max(0, index - _WINDOW_BEFORE)
            endless = queue.repeat_mode != RepeatMode.OFF or queue.is_dynamic
            return {"offset": offset, "total": len(items), "index": index,
                    "locked": max(index, queue.index_in_buffer or 0),
                    "after": -1 if endless else int(sum(item.duration or 0 for item in items[index + 1:]) * 1000),
                    "items": [_upcoming(client, item) for item in items[offset:offset + limit]]}


async def _edit_queue(server_url, token, player_id, kind, item_id, shift):
    async with aiohttp.ClientSession() as session:
        async with MusicAssistantClient(server_url, session, token=token) as client:
            queue = await client.player_queues.get_active_queue(player_id)
            if queue is None:
                return None
            queues = client.player_queues
            if kind == "move":
                await queues.move_item(queue.queue_id, item_id, pos_shift=shift)
            elif kind == "play_next":
                await queues.move_item(queue.queue_id, item_id, pos_shift=0)    # MA: 0 is "play next"
            elif kind == "move_end":
                await client.send_command("player_queues/move_item_end", queue_id=queue.queue_id,
                                          queue_item_id=item_id)
            elif kind == "delete":
                await queues.delete_item(queue.queue_id, item_id)
            elif kind == "mix":
                item = next((i for i in await _all_items(client, queue) if i.queue_item_id == item_id), None)
                if item is None or item.media_item is None:
                    return None
                await queues.play_media(queue.queue_id, f"radio_playlist://playlist/{item.media_item.uri}",
                                        QueueOption.REPLACE)
            else:
                raise ValueError(f"Unsupported queue edit: {kind}")
            return True


# Bars in the page's loudness graph; MA keeps 1800 points per song.


async def _wave_form(server_url, token, player_id, item_id):
    async with aiohttp.ClientSession() as session:
        async with MusicAssistantClient(server_url, session, token=token) as client:
            queue = await client.player_queues.get_active_queue(player_id)
            if queue is None:
                return None
            item = next((i for i in (queue.current_item, queue.next_item)
                         if i is not None and (not item_id or i.queue_item_id == item_id)), None)
            if item is None:
                return None
            # MA keys the analysis by the provider's own id, which the stream details carry.
            details = item.streamdetails
            if details is None:
                return None
            points = await client.send_command("audio_analysis/wave_form", item_id=details.item_id,
                                               provider_instance_id_or_domain=details.provider)
            if not points:
                return None
            return [round(p, 2) for p in points]


def _call(what, fn, player_id, *args):
    """fn(server_url, token, player_id, *args) on MA, or None on failure."""
    server_url = get_env_secret("MA_API_URL")
    if not server_url:
        logger.error("MA_API_URL is not set; cannot %s of MA player %s", what, player_id)
        return None
    try:
        return asyncio.run(fn(server_url, get_env_secret("MA_API_TOKEN"), player_id, *args))
    except (CannotConnect, ConnectionFailed, InvalidServerVersion) as e:
        logger.warning("Could not connect to Music Assistant at %s: %s", server_url, e)
    except MusicAssistantError as e:
        logger.warning("Music Assistant refused to %s of player %s: %s", what, player_id, e)
    except Exception:
        logger.exception("Unexpected error trying to %s of MA player %s", what, player_id)
    return None


def queue_window(player_id, offset=None, limit=110):
    """The playlist's rows: MA's queue items offset..offset+limit (default: from 10 before the playing one).

    {"offset", "total", "index" (playing), "locked" (MA can't move items up
    to here), "after" (ms of the songs after the playing one, -1 on repeat or
    radio), "items" (as _upcoming)}, or None.
    """
    return _call("read the queue", _queue_window, player_id, offset, limit)


def edit_queue(player_id, kind, item_id, shift=0):
    """Change MA's queue for item_id. True if MA did it.

    kind: "move" (shift places, positive: down), "play_next", "move_end",
    "delete", or "mix" (replace the queue with an endless mix of the song).
    """
    return bool(_call(f"{kind} a queue item", _edit_queue, player_id, kind, item_id, shift))


def wave_form(player_id, item_id=None):
    """The loudness graph of queue item item_id (default: the playing one), 0-1 per bar, or None.

    Only the playing and the next song: MA has a graph once it analysed the song.
    """
    return _call("read the loudness graph", _wave_form, player_id, item_id)
