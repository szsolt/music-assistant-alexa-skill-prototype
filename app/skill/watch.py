# -*- coding: utf-8 -*-
"""The page checks that run on a timer. Their state is on the Echo record (echo.watch) and its page (page.check).

Every CHECK_SECONDS the skill compares an open HTML page with MA. If they
disagree on playing for MISMATCH_CHECKS checks in a row, it pauses the
one that plays: corrections never start audio. A position far off MA's is
only logged for now.

Two more cases pause MA. In both the Echo has gone quiet, but MA would
still show it playing:
- A page says it plays, but its position hasn't moved for STALL_SECONDS.
  Its stream has stalled.
- A page has sent no report for GONE_SECONDS. It has closed.
  The skill forgets it. MA's pause goes without speech (see
  handoff.expect_silent_pause). If MA sent the Echo a new stream since,
  a new page is on its way. The skill looks again GONE_SECONDS after that
  stream, and pauses MA if no page came. It also looks again when MA
  didn't answer, up to UNKNOWN_RETRIES times.
Neither pauses MA if its player has other Echos: one of them may play it.

MA can also end its stream early: it restarts it for a track at a higher
sample rate. MA then waits for the player to report it has stopped, which
the Echo never does. So MA stays on the track it streamed last, at the
start, and the page plays on past that track's end into silence. Once the
page is PAST_END_MS beyond it, the skill asks MA for the next track, as
MA itself would. Once per track, and not if its player has other Echos.

A page just sent to an Echo has a watchdog: if it sends no event in time,
MA sends the stream again. And when Alexa closes a page with its own
error while MA plays, MA sends it again too (reopen_later).
"""

import logging
import threading

from . import actions, clock, devices, echo as echos, handoff, store, view
from .ma import player

logger = logging.getLogger(__name__)

CHECK_SECONDS = 5      # how often an open page is compared with MA
QUIET_SECONDS = 10     # no check this long after a press, a hand-off or a new page
MISMATCH_CHECKS = 2    # checks in a row that disagree before the skill corrects
OFF_MS = 3000          # a position this far from MA's gets logged
_STARTED_WITHIN = 60   # log how long a stream took to play, if it went out this recently
# A playing page's position moves with every report. 20 s rides out a slow
# start or a rebuffer, and still pauses MA before the user wonders why.
STALL_SECONDS = 20
# How far past the end of MA's track a page plays before the skill takes
# MA's stream as ended. MA moves on within seconds when it hasn't.
PAST_END_MS = 20_000
# A page reports every second. 30 s without a report means it closed,
# not that a few requests got lost.
GONE_SECONDS = 30
# How often the skill asks MA again about a gone page when MA didn't answer.
# MA may be down, or the Echo may have no MA player: don't ask for ever.
UNKNOWN_RETRIES = 2

# A page silent this long after it opened gets MA's stream again: a resend brings up a working page.
PAGE_WATCHDOG_S = 10
# An HTML page's loads can lag behind its playing video; a "playing" report stops the watchdog.
HTML_PAGE_WATCHDOG_S = 20
# At most one resend per this long per Echo.
PAGE_RESEND_PAUSE_S = 60
# A page Alexa closed with its own error is reopened by MA's stream, after Alexa's reply.
REOPEN_DELAY_S = 2
# At most one reopen per this long per Echo.
REOPEN_PAUSE_S = 60

_lock = threading.Lock()   # guards echo.watch and page.check; taken before echo's lock
_sweeping = False


class _Check:
    """The check of an open HTML page with MA."""

    def __init__(self, device_id, page_id, now):
        self.device_id, self.page_id = device_id, page_id
        self.quiet_until = now + QUIET_SECONDS
        self.next_check = 0
        self.mismatches = 0
        self.stream_at = now     # when its latest stream went out (a new page has one)
        self.off_logged_at = 0
        self.heard_at = now      # its last report (a new page counts from when it went out)
        self.moved_at = now      # when it last moved, was paused or got a stream
        self.stall_logged = False   # a stall left alone (see _shares_player) was logged
        self.ended_at = None     # the track end at which the skill last asked MA to move on


class _Watch:
    def __init__(self):
        self.timer = None        # the watchdog of its page just sent
        self.resent_at = None    # when the watchdog last had MA resend the stream
        self.reopened_at = None  # when reopen_later last had MA resend the stream
        self.stream_sent = None  # when MA last sent it a new stream for a new page
        self.recheck = None      # (since, tries): no page, look again GONE_SECONDS after since


