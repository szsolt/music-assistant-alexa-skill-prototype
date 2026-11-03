# -*- coding: utf-8 -*-
"""What the user asks of the player, on any input: voice, the page's buttons, the Echo's own.

run(device_id, action) does one action for the MA player paired with the
Echo device_id. It also keeps where each Echo paused, so a resume goes on
from there, and drops the intent MA's Alexa provider echoes back after a
pause or stop (see ma/player.py for why it does).
"""

import dataclasses
import logging
import threading

from . import clock, devices, handoff, view
from . import echo as echos
from .ma import library, player
from .ma.errors import MAFailed, MAUnavailable, or_none, safe_error

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class Play:
    position_ms: int = None     # None: where the page or MA paused


@dataclasses.dataclass(frozen=True)
class Pause:
    position_ms: int = None     # where the page paused in the track; None: MA keeps its own place
    page_paused: bool = False   # the page paused itself: MA's pause hand-off isn't needed


@dataclasses.dataclass(frozen=True)
class Next:
    pass


@dataclasses.dataclass(frozen=True)
class Previous:
    pass


@dataclasses.dataclass(frozen=True)
class Seek:
    position_ms: int


@dataclasses.dataclass(frozen=True)
class Jump:
    item_id: str                # a queue item, played from its start


@dataclasses.dataclass(frozen=True)
class StartOver:
    pass


@dataclasses.dataclass(frozen=True)
class FavoriteSong:
    on: bool


@dataclasses.dataclass(frozen=True)
class FavoriteAlbum:
    on: bool


@dataclasses.dataclass(frozen=True)
class FavoriteArtist:
    on: bool


@dataclasses.dataclass(frozen=True)
class Shuffle:
    on: bool


@dataclasses.dataclass(frozen=True)
class Repeat:
    mode: str                   # one of player.REPEAT_MODES


@dataclasses.dataclass(frozen=True)
class Stop:
    unless_paused: bool = False     # leave a paused MA as it is


@dataclasses.dataclass(frozen=True)
class QueueEnded:
    pass


@dataclasses.dataclass(frozen=True)
class Result:
    status: str                 # "ok", "unpaired" or "failed"
    unreachable: bool = False   # failed: MA couldn't be reached
    name: str = None            # a favourite: the name of what it marked; None: nothing of that kind plays

    @property
    def ok(self):
        return self.status == "ok"


OK = Result("ok")
UNPAIRED = Result("unpaired")
FAILED = Result("failed")


def run(device_id, action):
    """Do action for the MA player paired with the Echo device_id."""
    player_id = devices.get_player_for_device(device_id)
    if not player_id:
        logger.info("No MA player mapped for device_id=%s", (device_id or "")[-8:])
        return UNPAIRED
    try:
        result = _RUN[type(action)](device_id, player_id, action)
        return result if isinstance(result, Result) else OK
    except MAUnavailable as e:
        logger.warning("MA unreachable for %s: %s", type(action).__name__, safe_error(e))
        return Result("failed", unreachable=True)
    except MAFailed as e:
        logger.warning("MA refused %s: %s", type(action).__name__, safe_error(e))
        return FAILED


def _resume(device_id, player_id, position_ms=None, item_id=None):
    if position_ms is None and item_id is None:
        position_ms = page_paused(device_id)
    start = player.resume_at(player_id, position_ms, item_id)
    playing(device_id)
    logger.info("Resuming %s at %s ms", player_id, start)


def _echo(device_id, command):
    """Whether this is MA's echo of the command we just sent: then nothing is done."""
    if not is_echo_of_ma_command(device_id, command):
        return False
    logger.info("Suppressing MA %s: echo of our own MA-triggered command for device_id=%s",
                command, (device_id or "")[-8:])
    return True


def _send(device_id, player_id, command, unless_paused=False):
    """Send pause or stop, unless this is MA's echo of the one we just sent.

    unless_paused: leave a paused MA as it is (see CancelOrStopIntentHandler).
    """
    if _echo(device_id, command):
        return
    if unless_paused and or_none("read the track time", player.is_paused, player_id):
        logger.info("Not sending %s to MA player %s: it is paused", command, player_id)
        return
    mark_ma_triggered(device_id, command)
    player.send_player_command(player_id, command)


def _pause(device_id, player_id, action):
    # The page pauses itself: MA's pause hand-off isn't needed (or, unpatched,
    # MA makes the Echo hear "pause", which reopens the page paused).
    if _echo(device_id, "pause"):
        return
    paused_at(device_id, action.position_ms)
    mark_ma_triggered(device_id, "pause")
    player.send_player_command(player_id, "pause")
    if action.page_paused:
        handoff.take(device_id, "pause")


