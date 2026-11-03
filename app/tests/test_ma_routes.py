import pytest

flask = pytest.importorskip("flask")

from skill import devices, store  # noqa: E402
from skill import echo  # noqa: E402
from skill import handoff  # noqa: E402


@pytest.fixture
def client(app, monkeypatch):
    monkeypatch.setattr(devices, "get_devices_for_player", lambda player_id: ["d"])
    monkeypatch.setattr(devices, "wait_for_pairing", lambda player_id: None)
    monkeypatch.setattr(store, "save", lambda stream: stream.get("playerId") or "p")
    yield app.test_client()
    handoff.closed("d")


def _control(client, command="pause"):
    return client.post("/ma/control", json={"playerId": "p", "command": command,
                                          "canSkipSpeech": True}).get_json()["pageLive"]


def test_silent_pause_is_only_for_a_pause(client):
    handoff.expect_silent_pause("d")
    assert not _control(client, "resume")
    assert _control(client)


def test_an_open_page_takes_the_pause_before_the_silent_one(client):
    handoff.expect_silent_pause("d")
    handoff.heard_from("d")
    assert _control(client)
    assert handoff.take("d") == ("pause", None)


def test_a_stream_no_open_page_takes_is_noted_for_the_sweep(client):
    client.post("/ma/push-url", json={"streamUrl": "http://ma/flow/1", "playerId": "p",
                                   "canSkipSpeech": True})
    assert echo.find("d").watch.stream_sent is not None


def test_a_stream_the_open_page_takes_is_not_noted(client):
    handoff.heard_from("d")
    client.post("/ma/push-url", json={"streamUrl": "http://ma/flow/1", "playerId": "p",
                                   "canSkipSpeech": True})
    assert echo.find("d") is None or echo.find("d").watch is None
