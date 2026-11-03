import json
import queue
import threading

import pytest

from skill import actions, handoff, watch
from skill import echo as echos
from skill.page.html import channel
from sse import up_to_mark

_ma_state_of_ma = watch.ma_state   # the fixture stands in for it

_clock = []
_patch = []
_paused, _nexts = [], []   # the Echos whose MA the checks paused, or moved on
_streams = {}              # page id: the messages its SSE stream brought


@pytest.fixture(autouse=True)
def fresh(clock, monkeypatch):
    # A fixed clock: what the code stamps itself is never newer than the tests' times.
    clock.now = 0.0
    _clock[:], _patch[:] = [clock], [monkeypatch]
    _paused.clear(), _nexts.clear(), _streams.clear()

    def run(device_id, action):
        {actions.Pause: _paused, actions.Next: _nexts}[type(action)].append(device_id)
        return actions.OK
    monkeypatch.setattr(watch.actions, "run", run)
    _ma_state(None)
    yield
    for page in echos.open_pages("html"):
        echos.close_page(page.device_id, page.page_id)   # ends the page's SSE streams
    handoff.closed("d")


def _ma_state(state):
    """MA's state as the checks see it: (state, elapsed ms, page track offset ms, page track end ms) or None."""
    _patch[0].setattr(watch, "ma_state", state if callable(state) else lambda device_id, page_id: state)


def _peers(device_ids):
    """The Echos of MA's player."""
    _patch[0].setattr(watch.devices, "get_devices_for_player", lambda player_id: device_ids)


def _new_page(device_id="d"):
    """A new HTML page on device_id, just sent."""
    page_id = echos.open_page(device_id, "html").page_id
    watch.new_page(page_id)
    return page_id


def _report(page, at=None, playing=None, now=None):
    """The page's report at time now."""
    if now is not None:
        _clock[0].now = now
    channel.report(page, 0, at, playing)


def _sent(page):
    """What the skill sent the page since the last call, as its SSE stream brings it."""
    if page not in _streams:
        _streams[page] = got = queue.Queue()

        def read():
            for chunk in channel.stream(page):
                if chunk.startswith("data: "):
                    got.put(json.loads(chunk[len("data: "):]))
        threading.Thread(target=read, daemon=True).start()
    return up_to_mark(page, _streams[page])


def _checked_page(ma_state, playing=1):
    """A new page on Echo d that has reported playing (or paused) at time 0; the Echos whose MA got paused."""
    _ma_state(ma_state)
    _paused.clear()
    page = _new_page("d")
    _report(page, 0, playing, now=0.0)
    return page, _paused


def test_page_playing_while_ma_paused_gets_paused():
    page, calls = _checked_page(("paused", 1000, 0, None))
    _report(page, 5000, 1, now=1000)
    _report(page, 5000, 1, now=1001)
    assert _sent(page) == []                                      # too soon for a second check
    _report(page, 5000, 1, now=1000 + watch.CHECK_SECONDS)
    assert _sent(page) == [{"media": "pause"}]
    assert calls == []


def test_page_paused_while_ma_plays_pauses_ma():
    page, calls = _checked_page(("playing", 1000, 0, None), playing=0)
    for n in range(watch.MISMATCH_CHECKS):
        _report(page, 5000, 0, now=1000 + n * watch.CHECK_SECONDS)
    assert calls == ["d"]
    assert _sent(page) == []                                     # nothing for the page


def test_no_check_while_quiet_or_ma_idle():
    page, calls = _checked_page(("idle", 0, 0, None))
    for n in range(4):
        _report(page, 5000, 1, now=1000 + n * watch.CHECK_SECONDS)
    assert _sent(page) == [] and calls == []
    page, calls = _checked_page(("paused", 0, 0, None))
    watch.quiet("d", now=1000)                                     # a press: quiet for a while
    _report(page, 5000, 1, now=1000)
    _report(page, 5000, 1, now=1000 + watch.CHECK_SECONDS)
    assert _sent(page) == []


def test_position_far_off_ma_is_only_logged(caplog):
    page, calls = _checked_page(("playing", 10_000, 2_000, None))
    caplog.set_level("INFO", logger="skill.watch")
    _report(page, 17_000, 1, now=1000)                  # 15 s into the track vs 10 s
    assert any("5000 ms ahead of MA" in r.getMessage() for r in caplog.records)
    assert _sent(page) == [] and calls == []


def test_logs_when_a_stream_starts_playing(caplog, clock):
    start = clock.now
    page = _new_page("d")
    caplog.set_level("INFO", logger="skill.watch")
    _report(page, 0, 0, now=start + 0.5)
    _report(page, 0, 1, now=start + 1.25)
    assert any("plays 1250 ms after its stream went out" in r.getMessage() for r in caplog.records)


def test_no_position_log_early_in_a_track(caplog):
    page, calls = _checked_page(("playing", 2_000, 0, None))           # MA is on the next track
    caplog.set_level("INFO", logger="skill.watch")
    _report(page, 200_000, 1, now=1000)                # the page has the old start
    assert not any("ahead of MA" in r.getMessage() for r in caplog.records)