def _favorite(kind):
    def run_it(device_id, player_id, action):
        logger.info("Favourite %s: %s", kind, "on" if action.on else "off")
        result = library.favorite_playing(player_id, kind, action.on)
        return Result("ok", name=result[0]) if result else OK
    return run_it


_RUN = {
    Play: lambda d, p, a: _resume(d, p, a.position_ms),
    Pause: _pause,
    Next: lambda d, p, a: player.send_player_command(p, "next"),
    Previous: lambda d, p, a: player.send_player_command(p, "previous"),
    Seek: lambda d, p, a: _resume(d, p, a.position_ms),
    Jump: lambda d, p, a: _resume(d, p, item_id=a.item_id),
    StartOver: lambda d, p, a: player.send_player_command(p, "start_over"),
    FavoriteSong: _favorite("song"),
    FavoriteAlbum: _favorite("album"),
    FavoriteArtist: _favorite("artist"),
    Shuffle: lambda d, p, a: player.set_shuffle(p, a.on),
    Repeat: lambda d, p, a: player.set_repeat_mode(p, a.mode),
    Stop: lambda d, p, a: _send(d, p, "stop", a.unless_paused),
    QueueEnded: lambda d, p, a: _send(d, p, "stop"),
}


# How long to suppress the echoed intent caused by pause/stop.
# Alexa.TextCommand is a direct API call (not acoustic), so the round trip
# is consistently fast; this just needs to comfortably cover it.
_ECHO_SUPPRESS_SECONDS = 8


def mark_ma_triggered(device_id, command):
    """Record that we're about to send `command` to MA for device_id.

    Call this immediately before send_player_command() for pause/stop,
    so the intent MA's alexa provider echoes back gets recognized and not
    re-forwarded to MA again.
    """
    if not device_id:
        return
    with _lock:
        echos.get(device_id).sent[command] = clock.time() + _ECHO_SUPPRESS_SECONDS


def is_echo_of_ma_command(device_id, command):
    """Check (and consume) whether this looks like the echo of our own MA command."""
    echo = echos.find(device_id) if device_id else None
    if echo is None:
        return False
    with _lock:
        expires_at = echo.sent.pop(command, None)
    return expires_at is not None and expires_at >= clock.time()


_lock = threading.Lock()   # guards echo.positions and echo.sent; taken before echo's lock


class _Positions:
    """Where an Echo paused, each as (title key, ms into the track)."""

    def __init__(self):
        self.paused = None          # where its page was paused
        self.stopped = None         # its own player: (stream url, ms)


def _of(device_id):
    """device_id's positions, made on first use. Hold _lock."""
    echo = echos.get(device_id)
    if echo.positions is None:
        echo.positions = _Positions()
    return echo.positions


def _seen(device_id):
    """device_id's positions, or empty ones it doesn't keep. Hold _lock."""
    echo = echos.find(device_id) if device_id else None
    return echo.positions if echo is not None and echo.positions is not None else _Positions()


def _title_key(device_id):
    """The current track of the MA player paired with this Echo, else of the latest stream of any player."""
    return view.title_key(view.build(device_id, any_player=True))


def paused_at(device_id, position_ms):
    """Keep where the user paused device_id's page in the current track."""
    if device_id and position_ms is not None:
        key = _title_key(device_id)
        with _lock:
            _of(device_id).paused = (key, position_ms)


def page_paused(device_id):
    """Where the user paused device_id's page in the current track, or None."""
    key = _title_key(device_id)
    with _lock:
        entry = _seen(device_id).paused
    return entry[1] if entry and entry[0] == key else None


def playing(device_id):
    """device_id plays on: forget where it paused."""
    with _lock:
        _seen(device_id).paused = None


def stopped(device_id, url, offset_ms):
    """device_id's own player stopped url at offset_ms."""
    if device_id and url:
        with _lock:
            _of(device_id).stopped = (view.public_url(url), offset_ms or 0)


def resume_offset(device_id, url):
    """Where device_id's own player stopped url, or 0: the offset only fits that same stream.

    The Echo names the stream as it played it: on MA's public host.
    """
    with _lock:
        stopped_at = _seen(device_id).stopped
    return stopped_at[1] if stopped_at and stopped_at[0] == view.public_url(url) else 0
