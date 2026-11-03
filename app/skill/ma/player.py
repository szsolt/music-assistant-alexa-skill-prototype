# -*- coding: utf-8 -*-
"""MA's players and queues: transport, queue state, queue edits and transfer.

Next/Previous/StartOver are safe to send unconditionally: MA handles them
at the queue level and reacts by pushing new content via /ma/push-url,
not by speaking a command-like phrase.

Pause/Stop/Resume are different: the `alexa` MA player provider
implements them by speaking the corresponding phrase back into the
device via alexapy (Alexa.TextCommand - a direct API call, not TTS
through the speaker, so it reliably re-triggers a real intent request).
That would loop back into this same skill. Callers MUST use
actions.mark_ma_triggered()/is_echo_of_ma_command() to suppress exactly
the one echoed intent this causes, rather than calling send_player_command
for these unconditionally.

Every call raises MAUnavailable or MAFailed (see errors.py).
"""

from music_assistant_models.enums import ContentType, MediaType, PlaybackState, QueueOption, RepeatMode

from .. import clock, store, track_time
from . import library
from .client import LONG_TIMEOUT_S, active_queue, call, connected_client
from .errors import MAFailed, MAUnavailable

# What MA plays at a speed of choice, and skips through.
SPOKEN = (MediaType.AUDIOBOOK, MediaType.PODCAST_EPISODE)
SUPPORTED_COMMANDS = ("next", "previous", "start_over", "pause", "stop", "resume")
PLAY_OPTIONS = {"replace": QueueOption.REPLACE, "next": QueueOption.NEXT, "add": QueueOption.ADD}
REPEAT_MODES = ("off", "all", "one")
RANDOM_TRACKS = 100
# The playlist opens this many songs before the playing one.
_WINDOW_BEFORE = 10


async def _queue_id(client, player_id):
    queue = await client.player_queues.get_active_queue(player_id)
    return queue.queue_id if queue else player_id


# ---------- transport ----------

async def _start(player_id, start_ms, command):
    """Await command, which has MA stream player_id's queue from start_ms in a track (see store.starting)."""
    store.starting(player_id, start_ms)
    try:
        return await command
    except BaseException:
        store.starting(player_id, None)
        raise


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
    await _start(player_id, 0, client.player_queues.play_index(queue.queue_id, target_index))


async def _send_command(client, player_id, command):
    if command == "next":
        await _start(player_id, 0, client.players.next_track(player_id))
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
    return True


def send_player_command(player_id, command):
    """Send a command to a Music Assistant player. Returns True."""
    if command not in SUPPORTED_COMMANDS:
        raise ValueError(f"Unsupported MA command: {command}")
    return call(_send_command, player_id, command)


async def _resume_at(client, player_id, position_ms, item_id=None):
    queue = await client.player_queues.get_active_queue(player_id)
    if queue is None or queue.current_item is None:
        raise ValueError(f"No active queue/current track for player {player_id}")
    if item_id:
        await _start(player_id, 0, client.player_queues.play_index(queue.queue_id, item_id))
        return 0
    # corrected: where a playing queue is now, not at its last update.
    # The library track's length, as the page shows: MA cuts its queue item's to what it streamed.
    item = queue.current_item
    position = track_time.resume_position_s(position_ms, queue.corrected_elapsed_time,
                                            getattr(item.media_item, "duration", None) or item.duration)
    if position > (item.duration or 0):  # MA refuses a seek past its queue item's cut length
        command = client.player_queues.play_index(queue.queue_id, item.queue_item_id, seek_position=position)
    else:
        command = client.player_queues.seek(queue.queue_id, position)
    await _start(player_id, position * 1000, command)
    return position * 1000


def resume_at(player_id, position_ms=None, item_id=None):
    """Resume MA's current track at position_ms (default: where MA is), or queue item item_id at its start.

    Returns the position used, in ms.

    A queue seek, not players.play(): MA pushes a new stream from the position.
    """
    return call(_resume_at, player_id, position_ms, item_id)


# ---------- queue state ----------

