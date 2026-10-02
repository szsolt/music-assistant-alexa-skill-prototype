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


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVICE_MAPPING_PATH", str(tmp_path / "device_players.json"))
    monkeypatch.setattr(shared_store, "_store", None)
    monkeypatch.setattr(shared_store, "_streams", {})


def test_each_player_keeps_its_own_stream(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    assert shared_store.save({"streamUrl": "https://s/flow/a", "title": "A1", "playerId": "pa"}) == "pa"
    shared_store.save({"streamUrl": "https://s/flow/b", "title": "B1", "playerId": "pb"})
    # MA's next track in a flow: no player id, the player's stream URL
    assert shared_store.save({"streamUrl": "https://s/flow/a", "title": "A2"}) == "pa"
    assert shared_store.for_player("pa")["title"] == "A2"
    assert shared_store.for_player("pb")["title"] == "B1"
    assert shared_store.for_player("pc") is None
    assert shared_store._store["title"] == "A2"                  # the latest of any player


def test_unknown_stream_without_player_is_only_the_latest(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    assert shared_store.save({"streamUrl": "https://s/flow/x", "title": "X"}) is None
    assert shared_store._store["title"] == "X"
    assert shared_store.for_player(None)["title"] == "X"
    assert shared_store.for_player("pa")["title"] == "X"         # nobody knows whose it is
    shared_store.save({"streamUrl": "https://s/flow/b", "title": "B1", "playerId": "pb"})
    assert shared_store.for_player("pa") is None                 # pb's: not for pa


def test_players_streams_survive_a_restart(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    shared_store.save({"streamUrl": "https://s/flow/a", "title": "A1", "playerId": "pa", "version": 3})
    shared_store.save({"streamUrl": "https://s/flow/b", "title": "B1", "playerId": "pb", "version": 4})
    restarted = importlib.reload(shared_store)
    assert restarted.for_player("pa")["title"] == "A1"
    assert restarted._store["title"] == "B1" and restarted._version == 4


def test_old_single_stream_file_still_loads(tmp_path, monkeypatch):
    import json
    _fresh(tmp_path, monkeypatch)
    (tmp_path / "latest_stream.json").write_text(json.dumps(
        {"streamUrl": "https://s/flow/a", "title": "Old", "playerId": None, "version": 9}))
    shared_store._load()
    assert shared_store._store["title"] == "Old" and shared_store._version == 9
