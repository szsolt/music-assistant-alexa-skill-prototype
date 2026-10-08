# -*- coding: utf-8 -*-
"""Music Assistant calls for voice commands: library names, search and play.

Each call opens a short-lived connection, like ma_control. Errors come out
as MAUnavailable (MA can't be reached) or MAFailed (MA refused).
"""

import asyncio
import contextlib
import logging

import aiohttp
from music_assistant_client import MusicAssistantClient
from music_assistant_client.exceptions import CannotConnect, ConnectionFailed, InvalidServerVersion
from music_assistant_models.enums import MediaType, QueueOption, RepeatMode
from music_assistant_models.errors import MusicAssistantError

from env_secrets import get_env_secret
from . import voice_match

logger = logging.getLogger(__name__)

PAGE = 500
SEARCH_LIMIT = 25
RANDOM_TRACKS = 100

_MEDIA_TYPES = {"playlist": MediaType.PLAYLIST, "artist": MediaType.ARTIST,
                "album": MediaType.ALBUM, "song": MediaType.TRACK}
_RESULTS = {"playlist": "playlists", "artist": "artists", "album": "albums", "song": "tracks"}
_OPTIONS = {"replace": QueueOption.REPLACE, "next": QueueOption.NEXT, "add": QueueOption.ADD}


class MAUnavailable(Exception):
    """MA can't be reached."""


class MAFailed(Exception):
    """MA refused the command."""


@contextlib.asynccontextmanager
async def _client():
    server_url = get_env_secret("MA_API_URL")
    if not server_url:
        raise MAUnavailable("MA_API_URL is not set")
    async with aiohttp.ClientSession() as session:
        async with MusicAssistantClient(server_url, session, token=get_env_secret("MA_API_TOKEN")) as client:
            yield client


def _run(coro):
    try:
        return asyncio.run(coro)
    except (CannotConnect, ConnectionFailed, InvalidServerVersion, asyncio.TimeoutError) as e:
        raise MAUnavailable(str(e)) from e
    except MusicAssistantError as e:
        raise MAFailed(str(e)) from e


async def _queue_id(client, player_id):
    queue = await client.player_queues.get_active_queue(player_id)
    return queue.queue_id if queue else player_id


# ---------- library names (for the model upload) ----------

async def _all(fetch, **kwargs):
    items, offset = [], 0
    while True:
        page = await fetch(limit=PAGE, offset=offset, **kwargs)
        items.extend(page)
        if len(page) < PAGE:
            return items
        offset += PAGE


async def _library_names():
    async with _client() as client:
        fetchers = {"playlist": client.music.get_library_playlists,
                    "artist": client.music.get_library_artists,
                    "album": client.music.get_library_albums,
                    "song": client.music.get_library_tracks}
        names = {}
        for kind, fetch in fetchers.items():
            favourites = await _all(fetch, favorite=True, order_by="last_played_desc")
            everything = await _all(fetch, order_by="last_played_desc")
            names[kind] = [item.name for item in favourites + everything if item.name]
        return names


def library_names():
    """{kind: [name, ...]}: favourites first, then the rest by last played."""
    return _run(_library_names())


# ---------- players (to move the music to) ----------

async def _players():
    async with _client() as client:
        await client.players.fetch_state()
        return [(player.player_id, player.name) for player in client.players.players
                if player.available and player.enabled and not player.hide_in_ui and player.name]


def players():
    """[(player_id, name), ...] of the players the music can move to."""
    return _run(_players())


async def _move(player_id, target_id):
    async with _client() as client:
        await client.player_queues.transfer(await _queue_id(client, player_id),
                                            await _queue_id(client, target_id), auto_play=True)


def move(player_id, target_id):
    """Move player_id's queue, with its track and position, to target_id and play it there."""
    _run(_move(player_id, target_id))


# ---------- search ----------

def _candidate(kind, item):
    return {"kind": kind, "name": item.name, "uri": item.uri,
            "artist": getattr(item, "artist_str", "") or ""}


async def _search(heard, kinds, library_only):
    async with _client() as client:
        results = await client.music.search(heard, [_MEDIA_TYPES[k] for k in kinds],
                                            limit=SEARCH_LIMIT, library_only=library_only)
    return [_candidate(kind, item) for kind in kinds for item in getattr(results, _RESULTS[kind], []) or []]


def find(heard, kinds, artist=None):
    """(candidate, score) of the best match in the library, else in all of MA's providers.

    (None, score) if nothing is close enough. A candidate is a dict with
    kind, name, artist and uri.
    """
    query = heard
    best, best_score = None, 0.0
    for library_only in (True, False):
        candidates = _run(_search(query, kinds, library_only))
        best, best_score = voice_match.pick(heard, candidates, artist=artist, kinds=kinds)
        if best:
            best["from_library"] = library_only
            return best, best_score
        if library_only:
            found = _found_by_sound(heard, query, kinds, artist)
            if found:
                return found
        if artist and library_only:
            # "Z by X": X in the query finds Z when the name alone is too common.
            query = f"{heard} {artist}"
    return None, best_score


def _found_by_sound(heard, query, kinds, artist):
    """Search again by the library name that sounds most like heard.

    Alexa's English ears turn Hungarian names into other words ("deck bill
    julia"), which MA's search can't find.
    """
    from . import voice_lists
    guess = voice_match.closest(heard, voice_lists.names(kinds))
    if not guess or guess == query:
        return None
    logger.info("Voice: %r sounds like %r", heard, guess)
    best, best_score = voice_match.pick(guess, _run(_search(guess, kinds, True)), artist=artist, kinds=kinds)
    if not best:
        return None
    best["from_library"] = True
    return best, best_score


