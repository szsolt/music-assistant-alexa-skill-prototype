# -*- coding: utf-8 -*-
"""Picking the MA item a voice command meant.

Alexa hears a name ("deck bill"); MA's search returns candidates. Names
are compared without accents, case, brackets or punctuation, and by how
they sound (English speech recognition mangles Hungarian names). An
exact match wins, else the best fuzzy score. Below MIN_SCORE nothing
matches.
"""

from difflib import SequenceMatcher

from .voice_names import normalize, sound_key, sounds_like

MIN_SCORE = 0.55
# "By X" keeps candidates whose artist is at least this close to X.
ARTIST_SCORE = 0.6
# A match by sound counts a little less than one by spelling.
SOUND_WEIGHT = 0.95
# Heard words matching only part of a name ("deck" for "Deák Bill Gyula").
PART_WEIGHT = 0.9


def score(heard, name):
    """How well name matches heard, 0..1."""
    a, b = normalize(heard), normalize(name)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    heard_key = sound_key(heard)
    for spoken in {name, sounds_like(name) or name}:
        ratio = max(ratio, SOUND_WEIGHT * SequenceMatcher(None, heard_key, sound_key(spoken)).ratio())
    # "beatles" for "The Beatles": a whole-word part of the name.
    if f" {a} " in f" {b} " or f" {b} " in f" {a} ":
        ratio = max(ratio, 0.85)
    return ratio


def pick(heard, candidates, artist=None, kinds=None):
    """(candidate, score) of the best match for heard, or (None, best score).

    candidates: dicts with "kind", "name" and "artist". kinds: their order
    of preference when scores tie (default: as given). artist: keep only
    candidates by that artist, if any are.
    """
    if not heard or not candidates:
        return None, 0.0
    if artist:
        by_artist = [c for c in candidates if score(artist, c.get("artist") or "") >= ARTIST_SCORE]
        if by_artist:
            candidates = by_artist
    order = list(kinds or [])

    def rank(candidate):
        kind = candidate.get("kind")
        position = order.index(kind) if kind in order else len(order)
        return (round(score(heard, candidate.get("name") or ""), 3), -position)

    best = max(candidates, key=rank)
    best_score = rank(best)[0]
    if best_score < MIN_SCORE:
        return None, best_score
    return best, best_score


def _part_score(heard, name):
    """score against the words of name in a row, as many as heard has."""
    words = name.split()
    count = len(heard.split())
    if count >= len(words):
        return 0.0
    return PART_WEIGHT * max(score(heard, " ".join(words[i:i + count]))
                             for i in range(len(words) - count + 1))


def closest(heard, names):
    """The name in names that sounds most like heard, or None if none is close enough.

    Alexa often catches only part of a foreign name, so parts count too.
    """
    def best_score(name):
        return max(score(heard, name), _part_score(heard, name))

    best = max(names, key=best_score, default=None)
    return best if best is not None and best_score(best) >= MIN_SCORE else None
