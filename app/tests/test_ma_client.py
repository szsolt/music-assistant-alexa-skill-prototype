import asyncio
import threading
from types import SimpleNamespace as NS

import pytest

from skill import ma_client


@pytest.fixture
def connected(monkeypatch):
    """A client on a loop in its own thread, as the connection runs it."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(ma_client, "_client", NS(name="client"))
    monkeypatch.setattr(ma_client, "_loop", loop)
    ma_client._connected.set()
    yield
    ma_client._connected.clear()
    loop.call_soon_threadsafe(loop.stop)
    thread.join()
    loop.close()


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


def test_a_call_raises_what_ma_raises(connected):
    async def refused(client):
        raise ValueError("no")

    with pytest.raises(ValueError):
        ma_client.call(refused)


def test_a_slow_call_times_out(connected):
    async def hangs(client):
        await asyncio.sleep(5)

    with pytest.raises(ma_client.Unavailable):
        ma_client.call(hangs, timeout=0.1)


def test_no_connection(monkeypatch):
    monkeypatch.setattr(ma_client, "CONNECT_WAIT_S", 0.01)
    with pytest.raises(ma_client.Unavailable):
        ma_client.call(lambda client: None)


def test_active_queue_comes_from_the_live_client(monkeypatch):
    players = {"p": NS(synced_to=None, active_group=None, active_source=None),
               "g": NS(synced_to="p", active_group=None, active_source=None)}
    queues = {"p": "queue of p"}
    client = NS(players=NS(get=players.get), player_queues=NS(get=queues.get))
    assert ma_client.active_queue("p") is None
    monkeypatch.setattr(ma_client, "_client", client)
    assert ma_client.active_queue("p") == "queue of p"
    assert ma_client.active_queue("g") == "queue of p"
