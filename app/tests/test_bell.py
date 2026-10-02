import time

import pytest

from skill import bell
from skill import live_page as lp


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setenv("APL_BELL_URL", "https://bell.example/bell/")
    bell._pages.clear()
    bell._page_of.clear()
    lp._last_event.clear()
    lp._handoff.clear()
    monkeypatch.setattr(bell, "_handlers", {})
    monkeypatch.setattr(bell, "_run", lambda fn, *args: fn(*args))
    yield
    lp.closed("d")


def test_no_bell_without_url_or_device(monkeypatch):
    assert bell.new_page(None) == ""
    monkeypatch.setenv("APL_BELL_URL", "")
    assert bell.new_page("d") == ""
    assert not bell.has_page("d")


def test_bell_answers_ok_only_when_seen():
    page = bell.new_page("d")
    assert bell.has_page("d")
    assert not bell.rang(page, 0)    # the handshake: state 1 is news
    assert bell.rang(page, 1)
    bell.news(["d"])
    assert not bell.rang(page, 1)
    assert bell.rang(page, 2)


def test_bell_of_unknown_page_fails_and_bells_mean_live():
    assert not bell.rang("nope", 5)
    page = bell.new_page("d")
    assert not lp.is_live("d")
    bell.rang(page, 1)
    assert lp.is_live("d")


def test_new_page_replaces_the_old_one():
    old = bell.new_page("d")
    new = bell.new_page("d")
    assert not bell.rang(old, 1)
    assert bell.rang(new, 1)


def test_news_for_all_pages_or_some():
    a, b = bell.new_page("d"), bell.new_page("e")
    bell.news()
    assert not bell.rang(a, 1) and not bell.rang(b, 1)
    bell.news(["e", "unknown"])
    assert bell.rang(a, 2) and not bell.rang(b, 2)


def test_pull_answers_each_state_once_until_resend():
    page = bell.new_page("d")
    assert bell.pull(page, "d", 0, now=100) == (1, [])
    assert bell.pull(page, "d", 0, now=101) is None             # answer still on its way
    assert bell.pull(page, "d", 0, now=100 + bell.RESEND_SECONDS) == (1, [])
    assert bell.pull(page, "d", 1, now=110) is None             # up to date
    bell.news(["d"])
    assert bell.pull(page, "d", 1, now=110.5) == (2, [])        # a new state: no wait


def test_pull_only_from_the_pages_echo():
    page = bell.new_page("d")
    assert bell.pull(page, "e", 0) is None


def test_pull_takes_on_unknown_page_after_restart():
    assert bell.pull("old", "d", 7, now=100) == (8, [])
    assert bell.rang("old", 8)
    # but not when its Echo has a page already
    bell.new_page("e")
    assert bell.pull("other", "e", 0) is None


def test_handoff_pending_until_seen_and_resent_once_lost():
    page = bell.new_page("d")
    now = time.monotonic()
    bell.rang(page, 1)
    assert lp.offer("d", "stream", "u1")
    bell.news(["d"])
    assert bell.pull(page, "d", 1, now=now) == (2, [("stream", "u1")])
    bell.news(["d"])                                             # metadata, before the page confirms
    assert bell.pull(page, "d", 1, now=now + 1) == (3, [])       # not resent yet
    assert bell.pull(page, "d", 1, now=now + 1 + bell.RESEND_SECONDS) == (3, [("stream", "u1")])
    bell.rang(page, 3)                                           # the page has it
    bell.news(["d"])
    assert bell.pull(page, "d", 3, now=now + 10) == (4, [])


def test_new_stream_drops_older_pending():
    page = bell.new_page("d")
    now = time.monotonic()
    bell.rang(page, 1)
    lp.offer("d", "pause")
    bell.news(["d"])
    assert bell.pull(page, "d", 1, now=now) == (2, [("pause", None)])
    lp.offer("d", "stream", "u2")
    bell.news(["d"])
    assert bell.pull(page, "d", 1, now=now + bell.RESEND_SECONDS) == (3, [("stream", "u2")])


