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

from music_assistant_models.enums import ContentType, ImageType, MediaType, PlaybackState, QueueOption, RepeatMode
from music_assistant_models.errors import MusicAssistantError
from music_assistant_models.media_items import Artist

from . import ma_client, track_time

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


async def _send_command(client, player_id, command):
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
    return True


def send_player_command(player_id, command):
    """Send a command to a Music Assistant player. Returns True on success."""
    if command not in SUPPORTED_COMMANDS:
        raise ValueError(f"Unsupported MA command: {command}")
    return bool(_call(f"send {command}", _send_command, player_id, command))


async def _get_track_time(client, player_id):
    queue = await client.player_queues.get_active_queue(player_id)
    if queue is None or queue.current_item is None:
        return None
    duration = queue.current_item.duration
    return (int(duration * 1000) if duration else None,
            int(queue.corrected_elapsed_time * 1000),
            queue.state == PlaybackState.PAUSED,
            queue.next_item is None,
            _upcoming(client, queue.next_item),
            quality(queue.current_item),
            queue.current_item.queue_item_id)


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
        image = _image_url(client, item, 512) or ""
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
    """(duration_ms, elapsed_ms, paused, last, upcoming, quality, item_id) of MA's current track on player_id, or None.

    duration_ms is None when MA doesn't know the length (e.g. radio); last
    is True when nothing follows it in the queue. upcoming: the next
    track's title, secondary (artist), album, image, duration_ms (0 if
    unknown) and quality, or None: the page switches to it at the track's
    end. quality: the current track's (see quality()). item_id: its queue item.
    Called once per track change from the APL refresh, not per refresh.
    """
    return _call("read the track time", _get_track_time, player_id)


def get_queue_state(player_id):
    """(state, elapsed_ms) of MA's queue on player_id, or None.

    state is MA's playback state: "playing", "paused" or "idle". For the
    player page's check every few seconds (bell.py): read from the
    client's copy, so it asks MA nothing.
    """
    queue = ma_client.active_queue(player_id)
    if queue is None or queue.current_item is None:
        return None
    return queue.state.value, int(queue.corrected_elapsed_time * 1000)


def is_paused(player_id):
    """True if MA's queue on player_id is paused (False if unknown)."""
    result = get_current_track_time(player_id)
    return bool(result and result[2])


async def _resume_at(client, player_id, position_ms, item_id=None):
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
    return _call("resume", _resume_at, player_id, position_ms, item_id)


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


async def _queue_window(client, player_id, offset, limit):
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
        item = next((i for i in await _all_items(client, queue) if i.queue_item_id == item_id), None)
        if item is None or item.media_item is None:
            return None
        await queues.play_media(queue.queue_id, f"radio_playlist://playlist/{item.media_item.uri}",
                                QueueOption.REPLACE)
    else:
        raise ValueError(f"Unsupported queue edit: {kind}")
    return True


def _now_or_next(queue, item_id):
    """The playing or the next queue item with item_id (empty: the playing one), or None."""
    if queue is None:
        return None
    return next((i for i in (queue.current_item, queue.next_item)
                 if i is not None and (not item_id or i.queue_item_id == item_id)), None)


def _now_uris(media):
    """As MA's lists mark it: the uris of the song, its album and its artists."""
    return [x.uri for x in (media, getattr(media, "album", None), *(getattr(media, "artists", None) or [])) if x]


async def _now(client, player_id, item_id):
    item = _now_or_next(await client.player_queues.get_active_queue(player_id), item_id)
    return _now_uris(item.media_item if item else None)


async def _wave_form(client, player_id, item_id):
    item = _now_or_next(await client.player_queues.get_active_queue(player_id), item_id)
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


