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
from music_assistant_models.enums import PlaybackState
from music_assistant_models.errors import MusicAssistantError

from env_secrets import get_env_secret
from . import track_time

logger = logging.getLogger(__name__)

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
                    queue.state == PlaybackState.PAUSED)


def get_current_track_time(player_id):
    """(duration_ms, elapsed_ms, paused) of MA's current track on player_id, or None.

    duration_ms is None when MA doesn't know the length (e.g. radio).
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


async def _resume_at(server_url, token, player_id, position_ms):
    async with aiohttp.ClientSession() as session:
        async with MusicAssistantClient(server_url, session, token=token) as client:
            queue = await client.player_queues.get_active_queue(player_id)
            if queue is None or queue.current_item is None:
                raise ValueError(f"No active queue/current track for player {player_id}")
            position = track_time.resume_position_s(position_ms, queue.elapsed_time,
                                                    queue.current_item.duration)
            await client.player_queues.seek(queue.queue_id, position)
            return position * 1000


def resume_at(player_id, position_ms=None):
    """Resume MA's current track at position_ms (default: where MA paused).

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
        return asyncio.run(_resume_at(server_url, token, player_id, position_ms))
    except (CannotConnect, ConnectionFailed, InvalidServerVersion) as e:
        logger.error("Could not connect to Music Assistant at %s: %s", server_url, e)
    except MusicAssistantError as e:
        logger.error("Music Assistant rejected resume for player %s: %s", player_id, e)
    except Exception:
        logger.exception("Unexpected error resuming MA player %s", player_id)
    return None
