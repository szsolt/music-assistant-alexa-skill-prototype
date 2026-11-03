import importlib
import json


def test_latest_stream_survives_a_restart(store):
    store.save({"streamUrl": "https://ma.example/flow/1.mp3", "title": "Esti dal", "version": 7})
    restarted = importlib.reload(store)
    restarted.load()
    assert restarted.for_player(None)["title"] == "Esti dal"
    assert restarted.for_player(None)["version"] == 7


def test_no_kept_stream_is_fine(store):
    assert store.for_player(None) is None


def test_each_player_keeps_its_own_stream(store):
    assert store.save({"streamUrl": "https://ma.example/flow/a", "title": "Altató", "playerId": "Den"}) == "Den"
    store.save({"streamUrl": "https://ma.example/flow/b", "title": "Ringató", "playerId": "Kitchen"})
    # MA's next track in a flow: no player id, the player's stream URL
    assert store.save({"streamUrl": "https://ma.example/flow/a", "title": "Esti dal"}) == "Den"
    assert store.for_player("Den")["title"] == "Esti dal"
    assert store.for_player("Kitchen")["title"] == "Ringató"
    assert store.for_player("Bedroom") is None
    assert store.for_player(None)["title"] == "Esti dal"                 # the latest of any player


def test_unknown_stream_without_player_is_only_the_latest(store):
    assert store.save({"streamUrl": "https://ma.example/flow/x", "title": "Hajnali ének"}) is None
    assert store.for_player(None)["title"] == "Hajnali ének"
    assert store.for_player("Den")["title"] == "Hajnali ének"        # nobody knows whose it is
    store.save({"streamUrl": "https://ma.example/flow/b", "title": "Ringató", "playerId": "Kitchen"})
    assert store.for_player("Den") is None                           # Kitchen's: not for Den


def test_players_streams_survive_a_restart(store):
    store.save({"streamUrl": "https://ma.example/flow/a", "title": "Altató", "playerId": "Den", "version": 3})
    store.save({"streamUrl": "https://ma.example/flow/b", "title": "Ringató", "playerId": "Kitchen", "version": 4})
    restarted = importlib.reload(store)
    restarted.load()
    assert restarted.for_player("Den")["title"] == "Altató"
    assert restarted.for_player(None)["title"] == "Ringató" and restarted.for_player(None)["version"] == 4


def test_old_single_stream_file_still_loads(store, tmp_path):
    (tmp_path / "latest_stream.json").write_text(json.dumps(
        {"streamUrl": "https://ma.example/flow/a", "title": "Dal a hóról", "playerId": None, "version": 9}))
    restarted = importlib.reload(store)
    restarted.load()
    assert restarted.for_player(None)["title"] == "Dal a hóról" and restarted.for_player(None)["version"] == 9


def test_sent_since_counts_streams_to_play_not_flow_metadata(store, clock):
    store.save({"streamUrl": "https://ma.example/flow/a", "title": "Altató"})     # next track in the flow
    assert not store.sent_since("Den", 15)
    store.save({"streamUrl": "https://ma.example/flow/a", "title": "Altató", "playerId": "Den"})
    assert store.sent_since("Den", 15)
    clock.advance(16)
    assert not store.sent_since("Den", 15)


def test_a_stream_without_a_version_gets_the_next_one(store):
    store.save({"streamUrl": "https://ma.example/flow/a", "title": "Altató", "playerId": "Den", "version": 7})
    store.save({"streamUrl": "https://ma.example/flow/a", "title": "Esti dal"})
    assert store.version() == 8 and store.for_player("Den")["version"] == 8
