"""Shared store for MA and Alexa routes.

The latest stream MA pushed, for each MA player: two Echos play their
own music. _store is the latest of any player (a launch, /latest-url).
All are also kept on disk, next to the device mapping: after a restart
of the skill, an open page asks for its track info, and MA sends it only
with its next track.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

_store = None
_version = 0
_streams = {}   # player_id -> the latest stream MA pushed for it


def _path():
    mapping = os.environ.get("DEVICE_MAPPING_PATH", "/app/instance_data/device_players.json")
    return os.path.join(os.path.dirname(mapping), "latest_stream.json")


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

    Returns the stream's player id, or None if unknown.
    """
    global _store, _version
    player_id = _player_of(store)
    store = dict(store, playerId=player_id)
    _store, _version = store, store.get("version", _version)
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


def for_player(player_id):
    """The latest stream of player_id, or None. No player: the latest of any.

    A player without one of its own gets the latest if nobody knows whose
    that is (kept from before streams per player).
    """
    if not player_id:
        return _store
    if player_id in _streams:
        return _streams[player_id]
    return _store if _store and not _store.get("playerId") else None


def _load():
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


_load()
