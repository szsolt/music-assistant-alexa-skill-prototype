# -*- coding: utf-8 -*-
"""What a voice command asks for: "shuffle the album Y" -> what to find and how to play it.

The handlers in lambda_function do the talking to Alexa; this module only
reads the intent and words the answer.
"""

from . import data

# intent -> (slot with the name, kinds to search in order of preference
#            (None: the kind slot says, "queue the album X"), queue option,
#            radio mode)
INTENTS = {
    "PlayArtist": ("artist", ("artist",), "replace", False),
    "PlayAlbum": ("album", ("album",), "replace", False),
    "PlaySong": ("song", ("song",), "replace", False),
    "PlayPlaylist": ("playlist", ("playlist",), "replace", False),
    "PlayRadio": ("name", ("artist", "song", "album", "playlist"), "replace", True),
    "Queue": ("name", None, "add", False),
    "PlayNext": ("name", None, "next", False),
    # Retired, but Amazon's model has it ("play X") until the new one is
    # uploaded (60 s after a start, or pasted by hand).
    "PlayAnything": ("name", ("playlist", "artist", "album", "song"), "replace", False),
}
# Queue and play next in a model from before the kind slot.
_ANY_KIND = ("album", "playlist", "artist", "song")


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
    return slot_match(slots, name) or slot.value or None


def slot_match(slots, name):
    """The listed value Alexa matched for the slot called name, or None if the words aren't in the list."""
    slot = (slots or {}).get(name)
    try:
        for authority in slot.resolutions.resolutions_per_authority:
            if authority.status.code.value == "ER_SUCCESS_MATCH" and authority.values:
                return authority.values[0].value.name
    except AttributeError:
        pass
    return None


def request_of(intent_name, slots):
    """What to find and play for the intent, or None if it isn't a voice command.

    Returns a dict: meant (False if the play or kind word is missing, as
    when Alexa forces "turn off the light" into a music command), heard
    (None if nothing usable was heard), artist (from "by X"), kinds,
    option ("replace", "add", "next"), shuffle (True, False, or None to
    leave it) and radio.
    """
    if intent_name not in INTENTS:
        return None
    slot, kinds, option, radio = INTENTS[intent_name]
    heard = slot_text(slots, slot)
    meant = True
    song = slot_text(slots, "song")
    if kinds is None and "kind" not in (slots or {}):
        kinds = _ANY_KIND
        if not heard:
            heard, kinds = song, ("song",)
    elif kinds is None:
        kind = slot_match(slots, "kind")
        if kind == "song":
            heard = song or heard
        elif not kind and song:
            heard, kind = song, "song"
        meant, kinds = kind is not None, (kind,) if kind else ()
    shuffle = None
    if option == "replace" and not radio:
        mode = slot_match(slots, "mode")
        meant, shuffle = mode is not None, mode == "shuffle"
    return {"meant": meant, "heard": heard, "artist": slot_text(slots, "artist"), "kinds": kinds,
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
