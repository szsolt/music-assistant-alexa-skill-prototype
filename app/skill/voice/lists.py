# -*- coding: utf-8 -*-
"""The voice slot types from MA's library, kept up to date in a file.

One minute after start, then once a day, the skill builds the slot types
from MA's library names (names.py) and writes them to
voice_types.json next to latest_stream.json, with their hash. The model
upload takes them from there: build_model() merges them and the voice
template into a locale's model; model_upload.py sends it to Amazon.
"""

import json
import logging
import os
import threading
import time

from .. import config, model_upload
from ..ma import library, player
from ..ma.client import LONG_TIMEOUT_S
from ..ma.errors import MAFailed, MAUnavailable, safe_error
from . import model as voice_model, names as voice_names

logger = logging.getLogger(__name__)

START_DELAY_S = 60
EVERY_S = 24 * 3600


def path():
    return config.data_path("voice_types.json")


def names(kinds):
    """Library names of those kinds, as in the voice model."""
    wanted = {voice_names.SLOT_TYPES[kind] for kind in kinds}
    types = (load() or {}).get("types") or []
    return [v["name"]["value"] for t in types if t["name"] in wanted for v in t["values"]]


def load():
    """{"hash", "counts", "types"} as last written, or None."""
    try:
        with open(path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def refresh():
    """Rebuild the slot types from MA. Returns (hash, counts, changed)."""
    types, counts = voice_names.build_types(library.library_names())
    players = [name for _, name in player.players(timeout=LONG_TIMEOUT_S)]
    types.append(voice_names.player_type(players))
    counts["player"] = len(players)
    digest = voice_names.digest(types)
    old = load()
    if old and old.get("hash") == digest:
        return digest, counts, False
    tmp = path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"hash": digest, "counts": counts, "types": types}, f, ensure_ascii=False)
    os.replace(tmp, path())
    return digest, counts, True


def build_model(base, locale, types):
    """base (a locale's interaction model) with the voice intents and types, or None without a template."""
    template = voice_model.template_for(locale)
    return voice_model.merge(base, template, types) if template else None


def _loop():
    time.sleep(START_DELAY_S)
    while True:
        try:
            digest, counts, changed = refresh()
            logger.info("Voice names %s: %s (%s)", "changed" if changed else "unchanged", digest, counts)
        except (MAUnavailable, MAFailed) as e:
            logger.warning("Voice names: MA unreachable or refused, trying again tomorrow: %s", safe_error(e))
        except Exception:
            logger.exception("Voice names: could not build them, trying again tomorrow")
        try:
            uploaded = model_upload.upload(load())
            if uploaded is None:
                logger.info("Voice model: no LWA credentials, not uploading (download it from /status)")
        except Exception as e:
            logger.warning("Voice model: upload failed, trying again tomorrow: %s", e)
        time.sleep(EVERY_S)


def start():
    threading.Thread(target=_loop, name="voice-names", daemon=True).start()
