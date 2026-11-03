# -*- coding: utf-8 -*-
"""The HTML player page's channel: its SSE stream, and what goes over it.

The page gets its start state inline, then JSON messages over SSE:
{"track": fields}, {"set": {field: value}}, "src": new stream,
"media": "play"|"pause", "ready": True, and the answers of its page-only
commands (see commands.py) and {"toast": text}. It reports its position
every second (report()), with the number of the last hand-off it got ("seen").

The page follows MA's events, as MA's own app does (see ma/events.py):
each sends it MA's track, as the client's copy has it, if that changed.
MA's hand-offs (see handoff.py) go at once, numbered; one the page hasn't
confirmed goes once more, then MA does it the spoken way.
"""

import dataclasses
import json
import logging
import os
import queue
import threading

from ask_sdk_model.interfaces.alexa.presentation.html import (
    StartDirective, StartRequest, StartRequestMethod, Configuration)

from ... import actions, clock, devices, handoff, view, watch
from ... import echo as echos
from . import render

logger = logging.getLogger(__name__)

# The longest Amazon allows; the page closes when it runs out.
TIMEOUT_SECONDS = 1800
KEEPALIVE_SECONDS = 15
EARLY_CHANGE_MS = 15000     # a track change this much before the shown track's end is logged; crossfades are shorter

_lock = threading.Lock()   # guards the message queues of the pages


def url():
    """The page's URL, without its page id; '' sends APL pages."""
    return os.environ.get("HTML_PAGE_URL", "").strip()


def page(page_id):
    """The open HTML page page_id, or None."""
    found = echos.find_page(page_id)
    return found if found is not None and found.kind == "html" else None


def held(page_id):
    """True for a page the skill holds."""
    return page(page_id) is not None


def _device_page(device_id):
    found = echos.get(device_id).page if device_id else None
    return found if found is not None and found.kind == "html" else None


def has_page(device_id):
    """True if device_id has an HTML page (it needs no polling)."""
    return _device_page(device_id) is not None


def page_of(device_id):
    """The id of device_id's latest page, or None."""
    found = _device_page(device_id)
    return found.page_id if found else None


def start(builder, device_id, start_paused=False):
    """A new page for device_id with its stream, else the latest of any player's; nothing without one."""
    payload = view.stream_of(device_id, any_player=True)
    if not payload:
        logger.warning("No metadata available for the HTML page")
        return
    new = echos.open_page(device_id, "html")
    new.stream = payload["streamUrl"]
    watch.new_page(new.page_id)
    v, fresh = view.now(device_id, new.stream, 0, paused_at=actions.page_paused(device_id))
    if not fresh or v.offset_ms is None or v.offset_ms > 0:
        v = view.of_stream(payload)     # its first report brings MA's track
    new.start = render.start(new.stream, v, autoplay=not start_paused)
    builder.add_directive(StartDirective(
        request=StartRequest(uri=url() + new.page_id, method=StartRequestMethod.GET),
        configuration=Configuration(timeout_in_seconds=TIMEOUT_SECONDS)))


def start_state(page_id):
    """The page's start state, or None if the skill doesn't hold the page.

    A page kept from before a restart loads with what plays now, paused.
    """
    found = page(page_id)
    if found is None:
        return None
    if found.start is None:
        payload = view.stream_of(found.device_id, any_player=True)
        if not payload:
            return None
        found.stream = payload["streamUrl"]
        found.start = render.start(found.stream, view.of_stream(payload), autoplay=False)
    return found.start


def _push(found, update):
    """Send update to the page's streams, or keep it until one opens."""
    with _lock:
        if not found.streams:
            found.waiting.append(update)
        for q in found.streams:
            q.put(update)


def is_open(device_id):
    """True while device_id's page holds its message stream."""
    found = _device_page(device_id)
    with _lock:
        return bool(found and found.streams)


def drop_audio_player(response):
    """Remove AudioPlayer directives: Amazon rejects them in a reply to an open web app."""
    directives = [d for d in getattr(response, "directives", None) or []
                  if not type(d).__module__.startswith("ask_sdk_model.interfaces.audioplayer")]
    response.directives = directives or None


def send(page_id, update):
    """Send update (a page-only command's answer) to the page."""
    found = page(page_id)
    if found is not None:
        _push(found, update)


def stream(page_id):
    """The page's SSE stream of messages."""
    found = page(page_id)
    if found is None:
        return
    handoff.opened(page_id)
    q = queue.Queue()
    with _lock:
        found.streams.append(q)
        for update in found.waiting:
            q.put(update)
        found.waiting = []
    try:
        yield "retry: 1000\n\n"
        quiet_since = clock.monotonic()
        while held(page_id):
            try:
                update = q.get(timeout=0.2)
            except queue.Empty:
                if clock.monotonic() - quiet_since >= KEEPALIVE_SECONDS:
                    quiet_since = clock.monotonic()
                    yield ": keepalive\n\n"
                continue
            quiet_since = clock.monotonic()
            yield f"data: {json.dumps(update)}\n\n"
    finally:
        with _lock:
            found.streams.remove(q)
        # what went over this stream may not have reached the page
        handoff.reconnected(page_id)


def report(page_id, seen, at, playing):
    """The page's position, every second: the answer for the page, or None for a page the skill doesn't hold.

    seen: the number of the last hand-off it got.
    """
    found = page(page_id)
    if found is None:
        return None
    now = clock.monotonic()
    handoff.confirm(page_id, seen)
    deaf = handoff.page_reported(page_id, now)
    watch.report(page_id, at, playing, now)
    with found.lock:
        if found.version is None:
            found.version = seen    # kept from before a restart
        if found.stream is None:
            found.stream = (view.stream_of(found.device_id, any_player=True) or {}).get("streamUrl")
        _resend(found, now)
    if found.track is None:
        sync(found, wait=False)
    return {"reconnect": True} if deaf else {}