def test_resume_is_not_kept_pending():
    page = bell.new_page("d")
    now = time.monotonic()
    bell.rang(page, 1)
    lp.offer("d", "resume")
    bell.news(["d"])
    assert bell.pull(page, "d", 1, now=now) == (2, [("resume", None)])
    bell.news(["d"])
    assert bell.pull(page, "d", 1, now=now + bell.RESEND_SECONDS) == (3, [])


def test_one_handoff_per_answer_the_next_raises_news():
    page = bell.new_page("d")
    now = time.monotonic()
    bell.rang(page, 1)
    lp.offer("d", "stream", "u1")
    lp.offer("d", "pause")                                       # waits behind the stream
    bell.news(["d"])
    assert bell.pull(page, "d", 1, now=now) == (2, [("stream", "u1")])
    assert not bell.rang(page, 2)                                # more news: the pause
    assert bell.pull(page, "d", 2, now=now + 1) == (3, [("pause", None)])


def test_press_timed_on_both_paths(caplog):
    page = bell.new_page("d")
    caplog.set_level("INFO", logger="skill.bell")
    bell.rang(page, 1, "1next", now=100)
    bell.rang(page, 1, "1next", now=101)                          # the next bells repeat it
    bell.pressed("d", "1", "next", now=104.5)
    bell.pressed("d", 2.0, "pause", now=110)                      # Amazon first this time
    bell.rang(page, 1, "2pause", now=110.25)
    bell.rang(page, 1, "0play", now=111)                          # no press yet: ignored
    bell.pressed("e", 3, "play", now=112)                         # no page: ignored
    lines = [r.getMessage() for r in caplog.records if "Press" in r.getMessage()]
    assert lines == [
        "Press 1 (next) came first over the LAN",
        "Press 1 (next) came over Amazon 4500 ms after the LAN",
        "Press 2 (pause) came first over Amazon",
        "Press 2 (pause) came over the LAN 250 ms after Amazon",
    ]


def test_only_the_last_presses_kept():
    page = bell.new_page("d")
    for n in range(1, 20):
        bell.rang(page, 1, f"{n}next", now=n)
    assert sorted(bell._pages[page].presses) == list(range(20 - bell._PRESSES_KEPT, 20))
    bell.pressed("d", 1, "next", now=30)                          # forgotten: no entry again
    assert 1 not in bell._pages[page].presses


def test_lan_press_acts_once_and_amazon_copy_is_ignored():
    calls = []
    bell.set_handlers(press=lambda *a: calls.append(a))
    page = bell.new_page("d")
    bell.rang(page, 1, "1play81234", now=100)
    bell.rang(page, 1, "1play81234", now=101)                    # repeats do nothing
    assert calls == [("d", "play", 81234)]
    assert bell.pressed("d", 1, "play", now=102) is False         # the LAN copy acted
    assert bell.pressed("d", 2, "next", now=103) is True          # Amazon first: it acts
    bell.rang(page, 1, "2next0", now=104)
    assert calls == [("d", "play", 81234)]
    assert bell.pressed("d", None, "next") is True                # no number: act
    assert bell.pressed("e", 5, "next") is True                   # no page: act


def test_undo_goes_out_with_the_next_pull():
    page = bell.new_page("d")
    bell.rang(page, 1)
    bell.undo("d")
    assert not bell.rang(page, 1)
    assert bell.pull(page, "d", 1, now=time.monotonic()) == (2, [("undo", None)])


def _checked_page(ma_state):
    calls = []
    bell.set_handlers(ma_state=lambda device_id, page_id: ma_state,
                      pause_ma=lambda device_id: calls.append(device_id))
    page = bell.new_page("d")
    bell.rang(page, 1)
    bell._pages[page].quiet_until = 0
    bell._pages[page].playing = True
    return page, calls