def _ended_page(ma_state, playing=1):
    page, calls = _checked_page(ma_state, playing)
    return page, calls, _nexts


def test_page_past_the_end_of_mas_track_asks_ma_for_the_next(caplog):
    # MA ended its stream early: it shows its last track at the start, the page plays on.
    page, calls, nexts = _ended_page(("playing", 0, 5_546_016, 5_661_016))
    caplog.set_level("INFO", logger="skill.watch")
    _report(page, 5_661_016 + watch.PAST_END_MS - 1000, 1, now=1000)
    assert nexts == []
    _report(page, 5_661_016 + watch.PAST_END_MS, 1, now=1000 + watch.CHECK_SECONDS)
    assert nexts == ["d"] and calls == []
    assert any("MA's stream ended" in r.getMessage() for r in caplog.records)
    _report(page, 5_661_016 + 2 * watch.PAST_END_MS, 1, now=1000 + 4 * watch.CHECK_SECONDS)
    assert nexts == ["d"]                                         # once per track


def test_ma_on_the_next_track_is_not_asked_again():
    # MA moved on, the page hasn't heard yet: MA's elapsed covers the gap.
    page, calls, nexts = _ended_page(("playing", 25_000, 0, 200_000))
    _report(page, 226_000, 1, now=1000)
    assert nexts == [] and calls == []


def test_past_the_end_leaves_a_shared_player_alone():
    page, calls, nexts = _ended_page(("playing", 0, 0, 200_000))
    _peers(["d", "e"])
    _report(page, 200_000 + watch.PAST_END_MS, 1, now=1000)
    assert nexts == [] and calls == []


def test_paused_page_past_the_end_is_left_to_the_mismatch_check():
    page, calls, nexts = _ended_page(("playing", 0, 0, 200_000), playing=0)
    _report(page, 200_000 + watch.PAST_END_MS, 0, now=1000)
    assert nexts == []


def test_stalled_page_pauses_ma(caplog):
    page, calls = _checked_page(("playing", 30_000, 0, None))
    caplog.set_level("INFO", logger="skill.watch")
    for n in range(0, watch.STALL_SECONDS, watch.CHECK_SECONDS):
        _report(page, 30_000, 1, now=1000 + n)          # the position stands still
    assert calls == []
    _report(page, 30_000, 1, now=1000 + watch.STALL_SECONDS)
    assert calls == ["d"]
    assert any("stalled at 30000 ms" in r.getMessage() for r in caplog.records)


def test_moving_or_paused_page_is_not_stalled():
    page, calls = _checked_page(("playing", 30_000, 0, None))
    for n in range(0, 3 * watch.STALL_SECONDS, watch.CHECK_SECONDS):
        _report(page, 30_000 + n * 1000, 1, now=1000 + n)
    assert calls == []
    page, calls = _checked_page(("playing", 30_000, 0, None), playing=0)
    for n in range(0, 3 * watch.STALL_SECONDS, watch.CHECK_SECONDS):
        _report(page, 30_000, 0, now=1000 + n)          # paused: MA gets paused once
    calls.clear()
    played = 1000 + 3 * watch.STALL_SECONDS
    _report(page, 30_000, 1, now=played)
    _report(page, 30_000, 1, now=played + watch.QUIET_SECONDS)
    assert calls == []                                            # its pause doesn't count as a stall


def test_new_stream_restarts_the_stall_clock():
    page, calls = _checked_page(("playing", 30_000, 0, None))
    _report(page, 30_000, 1, now=1000)
    assert handoff.offer("d", "stream", "u")
    _clock[0].now = 1000 + watch.STALL_SECONDS - 1
    channel.deliver("d")
    assert [m.get("src") for m in _sent(page)] == ["u"]
    _report(page, 30_000, 1, now=1000 + watch.STALL_SECONDS - 1 + watch.QUIET_SECONDS)
    assert calls == []


def test_page_that_stops_ringing_is_forgotten_and_pauses_ma(caplog):
    page, calls = _checked_page(("playing", 30_000, 0, None))
    caplog.set_level("INFO", logger="skill.watch")
    _report(page, 30_000, 1, now=1000)
    watch.sweep(now=1000 + watch.GONE_SECONDS - 1)
    assert channel.has_page("d") and calls == []
    watch.sweep(now=1000 + watch.GONE_SECONDS)
    assert not channel.has_page("d") and calls == ["d"]
    assert any("gone while MA plays" in r.getMessage() for r in caplog.records)
    assert handoff.takes_silent_pause("d")                             # MA's pause stays silent


def test_gone_page_leaves_a_paused_ma_alone():
    page, calls = _checked_page(("paused", 30_000, 0, None))
    _report(page, 30_000, 0, now=1000)
    watch.sweep(now=1000 + watch.GONE_SECONDS)
    assert not channel.has_page("d") and calls == []


