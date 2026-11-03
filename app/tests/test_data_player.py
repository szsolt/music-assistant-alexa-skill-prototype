import shared_store
from skill import data


def test_latest_metadata_is_the_players_own(monkeypatch):
    monkeypatch.setattr(shared_store, "_streams", {
        "pa": {"streamUrl": "https://s/a.flac", "title": "A", "artist": "Art", "album": "Alb",
               "imageUrl": "https://i/a", "playerId": "pa"},
        "pb": {"streamUrl": "https://s/b", "title": "B", "playerId": "pb"}})
    monkeypatch.setattr(shared_store, "_store", shared_store._streams["pb"])
    info = data.get_latest(player_id="pa")["info"]
    assert info["primaryText"] == "A" and info["secondaryText"] == "Art" and info["albumText"] == "Alb"
    assert info["audioSources"] == "https://s/a.mp3" and info["coverImageSource"] == "https://i/a"
    assert data.get_latest(player_id="pb")["info"]["primaryText"] == "B"