async def _lyrics(client, player_id, item_id):
    item = _now_or_next(await client.player_queues.get_active_queue(player_id), item_id)
    if item is None or item.media_item is None:
        return None
    plain, lrc = await client.send_command("metadata/get_track_lyrics", track=item.media_item.to_dict())
    return {"plain": plain or "", "lrc": lrc or ""}


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
    elif kind == "crossfade":    # MA crossfades in the stream it sends
        crossfade = bool(value)
        await client.send_command("player_queues/crossfade", queue_id=queue.queue_id, crossfade_enabled=crossfade)
    expires = await client.send_command("players/sleep_timer/get", player_id=player_id)
    return {"sleep_left": max(0, int(expires - time.time())) if expires else 0, "autoplay": autoplay, "crossfade": crossfade}


def _track_title(media):
    version = getattr(media, "version", None)
    return f"{media.name} ({version})" if version else media.name


async def _play_media(client, player_id, uri, option, start):
    queue = await client.player_queues.get_active_queue(player_id)
    if queue is None:
        return None
    await client.player_queues.play_media(queue.queue_id, uri, _PLAY_OPTIONS[option], start_item=start)
    return True


_PLAY_OPTIONS = {"replace": QueueOption.REPLACE, "next": QueueOption.NEXT, "add": QueueOption.ADD}
_SEARCH_TYPES = (("Artists", MediaType.ARTIST, "artists"), ("Albums", MediaType.ALBUM, "albums"),
                 ("Playlists", MediaType.PLAYLIST, "playlists"), ("Songs", MediaType.TRACK, "tracks"),
                 ("Podcasts", MediaType.PODCAST, "podcasts"), ("Audiobooks", MediaType.AUDIOBOOK, "audiobooks"))


def _image_url(client, item, size):
    """A thumbnail from MA's own image proxy, as the MA frontend loads it.

    The client library sends remote images through images.weserv.nl, which cannot reach LAN sources.
    """
    media = getattr(item, "media_item", None)
    image = (media and client.music.get_media_item_image(media, ImageType.THUMB)) \
        or client.music.get_media_item_image(item, ImageType.THUMB)
    if image is None:
        return ""
    if image.proxy_id:
        return f"{client.server_info.base_url}/imageproxy/{image.proxy_id}?size={size}"
    return client.get_image_url(image, size)


def _sub(item):
    """A row's second line, as MA's lists: the artists, a podcast's publisher, an audiobook's authors or an episode's date."""
    released = getattr(getattr(item, "metadata", None), "release_date", None)
    if getattr(item.media_type, "value", item.media_type) == "podcast_episode" and released:
        return f"{released.day} {released:%b %Y}"
    authors = " / ".join(getattr(a, "name", a) for a in getattr(item, "authors", None) or [])
    return getattr(item, "artist_str", "") or authors or getattr(item, "publisher", "") or ""


def _row(client, item):
    """A browse row: {"uri", "type", "name", "sub", "image", "duration_ms", "disc", "favorite", "played", "go"}.

    played: "done", "part" or "" for an episode or audiobook. go: [kind, name, uri] of the first
    artist and the album, for "View artist" and "View album".
    """
    try:
        image = _image_url(client, item, 256) or ""
    except Exception:
        image = ""
    kind = getattr(item.media_type, "value", item.media_type)
    artist, album = (getattr(item, "artists", None) or [None])[0], getattr(item, "album", None)
    return {"uri": item.uri, "type": kind, "name": _track_title(item) if kind == "track" else item.name or "",
            "sub": _sub(item),
            "image": image, "duration_ms": int((getattr(item, "duration", 0) or 0) * 1000),
            "disc": getattr(item, "disc_number", 0) or 0, "favorite": bool(getattr(item, "favorite", False)),
            "played": "done" if getattr(item, "fully_played", None) else "part" if getattr(item, "resume_position_ms", None) else "",
            "go": [[k, x.name, x.uri] for k, x in (("artist", artist), ("album", album)) if x]}


async def _in_library(music, item):
    """item as MA's library has it (with its favourite flag), or None."""
    if item.provider == "library":
        return await music.get_item(item.media_type, item.item_id, "library")
    return await music.get_library_item_by_prov_id(item.media_type, item.item_id, item.provider)


