# -*- coding: utf-8 -*-
"""The player page as a web app (Alexa.Presentation.HTML), all over the LAN.

The page gets its start state inline, then JSON messages over SSE:
{"set": {field: value}, "src": new stream, "media": "play"|"pause", "ready": True}.
It sends its position every second and its buttons as actions.

The skill's logic speaks APL (commands out, UserEvents in): this module is
the adapter. Replies' APL commands become messages, and actions and
reports run through APLUserEventHandler as APL UserEvents.
"""

import json
import logging
import os
import queue
import secrets
import threading
import time
from datetime import datetime, timezone

from ask_sdk_model import RequestEnvelope
from ask_sdk_model.interfaces.alexa.presentation.apl import ExecuteCommandsDirective
from ask_sdk_model.interfaces.alexa.presentation.html import (
    StartDirective, StartRequest, StartRequestMethod, Configuration)
from ask_sdk_core.serialize import DefaultSerializer

from . import bell

logger = logging.getLogger(__name__)

# The longest Amazon allows; the page closes when it runs out.
TIMEOUT_SECONDS = 1800
KEEPALIVE_SECONDS = 15

_local = threading.local()
_lock = threading.Lock()
_started = {}   # page id -> its start state
_queues = {}    # page id -> [queue of messages], one per open SSE stream
_presses = {}   # page id -> its last press number (bell.pressed)
_serializer = DefaultSerializer()

# The page's fields, by the APL page's names for them
_FIELDS = {
    "coverImageSource": "cover", "backgroundImageSource": "background", "titleSize": "titleSize",
    "album": "album", "quality": "quality", "trackOffset": "trackOffset", "trackDuration": "trackDuration",
    "queueEnd": "queueEnd", "nextTitle": "nextTitle", "nextTitleSize": "nextTitleSize",
    "nextSecondary": "nextArtist", "nextAlbum": "nextAlbum", "nextQuality": "nextQuality",
    "nextImage": "nextImage", "nextDuration": "nextDuration", "songFavorite": "songFavorite",
    "albumFavorite": "albumFavorite", "shuffleOn": "shuffle", "repeatMode": "repeat", "bellSeen": "seen",
}
_TEXTS = {"Audio_PrimaryText": "title", "Audio_PrimaryTextLong": "title", "Audio_PrimaryTextLonger": "title",
          "Audio_SecondaryText": "artist"}
_IMAGES = {("Audio_CoverArt", "imageSource"): "cover",
           ("AlexaBackground", "backgroundImageSource"): "background"}


def url():
    """The page's URL, without its page id; '' sends APL pages."""
    return os.environ.get("HTML_PAGE_URL", "").strip()


def want(on):
    """Set per request: whether this request's Echo gets the HTML page."""
    _local.wanted = bool(on)


def wanted():
    return getattr(_local, "wanted", False)


def start_directive(page_id):
    return StartDirective(
        request=StartRequest(uri=url() + page_id, method=StartRequestMethod.GET),
        configuration=Configuration(timeout_in_seconds=TIMEOUT_SECONDS))


def started(page_id, template_item, autoplay):
    """Keep what a new page shows first (from the APL page's template item), until it loads."""
    start = {
        "stream": template_item.get("audioSources", ""), "autoplay": autoplay,
        "title": template_item.get("primaryText", ""), "artist": template_item.get("secondaryText", ""),
        "album": template_item.get("albumText", ""), "titleSize": template_item.get("primaryTextSize", 0),
        "cover": template_item.get("coverImageSource", ""),
        "background": template_item.get("backgroundImageSource", ""),
        "trackOffset": template_item.get("startOffset", 0), "trackDuration": template_item.get("startDuration", 0),
        "songFavorite": template_item.get("startSongFavorite", -1),
        "albumFavorite": template_item.get("startAlbumFavorite", -1),
        "shuffle": template_item.get("startShuffleOn", -1), "repeat": template_item.get("startRepeatMode", -1),
    }
    with _lock:
        if len(_started) > 100:
            _started.clear()
            _presses.clear()
        _started[page_id] = start


def start_state(page_id):
    """The page's start state, or None for a page the skill doesn't hold."""
    with _lock:
        start = _started.get(page_id)
    return start if start is not None and bell.device_of(page_id) else None


def message(commands):
    """The page's message for a reply's APL commands (serialized), or None if nothing for it."""
    out = {}

    def fold(command):
        kind = command.get("type")
        if kind in ("Parallel", "Sequential"):
            for inner in command.get("commands") or []:
                fold(inner)
        elif kind == "ControlMedia" and command.get("command") in ("play", "pause"):
            out["media"] = command["command"]
        elif kind == "PlayMedia":
            source = command.get("source")
            source = source[0] if isinstance(source, list) else source
            out["src"] = source.get("url") if isinstance(source, dict) else source
            out["media"] = "play"
        elif kind == "SetValue":
            component, name, value = command.get("componentId"), command.get("property"), command.get("value")
            if component in (None, "AudioPlayerRoot") and name in _FIELDS:
                out.setdefault("set", {})[_FIELDS[name]] = value
            elif component in _TEXTS and name == "text":
                out.setdefault("set", {})[_TEXTS[component]] = value
            elif (component, name) in _IMAGES:
                out.setdefault("set", {})[_IMAGES[(component, name)]] = value
            elif component == "Busy_Overlay" and value in ("none", 0):
                out["ready"] = True
        # the rest only drives the APL page itself (refresh timers, its slider, fades)

    for command in commands:
        fold(command)
    return out or None


