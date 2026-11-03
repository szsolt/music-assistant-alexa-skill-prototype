# -*- coding: utf-8 -*-
"""MA's music: browse, search, favourites, playlists, lyrics and the loudness graph.

Every call raises MAUnavailable or MAFailed (see errors.py).
"""

import asyncio
import functools
import logging

from music_assistant_models.enums import ImageType, MediaType
from music_assistant_models.errors import MusicAssistantError
from music_assistant_models.media_items import Artist

from ..voice import match as voice_match
from .client import LONG_TIMEOUT_S, call

logger = logging.getLogger(__name__)

PAGE = 500
SEARCH_LIMIT = 25
FAVORITE_KINDS = ("song", "album", "artist")

_MEDIA_TYPES = {"playlist": MediaType.PLAYLIST, "artist": MediaType.ARTIST,
                "album": MediaType.ALBUM, "song": MediaType.TRACK}
_RESULTS = {"playlist": "playlists", "artist": "artists", "album": "albums", "song": "tracks"}
_SEARCH_TYPES = (("Artists", MediaType.ARTIST, "artists"), ("Albums", MediaType.ALBUM, "albums"),
                 ("Playlists", MediaType.PLAYLIST, "playlists"), ("Songs", MediaType.TRACK, "tracks"),
                 ("Podcasts", MediaType.PODCAST, "podcasts"), ("Audiobooks", MediaType.AUDIOBOOK, "audiobooks"))


async def all_pages(fetch, total=None):
    """Every item of fetch(limit=, offset=), a page at a time: up to total, else to the first short page."""
    items = []
    while total is None or len(items) < total:
        page = await fetch(limit=PAGE, offset=len(items))
        items += page
        if not page or (total is None and len(page) < PAGE):
            break
    return items


def queue_items(client, queue):
    """Every item of MA's queue."""
    return all_pages(functools.partial(client.player_queues.get_queue_items, queue.queue_id), queue.items)


def image_url(client, item, size):
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


# ---------- what plays ----------

def _now_or_next(queue, item_id):
    """The playing or the next queue item with item_id (empty: the playing one), or None."""
    if queue is None:
        return None
    return next((i for i in (queue.current_item, queue.next_item)
                 if i is not None and (not item_id or i.queue_item_id == item_id)), None)


def _now_uris(media):
    """As MA's lists mark it: the uris of the song, its album and its artists."""
    return [x.uri for x in (media, getattr(media, "album", None), *(getattr(media, "artists", None) or [])) if x]


async def _now_playing(client, player_id, item_id, current_only):
    queue = await client.player_queues.get_active_queue(player_id)
    item = (queue and queue.current_item) if current_only else _now_or_next(queue, item_id)
    if item is None:
        return None
    media = item.media_item
    return {"title": (media.name if media else None) or item.name or "",
            "artist": getattr(media, "artist_str", "") or "",
            "uris": _now_uris(media)}


def now_playing(player_id, item_id=None, current_only=False, timeout=None):
    """Queue item item_id (default: the playing one, else the next): {"title", "artist", "uris"}, or None.

    uris: of its song, its album and its artists. current_only: only the playing one. timeout: as call's.
    """
    return call(_now_playing, player_id, item_id, current_only, timeout=timeout)


async def playing(client, player_id, kind):
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


def wave_form(player_id, item_id=None):
    """The loudness graph of queue item item_id (default: the playing one), 0-1 per bar, or None.

    Only the playing and the next song: MA has a graph once it analysed the song.
    """
    return call(_wave_form, player_id, item_id, timeout=LONG_TIMEOUT_S)


async def _lyrics(client, player_id, item_id):
    item = _now_or_next(await client.player_queues.get_active_queue(player_id), item_id)
    if item is None or item.media_item is None:
        return None
    plain, lrc = await client.send_command("metadata/get_track_lyrics", track=item.media_item.to_dict())
    return {"plain": plain or "", "lrc": lrc or ""}


