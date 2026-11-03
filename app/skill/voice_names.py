# -*- coding: utf-8 -*-
"""Slot values for voice commands, built from MA's library names.

Uploaded with the interaction model (model_upload.py), they bias Alexa's
recognition towards the names in the library. The skill matches what was
heard against MA anyway (voice_match.py), so a name left out still works,
just with weaker recognition.
"""

import hashlib
import json
import re
import unicodedata

# Amazon: at most 140 characters per slot value, 1.5 MB per model and
# 50,000 slot values in all. The names get most of it; the intents are small.
MAX_VALUE_CHARS = 140
MAX_BYTES = 1_200_000
MAX_VALUES = 45_000

# Kinds in budget order. MA_NAME (the name in "queue album X" and radio)
# holds every kind but songs.
KINDS = ("playlist", "artist", "album", "song")
SLOT_TYPES = {"playlist": "MA_PLAYLIST", "artist": "MA_ARTIST",
              "album": "MA_ALBUM", "song": "MA_SONG"}
ANY_TYPE = "MA_NAME"
PLAYER_TYPE = "MA_PLAYER"     # MA's players, for "move the music to X"

_BRACKETS = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_SPACES = re.compile(r"\s+")
# Characters Amazon doesn't accept in slot values.
_UNSUPPORTED = re.compile(r"[^\w\s'.&\-]", re.UNICODE)


def fold(text):
    """text without accents, e.g. "Deák" -> "Deak"."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def without_brackets(text):
    """text without bracketed parts, e.g. "Song (Remastered 2011)" -> "Song"."""
    return _SPACES.sub(" ", _BRACKETS.sub("", text)).strip()


def normalize(text):
    """For comparing names: no accents, case, brackets or punctuation."""
    text = fold(without_brackets(text or "")).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^\w\s]", " ", text)
    return _SPACES.sub(" ", text).strip()


# Hungarian spelling -> English, for English speech recognition. In order;
# capitals mark what is done, so "sz" doesn't become "sh" afterwards.
_HUNGARIAN = re.compile(r"[áéíóöőúüű]|gy|zs|cs|sz", re.IGNORECASE)
_RESPELL = (("dzs", "J"), ("cs", "CH"), ("gy", "DY"), ("ly", "Y"), ("sz", "S"), ("zs", "ZH"),
            ("s", "SH"), ("c", "TS"), ("j", "Y"), ("á", "AH"), ("é", "AY"), ("í", "EE"),
            ("ó", "OH"), ("ö", "ER"), ("ő", "ER"), ("ú", "OO"), ("ü", "EW"), ("ű", "EW"))
# Spellings that sound alike get the same key, e.g. "dyula" and "julia".
_SOUNDS = (("ph", "f"), ("ck", "k"), ("ch", "C"), ("sh", "S"), ("zh", "S"), ("dj", "J"), ("dy", "J"),
           ("dg", "J"), ("j", "J"), ("q", "k"), ("x", "ks"), ("c", "k"), ("z", "s"), ("w", "v"),
           ("y", "i"), ("h", ""))


def sounds_like(name):
    """name spelled as English speakers would hear it, if it looks Hungarian; else None.

    "Deák Bill Gyula" -> "deahk Bill dyula". Only words that look
    Hungarian change.
    """
    words = []
    changed = False
    for word in _clean(without_brackets(name or "")).split():
        if _HUNGARIAN.search(word):
            respelled = word.lower()
            for hungarian, english in _RESPELL:
                respelled = respelled.replace(hungarian, english)
            word = respelled.lower()
            changed = True
        words.append(word)
    return " ".join(words) if changed else None


def sound_key(text):
    """A rough key of how text sounds, for comparing what Alexa heard with a name."""
    key = normalize(text)
    for spelling, sound in _SOUNDS:
        key = key.replace(spelling, sound)
    return re.sub(r"(.)\1+", r"\1", key.lower())


def _clean(text):
    text = _SPACES.sub(" ", _UNSUPPORTED.sub(" ", text or "")).strip()
    return text[:MAX_VALUE_CHARS].strip()


def slot_value(name):
    """{"name": {"value", "synonyms"}} for name, or None if nothing is left."""
    value = _clean(name)
    if not value:
        return None
    synonyms = []
    for variant in (fold(value), _clean(without_brackets(name)), fold(_clean(without_brackets(name))),
                    sounds_like(name)):
        if variant and variant != value and variant not in synonyms:
            synonyms.append(variant)
    entry = {"value": value}
    if synonyms:
        entry["synonyms"] = synonyms
    return {"name": entry}


def build_types(names):
    """Slot types from names: {kind: [name, ...]} in priority order.

    Each kind's list comes favourites first, then recently played. Kinds
    are taken in KINDS order until MAX_BYTES or MAX_VALUES is reached; the
    rest is left out. Returns (types, counts), counts: {kind: kept}.
    """
    values = {kind: [] for kind in KINDS}
    any_values = []
    seen = {kind: set() for kind in KINDS}
    seen_any = set()
    size = 0
    count = 0
    full = False
    for kind in KINDS:
        for name in names.get(kind) or ():
            entry = slot_value(name)
            if not entry:
                continue
            key = entry["name"]["value"].lower()
            if key in seen[kind]:
                continue
            for_any = kind != "song" and key not in seen_any
            cost = len(json.dumps(entry, ensure_ascii=False).encode()) * (2 if for_any else 1)
            if size + cost > MAX_BYTES or count + 1 + for_any > MAX_VALUES:
                full = True
                break
            size += cost
            count += 1 + for_any
            seen[kind].add(key)
            values[kind].append(entry)
            if for_any:
                seen_any.add(key)
                any_values.append(entry)
        if full:
            break
    types = [{"name": SLOT_TYPES[kind], "values": values[kind]} for kind in KINDS]
    types.append({"name": ANY_TYPE, "values": any_values})
    for slot_type in types:
        if not slot_type["values"]:
            # Amazon rejects an empty slot type.
            slot_type["values"] = [{"name": {"value": "music assistant"}}]
    return types, {kind: len(values[kind]) for kind in KINDS}


def player_type(names):
    """The slot type of MA's player names."""
    values, seen = [], set()
    for name in names:
        entry = slot_value(name)
        if entry and entry["name"]["value"].lower() not in seen:
            seen.add(entry["name"]["value"].lower())
            values.append(entry)
    # Amazon rejects an empty slot type.
    return {"name": PLAYER_TYPE, "values": values or [{"name": {"value": "music assistant"}}]}


def digest(types):
    """A short hash of the slot types, to upload only when they changed.

    Order doesn't count: Amazon doesn't care, and play order changes daily.
    """
    rows = sorted((t["name"], json.dumps(v, sort_keys=True, ensure_ascii=False))
                  for t in types for v in t["values"])
    raw = json.dumps(rows, ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()[:16]