def deliver(device_id):
    """Send MA's hand-offs waiting for device_id's page, in order."""
    found = _device_page(device_id)
    if found is None:
        return
    while True:
        with found.deliver_lock:
            handed = handoff.hand_over(device_id)
            if handed is None:
                return
            command, value, on_missed = handed
            watch.handed(found.page_id, command, clock.monotonic())
            logger.info("Open page takes %s from MA", command)
            if command != "resume":
                _hand(found, command, value, on_missed)
                continue
        # resume: MA sends a new stream from the paused position, handed over the same way
        if not actions.run(device_id, actions.Play()).ok:
            _push(found, {"media": "play"})


def _hand(found, command, value, on_missed):
    """Send the page a stream or pause it took; a stream's track follows MA's events."""
    with found.lock:
        now = clock.monotonic()
        if command == "stream":
            found.stream, found.track = value, None
            update = {"src": value, "media": "play", "ready": True, "set": {}}
        else:
            _resend(found, now, unproven=True)
            update = {"media": "pause", "set": {}}
        _number(found, update)
        handoff.sent(found.page_id, found.version, now, (command, value), on_missed)
        _push(found, update)
    if command == "stream":
        sync(found)


def _number(found, update):
    """Give update the page's next hand-off number."""
    found.version = (found.version or 0) + 1
    update["set"]["seen"] = found.version


def _resend(found, now, unproven=False):
    """Send the page the hand-offs it hasn't confirmed once more. Hold found.lock."""
    for sent in handoff.overdue(found.page_id, now, unproven):
        command, value = sent.handed
        logger.warning("Page on %s didn't confirm %s: sending it again", found.device_id[-8:], command)
        if command == "stream":
            update = {"src": value, "media": "play", "ready": True, "set": {}}
        else:
            update = {"media": "pause", "set": {}}
        _number(found, update)
        sent.state = found.version
        _push(found, update)
        if command == "stream" and found.track and found.stream == value:
            _push(found, {"track": found.track})


def _position(found):
    """Where the page's video is now (ms), by its last report; None if unknown."""
    at, playing, when = found.report or (None, None, None)
    if at is None:
        return None
    return at + int((clock.monotonic() - when) * 1000) if playing else at


def sync(found, ask=(), wait=True):
    """Send the page MA's track, as the client's copy has it now, if that changed; also its buttons.

    ask: also "favorites", asked from MA (as on a new track). wait: else
    only if no sync is on its way.
    """
    if not found.news_lock.acquire(blocking=wait):
        return
    try:
        stream = found.stream
        known = (found.track or {}).get("item")
        at = _position(found)
        try:
            v, fresh = view.now(found.device_id, stream, at, known,
                                actions.page_paused(found.device_id))
        except Exception:
            logger.exception("Could not read MA's track for the page")
            return
        if not fresh:
            return
        track = render.fields(v, render.TRACK)
        buttons = render.fields(v, ("modes",))
        if v.item_id and (v.item_id != known or "favorites" in ask):
            player_id = devices.get_player_for_device(found.device_id)
            buttons.update(render.fields(dataclasses.replace(v, favorites=view.favorites(player_id)), ("favorites",)))
        with found.lock:
            if found.stream != stream:
                return      # a new stream came in between: its own sync follows
            if view.keep_offset(found.track, track) != found.track:
                _check_early_change(found, track)
                found.track = track
                logger.info("Page on %s plays %s %r: offset=%s ms duration=%s ms paused=%s last=%s next=%s at=%s ms",
                            found.device_id[-8:], v.item_id, v.title, track.get("trackOffset"), v.duration_ms,
                            v.paused, v.last, (v.next or {}).get("item_id"), at)
                _push(found, {"track": track})
            buttons = {k: b for k, b in buttons.items() if found.buttons.get(k) != b}
            if buttons:
                found.buttons.update(buttons)
                _push(found, {"set": buttons})
    finally:
        found.news_lock.release()


def news(device_ids=None, ask=()):
    """sync these Echos' pages (None: all pages); ask: as sync's."""
    if device_ids is None:
        pages = echos.open_pages("html")
    else:
        pages = [p for p in (_device_page(d) for d in device_ids) if p is not None]
    for found in pages:
        sync(found, ask)


def player_news(device_id, ask):
    """news for the pages of every Echo on device_id's player."""
    player_id = devices.get_player_for_device(device_id)
    news(devices.get_devices_for_player(player_id) if player_id else [device_id], ask)


def pause(device_id):
    """Pause device_id's page."""
    found = _device_page(device_id)
    if found is not None:
        _push(found, {"media": "pause"})


def _check_early_change(found, track):
    """Warn when MA names a new track in the same stream well before the shown one ends: MA's place is off."""
    old = found.track or {}
    if not (old.get("item") and old.get("trackDuration") and "trackOffset" in old and "trackOffset" in track
            and track.get("item") != old["item"]):
        return
    early = old["trackOffset"] + old["trackDuration"] - track["trackOffset"]
    if early > EARLY_CHANGE_MS:
        logger.warning("MA moved the page on %s from %s to %s %d ms before its end (offsets %d -> %d ms)",
                       found.device_id[-8:], old["item"], track.get("item"), early, old["trackOffset"],
                       track["trackOffset"])
