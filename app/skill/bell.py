# -*- coding: utf-8 -*-
"""The player page's doorbell: the skill tells an open page about news over the LAN.

Without it the page learns everything by asking the skill through Amazon,
each answer scheduling the next question: one lost request or answer ends
the chain, and the screen freezes.

With APL_BELL_URL set, the page instead reloads a tiny image from the skill
every second: <APL_BELL_URL><page>/<seen>/<tick>/<at>/<playing>/<press>.png.
page is the id the skill gave the page, seen the last state number the
page got from the skill (0 at first), tick only makes each URL new, at is
the video position (ms) at that tick, playing 1 or 0, press the page's last
button press (see below). The skill answers with the
image while the page is up to date, and with a 404 when it has news (a
newer state number). The image's onFail then sends a Pull event through
Amazon; the answer carries the news and the new state number, which the
page puts in its next bell.

A lost pull or answer only delays the news: the bell keeps failing until
the page reports the new number. MA's hand-offs (live_page) go out with a
pull answer and stay pending until the page reports that answer's number;
after RESEND_SECONDS without it, the next pull sends them again.

Without APL_BELL_URL pages have no bell and poll as before (MetadataRefresh).

Button presses: the page counts them into the bell URL
(<number><kind><track position>, e.g. 5play81234) and sends the same number
with the button's event through Amazon. The first copy to arrive is acted
on, the other is ignored. Over the LAN that is about a second sooner.

Every CHECK_SECONDS the skill compares an open page with MA. If they
disagree on playing for MISMATCH_CHECKS checks in a row, it pauses the
one that plays: corrections never start audio. A position far off MA's is
only logged for now.
"""

import base64
import logging
import os
import re
import secrets
import threading
import time

from . import live_page

logger = logging.getLogger(__name__)

# A 1x1 transparent PNG: the page draws the bell 4x4, the Show may not load
# an image it doesn't draw.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")

# A pull answer that the page hasn't confirmed by then counts as lost. The
# page bells every second: an answer it got shows in its next bell.
RESEND_SECONDS = 3
# Presses kept per page, to match their two copies.
_PRESSES_KEPT = 8
CHECK_SECONDS = 5      # how often an open page is compared with MA
QUIET_SECONDS = 10     # no check this long after a press, a hand-off or a new page
MISMATCH_CHECKS = 2    # checks in a row that disagree before the skill corrects
OFF_MS = 3000          # a position this far from MA's gets logged
_STARTED_WITHIN = 60   # log how long a stream took to play, if it went out this recently

# Set by the skill (set_handlers): press(device_id, kind, position_ms) acts on
# a LAN press; ma_state(device_id, page_id) gives (MA state, MA elapsed ms,
# page track offset ms) or None; pause_ma(device_id) pauses MA.
_handlers = {}

_lock = threading.Lock()
_pages = {}     # page id -> _Page
_page_of = {}   # device_id -> the id of its latest page


class _Page:
    def __init__(self, device_id, state=1):
        self.device_id = device_id
        self.state = state       # the skill's latest state number for the page
        self.answered = None     # (state, time) of the last pull answer
        self.pending = []        # [state, time sent, (command, value)] not yet seen
        self.presses = {}        # press number -> {"kind", "lan", "amazon"} arrival times
        self.extra = []          # (command, value) to send with the next pull: undo, pause
        now = time.monotonic()
        self.quiet_until = now + QUIET_SECONDS
        self.next_check = 0
        self.mismatches = 0
        self.playing = None      # from the last bell
        self.stream_at = now     # when its latest stream went out (a new page has one)
        self.off_logged_at = 0
        # A page taken on after a restart: its first bell's press is old,
        # handled before the restart. Presses up to old_press aren't acted on over the LAN.
        self.taken_on = False
        self.old_press = 0


def set_handlers(**handlers):
    _handlers.update(handlers)


def _run(fn, *args):
    """Off the bell's request: MA can take a second."""
    threading.Thread(target=fn, args=args, daemon=True).start()


def base_url():
    return os.environ.get("APL_BELL_URL", "").strip()


def new_page(device_id):
    """The id for a page about to be sent to device_id, or '' for a page without a bell."""
    if not base_url() or not device_id:
        return ""
    page_id = secrets.token_hex(8)
    with _lock:
        _pages.pop(_page_of.get(device_id), None)
        _pages[page_id] = _Page(device_id)
        _page_of[device_id] = page_id
    return page_id


