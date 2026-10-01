# -*- coding: utf-8 -*-
"""Keeping the player page open across MA's operations.

Without this, every next/previous/resume ends with MA's provider making the
Echo hear "play audio" (pause: "pause"). That is a new utterance: it ends
the skill session, and the skill answers with a new page. While a page is
open it asks the skill for a refresh every ~2.5 s, so instead the skill
can tell MA "the page is open, leave it to me" and hand the page the new
stream (or the pause) with its next refresh.

MA must say it can leave the speaking out (canSkipSpeech in its request),
else it speaks anyway and a new page arrives: the open page must not also
switch, or the Show would fetch the stream twice and MA would restart it.
If the page doesn't take a hand-off in time (closed meanwhile), on_missed
runs, to have MA send it the spoken way.
"""

import threading
import time

# A page is open while its refreshes keep coming: one every ~2.5 s.
LIVE_SECONDS = 6
# How long a hand-off waits for the page's next refresh.
HANDOFF_SECONDS = 6
COMMANDS = ("stream", "pause", "resume")

_lock = threading.Lock()
_last_event = {}   # device_id -> monotonic time of the page's last event
# device_id -> hand-offs in the order the page takes them, each
# [command, value, on_missed, Timer]: at most a stream and a pause after it.
_handoff = {}


def heard_from(device_id, now=None):
    """The device's page sent an event: it is open."""
    if device_id:
        with _lock:
            _last_event[device_id] = time.monotonic() if now is None else now


def closed(device_id):
    """The device's page closed or was replaced: no open page, no hand-off."""
    with _lock:
        _last_event.pop(device_id, None)
        entries = _handoff.pop(device_id, [])
    for entry in entries:
        _disarm(entry)


def is_live(device_id, now=None):
    now = time.monotonic() if now is None else now
    with _lock:
        last = _last_event.get(device_id)
    return last is not None and now - last <= LIVE_SECONDS


def offer(device_id, command, value=None, on_missed=None):
    """Hand command to the device's open page. False if it has none.

    The page takes it with its next refresh (take()). A stream replaces
    whatever the page hasn't taken yet. A pause or resume behind an untaken
    stream waits for it: the page loads the new track first, then pauses
    it (a resume adds nothing, the new track plays). Otherwise it replaces
    an untaken one. If the page doesn't take one within HANDOFF_SECONDS of it becoming the
    next, on_missed(device_id, command) of the latest one runs.
    """
    if command not in COMMANDS:
        raise ValueError(command)
    if not device_id or not is_live(device_id):
        return False
    entry = [command, value, on_missed, None]
    with _lock:
        queue = _handoff.get(device_id, [])
        if command != "stream" and queue and queue[0][0] == "stream":
            dropped, kept = queue[1:], queue[:1]
            if command == "pause":
                kept.append(entry)
        else:
            dropped, kept = queue, [entry]
            _arm(device_id, entry)
        _handoff[device_id] = kept
    for old in dropped:
        _disarm(old)
    return True


def take(device_id, command=None):
    """(command, value) handed to the device's page, or None; once, in order.

    With command, only a hand-off of that command is taken.
    """
    with _lock:
        queue = _handoff.get(device_id)
        if not queue or (command is not None and queue[0][0] != command):
            return None
        entry = queue.pop(0)
        if queue:
            _arm(device_id, queue[0])
        else:
            del _handoff[device_id]
    _disarm(entry)
    return entry[0], entry[1]


def _arm(device_id, entry):
    """Start entry's wait for the page; call with _lock held."""
    def missed():
        with _lock:
            queue = _handoff.get(device_id)
            if not queue or queue[0] is not entry:
                return
            queue = _handoff.pop(device_id)
            _last_event.pop(device_id, None)   # not open after all
        for old in queue[1:]:
            _disarm(old)
        # MA's latest command counts: a stream with a pause behind it falls back as a pause.
        latest = queue[-1]
        if latest[2]:
            latest[2](device_id, latest[0])

    entry[3] = threading.Timer(HANDOFF_SECONDS, missed)
    entry[3].daemon = True
    entry[3].start()


def _disarm(entry):
    if entry[3]:
        entry[3].cancel()
