# -*- coding: utf-8 -*-
"""MA's hand-offs to an Echo's open page: a new stream, a pause or a resume.

Without them, every next/previous/resume ends with MA's provider making the
Echo hear "play audio" (pause: "pause"). That is a new utterance: it ends
the skill session, and the skill answers with a new page. While a page is
open it reports to the skill (an APL page asks for a refresh every ~2.5 s,
the HTML page reports every second), so instead the skill can tell MA "the
page is open, leave it to me" and hand the page the new stream (or the
pause) with its next refresh.

MA must say it can leave the speaking out (canSkipSpeech in its request),
else it speaks anyway and a new page arrives: the open page must not also
switch, or the Show would fetch the stream twice and MA would restart it.
If the page doesn't take a hand-off in time, or closes before it does,
on_missed runs, to have MA send it the spoken way.

An HTML page gets a hand-off over its SSE stream at once, numbered (see
page/html/channel.py). The skill keeps it until the page reports that
number. One the page hasn't confirmed RESEND_SECONDS later goes once more.
One it hasn't confirmed HANDOFF_SECONDS after it went out is missed:
on_missed runs. After an SSE reconnect, those the page hasn't confirmed go
again before a newer number can confirm them.

The state lives on the Echo record (echo.handoffs) and its HTML page (page.handed).
"""

import threading

from . import clock, echo as echos

# A page is open while its refreshes keep coming: one every ~2.5 s.
LIVE_SECONDS = 6
# How long a hand-off waits for the page's next refresh.
HANDOFF_SECONDS = 6
COMMANDS = ("stream", "pause", "resume")
# How long the mark set by expect_silent_pause lasts. MA's pause comes back
# through /control within a second or two. 10 s leaves room for a slow MA,
# without silencing a later pause asked for by the user.
SILENT_PAUSE_SECONDS = 10
# A sent hand-off that the page hasn't confirmed by then counts as lost. The
# page reports every second: an answer it got shows in its next report.
RESEND_SECONDS = 3

_lock = threading.Lock()   # guards echo.handoffs and page.handed; taken before echo's lock


class _Handoffs:
    def __init__(self):
        self.heard_at = None       # monotonic time of the page's last event
        self.claimed = False       # the next stream goes to a skill reply (a voice command), not the page
        # The hand-offs in the order the page takes them, each [command, value,
        # on_missed, Timer]: at most a stream and a pause after it.
        self.waiting = []
        self.silent_until = None   # until when MA's next pause needs no speech


class _Sent:
    """A hand-off sent to an HTML page, until the page reports its number."""

    def __init__(self, state, at, handed, on_missed):
        self.state, self.at, self.handed, self.on_missed = state, at, handed, on_missed
        self.unproven = False      # sent before the page's SSE stream reconnected
        self.resent = False
        self.timer = None


def _of(device_id):
    """device_id's hand-offs, made on first use. Hold _lock."""
    echo = echos.get(device_id)
    if echo.handoffs is None:
        echo.handoffs = _Handoffs()
    return echo.handoffs


def _seen(device_id):
    """device_id's hand-offs, or empty ones it doesn't keep if it has none. Hold _lock."""
    echo = echos.find(device_id) if device_id else None
    return echo.handoffs if echo is not None and echo.handoffs is not None else _Handoffs()


def heard_from(device_id, now=None):
    """The device's page sent an event: it is open."""
    if device_id:
        with _lock:
            _of(device_id).heard_at = clock.monotonic() if now is None else now


def page_reported(page_id, now=None):
    """The HTML page reported: it is open. True if it is deaf (see _unconfirmed)."""
    with _lock:
        page = echos.find_page(page_id)
        if page is not None:
            _of(page.device_id).heard_at = clock.monotonic() if now is None else now
            return page.deaf
    return False


def opened(page_id):
    """The HTML page's SSE stream opened: it takes hand-offs again."""
    with _lock:
        page = echos.find_page(page_id)
        if page is not None:
            page.deaf = False


def closed(device_id, fall_back=False):
    """The device's page closed or was replaced: no open page, no hand-off.

    fall_back: run on_missed for a hand-off the page didn't take. Not when
    a new page replaces it (it has the latest stream), nor on a Stop (the
    skill syncs the stop to MA). True if an on_missed ran.
    """
    with _lock:
        handoffs = _seen(device_id)
        handoffs.heard_at, handoffs.claimed = None, False
        entries, handoffs.waiting = handoffs.waiting, []
    return _drop(device_id, entries, fall_back)


def claim(device_id):
    """The device's next stream is for a skill reply: the open page mustn't take it.

    A voice command's reply brings its own page; if the old page took the
    stream, the reply would only speak and leave that page stuck.
    """
    if device_id:
        with _lock:
            _of(device_id).claimed = True


def expect_silent_pause(device_id, now=None):
    """The skill is about to pause MA for a device whose page has closed.

    MA's pause then needs no spoken "pause". The Echo plays nothing of
    MA's, and a spoken pause would stop whatever else it plays. This only
    works when MA can leave its speech out (canSkipSpeech).
    """
    now = clock.monotonic() if now is None else now
    with _lock:
        _of(device_id).silent_until = now + SILENT_PAUSE_SECONDS