def now_playing(player_id, queue=None):
    """What MA's queue on player_id plays, from the client's copy, as MA's own app shows it; None without a track.

    {"item_id", "title", "artist", "album", "image", "duration_ms" (None:
    unknown, e.g. radio), "quality", "elapsed_ms", "paused", "last", "next"
    (see _upcoming), "speed" (MA's playback speed; None: music, which has
    none), "modes" (with "sleep_at": when MA's sleep timer ends,
    clock.time(); None: off), "track_at" (when the track's start played, by
    MA's queue, clock.time(); None unless it plays), "anchored_at" (when MA's
    queue last set its place, clock.time())}. queue: MA's queue, if just asked.
    """
    client = connected_client()
    queue = queue or active_queue(player_id)
    player = client and client.players.get(player_id)
    if not player or queue is None or queue.current_item is None:
        return None
    item = queue.current_item
    now = _upcoming(client, item)
    speed = (queue.playback_speed or 1.0) if item.media_type in SPOKEN else None
    try:
        following = call(_next_item, queue)
    except (MAUnavailable, MAFailed):
        following = queue.next_item
    elapsed = queue.elapsed_time + (speed or 1) * (clock.time() - queue.elapsed_time_last_updated
                                                   if queue.state == PlaybackState.PLAYING else 0)
    # MA keeps its queue's place on the track's start; its player's own place isn't in its events
    track_at = (queue.elapsed_time_last_updated - queue.elapsed_time / (speed or 1)
                if queue.state == PlaybackState.PLAYING else None)
    return {"item_id": now["item_id"], "title": now["title"], "artist": now["secondary"], "album": now["album"],
            "image": now["image"], "duration_ms": now["duration_ms"] or None, "quality": now["quality"],
            "elapsed_ms": int(elapsed * 1000), "paused": queue.state == PlaybackState.PAUSED,
            "last": following is None, "next": _upcoming(client, following),
            "modes": {**_modes_of(queue), "sleep_at": player.sleep_timer_expires_at},
            "track_at": track_at, "anchored_at": queue.elapsed_time_last_updated, "speed": speed}


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
        image = library.image_url(client, item, 512) or ""
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


async def _live_queue(client, player_id):
    return await client.player_queues.get_active_queue(player_id)


def live_queue(player_id):
    """MA's active queue on player_id, asked now (the client's copy follows MA's events)."""
    return call(_live_queue, player_id)


def get_queue_state(player_id):
    """(state, elapsed_ms) of MA's queue on player_id, or None.

    state is MA's playback state: "playing", "paused" or "idle". For the
    player page's check every few seconds (watch.py): read from the
    client's copy, so it asks MA nothing.
    """
    queue = active_queue(player_id)
    if queue is None or queue.current_item is None:
        return None
    return queue.state.value, int(queue.corrected_elapsed_time * 1000)


def is_paused(player_id):
    """True if MA's queue on player_id is paused (False if it has no current track)."""
    now = now_playing(player_id, live_queue(player_id))
    return bool(now and now["paused"])


async def _next_item(client, queue):
    """The queue item after the current one, by the repeat mode. MA's next_item lags a mode change."""
    if queue.repeat_mode == RepeatMode.ONE:
        return queue.current_item
    after = await client.player_queues.get_queue_items(queue.queue_id, limit=1, offset=(queue.current_index or 0) + 1)
    if not after and queue.repeat_mode == RepeatMode.ALL:
        after = await client.player_queues.get_queue_items(queue.queue_id, limit=1, offset=0)
    return after[0] if after else None


def _modes_of(queue):
    repeat = queue.repeat_mode.value if queue.repeat_mode.value in REPEAT_MODES else "off"
    return {"shuffle": bool(queue.shuffle_enabled), "repeat": repeat}


# ---------- the playlist and queue edits ----------

async def _queue_window(client, player_id, offset, limit):
    queue = await client.player_queues.get_active_queue(player_id)
    if queue is None:
        return None
    items = await library.queue_items(client, queue)
    index = queue.current_index or 0
    if offset is None:
        offset = max(0, index - _WINDOW_BEFORE)
    endless = queue.repeat_mode != RepeatMode.OFF or queue.is_dynamic
    return {"offset": offset, "total": len(items), "index": index, "dynamic": bool(queue.is_dynamic),
            "locked": max(index, queue.index_in_buffer or 0),
            "after": -1 if endless else int(sum(item.duration or 0 for item in items[index + 1:]) * 1000),
            "items": [_upcoming(client, item) for item in items[offset:offset + limit]]}


def queue_window(player_id, offset=None, limit=110):
    """The playlist's rows: MA's queue items offset..offset+limit (default: from 10 before the playing one).

    {"offset", "total", "index" (playing), "locked" (MA can't move items up
    to here), "after" (ms of the songs after the playing one, -1 on repeat or
    radio), "items" (as _upcoming)}, or None.
    """
    return call(_queue_window, player_id, offset, limit, timeout=LONG_TIMEOUT_S)


async def _edit_queue(client, player_id, kind, item_id, shift):
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
    elif kind == "clear":
        await client.send_command("player_queues/clear", queue_id=queue.queue_id)
    elif kind == "mix":
        item = next((i for i in await library.queue_items(client, queue) if i.queue_item_id == item_id), None)
        if item is None or item.media_item is None:
            return None
        await _start(player_id, 0, queues.play_media(queue.queue_id, f"radio_playlist://playlist/{item.media_item.uri}",
                                                     QueueOption.REPLACE))
    else:
        raise ValueError(f"Unsupported queue edit: {kind}")
    return True


def edit_queue(player_id, kind, item_id, shift=0):
    """Change MA's queue for item_id. True if MA did it, None without a queue.

    kind: "move" (shift places, positive: down), "play_next", "move_end",
    "delete", "clear" (the whole queue; item_id unused), or "mix" (replace the queue with an endless mix of the song).
    """
    return call(_edit_queue, player_id, kind, item_id, shift, timeout=LONG_TIMEOUT_S)