def ma_state(device_id, page_id):
    """(MA state, MA elapsed ms, the page's track offset and end ms) for the page check, or None.

    No elapsed on the page's first track after a seek: MA's is off there with repeat one.
    """
    player_id = devices.get_player_for_device(device_id)
    state = player.get_queue_state(player_id) if player_id else None
    if not state:
        return None
    page = echos.find_page(page_id)
    track = (page.track if page is not None else None) or {}
    offset, duration = track.get("trackOffset"), track.get("trackDuration")
    # on MA's last track the page ends the queue itself
    end = offset + duration if offset is not None and duration and not track.get("queueEnd") else None
    seeked = offset is not None and offset <= 0 and (store.for_player(player_id) or {}).get("startMs")
    return state[0], None if seeked else state[1], offset, end


def _resume(device_id, elapsed_ms):
    """Have MA send the stream again from where its track is now; True if it did.

    elapsed_ms: MA's place, if the skill has none of its own (view.place_ms).
    """
    position = view.place_ms(device_id)
    return actions.run(device_id, actions.Play(elapsed_ms if position is None else position)).ok


def _of(device_id):
    """device_id's watch, made on first use. Hold _lock."""
    echo = echos.get(device_id)
    if echo.watch is None:
        echo.watch = _Watch()
    return echo.watch


def _html_page(device_id):
    echo = echos.find(device_id) if device_id else None
    page = echo.page if echo is not None else None
    return page if page is not None and page.kind == "html" else None


def _page_check(page_id):
    """The check of HTML page page_id, or None for a page the skill doesn't hold. Hold _lock."""
    return _check_of(echos.find_page(page_id))


def _check_of(page):
    """The check of an HTML page, or None for no page or another kind. Hold _lock."""
    if page is None or page.kind != "html":
        return None
    if page.check is None:
        page.check = _Check(page.device_id, page.page_id, clock.monotonic())   # a new page, or one kept from before a restart
    return page.check


def new_page(page_id):
    """HTML page page_id has just been sent: its checks count from now."""
    with _lock:
        _page_check(page_id)


def report(page_id, at=None, playing=None, now=None):
    """A report from page_id: at, playing are its video position (ms) and play state."""
    now = clock.monotonic() if now is None else now
    with _lock:
        page = echos.find_page(page_id)
        check = _check_of(page)
        if check is None:
            return
        device_id = check.device_id
        last_at, last_playing, _ = page.report or (None, None, None)
        page.reported(at, playing, now)
        started_after = None
        if playing is not None:
            playing = bool(playing)
            if playing != bool(last_playing):
                # a change of its own: give it time to settle before checking
                check.quiet_until = max(check.quiet_until, now + QUIET_SECONDS)
                check.mismatches = 0
                if playing and check.stream_at is not None and 0 <= now - check.stream_at < _STARTED_WITHIN:
                    started_after = now - check.stream_at
                    check.stream_at = None
        check.heard_at = now
        if at is not None:
            if not playing or at != last_at:
                check.moved_at = now
        due = (playing is not None and at is not None and now >= check.next_check and now >= check.quiet_until)
        if due:
            check.next_check = now + CHECK_SECONDS
    if playing:
        page_plays(device_id)
    if started_after is not None:
        logger.info("Page on %s plays %d ms after its stream went out",
                    device_id[-8:], started_after * 1000)
    if due:
        clock.spawn(_check, page_id, at, playing, now)   # off the page's request: MA can take a second


def quiet(device_id, now=None):
    """A button press on device_id's page: no check with MA for a while."""
    now = clock.monotonic() if now is None else now
    page = _html_page(device_id)
    with _lock:
        check = _check_of(page)
        if check is not None:
            check.quiet_until = max(check.quiet_until, now + QUIET_SECONDS)
            check.mismatches = 0


def handed(page_id, kind, now=None):
    """MA's hand-off kind goes to page_id: no check for a while; a stream counts as a move."""
    now = clock.monotonic() if now is None else now
    with _lock:
        check = _page_check(page_id)
        if check is None:
            return
        check.quiet_until = max(check.quiet_until, now + QUIET_SECONDS)
        check.mismatches = 0
        if kind == "stream":
            check.stream_at = now
            check.moved_at = now


