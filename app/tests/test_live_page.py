import threading

from skill import live_page as lp


def setup_function():
    lp._last_event.clear()
    lp._handoff.clear()


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
