from types import SimpleNamespace as NS

import pytest

pytest.importorskip("music_assistant_client")

from skill import ma_events  # noqa: E402


@pytest.fixture
def pages(monkeypatch):
    """Player p has Echos d (page open) and e (no page); timers wait for fire(). Yields the sends."""
    timers = []

    def timer(delay, fn):
        t = NS(daemon=False, cancelled=False, start=lambda: None)
        t.cancel = lambda: setattr(t, "cancelled", True)
        t.fire = lambda: None if t.cancelled else fn()
        timers.append(t)
        return t
    monkeypatch.setattr(ma_events.threading, "Timer", timer)
    monkeypatch.setattr(ma_events.device_mapping, "get_devices_for_player",
                        lambda player_id: {"p": ["d", "e"], "g": ["f"]}.get(player_id, []))
    monkeypatch.setattr(ma_events.bell, "has_page", lambda device_id: device_id in ("d", "f"))
    sent = []
    monkeypatch.setattr(ma_events.bell, "send", lambda device_id, handoff: sent.append((device_id, handoff)))
    ma_events._pending.clear()
    yield NS(sent=sent, fire=lambda: [t.fire() for t in list(timers)])
    ma_events._pending.clear()


def test_a_burst_of_queue_changes_reaches_the_open_page_once(pages):
    for _ in range(3):
        ma_events.queue_changed("p")
    pages.fire()
    assert pages.sent == [("d", ("modes", None))]


def test_other_queues_and_closed_pages_are_left_alone(pages):
    ma_events.queue_changed("other")
    ma_events.queue_changed(None)
    pages.fire()
    assert pages.sent == []


def test_a_replaced_timer_that_fires_anyway_sends_nothing(pages, monkeypatch):
    ma_events.queue_changed("p")
    first = ma_events._pending["p"]
    first.cancel = lambda: None          # too late to stop: it fires all the same
    ma_events.queue_changed("p")
    pages.fire()
    assert pages.sent == [("d", ("modes", None))]


def test_a_player_synced_to_the_queue_hears_of_it(pages):
    ma_events.queue_changed("leader", ["g"])
    pages.fire()
    assert pages.sent == [("f", ("modes", None))]


def test_players_of_a_queue():
    players = [NS(player_id="g", synced_to="leader", active_group=None, active_source=None),
               NS(player_id="h", synced_to=None, active_group=None, active_source="h"),
               NS(player_id="i", synced_to=None, active_group="leader", active_source=None)]
    client = NS(players=NS(players=players))
    assert ma_events._players_of(client, "leader") == ["g", "i"]