def _check(page_id, at, playing, now=None):
    """Compare the page with MA (see the module docstring)."""
    with _lock:
        check = _page_check(page_id)
    if check is None:
        return
    device_id = check.device_id
    state = ma_state(device_id, page_id)
    if not state or state[0] not in ("playing", "paused"):
        return
    ma_now, elapsed_ms, offset_ms, end_ms = state
    now = clock.monotonic() if now is None else now
    fix = off = None
    with _lock:
        if _page_check(page_id) is not check or now < check.quiet_until:
            return   # something happened during the query
        stalled = (playing and ma_now == "playing"
                   and now - check.moved_at >= STALL_SECONDS)
        # MA, still at the start of its track, isn't where the page is.
        ended = (not stalled and playing and ma_now == "playing" and end_ms is not None
                 and at - end_ms - (elapsed_ms or 0) >= PAST_END_MS and check.ended_at != end_ms)
        if ended:
            check.ended_at = end_ms
            check.mismatches = 0
            check.quiet_until = now + QUIET_SECONDS
        elif stalled:
            check.mismatches = 0
            check.quiet_until = now + QUIET_SECONDS
            check.moved_at = now
        elif playing == (ma_now == "playing"):
            check.mismatches = 0
        else:
            check.mismatches += 1
            if check.mismatches >= MISMATCH_CHECKS:
                check.mismatches = 0
                check.quiet_until = now + QUIET_SECONDS
                fix = "page" if playing else "ma"
        # Not early in a track: MA is on the next one before the page has its start.
        if (playing and ma_now == "playing" and offset_ms is not None and elapsed_ms is not None
                and elapsed_ms >= QUIET_SECONDS * 1000 and now - check.off_logged_at > _STARTED_WITHIN):
            diff = at - offset_ms - elapsed_ms
            if abs(diff) > OFF_MS:
                check.off_logged_at = now
                off = diff
    if ended:
        if _shares_player(device_id):
            logger.info("Page on %s plays %d ms past the end of MA's track; "
                        "MA's player has other Echos: leaving MA", device_id[-8:], at - end_ms)
        else:
            logger.warning("Page on %s plays %d ms past the end of MA's track: "
                           "MA's stream ended, asking MA for the next track", device_id[-8:], at - end_ms)
            actions.run(device_id, actions.Next())
        return
    if stalled:
        if not _shares_player(device_id):
            logger.warning("Page on %s stalled at %d ms while MA plays: pausing MA",
                           device_id[-8:], at)
            actions.run(device_id, actions.Pause())
        elif not check.stall_logged:
            check.stall_logged = True
            logger.info("Page on %s stalled at %d ms; MA's player has other Echos: leaving MA",
                        device_id[-8:], at)
        return
    if off is not None:
        logger.warning("Page on %s is %d ms %s MA", device_id[-8:], abs(off),
                       "ahead of" if off > 0 else "behind")
    if fix == "page":
        logger.warning("Page on %s plays while MA is paused: pausing the page", device_id[-8:])
        from .page.html import channel   # here: channel imports this module
        channel.pause(device_id)
    elif fix == "ma":
        logger.warning("Page on %s is paused while MA plays: pausing MA", device_id[-8:])
        actions.run(device_id, actions.Pause())


def stream_sent(device_ids, now=None):
    """MA sent these Echos a new stream that no open page took: a new page is on its way."""
    now = clock.monotonic() if now is None else now
    with _lock:
        for device_id in device_ids:
            _of(device_id).stream_sent = now


def sweep(now=None):
    """Forget the pages that stopped ringing. Pause MA where it still plays to one.

    See the module docstring.
    """
    now = clock.monotonic() if now is None else now
    with _lock:
        checks = [_page_check(page.page_id) for page in echos.open_pages("html")]
        gone = [(check.device_id, check.page_id, check.heard_at, 0) for check in checks
                if check is not None and now - check.heard_at >= GONE_SECONDS]
        for device_id, page_id, _, _ in gone:
            echos.close_page(device_id, page_id)
        due = []
        for echo in echos.every():
            watch = echo.watch
            if watch is not None and watch.recheck is not None and now - watch.recheck[0] >= GONE_SECONDS:
                due.append((echo.device_id, None) + watch.recheck)
                watch.recheck = None
    for device_id, page_id, since, tries in gone + due:
        _pause_if_playing(device_id, page_id, since, tries, now)


def _shares_player(device_id):
    """True if device_id's MA player has other Echos too.

    One of them may play MA: an Echo without a screen has no page that
    shows it. Pausing MA would stop it.
    """
    peers = devices.get_devices_for_player(devices.get_player_for_device(device_id))
    return any(d != device_id for d in peers)


