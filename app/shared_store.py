"""Shared store for MA and Alexa routes.

The latest stream MA pushed is also kept on disk, next to the device
mapping: after a restart of the skill, an open page asks for its track
info, and MA sends it only with its next track.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

_store = None
_version = 0


def _path():
    mapping = os.environ.get("DEVICE_MAPPING_PATH", "/app/instance_data/device_players.json")
    return os.path.join(os.path.dirname(mapping), "latest_stream.json")


def save(store):
    """Set the latest stream, and keep it for the next start."""
    global _store, _version
    _store, _version = store, store.get("version", _version)
    path = _path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(store, f)
        os.replace(path + ".tmp", path)
    except Exception:
        logger.exception("Failed to keep the latest stream in %s", path)


def _load():
    global _store, _version
    try:
        with open(_path(), encoding="utf-8") as f:
            store = json.load(f)
    except FileNotFoundError:
        return
    except Exception:
        logger.exception("Failed to read the latest stream from %s", _path())
        return
    if isinstance(store, dict) and store.get("streamUrl"):
        _store, _version = store, int(store.get("version") or 0)


_load()
