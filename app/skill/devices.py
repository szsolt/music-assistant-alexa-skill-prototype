# -*- coding: utf-8 -*-
"""Persistent mapping of Alexa device_id -> Music Assistant player_id.

Alexa's Custom Skill API only exposes an opaque, per-skill device_id in
the request context - there is no way to resolve it to a friendly device
name or to the corresponding MA player. The id is stable per device
though, so a one-time manual mapping (configured via the /devices page)
is a durable workaround.

An Echo also pairs itself: when MA sends a stream for a player with no
Echo yet, the next unpaired Echo that opens the skill within
PAIR_WINDOW_S is that player's Echo (MA just told it to play).
"""

import json
import logging
import os
import threading

from . import clock, config

logger = logging.getLogger(__name__)

_lock = threading.RLock()   # set_player_for_device holds it across its load and save
_read = (None, None, {})     # the file last read: path, (mtime, size), its mapping


def load_mapping():
    """Return the device_id -> player_id mapping dict (empty if none saved yet)."""
    global _read
    path = config.device_mapping_path()
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return {}
    stamp = (st.st_mtime_ns, st.st_size)
    if _read[:2] == (path, stamp):
        return dict(_read[2])
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        logger.exception("Failed to read device mapping from %s", path)
        return {}
    if not isinstance(data, dict):
        return {}
    _read = (path, stamp, data)
    return dict(data)


def save_mapping(mapping):
    global _read
    path = config.device_mapping_path()
    try:
        with _lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp_path = path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(mapping, f, indent=2, sort_keys=True)
            os.replace(tmp_path, path)
            _read = (None, None, {})
        return True
    except Exception:
        logger.exception("Failed to write device mapping to %s", path)
        return False


def get_player_for_device(device_id):
    if not device_id:
        return None
    return load_mapping().get(device_id)


def get_devices_for_player(player_id):
    """The device_ids paired with player_id."""
    if not player_id:
        return []
    return [d for d, p in load_mapping().items() if p == player_id]


def set_player_for_device(device_id, player_id):
    with _lock:
        mapping = load_mapping()
        if player_id:
            mapping[device_id] = player_id
        else:
            mapping.pop(device_id, None)
        return save_mapping(mapping)


def is_another_echos_stream(device_id, player_id):
    """True if device_id is unpaired and player_id, the latest stream's, has an Echo.

    The skill keeps one latest stream for all players: an unpaired Echo
    would play the stream of whatever player sent last.
    """
    return not get_player_for_device(device_id) and bool(get_devices_for_player(player_id))



def is_other_players_stream(device_id, player_id):
    """True if device_id is paired with a player other than player_id, the latest stream's."""
    own = get_player_for_device(device_id)
    return bool(own and player_id and own != player_id)

PAIR_WINDOW_S = 10
_waiting = {}   # player_id -> clock.monotonic() of its stream, for players with no Echo
_waiting_lock = threading.Lock()


def wait_for_pairing(player_id):
    """MA sent a stream for player_id: pair the next unpaired Echo with it."""
    if not player_id or get_devices_for_player(player_id):
        return
    with _waiting_lock:
        _waiting[player_id] = clock.monotonic()


def pair_if_waiting(device_id):
    """Pair an unpaired device_id with the one player waiting for an Echo.

    Only if exactly one player is waiting: with two, which Echo is which
    is a guess. Returns the player_id it paired, else None.
    """
    if not device_id or get_player_for_device(device_id):
        return None
    now = clock.monotonic()
    with _waiting_lock:
        for player_id, since in list(_waiting.items()):
            if now - since > PAIR_WINDOW_S:
                del _waiting[player_id]
        if len(_waiting) != 1:
            return None
        player_id = _waiting.popitem()[0]
    if not set_player_for_device(device_id, player_id):
        return None
    logger.info("Paired device %s with MA player %s", device_id[-8:], player_id)
    return player_id
