import asyncio
import threading
import time
from types import SimpleNamespace as NS

import pytest

from music_assistant_models.errors import MusicAssistantError

from fake_ma import close
from skill import devices
from skill.ma import client as ma_client
from skill.ma.errors import MAFailed, MAUnavailable


@pytest.fixture
def connected():
    """A client on a loop in its own thread, as the connection runs it."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    ma_client.attach(NS(name="client"), loop)
    yield
    ma_client.attach(None, None)
    loop.call_soon_threadsafe(loop.stop)
    thread.join()
    close(loop)


def test_calls_run_on_the_connection_and_may_overlap(connected):
    async def slow(client, n):
        await asyncio.sleep(0.2)
        return client.name, n

    results = [None] * 5
    threads = [threading.Thread(target=lambda n=n: results.__setitem__(n, ma_client.call(slow, n)))
               for n in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [("client", n) for n in range(5)]


@pytest.mark.parametrize("error", [MusicAssistantError, ValueError])
def test_a_call_that_ma_refuses_or_that_fails_raises_ma_failed(connected, error):
    async def refused(client):
        raise error("no")

    with pytest.raises(MAFailed):
        ma_client.call(refused)


def test_a_slow_call_times_out(connected):
    async def hangs(client):
        await asyncio.sleep(5)

    with pytest.raises(MAUnavailable):
        ma_client.call(hangs, timeout=0.1)


def test_no_connection(monkeypatch):
    monkeypatch.setattr(ma_client, "CONNECT_WAIT_S", 0.01)
    with pytest.raises(MAUnavailable):
        ma_client.call(lambda client: None)


def test_active_queue_comes_from_the_live_client():
    players = {"p": NS(synced_to=None, active_group=None, active_source=None),
               "g": NS(synced_to="p", active_group=None, active_source=None)}
    queues = {"p": "queue of p"}
    client = NS(players=NS(get=players.get), player_queues=NS(get=queues.get))
    assert ma_client.active_queue("p") is None
    ma_client.attach(client, None)
    try:
        assert ma_client.active_queue("p") == "queue of p"
        assert ma_client.active_queue("g") == "queue of p"
    finally:
        ma_client.attach(None, None)


def test_a_call_on_the_alexa_path_gives_up_before_alexa_does(connected, monkeypatch):
    monkeypatch.setattr(ma_client, "REQUEST_TIMEOUT_S", 0.2)

    async def hangs(client):
        await asyncio.sleep(2)

    started = time.monotonic()
    with pytest.raises(MAUnavailable):
        ma_client.call(hangs)
    assert time.monotonic() - started < 1


def test_a_skill_reply_comes_before_alexa_gives_up_when_ma_hangs(ma, echo, monkeypatch):
    monkeypatch.setattr(ma_client, "REQUEST_TIMEOUT_S", 0.2)

    async def hangs(player_id):
        await asyncio.sleep(2)

    devices.set_player_for_device("d", "Den")
    ma.players.next_track = hangs
    started = time.monotonic()
    echo("d", "none").intent("AMAZON.NextIntent")
    assert time.monotonic() - started < 1


@pytest.mark.parametrize("ma_down", ["hung", "disconnected"])
def test_stop_replies_before_alexa_gives_up_when_ma_is_down(ma, echo, monkeypatch, ma_down):
    monkeypatch.setattr(ma_client, "REQUEST_TIMEOUT_S", 0.3)

    async def hangs(*args):
        await asyncio.sleep(2)

    if ma_down == "hung":
        ma.player_queues.get_active_queue = ma.players.stop = hangs
    else:
        ma_client.attach(None, None)
    devices.set_player_for_device("d", "Den")
    started = time.monotonic()
    reply = echo("d", "none").intent("AMAZON.StopIntent")
    assert reply is not None
    assert time.monotonic() - started < 0.5