def has_page(device_id):
    """True if device_id's latest page has a bell (it needs no polling)."""
    with _lock:
        return device_id in _page_of


def rang(page_id, seen, press=None, at=None, playing=None, now=None):
    """A bell from page_id, which has seen state number seen: True if it is up to date.

    press: the page's last button press, e.g. "3next0" (see pressed()).
    at, playing: the video position (ms) and play state, for the check with MA.
    """
    now = time.monotonic() if now is None else now
    match = re.fullmatch(r"(\d+)([a-z]*)(\d*)", press or "")
    number = int(match.group(1)) if match else 0
    with _lock:
        page = _pages.get(page_id)
        if page is None:
            return False
        _confirm(page, seen)
        if page.taken_on and press is not None:
            page.taken_on, page.old_press = False, number
        old = number <= page.old_press
        device_id, current = page.device_id, seen >= page.state
        started_after = None
        if playing is not None:
            playing = bool(playing)
            if playing != bool(page.playing):
                # a change of its own: give it time to settle before checking
                page.quiet_until = max(page.quiet_until, now + QUIET_SECONDS)
                page.mismatches = 0
                if playing and page.stream_at is not None and 0 <= now - page.stream_at < _STARTED_WITHIN:
                    started_after = now - page.stream_at
                    page.stream_at = None
            page.playing = playing
        check = (playing is not None and at is not None and "ma_state" in _handlers
                 and now >= page.next_check and now >= page.quiet_until)
        if check:
            page.next_check = now + CHECK_SECONDS
    live_page.heard_from(device_id, now)
    if started_after is not None:
        logger.info("Page on %s plays %d ms after its stream went out",
                    device_id[-8:], started_after * 1000)
    if number > 0 and not old:
        kind = match.group(2)
        position = int(match.group(3)) if match.group(3) else None
        if _press_arrived(page, number, kind, "lan", now) and "press" in _handlers:
            _run(_handlers["press"], device_id, kind, position)
    if check:
        _run(_check, page_id, at, playing, now)
    return current


def pressed(device_id, number, kind, now=None):
    """A button event through Amazon carried press number number: True to act on it.

    False when the same press came over the LAN first (the skill acted then).
    """
    try:
        number = int(float(number))
    except (TypeError, ValueError):
        return True
    with _lock:
        page = _pages.get(_page_of.get(device_id))
    if page is None or number <= 0:
        return True
    return _press_arrived(page, number, kind, "amazon", now)


def _press_arrived(page, number, kind, path, now=None):
    """Note a press's copy; True if it is the first. Logs how much later the second came."""
    now = time.monotonic() if now is None else now
    with _lock:
        entry = page.presses.get(number)
        if entry is None:
            if page.presses and number < min(page.presses):
                return False   # an old press, already forgotten
            entry = page.presses[number] = {"kind": kind}
            while len(page.presses) > _PRESSES_KEPT:
                del page.presses[min(page.presses)]
        if path in entry:
            return False   # a repeat (the bell sends the last press every second)
        entry[path] = now
        other = "amazon" if path == "lan" else "lan"
        first = entry.get(other)
        if first is None:
            page.quiet_until = max(page.quiet_until, now + QUIET_SECONDS)
            page.mismatches = 0
    if first is None:
        logger.info("Press %s (%s) came first over %s", number, entry["kind"],
                    "the LAN" if path == "lan" else "Amazon")
    else:
        logger.info("Press %s (%s) came over %s %d ms after %s", number, entry["kind"],
                    "the LAN" if path == "lan" else "Amazon", (now - first) * 1000,
                    "Amazon" if path == "lan" else "the LAN")
    return first is None


def undo(device_id):
    """The skill couldn't do what a LAN press asked: the page undoes it with its next pull."""
    with _lock:
        page_id = _page_of.get(device_id)
    if page_id:
        _send(page_id, ("undo", None))


def _send(page_id, handoff):
    """Send handoff with the page's next pull."""
    with _lock:
        page = _pages.get(page_id)
        if page is None:
            return
        page.extra.append(handoff)
        device_id = page.device_id
    news([device_id])