def lyrics(player_id, item_id=None):
    """MA's lyrics of queue item item_id (default: the playing one): {"plain", "lrc" (synced)}, or None."""
    return call(_lyrics, player_id, item_id, timeout=LONG_TIMEOUT_S)


# ---------- favourites ----------

async def _in_library(music, item):
    """item as MA's library has it (with its favourite flag), or None."""
    if item.provider == "library":
        return await music.get_item(item.media_type, item.item_id, "library")
    return await music.get_library_item_by_prov_id(item.media_type, item.item_id, item.provider)


async def _favorite(client, item, on):
    """Make item a favourite (on True), not one (False), or toggle it (None). Returns whether it is one now."""
    library = await _in_library(client.music, item)
    now = bool(library and library.favorite)
    wanted = not now if on is None else on
    if wanted and not now:
        await client.music.add_item_to_favorites(item.uri)    # MA adds it to the library if needed
    elif now and not wanted:
        await client.music.remove_item_from_favorites(item.media_type, library.item_id)
    return wanted


async def _favorites(client, player_id):
    state = {}
    for kind in ("song", "album"):
        item = await playing(client, player_id, kind)
        library = await _in_library(client.music, item) if item else None
        state[kind] = bool(library and library.favorite) if item else None
    return state


def favorites(player_id):
    """{"song", "album"}: True or False if what plays is a favourite, None if it can't be one."""
    return call(_favorites, player_id)


async def _favorite_playing(client, player_id, kind, on):
    item = await playing(client, player_id, kind)
    if item is None:
        return None
    return item.name, await _favorite(client, item, on)


def favorite_playing(player_id, kind, on=None):
    """Make the playing song, album or artist a favourite (on True), not one (False), or toggle (None).

    Returns (its name, favourite now), or None if nothing of that kind plays.
    """
    return call(_favorite_playing, player_id, kind, on)


async def _set_favorite(client, uri, on):
    await _favorite(client, await client.music.get_item_by_uri(uri), on)
    return True


def set_favorite(uri, on):
    """Make uri (an artist, album or playlist) a favourite, or not one. Returns True."""
    return call(_set_favorite, uri, on, timeout=LONG_TIMEOUT_S)


# ---------- library names (for the model upload) ----------

async def _library_names(client):
    fetchers = {"playlist": client.music.get_library_playlists,
                "artist": client.music.get_library_artists,
                "album": client.music.get_library_albums,
                "song": client.music.get_library_tracks}
    names = {}
    for kind, fetch in fetchers.items():
        favourites = await all_pages(functools.partial(fetch, favorite=True, order_by="last_played_desc"))
        everything = await all_pages(functools.partial(fetch, order_by="last_played_desc"))
        names[kind] = [item.name for item in favourites + everything if item.name]
    return names


def library_names():
    """{kind: [name, ...]}: favourites first, then the rest by last played."""
    return call(_library_names, timeout=LONG_TIMEOUT_S)


# ---------- search (voice) ----------

def _candidate(kind, item):
    return {"kind": kind, "name": item.name, "uri": item.uri,
            "artist": getattr(item, "artist_str", "") or ""}


async def _search(client, heard, kinds, library_only):
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
        candidates = call(_search, query, kinds, library_only)
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
    from ..voice import lists as voice_lists
    guess = voice_match.closest(heard, voice_lists.names(kinds))
    if not guess or guess == query:
        return None
    logger.info("Voice: %r sounds like %r", heard, guess)
    best, best_score = voice_match.pick(guess, call(_search, guess, kinds, True), artist=artist, kinds=kinds)
    if not best:
        return None
    best["from_library"] = True
    return best, best_score


# ---------- browse (the page) ----------