def test_gone_page_leaves_ma_alone_after_a_new_stream():
    # "Alexa, stop", then "open music assistant": MA resumes before the new page comes.
    page, calls = _checked_page(("playing", 30_000, 0, None))
    _report(page, 30_000, 1, now=1000)
    watch.stream_sent(["d"], now=1010)
    watch.sweep(now=1000 + watch.GONE_SECONDS)
    assert not channel.has_page("d") and calls == []
    assert not handoff.takes_silent_pause("d")


def test_gone_page_is_looked_at_again_when_mas_state_is_unknown():
    state = [None]
    page, calls = _checked_page(None)
    _ma_state(lambda device_id, page_id: state[0])
    _report(page, 30_000, 1, now=1000)
    watch.sweep(now=1000 + watch.GONE_SECONDS)
    assert calls == []
    state[0] = ("playing", 30_000, 0, None)
    watch.sweep(now=1000 + 2 * watch.GONE_SECONDS - 1)
    assert calls == []
    watch.sweep(now=1000 + 2 * watch.GONE_SECONDS)
    assert calls == ["d"]


def test_unknown_mas_state_is_asked_only_a_few_times(caplog):
    asked = []
    page, calls = _checked_page(None)
    _report(page, 30_000, 1, now=1000)
    _ma_state(lambda device_id, page_id: asked.append(page_id))
    for n in range(1, 10):
        watch.sweep(now=1000 + n * watch.GONE_SECONDS)
    assert len(asked) == 1 + watch.UNKNOWN_RETRIES and calls == []
    assert any("still unknown" in r.getMessage() for r in caplog.records)


def test_ma_is_paused_if_no_page_comes_for_its_new_stream():
    page, calls = _checked_page(("playing", 30_000, 0, None))
    _report(page, 30_000, 1, now=1000)
    watch.stream_sent(["d"], now=1010)
    watch.sweep(now=1000 + watch.GONE_SECONDS)
    assert calls == []
    watch.sweep(now=1010 + watch.GONE_SECONDS)
    assert calls == ["d"]


def test_ma_is_left_alone_once_the_new_page_comes():
    page, calls = _checked_page(("playing", 30_000, 0, None))
    _report(page, 30_000, 1, now=1000)
    watch.stream_sent(["d"], now=1010)
    watch.sweep(now=1000 + watch.GONE_SECONDS)
    _report(_new_page("d"), now=1000 + watch.GONE_SECONDS)
    watch.sweep(now=1010 + watch.GONE_SECONDS)
    assert calls == []


def test_gone_page_with_live_events_leaves_ma_alone():
    page, calls = _checked_page(("playing", 30_000, 0, None))
    _report(page, 30_000, 1, now=1000)
    handoff.heard_from("d", now=1000 + watch.GONE_SECONDS - 1)          # its MetadataRefresh still comes
    watch.sweep(now=1000 + watch.GONE_SECONDS)
    assert calls == []


def test_a_player_with_other_echos_keeps_playing(caplog):
    # The other Echo may have no screen: nothing shows that it plays.
    page, calls = _checked_page(("playing", 30_000, 0, None))
    caplog.set_level("INFO", logger="skill.watch")
    _peers(["d", "e"])
    for n in range(0, 3 * watch.STALL_SECONDS, watch.CHECK_SECONDS):
        _report(page, 30_000, 1, now=1000 + n)          # stalls, more than once
    assert calls == []
    assert sum("stalled" in r.getMessage() for r in caplog.records) == 1
    watch.sweep(now=1000 + 3 * watch.STALL_SECONDS + watch.GONE_SECONDS)
    assert not channel.has_page("d") and calls == []


def test_a_report_keeps_the_page_it_found(monkeypatch):
    page = _new_page("d")
    find_page = watch.echos.find_page
    found = []
    monkeypatch.setattr(watch.echos, "find_page", lambda page_id: None if found else found.append(1) or find_page(page_id))
    watch.report(page, 5000, True, now=1.0)
    monkeypatch.undo()
    assert watch.echos.find_page(page).report[:2] == (5000, True)



@pytest.mark.parametrize("start_ms, offset, elapsed", [(200_000, -200_000, None), (200_000, 31_000, 5_000),
                                                         (None, 0, 5_000)])
def test_no_ma_place_on_the_first_track_after_a_seek(monkeypatch, start_ms, offset, elapsed):
    page_id = _new_page("d")
    echos.find_page(page_id).track = {"trackOffset": offset, "trackDuration": 231_000}
    monkeypatch.setattr(watch.devices, "get_player_for_device", lambda device_id: "p")
    monkeypatch.setattr(watch.player, "get_queue_state", lambda player_id: ("playing", 5_000))
    monkeypatch.setattr(watch.store, "for_player", lambda player_id: {"startMs": start_ms})
    assert _ma_state_of_ma("d", page_id) == ("playing", elapsed, offset, offset + 231_000)