def takes_silent_pause(device_id, now=None):
    """True, once, if MA's pause for the device needs no speech (see expect_silent_pause)."""
    now = clock.monotonic() if now is None else now
    with _lock:
        handoffs = _seen(device_id)
        until, handoffs.silent_until = handoffs.silent_until, None
    return until is not None and now <= until


def is_live(device_id, now=None):
    now = clock.monotonic() if now is None else now
    with _lock:
        last = _seen(device_id).heard_at
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
        echo = echos.find(device_id)
        if echo is not None and echo.page is not None and echo.page.deaf:
            return False
        handoffs = _of(device_id)
        queue = handoffs.waiting
        if command != "stream" and queue and queue[0][0] == "stream":
            dropped, kept = queue[1:], queue[:1]
            if command == "pause":
                kept.append(entry)
        else:
            dropped, kept = queue, [entry]
            _arm(device_id, handoffs, entry)
        handoffs.waiting = kept
    for old in dropped:
        _disarm(old)
    return True


def take(device_id, command=None, for_reply=False):
    """(command, value) handed to the device's page, or None; once, in order.

    With command, only a hand-off of that command is taken. A claimed
    stream goes only to the skill reply (for_reply).
    """
    entry = _pop(device_id, command, for_reply)
    return entry and (entry[0], entry[1])


def hand_over(device_id):
    """(command, value, on_missed) for the device's HTML page, or None; once, in order."""
    entry = _pop(device_id)
    return entry and (entry[0], entry[1], entry[2])


def _pop(device_id, command=None, for_reply=False):
    with _lock:
        handoffs = _seen(device_id)
        queue = handoffs.waiting
        if not queue or (command is not None and queue[0][0] != command):
            return None
        if handoffs.claimed and queue[0][0] == "stream":
            if not for_reply:
                return None
            handoffs.claimed = False
        entry = queue.pop(0)
        if queue:
            _arm(device_id, handoffs, queue[0])
    _disarm(entry)
    return entry


def _arm(device_id, handoffs, entry):
    """Start entry's wait for the page. Hold _lock."""
    def missed():
        with _lock:
            if not handoffs.waiting or handoffs.waiting[0] is not entry:
                return
            queue, handoffs.waiting = handoffs.waiting, []
            handoffs.heard_at = None   # not open after all
        _drop(device_id, queue, fall_back=True)

    entry[3] = clock.after(HANDOFF_SECONDS, missed)


def _drop(device_id, queue, fall_back):
    """Drop queue's hand-offs; True if one fell back."""
    for entry in queue:
        _disarm(entry)
    # MA's latest command counts: a stream with a pause behind it falls back as a pause.
    if fall_back and queue and queue[-1][2]:
        queue[-1][2](device_id, queue[-1][0])
        return True
    return False


def _disarm(entry):
    if entry[3]:
        entry[3].cancel()


def _handed(page_id):
    """The hand-offs sent to HTML page page_id ([] for a page the skill doesn't hold). Hold _lock."""
    page = echos.find_page(page_id)
    return page.handed if page is not None else []


def sent(page_id, state, now, handed, on_missed=None):
    """handed went out to the HTML page as number state: keep it until the page has seen that."""
    entry = _Sent(state, now, handed, on_missed)
    with _lock:
        kept = _handed(page_id)
        dropped = list(kept) if handed[0] == "stream" else []   # a new stream makes the older ones moot
        kept[:] = [s for s in kept if s not in dropped]
        if handed[0] != "resume":
            # A resume is done by the skill (MA sends a stream), not by the page.
            kept.append(entry)
            entry.timer = clock.after(HANDOFF_SECONDS, _unconfirmed, page_id, entry)
    _cancel(dropped)


def _unconfirmed(page_id, entry):
    """The page hasn't confirmed entry in time: MA does it again, and the page is deaf.

    A deaf page stays open, but MA does its changes the spoken way until
    its SSE stream opens again. Its next report asks it to reopen it.
    """
    with _lock:
        page = echos.find_page(page_id)
        if page is None or entry not in page.handed:
            return
        missed, page.handed[:] = list(page.handed), []
        page.deaf = True
        device_id = page.device_id
        handoffs = _seen(device_id)
        waiting, handoffs.waiting = handoffs.waiting, []
    _cancel(missed)
    # MA's latest command counts: those still waiting are newer than those sent
    if not _drop(device_id, waiting, fall_back=True) and missed[-1].on_missed:
        missed[-1].on_missed(device_id, missed[-1].handed[0])


def _cancel(entries):
    for s in entries:
        if s.timer:
            s.timer.cancel()


def confirm(page_id, seen):
    """The HTML page has seen number seen: the hand-offs sent up to it are done."""
    with _lock:
        kept = _handed(page_id)
        done = [s for s in kept if s.state <= seen]
        kept[:] = [s for s in kept if s.state > seen]
    _cancel(done)


def reconnected(page_id):
    """The HTML page's SSE stream closed: what went out over it may be lost."""
    with _lock:
        for s in _handed(page_id):
            s.unproven = True


def overdue(page_id, now, unproven=False):
    """The page's hand-offs to send once more: unconfirmed RESEND_SECONDS on.

    unproven: also those sent before an SSE stream closed, now. The caller
    numbers the ones it sends again (their state).
    """
    with _lock:
        again = [s for s in _handed(page_id) if not s.resent and (
            now - s.at >= RESEND_SECONDS or (unproven and s.unproven))]
        for s in again:
            s.at, s.unproven, s.resent = now, False, True
    return again