async def _within(call, seconds=8):
    """call's list, or [] when MA fails or is slow (a streaming service artist's songs can take minutes)."""
    try:
        return await asyncio.wait_for(call, seconds) or []
    except (TimeoutError, MusicAssistantError):
        return []


async def _browse(client, player_id, kind, text, item_id=None):
    music, uri, opened = client.music, "", {}
    item = _now_or_next(await client.player_queues.get_active_queue(player_id), item_id)
    media = item.media_item if item else None
    album, now = getattr(media, "album", None), _now_uris(media)
    if kind == "playing":    # text "artist" opens the song's artist, else its album
        target = (getattr(media, "artists", None) or [None])[0] if text == "artist" else album
        if target is None:
            return None
        kind, text = "open", target.uri
    if kind == "recent":
        title, sections = "Recently played", [["", await music.recently_played(limit=40, fully_played_only=False)]]
    elif kind == "search":
        found = await music.search(text, [t for _, t, _ in _SEARCH_TYPES], limit=10)
        title, sections = text, [[name, getattr(found, field)] for name, _, field in _SEARCH_TYPES]
    elif kind == "open":
        item = await music.get_item_by_uri(text)
        kind_of = getattr(item.media_type, "value", item.media_type)
        title = item.name
        library = await _in_library(music, item)
        opened = {"type": kind_of, "item": text, "favorite": bool(library and library.favorite)}
        if kind_of == "artist":    # as MA's artist page
            similar_of = library or item    # a streaming service often has no similar artists; MA's library has
            albums, songs, similar = await asyncio.gather(
                music.get_artist_albums(item.item_id, item.provider),
                _within(music.get_artist_tracks(item.item_id, item.provider)),
                _within(client.send_command("music/artists/similar_artists", item_id=similar_of.item_id,
                                            provider_instance_id_or_domain=similar_of.provider, limit=20)))
            sections = [["Albums", albums], ["Songs", songs],
                        ["Similar artists", [Artist.from_dict(a) for a in similar]]]
            opened["header"] = {"image": _image_url(client, item, 512), "artist": "", "artist_uri": "", "year": 0}
        elif kind_of == "album":
            uri, sections = item.uri, [["", await music.get_album_tracks(item.item_id, item.provider)]]
            artist = (item.artists or [None])[0]
            opened["header"] = {"image": _image_url(client, item, 512), "artist": artist.name if artist else "",
                                "artist_uri": artist.uri if artist else "", "year": item.year or 0}
        elif kind_of == "playlist":
            uri = item.uri
            sections = [["", await music.get_playlist_tracks(item.item_id, item.provider, limit=500)]]
        elif kind_of == "podcast":    # as MA's podcast page
            episodes = await music.get_podcast_episodes(item.item_id, item.provider)
            sections = [["Episodes", sorted(episodes, key=lambda e: e.position, reverse=True)]]
            opened["header"] = {"image": _image_url(client, item, 512), "artist": item.publisher or "",
                                "artist_uri": "", "year": 0}
        else:
            return None
    else:
        title = "Favourites"
        sections = [(name, await get(favorite=True, limit=100)) for name, get in (
            ("Albums", music.get_library_albums), ("Playlists", music.get_library_playlists),
            ("Artists", music.get_library_artists), ("Songs", music.get_library_tracks),
            ("Podcasts", music.get_library_podcasts), ("Audiobooks", music.get_library_audiobooks))]
    return {"title": title, "uri": uri, "now": now, **opened, "sections": [{"name": name, "rows": [_row(client, i) for i in items]}
                                         for name, items in sections if items]}


async def _set_favorite(client, player_id, uri, on):
    if on:
        await client.music.add_item_to_favorites(uri)    # MA adds it to the library if needed
    else:
        library = await _in_library(client.music, await client.music.get_item_by_uri(uri))
        if library:
            await client.music.remove_item_from_favorites(library.media_type, library.item_id)
    return True


async def _playlists(client, player_id):
    return [[p.item_id, p.name] for p in await client.music.get_library_playlists() if p.is_editable]