async def _options(client, player_id, kind, value):
    queue = await client.player_queues.get_active_queue(player_id)
    if queue is None:
        return None
    autoplay, crossfade = queue.autoplay_enabled, queue.crossfade_enabled
    if kind == "sleep" and value:
        await client.send_command("players/sleep_timer/set", player_id=player_id, seconds=value * 60)
    elif kind == "sleep":
        await client.send_command("players/sleep_timer/clear", player_id=player_id)
    elif kind == "autoplay":
        autoplay = bool(value)
        await client.send_command("player_queues/autoplay", queue_id=queue.queue_id, autoplay_enabled=autoplay)
    elif kind == "speed":    # MA resumes at the new speed with a new stream
        await client.send_command("player_queues/set_playback_speed", queue_id=queue.queue_id, speed=value)
    elif kind == "crossfade":    # MA crossfades in the stream it sends
        crossfade = bool(value)
        await client.send_command("player_queues/crossfade", queue_id=queue.queue_id, crossfade_enabled=crossfade)
    return {"autoplay": autoplay, "crossfade": crossfade}


def options(player_id, kind=None, value=0):
    """The queue's Autoplay and crossfade: {"autoplay", "crossfade"}, or None.

    kind "sleep" sets the timer to value minutes first (0 clears it); "speed" the playback speed;
    "autoplay" or "crossfade" sets that to value.
    """
    return call(_options, player_id, kind, value, timeout=LONG_TIMEOUT_S)


# ---------- playing ----------

async def _play_media(client, player_id, uri, option, start):
    queue = await client.player_queues.get_active_queue(player_id)
    if queue is None:
        return None
    command = client.player_queues.play_media(queue.queue_id, uri, PLAY_OPTIONS[option], start_item=start)
    await (_start(player_id, 0, command) if option == "replace" else command)
    return True


def play_media(player_id, uri, option="replace", start=None):
    """Play uri (an album, playlist, artist or song) on the player's active queue. True, or None without one.

    option: "replace" the queue (from track uri start), play "next", or "add" to the end.
    """
    return call(_play_media, player_id, uri, option, start, timeout=LONG_TIMEOUT_S)


async def _play(client, player_id, uri, option, shuffle, radio):
    queue_id = await _queue_id(client, player_id)
    if shuffle is not None:
        await client.player_queues.shuffle(queue_id, shuffle)
    command = client.player_queues.play_media(queue_id, uri, option=PLAY_OPTIONS[option], radio_mode=radio)
    await (_start(player_id, 0, command) if option == "replace" else command)


def play(player_id, uri, option="replace", shuffle=None, radio=False):
    """Play uri on player_id's queue. shuffle: set it first (None: leave it)."""
    call(_play, player_id, uri, option, shuffle, radio)


async def _play_random(client, player_id):
    tracks = await client.music.get_library_tracks(order_by="random", limit=RANDOM_TRACKS)
    if not tracks:
        return False
    queue_id = await _queue_id(client, player_id)
    await client.player_queues.shuffle(queue_id, True)
    await _start(player_id, 0, client.player_queues.play_media(queue_id, [t.uri for t in tracks],
                                                               option=QueueOption.REPLACE))
    return True


def play_random(player_id):
    """Random library tracks, shuffled. False if the library is empty."""
    return call(_play_random, player_id)


async def _play_playing_album(client, player_id):
    album = await library.playing(client, player_id, "album")
    if album is None:
        return None
    queue_id = await _queue_id(client, player_id)
    # The whole album, in order.
    await client.player_queues.shuffle(queue_id, False)
    await _start(player_id, 0, client.player_queues.play_media(queue_id, album.uri, option=QueueOption.REPLACE))
    return album.name


def play_playing_album(player_id):
    """Play the whole album of the song that plays, from its first song. Its name, or None."""
    return call(_play_playing_album, player_id)


async def _set_shuffle(client, player_id, on):
    await client.player_queues.shuffle(await _queue_id(client, player_id), on)


def set_shuffle(player_id, on):
    call(_set_shuffle, player_id, on)


async def _set_repeat_mode(client, player_id, mode):
    await client.player_queues.repeat(await _queue_id(client, player_id), RepeatMode(mode))


def set_repeat_mode(player_id, mode):
    """Repeat "off", "all" (the queue) or "one" (the song)."""
    call(_set_repeat_mode, player_id, mode)


# ---------- transfer ----------

async def _players(client):
    return [(player.player_id, player.name) for player in client.players.players
            if player.available and player.enabled and not player.hide_in_ui and player.name]


def players(timeout=None):
    """[(player_id, name), ...] of the players the music can move to. timeout: as call's."""
    return call(_players, timeout=timeout)


async def _move(client, player_id, target_id):
    await client.player_queues.transfer(await _queue_id(client, player_id),
                                        await _queue_id(client, target_id), auto_play=True)


def move(player_id, target_id, timeout=None):
    """Move player_id's queue, with its track and position, to target_id and play it there. timeout: as call's."""
    call(_move, player_id, target_id, timeout=timeout)