def take_commands(device_id, response):
    """Move the ExecuteCommands of a reply to device_id's HTML page, if it has one."""
    directives = getattr(response, "directives", None) or []
    commands = [d for d in directives if isinstance(d, ExecuteCommandsDirective)]
    page_id = bell.page_of(device_id)
    with _lock:
        if not commands or page_id not in _started:
            return
        update = message(_serializer.serialize([c for d in commands for c in d.commands or []]))
        for q in _queues.get(page_id, []) if update else []:
            q.put(update)
    response.directives = [d for d in directives if d not in commands] or None


def is_open(device_id):
    """True while device_id's page holds its message stream."""
    page_id = bell.page_of(device_id)
    with _lock:
        return page_id in _queues


def drop_audio_player(response):
    """Remove AudioPlayer directives: Amazon rejects them in a reply to an open web app."""
    directives = [d for d in getattr(response, "directives", None) or []
                  if not type(d).__module__.startswith("ask_sdk_model.interfaces.audioplayer")]
    response.directives = directives or None


def stream(page_id):
    """The page's SSE stream of messages."""
    q = queue.Queue()
    with _lock:
        _queues.setdefault(page_id, []).append(q)
    try:
        yield "retry: 1000\n\n"
        quiet_since = time.monotonic()
        while bell.device_of(page_id):
            try:
                update = q.get(timeout=0.2)
            except queue.Empty:
                if time.monotonic() - quiet_since >= KEEPALIVE_SECONDS:
                    quiet_since = time.monotonic()
                    yield ": keepalive\n\n"
                continue
            quiet_since = time.monotonic()
            yield f"data: {json.dumps(update)}\n\n"
    finally:
        with _lock:
            queues = _queues.get(page_id, [])
            if q in queues:
                queues.remove(q)
            if not queues:
                _queues.pop(page_id, None)


def report(skill, page_id, seen, at, playing):
    """The page's position, every second: False for a page the skill doesn't hold.

    It counts as the page's bell (bell.rang); a page behind on news gets
    them as a message.
    """
    if not bell.device_of(page_id):
        return False
    if not bell.rang(page_id, seen, None, at, playing):
        _invoke(skill, page_id, ["Pull", seen, at, playing, page_id])
    return True


# action -> its APL UserEvent; n is the press number
_ACTIONS = {
    "next": lambda n, a: ["Next", n],
    "previous": lambda n, a: ["Previous", n],
    "play_album": lambda n, a: ["PlayAlbum", n],
    "play": lambda n, a: ["Play", int(a["position"]), n],
    "pause": lambda n, a: ["Pause", int(a["position"]), n],
    "seek": lambda n, a: ["Seek", int(a["position"]), n],
    "favorite_song": lambda n, a: ["FavSong", n, int(bool(a["on"]))],
    "favorite_album": lambda n, a: ["FavAlbum", n, int(bool(a["on"]))],
    "shuffle": lambda n, a: ["Shuffle", n, int(bool(a["on"]))],
    "repeat": lambda n, a: ["Repeat", n, int(a["mode"]) % 3],
    "queue_ended": lambda n, a: ["QueueEnded"],
}


def act(skill, page_id, action):
    """A button on the page, e.g. {"action": "seek", "position": 61000}.

    None for a page the skill doesn't hold; raises ValueError for a bad action.
    """
    if not bell.device_of(page_id):
        return None
    with _lock:
        number = _presses.get(page_id, 0) + 1
    try:
        arguments = _ACTIONS[action["action"]](number, action)
    except (KeyError, TypeError, ValueError):
        raise ValueError("bad action")
    with _lock:
        _presses[page_id] = number
    _invoke(skill, page_id, arguments)
    return True


def _invoke(skill, page_id, arguments):
    """Run arguments through the skill as an APL UserEvent from the page's Echo."""
    device_id = bell.device_of(page_id)
    if not device_id:
        return
    app = {"applicationId": skill.skill_id or ""}
    payload = {
        "version": "1.0",
        "session": {"new": False, "sessionId": "html." + page_id, "application": app,
                    "user": {"userId": "html"}},
        "context": {"System": {
            "application": app,
            "user": {"userId": "html"},
            "device": {"deviceId": device_id, "supportedInterfaces": {
                "AudioPlayer": {}, "Alexa.Presentation.APL": {}, "Alexa.Presentation.HTML": {}}},
        }},
        "request": {
            "type": "Alexa.Presentation.APL.UserEvent",
            "requestId": "html." + secrets.token_hex(8),
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "locale": "en-US",
            "token": "playbackToken",
            "arguments": list(arguments),
        },
    }
    envelope = skill.serializer.deserialize(json.dumps(payload), RequestEnvelope)
    skill.invoke(request_envelope=envelope, context=None)