def _pause_if_playing(device_id, page_id, since, tries, now):
    """device_id has had no page from since on. Pause MA if it still plays to it.

    tries: how often MA's state was unknown for it so far.
    """
    state = ma_state(device_id, page_id)
    if state is None:
        if tries < UNKNOWN_RETRIES:
            logger.info("Page on %s is gone; MA's state is unknown: looking again in %d s",
                        device_id[-8:], GONE_SECONDS)
            with _lock:
                watch = _of(device_id)
                if _html_page(device_id) is None and watch.recheck is None:
                    watch.recheck = (now, tries + 1)
        else:
            logger.warning("Page on %s is gone; MA's state is still unknown: leaving MA",
                           device_id[-8:])
        return
    with _lock:
        # Checked after the query: MA may have started something new meanwhile.
        page_open = _html_page(device_id) is not None
        watch = _of(device_id)
        sent = watch.stream_sent if watch.stream_sent is not None else since
        if state[0] == "playing" and not page_open and sent > since:
            # Its new page should come by GONE_SECONDS after the stream.
            watch.recheck = (sent, 0)
            logger.info("Page on %s is gone; MA sent a new stream: waiting for its page",
                        device_id[-8:])
            return
    if state[0] != "playing" or page_open or handoff.is_live(device_id, now):
        logger.info("Page on %s is gone; MA isn't playing to it", device_id[-8:])
    elif _shares_player(device_id):
        logger.info("Page on %s is gone; MA's player has other Echos: leaving MA", device_id[-8:])
    else:
        logger.warning("Page on %s is gone while MA plays: pausing MA", device_id[-8:])
        handoff.expect_silent_pause(device_id, now)
        actions.run(device_id, actions.Pause())


def start_sweeping():
    """Sweep every CHECK_SECONDS, in the background. Once: later calls do nothing."""
    global _sweeping
    with _lock:
        if _sweeping:
            return
        _sweeping = True

    def tick():
        try:
            sweep()
        except Exception:
            logger.exception("Page sweep failed")
        clock.after(CHECK_SECONDS, tick)
    clock.after(CHECK_SECONDS, tick)


def watch_page(device_id):
    """Have MA resend the stream if the page just sent sends no event in time."""
    player_id = devices.get_player_for_device(device_id)
    if not player_id:
        return
    wait = HTML_PAGE_WATCHDOG_S if _html_page(device_id) else PAGE_WATCHDOG_S

    def check():
        with _lock:
            watch = _of(device_id)
            if watch.timer is not timer:
                return
            watch.timer = None
            last = watch.resent_at
            if last is not None and clock.monotonic() - last < PAGE_RESEND_PAUSE_S:
                logger.warning("Player page on %s sent no event in %d s; resent recently, leaving it",
                               player_id, wait)
                return
            watch.resent_at = clock.monotonic()
        state = ma_state(device_id, None)
        if not state or state[0] != "playing":
            return   # MA has nothing to play, or is paused: a resend would play it
        logger.warning("Player page on %s sent no event in %d s: asking MA to resend the stream",
                       player_id, wait)
        if not _resume(device_id, state[1]):
            logger.warning("MA did not resend the stream for %s", player_id)

    with _lock:
        watch = _of(device_id)
        old = watch.timer
        timer = watch.timer = clock.after(wait, check)
    if old:
        old.cancel()


def page_plays(device_id):
    """The page on device_id is alive: stop its watchdog."""
    with _lock:
        echo = echos.find(device_id)
        watch = echo.watch if echo is not None else None
        timer = watch.timer if watch is not None else None
        if timer:
            watch.timer = None
    if timer:
        timer.cancel()


def reopen_later(device_id):
    """Have MA resend the stream to device_id after REOPEN_DELAY_S, if MA still plays to it."""
    player_id = devices.get_player_for_device(device_id)
    if not player_id:
        return

    def reopen():
        # Not the open page: a closed page stays on the books for a while.
        if handoff.is_live(device_id):
            logger.info("Alexa closed the page on %s; a new one has come, leaving it", player_id)
            return
        state = ma_state(device_id, None)
        if not state or state[0] != "playing":
            logger.info("Alexa closed the page on %s; MA isn't playing, leaving it", player_id)
            return
        if any(d != device_id for d in devices.get_devices_for_player(player_id)):
            logger.info("Alexa closed the page on %s; its MA player has other Echos, leaving it", player_id)
            return
        now = clock.monotonic()
        with _lock:
            watch = _of(device_id)
            if watch.reopened_at is not None and now - watch.reopened_at < REOPEN_PAUSE_S:
                logger.warning("Alexa closed the page on %s again; reopened recently, leaving it", player_id)
                return
            watch.reopened_at = now
        logger.warning("Alexa closed the page on %s while MA plays: reopening it", player_id)
        # Where MA is now: a position saved at an older pause would rewind.
        if not _resume(device_id, state[1]):
            logger.warning("MA did not resend the stream for %s", player_id)

    clock.after(REOPEN_DELAY_S, reopen)
