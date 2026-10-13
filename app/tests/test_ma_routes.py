import pytest

flask = pytest.importorskip("flask")

from music_assistant_api import ma_routes  # noqa: E402
from skill import bell  # noqa: E402
from skill import live_page as lp  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    lp._last_event.clear()
    lp._handoff.clear()
    lp._silent_pause.clear()
    bell._stream_sent.clear()
    monkeypatch.setattr(ma_routes.device_mapping, "get_devices_for_player", lambda player_id: ["d"])
    monkeypatch.setattr(ma_routes.device_mapping, "wait_for_pairing", lambda player_id: None)
    monkeypatch.setattr(ma_routes.shared_store, "save", lambda store: store.get("playerId") or "p")
    app = flask.Flask(__name__)
    bp = flask.Blueprint("ma", __name__)
    ma_routes.register_routes(bp)
    app.register_blueprint(bp)
    yield app.test_client()
    lp.closed("d")


def _control(client, command="pause"):
    return client.post("/control", json={"playerId": "p", "command": command,
                                          "canSkipSpeech": True}).get_json()["pageLive"]


def test_pause_after_a_closed_page_needs_no_speech_once(client):
    lp.expect_silent_pause("d")
    assert _control(client)
    assert not _control(client)


def test_silent_pause_is_only_for_a_pause(client):
    lp.expect_silent_pause("d")
    assert not _control(client, "resume")
    assert _control(client)


def test_an_open_page_takes_the_pause_before_the_silent_one(client):
    lp.expect_silent_pause("d")
    lp.heard_from("d")
    assert _control(client)
    assert lp.take("d") == ("pause", None)


def test_a_stream_no_open_page_takes_is_noted_for_the_sweep(client):
    client.post("/push-url", json={"streamUrl": "http://ma/flow/1", "playerId": "p",
                                   "canSkipSpeech": True})
    assert "d" in bell._stream_sent


def test_a_stream_the_open_page_takes_is_not_noted(client):
    lp.heard_from("d")
    client.post("/push-url", json={"streamUrl": "http://ma/flow/1", "playerId": "p",
                                   "canSkipSpeech": True})
    assert "d" not in bell._stream_sent
