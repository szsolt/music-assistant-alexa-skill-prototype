"""The streams MA pushed: the latest of each MA player, and the latest of any.

Two Echos play their own music. The latest of any serves a launch and
/ma/latest-url. All are also kept on disk, next to the device mapping:
after a restart of the skill, an open page asks for its track info, and
MA sends it only with its next track.
"""

import json
import logging
import os
import threading

from . import clock, config

logger = logging.getLogger(__name__)

_store = None
_version = 0
_streams = {}   # player_id -> the latest stream MA pushed for it
_sent_at = {}   # player_id -> monotonic time MA last sent it a stream to play
_starts = {}    # player_id -> (where in its track the skill asked MA to start a stream, ms; monotonic time)
START_PUSH_S = 10  # MA pushes the stream the skill asked for within this
_lock = threading.Lock()


def _path():
    return config.data_path("latest_stream.json")


def _player_of(store):
    """The store's MA player: its playerId, or the player whose stream it is.

    MA's push for the next track in its flow has no playerId, but the
    same stream URL as the player's last push.
    """
    if store.get("playerId"):
        return store["playerId"]
    for player_id, kept in _streams.items():
        if kept.get("streamUrl") == store.get("streamUrl"):
            return player_id
    return None


def save(store):
    """Set the latest stream (of its player), and keep it for the next start.

    Without a "version", the stream gets the next one. "since": when MA
    sent its player the stream (clock.time()); its flow's next tracks keep it.
    "startMs": where the skill asked MA to start the stream's first track (see starting).
    Returns the stream's player id, or None if unknown.
    """
    global _store, _version
    with _lock:
        if store.get("playerId"):
            _sent_at[store["playerId"]] = clock.monotonic()
            start_ms, at = _starts.pop(store["playerId"], (None, -START_PUSH_S))
            if clock.monotonic() - at < START_PUSH_S:
                store = dict(store, startMs=start_ms)
        player_id = _player_of(store)
        since = clock.time() if store.get("playerId") else (_streams.get(player_id) or {}).get("since")
        store = dict(store, playerId=player_id, since=since)
        store.setdefault("version", _version + 1)
        _store, _version = store, store["version"]
        if player_id:
            _streams[player_id] = store
        path = _path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                json.dump({"latest": store, "players": _streams}, f)
            os.replace(path + ".tmp", path)
        except Exception:
            logger.exception("Failed to keep the latest stream in %s", path)
    return player_id


def starting(player_id, start_ms):
    """The skill asks MA for a stream of player_id's track from start_ms: MA's next push starts there.

    None: MA refused, so no such push comes.
    """
    with _lock:
        if start_ms is None:
            _starts.pop(player_id, None)
        else:
            _starts[player_id] = (start_ms, clock.monotonic())


def start_pending(player_id):
    """Whether MA has yet to push the stream the skill asked for on player_id."""
    with _lock:
        return clock.monotonic() - _starts.get(player_id, (None, -START_PUSH_S))[1] < START_PUSH_S


def for_player(player_id):
    """The latest stream of player_id, or None. No player: the latest of any.

    A player without one of its own gets the latest if nobody knows whose
    that is (kept from before streams per player).
    """
    with _lock:
        if not player_id:
            return _store
        if player_id in _streams:
            return _streams[player_id]
        return _store if _store and not _store.get("playerId") else None


def version():
    """The version of the latest stream of any player (0 before any)."""
    with _lock:
        return _version


def load():
    """Read the streams kept from before this start."""
    global _store, _version, _streams
    try:
        with open(_path(), encoding="utf-8") as f:
            kept = json.load(f)
    except FileNotFoundError:
        return
    except Exception:
        logger.exception("Failed to read the latest stream from %s", _path())
        return
    if not isinstance(kept, dict):
        return
    if "latest" not in kept:   # the file from before streams per player
        kept = {"latest": kept, "players": {}}
    store = kept.get("latest")
    if isinstance(store, dict) and store.get("streamUrl"):
        _store, _version = store, int(store.get("version") or 0)
    players = kept.get("players")
    if isinstance(players, dict):
        _streams = {p: s for p, s in players.items() if isinstance(s, dict)}



def sent_since(player_id, seconds, now=None):
    """True if MA sent player_id a stream to play in the last seconds.

    Not its pushes for the next track in its flow: those only bring metadata.
    """
    now = clock.monotonic() if now is None else now
    with _lock:
        sent = _sent_at.get(player_id)
    return sent is not None and now - sent <= seconds