async def _add_to_playlist(client, player_id, playlist_id, name, uri, item_id, queue):
    if uri:
        uris = [uri]    # MA adds an album's or a playlist's songs
    else:
        active = await client.player_queues.get_active_queue(player_id)
        if active is None:
            return None
        items = await _all_items(client, active)
        if not queue:
            items = [i for i in items if i.queue_item_id == item_id]
        uris = [i.media_item.uri for i in items if i.media_item is not None]
        if not uris:
            return None
    if not playlist_id:
        playlist_id = (await client.music.create_playlist(name)).item_id    # in MA's own library
    await client.music.add_playlist_tracks(playlist_id, uris)
    return name


def _call(what, fn, player_id, *args):
    """fn(client, player_id, *args) on MA, or None on failure."""
    try:
        return ma_client.call(fn, player_id, *args)
    except ma_client.Unavailable as e:
        logger.warning("Could not reach Music Assistant to %s of player %s: %s", what, player_id, e)
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
    "delete", "clear" (the whole queue; item_id unused), or "mix" (replace the queue with an endless mix of the song).
    """
    return bool(_call(f"{kind} a queue item", _edit_queue, player_id, kind, item_id, shift))


def wave_form(player_id, item_id=None):
    """The loudness graph of queue item item_id (default: the playing one), 0-1 per bar, or None.

    Only the playing and the next song: MA has a graph once it analysed the song.
    """
    return _call("read the loudness graph", _wave_form, player_id, item_id)


def lyrics(player_id, item_id=None):
    """MA's lyrics of queue item item_id (default: the playing one): {"plain", "lrc" (synced)}, or None."""
    return _call("read the lyrics", _lyrics, player_id, item_id)


def options(player_id, kind=None, value=0):
    """The player's sleep timer, Autoplay and crossfade: {"sleep_left" (s, 0: off), "autoplay", "crossfade"}, or None.

    kind "sleep" sets the timer to value minutes first (0 clears it); "autoplay" or "crossfade" sets that to value.
    """
    return _call("set the player options", _options, player_id, kind, value)


def play_media(player_id, uri, option="replace", start=None):
    """Play uri (an album, playlist, artist or song) on the player. True if MA did it.

    option: "replace" the queue (from track uri start), play "next", or "add" to the end.
    """
    return bool(_call("play media", _play_media, player_id, uri, option, start))


def now_playing(player_id, item_id=None):
    """The uris of queue item item_id's song (default: the playing one), its album and its artists, or None."""
    return _call("read what plays", _now, player_id, item_id)


def browse(player_id, kind, text="", item_id=None):
    """Music to pick from: {"title", "uri", "sections": [{"name", "rows": [_row]}]}, or None.

    uri is the opened album or playlist, which its songs play from; else "".
    "open" adds "type", "item" (the opened uri) and "favorite"; an album or artist also
    "header": {"image", "artist", "artist_uri", "year"}.

    kind: "favorites", "recent", "search" (text: what to look for), "open" (text: an artist, album or playlist uri),
    "playing" (the playing song's album opened, or its artist when text is "artist"). "now": now_playing(item_id).
    """
    return _call("browse the music", _browse, player_id, kind, text, item_id)


def set_favorite(player_id, uri, on):
    """Make uri (an artist, album or playlist) a favourite, or not one. True if MA did it."""
    return bool(_call("change a favourite", _set_favorite, player_id, uri, on))


def playlists(player_id):
    """[[playlist_id, name]] of the library playlists songs can be added to, or None."""
    return _call("list the playlists", _playlists, player_id)


def add_to_playlist(player_id, playlist_id, name, uri="", item_id="", queue=False):
    """Add uri (a song, album or playlist), queue item item_id, or the whole queue to a playlist.

    Empty playlist_id: a new playlist named name. Returns the name, or None.
    """
    return _call("add to a playlist", _add_to_playlist, player_id, playlist_id, name, uri, item_id, queue)