def test_page_playing_while_ma_paused_gets_paused():
    page, calls = _checked_page(("paused", 1000, 0))
    bell.rang(page, 1, "0", 5000, 1, now=1000)
    assert bell.rang(page, 1, "0", 5000, 1, now=1001)            # too soon for a second check
    bell.rang(page, 1, "0", 5000, 1, now=1000 + bell.CHECK_SECONDS)
    assert not bell.rang(page, 1, "0", 5000, 1, now=1000 + bell.CHECK_SECONDS + 1)
    assert bell.pull(page, "d", 1, now=time.monotonic()) == (2, [("pause", None)])
    assert calls == []


def test_page_paused_while_ma_plays_pauses_ma():
    page, calls = _checked_page(("playing", 1000, 0))
    bell._pages[page].playing = False
    for n in range(bell.MISMATCH_CHECKS):
        bell.rang(page, 1, "0", 5000, 0, now=1000 + n * bell.CHECK_SECONDS)
    assert calls == ["d"]
    assert bell.rang(page, 1)                                     # nothing for the page


def test_no_check_while_quiet_or_ma_idle():
    page, calls = _checked_page(("idle", 0, 0))
    for n in range(4):
        bell.rang(page, 1, "0", 5000, 1, now=1000 + n * bell.CHECK_SECONDS)
    assert bell.rang(page, 1) and calls == []
    page, calls = _checked_page(("paused", 0, 0))
    bell.rang(page, 1, "1next0", 5000, 1, now=1000)               # a press: quiet for a while
    bell.rang(page, 1, "1next0", 5000, 1, now=1000 + bell.CHECK_SECONDS)
    assert bell.rang(page, 1)


def test_position_far_off_ma_is_only_logged(caplog):
    page, calls = _checked_page(("playing", 10_000, 2_000))
    caplog.set_level("INFO", logger="skill.bell")
    bell.rang(page, 1, "0", 17_000, 1, now=1000)                  # 15 s into the track vs 10 s
    assert any("5000 ms ahead of MA" in r.getMessage() for r in caplog.records)
    assert bell.rang(page, 1) and calls == []


def test_logs_when_a_stream_starts_playing(caplog):
    page = bell.new_page("d")
    caplog.set_level("INFO", logger="skill.bell")
    start = bell._pages[page].stream_at
    bell.rang(page, 1, "0", 0, 0, now=start + 0.5)
    bell.rang(page, 1, "0", 0, 1, now=start + 1.25)
    assert any("plays 1250 ms after its stream went out" in r.getMessage() for r in caplog.records)


def test_seek_over_the_lan_carries_the_target():
    calls = []
    bell.set_handlers(press=lambda *a: calls.append(a))
    page = bell.new_page("d")
    bell.rang(page, 1, "7seek120000", now=100)
    assert calls == [("d", "seek", 120000)]
    assert bell.pressed("d", 7, "seek", now=101) is False


def test_page_taken_on_after_restart_does_not_repeat_its_last_press():
    calls = []
    bell.set_handlers(press=lambda *a: calls.append(a))
    bell.pull("old", "d", 7, now=100)                            # the skill restarted
    bell.rang("old", 8, "4seek187792", now=101)                  # pressed before the restart
    bell.rang("old", 8, "4seek187792", now=102)
    assert calls == []
    bell.rang("old", 8, "5next0", now=103)                       # a new press acts
    assert calls == [("d", "next", 0)]


def test_no_position_log_early_in_a_track(caplog):
    page, calls = _checked_page(("playing", 2_000, 0))           # MA is on the next track
    caplog.set_level("INFO", logger="skill.bell")
    bell.rang(page, 1, "0", 200_000, 1, now=1000)                # the page has the old start
    assert not any("ahead of MA" in r.getMessage() for r in caplog.records)
