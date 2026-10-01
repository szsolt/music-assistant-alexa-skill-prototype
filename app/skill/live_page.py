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
_handoff = {}      # device_id -> (command, value, Timer)


def heard_from(device_id, now=None):
    """The device's page sent an event: it is open."""
    if device_id:
        with _lock:
            _last_event[device_id] = time.monotonic() if now is None else now


def closed(device_id):
    """The device's page closed or was replaced: no open page, no hand-off."""
    with _lock:
        _last_event.pop(device_id, None)
        entry = _handoff.pop(device_id, None)
    if entry:
        entry[2].cancel()


def is_live(device_id, now=None):
    now = time.monotonic() if now is None else now
    with _lock:
        last = _last_event.get(device_id)
    return last is not None and now - last <= LIVE_SECONDS


def offer(device_id, command, value=None, on_missed=None):
    """Hand command to the device's open page. False if it has none.

    The page takes it with its next refresh (take()); a later offer
    replaces an untaken one. on_missed(device_id, command) runs if the
    page doesn't take it within HANDOFF_SECONDS.
    """
    if command not in COMMANDS:
        raise ValueError(command)
    if not device_id or not is_live(device_id):
        return False

    def missed():
        with _lock:
            entry = _handoff.get(device_id)
            if not entry or entry[2] is not timer:
                return
            del _handoff[device_id]
            _last_event.pop(device_id, None)   # not open after all
        if on_missed:
            on_missed(device_id, command)

    timer = threading.Timer(HANDOFF_SECONDS, missed)
    timer.daemon = True
    with _lock:
        old = _handoff.pop(device_id, None)
        _handoff[device_id] = (command, value, timer)
    if old:
        old[2].cancel()
    timer.start()
    return True


def take(device_id):
    """(command, value) handed to the device's page, or None; once."""
    with _lock:
        entry = _handoff.pop(device_id, None)
    if not entry:
        return None
    entry[2].cancel()
    return entry[0], entry[1]
