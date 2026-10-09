import threading

import pytest

from skill import sleep_timer as st


@pytest.mark.parametrize("value, seconds", [
    ("PT10S", 10), ("PT30M", 1800), ("PT1H30M", 5400), ("PT1H0M5S", 3605), ("P1D", 86400)])
def test_durations_alexa_sends(value, seconds):
    assert st.parse_duration(value) == seconds


@pytest.mark.parametrize("value", [None, "", "?", "PT0S", "P2D", "PT10M?", "P1W"])
def test_durations_that_cant_be_used(value):
    assert st.parse_duration(value) is None


@pytest.mark.parametrize("seconds, words", [
    (1, "1 second"), (1800, "30 minutes"), (5400, "1 hour and 30 minutes"),
    (3661, "1 hour, 1 minute and 1 second")])
def test_spoken(seconds, words):
    assert st.spoken(seconds) == words


def test_timer_runs_out_once():
    due = threading.Event()
    st.start("d", 0.01, lambda device_id: due.set())
    assert due.wait(2)
    assert not st.cancel("d")


def test_new_timer_replaces_the_old_one():
    fired = []
    st.start("d", 0.05, lambda device_id: fired.append("old"))
    done = threading.Event()
    st.start("d", 0.1, lambda device_id: (fired.append("new"), done.set()))
    assert done.wait(2)
    assert fired == ["new"]


def test_cancelled_timer_does_nothing():
    st.start("d", 0.05, lambda device_id: pytest.fail("paused"))
    assert st.cancel("d")
    assert not st.cancel("d")