# ---------- playing ----------

async def _play(player_id, uri, option, shuffle, radio):
    async with _client() as client:
        queue_id = await _queue_id(client, player_id)
        if shuffle is not None:
            await client.player_queues.shuffle(queue_id, shuffle)
        await client.player_queues.play_media(queue_id, uri, option=_OPTIONS[option], radio_mode=radio)


def play(player_id, uri, option="replace", shuffle=None, radio=False):
    """Play uri on player_id's queue. shuffle: set it first (None: leave it)."""
    _run(_play(player_id, uri, option, shuffle, radio))


async def _play_random(player_id):
    async with _client() as client:
        tracks = await client.music.get_library_tracks(order_by="random", limit=RANDOM_TRACKS)
        if not tracks:
            return False
        queue_id = await _queue_id(client, player_id)
        await client.player_queues.shuffle(queue_id, True)
        await client.player_queues.play_media(queue_id, [t.uri for t in tracks], option=QueueOption.REPLACE)
        return True


def play_random(player_id):
    """Random library tracks, shuffled. False if the library is empty."""
    return _run(_play_random(player_id))


async def _set_mode(player_id, shuffle, repeat):
    async with _client() as client:
        queue_id = await _queue_id(client, player_id)
        if shuffle is not None:
            await client.player_queues.shuffle(queue_id, shuffle)
        if repeat is not None:
            await client.player_queues.repeat(queue_id, RepeatMode.ALL if repeat else RepeatMode.OFF)


def set_shuffle(player_id, on):
    _run(_set_mode(player_id, on, None))


def set_repeat(player_id, on):
    _run(_set_mode(player_id, None, on))


REPEAT_MODES = ("off", "all", "one")


async def _modes(player_id):
    async with _client() as client:
        queue = await client.player_queues.get_active_queue(player_id)
        if queue is None:
            return None
        repeat = queue.repeat_mode.value if queue.repeat_mode.value in REPEAT_MODES else "off"
        return {"shuffle": bool(queue.shuffle_enabled), "repeat": repeat}


def modes(player_id):
    """{"shuffle": bool, "repeat": "off", "all" or "one"} of player_id's queue, or None."""
    return _run(_modes(player_id))


async def _set_repeat_mode(player_id, mode):
    async with _client() as client:
        await client.player_queues.repeat(await _queue_id(client, player_id), RepeatMode(mode))


def set_repeat_mode(player_id, mode):
    """Repeat "off", "all" (the queue) or "one" (the song)."""
    _run(_set_repeat_mode(player_id, mode))


async def _now_playing(player_id):
    async with _client() as client:
        queue = await client.player_queues.get_active_queue(player_id)
        item = queue.current_item if queue else None
        if item is None:
            return None
        media = item.media_item
        title = (media.name if media else None) or item.name or ""
        return title, (getattr(media, "artist_str", "") or "")


def now_playing(player_id):
    """(title, artist) of the current track, or None."""
    return _run(_now_playing(player_id))


# ---------- favourites ----------

FAVORITE_KINDS = ("song", "album", "artist")


async def _playing(client, player_id, kind):
    """The song, album or artist of what plays on player_id, or None (e.g. radio)."""
    queue = await client.player_queues.get_active_queue(player_id)
    media = queue.current_item.media_item if queue and queue.current_item else None
    if media is None or media.media_type != MediaType.TRACK:
        return None
    if kind == "song":
        return media
    if kind == "album":
        return getattr(media, "album", None)
    artists = getattr(media, "artists", None) or []
    return artists[0] if artists else None


async def _in_library(client, item, kind):
    """item as MA's library has it (with its favourite flag), or None."""
    media_type = _MEDIA_TYPES[kind]
    if item.provider == "library":
        return await client.music.get_item(media_type, item.item_id, "library")
    return await client.music.get_library_item_by_prov_id(media_type, item.item_id, item.provider)


async def _favorites(player_id):
    async with _client() as client:
        state = {}
        for kind in ("song", "album"):
            item = await _playing(client, player_id, kind)
            library = await _in_library(client, item, kind) if item else None
            state[kind] = bool(library and library.favorite) if item else None
        return state


def favorites(player_id):
    """{"song", "album"}: True or False if what plays is a favourite, None if it can't be one."""
    return _run(_favorites(player_id))


async def _set_favorite(player_id, kind, on):
    async with _client() as client:
        item = await _playing(client, player_id, kind)
        if item is None:
            return None
        library = await _in_library(client, item, kind)
        now = bool(library and library.favorite)
        wanted = not now if on is None else on
        if wanted and not now:
            # MA adds an item that isn't in the library yet.
            await client.music.add_item_to_favorites(item.uri)
        elif now and not wanted:
            await client.music.remove_item_from_favorites(_MEDIA_TYPES[kind], library.item_id)
        return item.name, wanted


def set_favorite(player_id, kind, on=None):
    """Make the playing song, album or artist a favourite (on True), not one (False), or toggle (None).

    Returns (its name, favourite now), or None if nothing of that kind plays.
    """
    return _run(_set_favorite(player_id, kind, on))


async def _play_playing_album(player_id):
    async with _client() as client:
        album = await _playing(client, player_id, "album")
        if album is None:
            return None
        queue_id = await _queue_id(client, player_id)
        # The whole album, in order.
        await client.player_queues.shuffle(queue_id, False)
        await client.player_queues.play_media(queue_id, album.uri, option=QueueOption.REPLACE)
        return album.name


def play_playing_album(player_id):
    """Play the whole album of the song that plays, from its first song. Its name, or None."""
    return _run(_play_playing_album(player_id))