def _check(page_id, at, playing, now=None):
    """Compare the page with MA (see the module docstring)."""
    with _lock:
        page = _pages.get(page_id)
    if page is None:
        return
    device_id = page.device_id
    state = _handlers["ma_state"](device_id, page_id)
    if not state or state[0] not in ("playing", "paused"):
        return
    ma_state, elapsed_ms, offset_ms = state
    now = time.monotonic() if now is None else now
    fix = off = None
    with _lock:
        if _pages.get(page_id) is not page or now < page.quiet_until:
            return   # something happened during the query
        if playing == (ma_state == "playing"):
            page.mismatches = 0
        else:
            page.mismatches += 1
            if page.mismatches >= MISMATCH_CHECKS:
                page.mismatches = 0
                page.quiet_until = now + QUIET_SECONDS
                fix = "page" if playing else "ma"
        # Not early in a track: MA is on the next one before the page has its start.
        if (playing and ma_state == "playing" and offset_ms is not None and elapsed_ms is not None
                and elapsed_ms >= QUIET_SECONDS * 1000 and now - page.off_logged_at > _STARTED_WITHIN):
            diff = at - offset_ms - elapsed_ms
            if abs(diff) > OFF_MS:
                page.off_logged_at = now
                off = diff
    if off is not None:
        logger.warning("Page on %s is %d ms %s MA", device_id[-8:], abs(off),
                       "ahead of" if off > 0 else "behind")
    if fix == "page":
        logger.warning("Page on %s plays while MA is paused: pausing the page", device_id[-8:])
        _send(page_id, ("pause", None))
    elif fix == "ma":
        logger.warning("Page on %s is paused while MA plays: pausing MA", device_id[-8:])
        _handlers["pause_ma"](device_id)


def news(device_ids=None):
    """Raise the state number of these Echos' pages (None: all pages), so they pull."""
    with _lock:
        if device_ids is None:
            pages = list(_pages.values())
        else:
            pages = [_pages[_page_of[d]] for d in device_ids if d in _page_of]
        for page in pages:
            page.state += 1


def pull(page_id, device_id, seen, now=None):
    """What to answer a Pull from page_id: (state number, hand-offs), or None for nothing.

    Nothing while the page is up to date, or when this state number was
    answered less than RESEND_SECONDS ago (the page pulls on every failed
    bell, while the first answer may still be on its way). The hand-offs
    are the pending ones that seem lost, then a new one from live_page.
    A page the skill doesn't know (the skill restarted) is taken on, if its
    Echo has no other page.
    """
    now = time.monotonic() if now is None else now
    with _lock:
        page = _pages.get(page_id)
        if page is None:
            if not page_id or not device_id or device_id in _page_of:
                return None
            page = _pages[page_id] = _Page(device_id, max(seen, 0) + 1)
            page.taken_on = True
            page.stream_at = None    # its stream went out before the restart
            _page_of[device_id] = page_id
            logger.info("Taking on bell page %s of %s", page_id, device_id[-8:])
        if page.device_id != device_id:
            return None
        _confirm(page, seen)
        if seen >= page.state:
            return None
        if (page.answered and page.answered[0] == page.state
                and now - page.answered[1] < RESEND_SECONDS):
            return None
        page.answered = (page.state, now)
        state = page.state
        handoffs = []
        for entry in page.pending:
            if now - entry[1] >= RESEND_SECONDS:
                entry[0], entry[1] = state, now
                handoffs.append(entry[2])
    if handoffs:
        logger.warning("Page on %s didn't confirm %s: sending it again",
                       device_id[-8:], [h[0] for h in handoffs])
    with _lock:
        extra, page.extra = page.extra, []
    for handed in extra:
        handoffs = _add_pending(page, state, now, handed, handoffs)
    handed = live_page.take(device_id)
    if handed:
        handoffs = _add_pending(page, state, now, handed, handoffs)
    if live_page.waiting(device_id):
        news([device_id])   # one hand-off per answer: the page loads a stream before pausing it
    return state, handoffs


def _add_pending(page, state, now, handed, handoffs):
    """Keep handed until the page has seen state; returns the hand-offs to send."""
    with _lock:
        page.quiet_until = max(page.quiet_until, now + QUIET_SECONDS)
        page.mismatches = 0
        if handed[0] == "stream":
            # A new stream makes the page's older hand-offs moot.
            page.pending.clear()
            handoffs = []
            page.stream_at = now
        if handed[0] != "resume":
            # A resume is done by the skill (MA sends a stream), not by the page.
            page.pending.append([state, now, handed])
    return handoffs + [handed]


def _confirm(page, seen):
    """The page has seen state number seen: its hand-offs up to it are done. Call with _lock held."""
    page.pending = [entry for entry in page.pending if entry[0] > seen]
