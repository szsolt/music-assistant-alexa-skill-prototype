import threading

from skill import live_page as lp


def setup_function():
    lp._last_event.clear()
    lp._handoff.clear()
    lp._claimed.clear()


def test_live_only_after_recent_event():
    assert not lp.is_live("d", now=100)
    lp.heard_from("d", now=100)
    assert lp.is_live("d", now=100 + lp.LIVE_SECONDS)
    assert not lp.is_live("d", now=101 + lp.LIVE_SECONDS)


def test_offer_needs_open_page_and_is_taken_once():
    assert not lp.offer("d", "stream", "u1")
    lp.heard_from("d")
    assert lp.offer("d", "stream", "u1")
    assert lp.offer("d", "stream", "u2")      # replaces the untaken one
    assert lp.take("d") == ("stream", "u2")
    assert lp.take("d") is None


def test_take_of_one_command_leaves_others():
    lp.heard_from("d")
    lp.offer("d", "stream", "u1")
    assert lp.take("d", "pause") is None
    assert lp.take("d", "stream") == ("stream", "u1")
    lp.closed("d")


def test_closed_drops_page_and_handoff():
    lp.heard_from("d")
    lp.offer("d", "pause")
    lp.closed("d")
    assert lp.take("d") is None
    assert not lp.is_live("d")


def test_missed_handoff_calls_back_and_page_counts_closed(monkeypatch):
    monkeypatch.setattr(lp, "HANDOFF_SECONDS", 0.05)
    done = threading.Event()
    got = []
    lp.heard_from("d")
    lp.offer("d", "stream", "u", on_missed=lambda d, c: (got.append((d, c)), done.set()))
    assert done.wait(1)
    assert got == [("d", "stream")]
    assert not lp.is_live("d")
    assert lp.take("d") is None


def test_pause_after_untaken_stream_waits_for_it():
    lp.heard_from("d")
    lp.offer("d", "stream", "u1")
    assert lp.offer("d", "pause")
    assert lp.take("d") == ("stream", "u1")
    assert lp.take("d") == ("pause", None)
    assert lp.take("d") is None


def test_resume_after_untaken_stream_adds_nothing():
    lp.heard_from("d")
    lp.offer("d", "stream", "u1")
    lp.offer("d", "pause")
    assert lp.offer("d", "resume")
    assert lp.take("d") == ("stream", "u1")
    assert lp.take("d") is None


def test_stream_replaces_everything_untaken():
    lp.heard_from("d")
    lp.offer("d", "stream", "u1")
    lp.offer("d", "pause")
    lp.offer("d", "stream", "u2")
    assert lp.take("d") == ("stream", "u2")
    assert lp.take("d") is None


def test_pause_behind_stream_is_not_taken_by_command_first():
    lp.heard_from("d")
    lp.offer("d", "stream", "u1")
    lp.offer("d", "pause")
    assert lp.take("d", "pause") is None
    assert lp.take("d") == ("stream", "u1")
    assert lp.take("d", "pause") == ("pause", None)


def test_missed_stream_with_pause_behind_falls_back_as_pause(monkeypatch):
    monkeypatch.setattr(lp, "HANDOFF_SECONDS", 0.05)
    done = threading.Event()
    got = []
    lp.heard_from("d")
    callback = lambda d, c: (got.append((d, c)), done.set())
    lp.offer("d", "stream", "u", on_missed=callback)
    lp.offer("d", "pause", on_missed=callback)
    assert done.wait(1)
    assert got == [("d", "pause")]
    assert lp.take("d") is None


def test_pause_wait_starts_when_stream_is_taken(monkeypatch):
    monkeypatch.setattr(lp, "HANDOFF_SECONDS", 0.2)
    got = []
    lp.heard_from("d")
    lp.offer("d", "stream", "u", on_missed=lambda d, c: got.append(c))
    lp.offer("d", "pause", on_missed=lambda d, c: got.append(c))
    threading.Event().wait(0.15)
    assert lp.take("d") == ("stream", "u")
    threading.Event().wait(0.1)          # 0.25 s after the offer, 0.1 s after the take
    assert lp.take("d") == ("pause", None)
    assert got == []


def test_closed_falls_back_for_untaken_handoff_only_when_asked():
    got = []
    lp.heard_from("d")
    lp.offer("d", "stream", "u", on_missed=lambda d, c: got.append((d, c)))
    lp.closed("d")
    assert got == []
    lp.heard_from("d")
    lp.offer("d", "stream", "u", on_missed=lambda d, c: got.append((d, c)))
    lp.offer("d", "pause", on_missed=lambda d, c: got.append((d, c)))
    lp.closed("d", fall_back=True)
    assert got == [("d", "pause")]
    assert lp.take("d") is None


def test_a_claimed_stream_goes_only_to_the_reply():
    lp.heard_from("d")
    lp.claim("d")
    assert lp.offer("d", "stream", "url")
    assert lp.take("d") is None                         # the old page's refresh
    assert lp.take("d", "stream", for_reply=True) == ("stream", "url")
    lp.heard_from("d")
    assert lp.offer("d", "stream", "url2")
    assert lp.take("d") == ("stream", "url2")           # claim used up
    lp.closed("d")
