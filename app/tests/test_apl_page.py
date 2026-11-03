"""The basic APL page as Alexa gets it: what it starts with, and what a refresh sets."""
import pytest

pytest.importorskip("ask_sdk_model")

from skill import devices, handoff  # noqa: E402

COVER = "http://192.0.2.10:8095/imageproxy?path=cover%2F7.jpg&size=512"


@pytest.fixture
def den(ma, store, echo, monkeypatch):
    """Echo d on Den shows the APL page; MA is 61.4 s into Kicsi, gyere velem."""
    monkeypatch.setenv("ENABLE_APL", "true")
    monkeypatch.delenv("MA_HOSTNAME", raising=False)
    devices.set_player_for_device("d", "Den")
    store.save({"playerId": "Den", "streamUrl": "http://192.0.2.10:8097/flow/den/1.mp3",
                "title": "Kicsi, gyere velem", "artist": "Kaláka", "album": "Az én szívem", "imageUrl": COVER})
    yield echo("d", "apl")
    handoff.closed("d")


def test_the_page_starts_with_the_track_its_time_and_buttons(den):
    [directive] = den.intent("PlayAudio")["directives"]
    item = directive["document"]["mainTemplate"]["items"][0]
    assert directive["type"] == "Alexa.Presentation.APL.RenderDocument" and directive["datasources"] == {}
    assert {k: v for k, v in item.items() if k not in ("type", "id", "bellPage")} == {
        "audioSources": "http://192.0.2.10:8097/flow/den/1.mp3",
        "primaryText": "Kicsi, gyere velem", "secondaryText": "Kaláka", "albumText": "Az én szívem",
        "coverImageSource": COVER, "backgroundImageSource": COVER, "primaryTextSize": 0,
        "headerTitle": "", "headerSubtitle": "", "sliderType": "determinate",
        "startOffset": -61_000, "startDuration": 192_000, "startShuffleOn": 0, "startRepeatMode": 0,
        "startSongFavorite": 0, "startAlbumFavorite": 1}


def test_the_first_refresh_sets_texts_track_time_next_track_and_buttons(den):
    page = den.intent("PlayAudio")["directives"][0]["document"]["mainTemplate"]["items"][0]["bellPage"]
    texts, timing = den.user_event("MetadataRefresh", 1, 2000, page)["directives"]

    def values(directive):
        return [(c["componentId"], c["property"], c["value"]) for c in directive["commands"] if c["type"] == "SetValue"]
    title, root = "Kicsi, gyere velem", "AudioPlayerRoot"
    assert values(texts) == [
        ("Audio_PrimaryText", "text", title), ("Audio_PrimaryTextLong", "text", title),
        ("Audio_SecondaryText", "text", "Kaláka"), ("Audio_PrimaryTextLonger", "text", title),
        (root, "titleSize", 0), (root, "album", "Az én szívem")]
    assert values(timing) == [
        (root, "queueEnd", 0), (root, "trackOffset", -61_000), (root, "trackDuration", 192_000),
        (root, "nextTitle", "A bolond levele"), (root, "nextSecondary", "Kaláka"),
        (root, "nextImage", "http://ma/imageproxy/library://track/14?size=512"), (root, "nextDuration", 193_000),
        (root, "songFavorite", 0), (root, "albumFavorite", 1), (root, "shuffleOn", 0), (root, "repeatMode", 0),
        (root, "nextTitleSize", 0), (root, "nextAlbum", "Az én szívem"), (root, "nextQuality", ""),
        (root, "nextItem", "q13"), (root, "quality", "")]
    assert [c["type"] for c in timing["commands"][-2:]] == ["Idle", "SendEvent"]

