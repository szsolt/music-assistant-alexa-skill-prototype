import importlib

import shared_store


def test_latest_stream_survives_a_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVICE_MAPPING_PATH", str(tmp_path / "device_players.json"))
    shared_store.save({"streamUrl": "https://s.example/flow.mp3", "title": "Rain", "version": 7})
    restarted = importlib.reload(shared_store)
    assert restarted._store["title"] == "Rain"
    assert restarted._version == 7


def test_no_kept_stream_is_fine(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVICE_MAPPING_PATH", str(tmp_path / "device_players.json"))
    monkeypatch.setattr(shared_store, "_store", None)
    shared_store._load()
    assert shared_store._store is None
