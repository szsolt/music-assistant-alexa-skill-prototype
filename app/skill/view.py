# -*- coding: utf-8 -*-
"""What a player page shows: the stream MA pushed for the Echo's player, and MA's queue.

It also owns the one rewrite of MA's URLs to its public host, and the one
.flac to .mp3 rule for the stream.
"""

import dataclasses
import ipaddress
import logging
import re
from urllib.parse import quote, urlsplit, urlunsplit

from . import clock, config, devices, store, track_time
from .ma import client, library, player
from .ma.errors import MAFailed, MAUnavailable, safe_error

logger = logging.getLogger(__name__)

# MA hasn't said (late, or failed in an unexpected way): the page keeps what it shows.
UNKNOWN = object()
_unfollowed = None    # the last stream logged as not followed, so it is logged once


@dataclasses.dataclass(frozen=True)
class View:
    title: str = ""
    artist: str = ""
    album: str = ""
    cover: str = ""             # also the background
    duration_ms: int = None
    paused: bool = False
    last: bool = False          # the queue's last track
    next: dict = None           # the queue's next track (player.now_playing), image on the public host
    quality: str = ""
    item_id: str = None
    speed: float = None         # MA's playback speed; None: music, which has none
    offset_ms: int = None       # where the track starts in the page's video; None: unknown
    queue_end_ms: int = None    # where MA's queue ends in the page's video; None: not on its last track
    favorites: dict = dataclasses.field(default_factory=dict)  # {"song", "album"}: None can't be one; {} unknown
    modes: object = UNKNOWN     # {"shuffle", "repeat", "sleep_at"}; None: MA can't say, the buttons hide


def stream_of(device_id, any_player=False):
    """The stream MA pushed for device_id's player, or None.

    any_player: else the latest of any player. No player: always that one.
    """
    payload = store.for_player(devices.get_player_for_device(device_id))
    if not payload and any_player:
        payload = store.for_player(None)
    return payload if payload and payload.get("streamUrl") else None


def of_stream(payload):
    """The View of an MA stream push, without MA's queue."""
    return View(title=payload.get("title") or "", artist=payload.get("artist") or "",
                album=payload.get("album") or "", cover=public_url(payload.get("imageUrl") or ""))


def build(device_id, any_player=False):
    """The View of device_id's stream (see stream_of), without MA's queue; None without a stream."""
    payload = stream_of(device_id, any_player)
    return of_stream(payload) if payload else None


def title_key(v):
    """The track by title, artist and album only: MA's resume gives it a new stream URL."""
    return (v.title, v.artist, v.album) if v else ("", "", "")


def favorites(player_id):
    """MA's favourite flags for what plays ({"song", "album"}), or {} if MA can't say."""
    try:
        return library.favorites(player_id)
    except (MAUnavailable, MAFailed) as e:
        logger.warning("Could not read favourites from MA: %s", safe_error(e))
        return {}


def now(device_id, stream, position_ms=None, known_item=None, paused_at=None, queue=None):
    """MA's track on device_id's page, whose video plays stream and is at position_ms: (View, fresh).

    From the client's copy of MA, as MA's own app shows it (queue: MA's
    queue, if just asked). Not fresh: MA's copy doesn't follow the page's
    stream yet. Paused, the track the page shows (known_item) keeps its
    place; another one starts where the page paused in it (paused_at), else
    at MA's place. Radio has the names MA pushed with its stream.
    """
    payload = stream_of(device_id, any_player=True) or {}
    v = of_stream(payload)
    if client.connected_client() is None:
        return v, False
    player_id = devices.get_player_for_device(device_id)
    playing = player.now_playing(player_id, queue) if player_id else None
    if not playing or not playing["duration_ms"]:
        return v, True
    if store.start_pending(player_id):
        return v, False     # MA has yet to push the stream the skill asked for
    offset = None
    if playing["paused"]:
        if playing["item_id"] != known_item and position_ms is not None:
            offset = position_ms - (playing["elapsed_ms"] if paused_at is None else paused_at)
    elif stream == payload.get("streamUrl"):
        offset = track_time.stream_offset_ms(payload.get("startMs"), playing["track_at"], playing["anchored_at"],
                                             payload.get("since") or 0, playing["speed"])
    if offset is None and not playing["paused"]:
        global _unfollowed
        if _unfollowed != stream:
            _unfollowed = stream
            logger.info("MA's copy doesn't follow the page's stream on %s yet: playing=%s same stream=%s "
                        "anchored_at-since=%s ms", device_id[-8:], playing["track_at"] is not None,
                        stream == payload.get("streamUrl"),
                        round(1000 * (playing["anchored_at"] - (payload.get("since") or 0))))
        return v, False
    duration, speed = playing["duration_ms"], playing["speed"]
    return View(title=playing["title"], artist=playing["artist"], album=playing["album"],
                cover=public_url(playing["image"]), duration_ms=duration, paused=playing["paused"], last=playing["last"],
                next=public_next(playing["next"]), quality=playing["quality"], item_id=playing["item_id"],
                speed=speed, offset_ms=offset,
                queue_end_ms=offset + round(duration / (speed or 1)) if playing["last"] and offset is not None else None,
                modes=playing["modes"]), True


def place_ms(device_id):
    """Where MA's track on device_id's player is now, by the page's track time, in ms; None: MA doesn't say."""
    payload = stream_of(device_id) or {}
    player_id = devices.get_player_for_device(device_id)
    try:
        playing = player.now_playing(player_id) if player_id and payload else None
    except (MAUnavailable, MAFailed):
        return None
    if not playing or playing["paused"]:
        return None
    since = payload.get("since") or 0
    offset = track_time.stream_offset_ms(payload.get("startMs"), playing["track_at"], playing["anchored_at"],
                                         since, playing["speed"])
    return None if offset is None else max(round((1000 * (clock.time() - since) - offset) * (playing["speed"] or 1)), 0)


def keep_offset(old, track):
    """track's trackOffset, kept at old's for the same item when MA gives none (paused) or one within 1.5 s."""
    old = old or {}
    if (old.get("item") == track.get("item") and "trackOffset" in old
            and abs(old["trackOffset"] - track.get("trackOffset", old["trackOffset"])) <= 1500):
        track["trackOffset"] = old["trackOffset"]
    return track


def public_url(url):
    """An MA URL on MA's public host (MA_HOSTNAME), when that is set; other hosts and relative URLs stay."""
    host = config.ma_hostname(raise_on_http_scheme=False)
    if not url or not host or not _ma_host(urlsplit(url).hostname):
        return url
    return quote(urlunsplit(urlsplit(host)[:2] + urlsplit(url)[2:]), safe=":/%?=&")


def _ma_host(hostname):
    """Whether hostname is MA's: its base URL's host, or an IP address (MA pushes its LAN address)."""
    if not hostname:
        return False
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return hostname == client.server_host()


def public_next(upcoming):
    """MA's next track (player.now_playing) with its image on the public host."""
    if not upcoming or not upcoming.get("image"):
        return upcoming
    return dict(upcoming, image=public_url(upcoming["image"]))


def stream_url(device_id):
    """The stream of device_id's player (see stream_of) as Alexa plays it; None without one."""
    url = (stream_of(device_id) or {}).get("streamUrl")
    return re.sub(r"(?i)\.flac(?=$|\?)", ".mp3", url) if url else None
