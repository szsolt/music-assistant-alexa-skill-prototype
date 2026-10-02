# -*- coding: utf-8 -*-
"""What a voice command asks for: "shuffle the album Y" -> what to find and how to play it.

The handlers in lambda_function do the talking to Alexa; this module only
reads the intent and words the answer.
"""

from . import data

# intent -> (slot with the name, kinds to search in order of preference,
#            queue option, radio mode)
INTENTS = {
    "PlayArtist": ("artist", ("artist",), "replace", False),
    "PlayAlbum": ("album", ("album",), "replace", False),
    "PlaySong": ("song", ("song",), "replace", False),
    "PlayPlaylist": ("playlist", ("playlist",), "replace", False),
    "PlayAnything": ("name", ("playlist", "artist", "album", "song"), "replace", False),
    "PlayRadio": ("name", ("artist", "song", "album", "playlist"), "replace", True),
    "Queue": ("name", ("album", "playlist", "artist", "song"), "add", False),
    "PlayNext": ("name", ("album", "playlist", "artist", "song"), "next", False),
}


# intent -> True to mark as a favourite, False to unmark
FAVORITE_INTENTS = {"AddFavorite": True, "RemoveFavorite": False}
_FAVORITE_WORDS = {"song": "song", "track": "song", "tune": "song", "album": "album", "record": "album",
                   "artist": "artist", "band": "artist", "singer": "artist"}


def favorite_kind(slots):
    """What "like this X" means: "song" (also when X is missing), "album" or "artist"."""
    return _FAVORITE_WORDS.get((slot_text(slots, "what") or "").lower(), "song")


def slot_text(slots, name):
    """The library name Alexa matched for slot name, else the words it heard."""
    slot = (slots or {}).get(name)
    if slot is None:
        return None
    try:
        for authority in slot.resolutions.resolutions_per_authority:
            if authority.status.code.value == "ER_SUCCESS_MATCH" and authority.values:
                return authority.values[0].value.name
    except AttributeError:
        pass
    return slot.value or None


def request_of(intent_name, slots):
    """What to find and play for the intent, or None if it isn't a voice command.

    Returns a dict: heard (None if nothing usable was heard), artist (from
    "by X"), kinds, option ("replace", "add", "next"), shuffle (True, False,
    or None to leave it) and radio.
    """
    if intent_name not in INTENTS:
        return None
    slot, kinds, option, radio = INTENTS[intent_name]
    heard = slot_text(slots, slot)
    if not heard and intent_name in ("Queue", "PlayNext"):
        heard, kinds = slot_text(slots, "song"), ("song",)
    shuffle = None
    if option == "replace" and not radio:
        mode = (slot_text(slots, "mode") or "").lower()
        shuffle = "shuffle" in mode or mode == "mix"
    return {"heard": heard, "artist": slot_text(slots, "artist"), "kinds": kinds,
            "option": option, "shuffle": shuffle, "radio": radio}


def answer(_, request, match):
    """What Alexa says once match plays."""
    name = match["name"]
    if match["kind"] in ("song", "album") and match.get("artist"):
        name = _(data.BY_MSG).format(name, match["artist"])
    if request["radio"]:
        return _(data.RADIO_MSG).format(name)
    if request["option"] == "add":
        return _(data.QUEUED_MSG).format(name)
    if request["option"] == "next":
        return _(data.PLAYING_NEXT_MSG).format(name)
    if request["shuffle"]:
        return _(data.SHUFFLING_MSG).format(name)
    return _(data.PLAYING_MSG).format(name)