def _track_title(media):
    version = getattr(media, "version", None)
    return f"{media.name} ({version})" if version else media.name


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
        image = image_url(client, item, 256) or ""
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
            opened["header"] = {"image": image_url(client, item, 512), "artist": "", "artist_uri": "", "year": 0}
        elif kind_of == "album":
            uri, sections = item.uri, [["", await music.get_album_tracks(item.item_id, item.provider)]]
            artist = (item.artists or [None])[0]
            opened["header"] = {"image": image_url(client, item, 512), "artist": artist.name if artist else "",
                                "artist_uri": artist.uri if artist else "", "year": item.year or 0}
        elif kind_of == "playlist":
            uri = item.uri
            sections = [["", await music.get_playlist_tracks(item.item_id, item.provider, limit=500)]]
        elif kind_of == "podcast":    # as MA's podcast page
            episodes = await music.get_podcast_episodes(item.item_id, item.provider)
            sections = [["Episodes", sorted(episodes, key=lambda e: e.position, reverse=True)]]
            opened["header"] = {"image": image_url(client, item, 512), "artist": item.publisher or "",
                                "artist_uri": "", "year": 0}
        else:
            return None
    else:    # favourites, or the whole library by name without its songs (thousands; albums and search reach them)
        library = kind == "library"
        title, filters = ("Library", {"limit": 1000}) if library else ("Favourites", {"favorite": True, "limit": 100})
        sections = [(name, await get(**filters)) for name, get in (
            ("Albums", music.get_library_albums), ("Playlists", music.get_library_playlists),
            ("Artists", music.get_library_artists), ("Songs", None if library else music.get_library_tracks),
            ("Podcasts", music.get_library_podcasts), ("Audiobooks", music.get_library_audiobooks)) if get]
    return {"title": title, "uri": uri, "now": now, **opened, "sections": [{"name": name, "rows": [_row(client, i) for i in items]}
                                         for name, items in sections if items]}


def browse(player_id, kind, text="", item_id=None):
    """Music to pick from: {"title", "uri", "sections": [{"name", "rows": [_row]}]}, or None.

    uri is the opened album or playlist, which its songs play from; else "".
    "open" adds "type", "item" (the opened uri) and "favorite"; an album or artist also
    "header": {"image", "artist", "artist_uri", "year"}.

    kind: "favorites", "library", "recent", "search" (text: what to look for), "open" (text: an artist, album or playlist uri),
    "playing" (the playing song's album opened, or its artist when text is "artist"). "now": now_playing(item_id)'s uris.
    """
    return call(_browse, player_id, kind, text, item_id, timeout=LONG_TIMEOUT_S)


# ---------- playlists ----------

async def _playlists(client):
    return [[p.item_id, p.name] for p in await client.music.get_library_playlists() if p.is_editable]


def playlists():
    """[[playlist_id, name]] of the library playlists songs can be added to."""
    return call(_playlists, timeout=LONG_TIMEOUT_S)


async def _add_to_playlist(client, player_id, playlist_id, name, uri, item_id, queue):
    if uri:
        uris = [uri]    # MA adds an album's or a playlist's songs
    else:
        active = await client.player_queues.get_active_queue(player_id)
        if active is None:
            return None
        items = await queue_items(client, active)
        if not queue:
            items = [i for i in items if i.queue_item_id == item_id]
        uris = [i.media_item.uri for i in items if i.media_item is not None]
        if not uris:
            return None
    if not playlist_id:
        playlist_id = (await client.music.create_playlist(name)).item_id    # in MA's own library
    await client.music.add_playlist_tracks(playlist_id, uris)
    return name


def add_to_playlist(player_id, playlist_id, name, uri="", item_id="", queue=False):
    """Add uri (a song, album or playlist), queue item item_id, or the whole queue to a playlist.

    Empty playlist_id: a new playlist named name. Returns the name, or None if there is nothing to add.
    """
    return call(_add_to_playlist, player_id, playlist_id, name, uri, item_id, queue, timeout=LONG_TIMEOUT_S)
