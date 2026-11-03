
import pytest

pytest.importorskip("ask_sdk_model")

from skill import devices, view  # noqa: E402
from skill.ma import client as ma_client  # noqa: E402

COVER = "http://192.0.2.10:8095/imageproxy?path=cover%2F7.jpg&size=512"


@pytest.fixture
def players(store):
    """Echo d plays MA player Den, Echo k plays Kitchen; both have a stream."""
    devices.set_player_for_device("d", "Den")
    devices.set_player_for_device("k", "Kitchen")
    store.save({"streamUrl": "http://192.0.2.10:8097/flow/den/1.flac", "title": "Altató", "artist": "Kaláka",
                "album": "Az én szívem", "imageUrl": COVER, "playerId": "Den"})
    store.save({"streamUrl": "http://192.0.2.10:8097/flow/kitchen/1.mp3", "title": "Ringató",
                "playerId": "Kitchen"})


def test_each_echo_sees_its_players_stream(players):
    den = view.build("d")
    assert (den.title, den.artist, den.album, den.cover) == ("Altató", "Kaláka", "Az én szívem", COVER)
    assert view.build("k").title == "Ringató"


def test_alexa_plays_the_flac_stream_as_mp3(players, store):
    assert view.stream_url("d") == "http://192.0.2.10:8097/flow/den/1.mp3"
    assert view.stream_url("k") == "http://192.0.2.10:8097/flow/kitchen/1.mp3"
    store.save({"streamUrl": "http://ma/flow/1.FLAC?fmt=1", "playerId": "Den"})
    assert view.stream_url("d") == "http://ma/flow/1.mp3?fmt=1"
    store.save({"streamUrl": "http://ma/flac/1.mp3", "playerId": "Den"})
    assert view.stream_url("d") == "http://ma/flac/1.mp3"


def test_a_player_without_a_stream_gets_the_latest_only_when_asked(store):
    devices.set_player_for_device("k", "Kitchen")
    store.save({"streamUrl": "http://192.0.2.10:8097/flow/den/1.mp3", "title": "Altató", "playerId": "Den"})
    assert view.build("k") is None
    assert view.build("k", any_player=True).title == "Altató"


def test_ma_urls_move_to_the_public_host(players, ma, monkeypatch):
    monkeypatch.setenv("MA_HOSTNAME", "ma.example.com")
    public = "https://ma.example.com/imageproxy?path=cover%2F7.jpg&size=512"
    assert view.public_url(COVER) == public
    assert view.public_url(public) == public
    assert view.public_url("http://ma/imageproxy/library://track/14?size=512") == \
        "https://ma.example.com/imageproxy/library://track/14?size=512"
    assert view.build("d").cover == public


def test_ma_pushes_move_to_the_public_host(ma, monkeypatch):
    monkeypatch.setenv("MA_HOSTNAME", "ma.example.com")
    devices.set_player_for_device("d", "Den")
    ma.push({"streamUrl": "https://192.0.2.10:8097/flow/den/1.mp3", "playerId": "Den", "title": "Altató",
             "imageUrl": "https://192.0.2.10:8095/imageproxy?path=cover%2F7.jpg&size=512"})
    assert view.stream_url("d") == "https://ma.example.com/flow/den/1.mp3"
    assert view.build("d").cover == "https://ma.example.com/imageproxy?path=cover%2F7.jpg&size=512"


def test_images_on_other_hosts_stay(ma, monkeypatch):
    monkeypatch.setenv("MA_HOSTNAME", "ma.example.com")
    external = "https://images.weserv.nl/?url=https%3A%2F%2Fcovers.example.org%2F7.jpg&w=512"
    assert view.public_url(external) == external
    assert view.public_next({"title": "A bolond levele", "image": external})["image"] == external


def test_ma_urls_stay_without_a_public_host(monkeypatch):
    monkeypatch.delenv("MA_HOSTNAME", raising=False)
    assert view.public_url(COVER) == COVER


def test_the_same_song_on_another_album_is_another_track():
    song = view.View(title="Esti dal", artist="Kaláka", album="Az én szívem")
    assert view.title_key(song) != view.title_key(view.View(title="Esti dal", artist="Kaláka", album="Esti mese"))
    assert view.title_key(None) == ("", "", "")


def test_mas_urls_stay_on_the_public_host_while_ma_reconnects(ma, monkeypatch):
    monkeypatch.setenv("MA_HOSTNAME", "music.example")
    url = "http://ma/imageproxy?path=cover.jpg"
    public = view.public_url(url)
    ma_client.attach(None, None)
    assert view.public_url(url) == public != url



def test_place_after_a_seek_is_the_skills(monkeypatch):
    # repeat one: MA's clock puts the track that was seeked to 200 s at 0 when the stream went out, 10 s ago
    since = 1_762_140_795.15
    monkeypatch.setattr(view, "stream_of", lambda device_id: {"streamUrl": "s", "since": since, "startMs": 200_000})
    monkeypatch.setattr(view.devices, "get_player_for_device", lambda device_id: "p")
    monkeypatch.setattr(view.player, "now_playing", lambda player_id: {
        "paused": False, "track_at": since + 0.02, "anchored_at": since + 0.01, "speed": 1})
    monkeypatch.setattr(view.clock, "time", lambda: since + 10)
    assert view.place_ms("d") == 210_000
