import json

import pytest

pytest.importorskip("ask_sdk_model")

from skill import view  # noqa: E402
from skill.page.apl import render  # noqa: E402


class _Builder:
    def __init__(self):
        self.directives = []

    def add_directive(self, directive):
        self.directives.append(directive)


def _set(v, shown):
    return [(c["componentId"], c["property"]) for c in render.metadata_commands(v, shown)]


def _song(image):
    return view.View(title="Song", artist="Artist", album="Album", cover=image)


def _playing(store, image, **more):
    """MA's latest stream: _song(image)'s song."""
    store.save(dict({"streamUrl": "https://ma.example/flow/1.mp3", "title": "Song", "artist": "Artist",
                     "album": "Album", "imageUrl": image}, **more))


def test_images_the_page_shows_are_not_set_again():
    shown = {}
    assert ("AlexaBackground", "backgroundImageSource") in _set(_song("a.jpg"), shown)
    assert _set(_song("a.jpg"), shown) == [
        ("Audio_PrimaryText", "text"), ("Audio_PrimaryTextLong", "text"), ("Audio_SecondaryText", "text"),
        ("Audio_PrimaryTextLonger", "text"), ("AudioPlayerRoot", "titleSize"), ("AudioPlayerRoot", "album")]


def test_the_image_the_page_switched_to_itself_is_not_set_again():
    shown = {"cover": "a.jpg", "background": "a.jpg", "next": "b.jpg"}
    assert ("Audio_CoverArt", "imageSource") not in _set(_song("b.jpg"), shown)
    assert shown == {"cover": "b.jpg", "background": "b.jpg", "next": None}
    assert ("Audio_CoverArt", "imageSource") in _set(_song("c.jpg"), shown)


def test_an_ma_image_on_the_page_matches_its_stream_server_url():
    shown = {"cover": "a.jpg", "background": "a.jpg",
             "next": "https://ma.example.com/imageproxy/b1?size=512"}
    commands = _set(_song("https://stream.example.com/imageproxy/b1?size=512&fmt=jpg"), shown)
    assert ("Audio_CoverArt", "imageSource") not in commands
    assert ("AlexaBackground", "backgroundImageSource") not in commands


def test_long_titles_have_smaller_twins_that_follow_track_changes():
    document = render.document()
    layout = document["layouts"]["AudioPlayer"]
    texts = {c.get("id"): c for c in render.components(layout) if c.get("type") == "Text"}
    twins = [texts[i] for i in ("Audio_PrimaryText", "Audio_PrimaryTextLong", "Audio_PrimaryTextLonger")]
    assert len({t["maxLines"] for t in twins}) == 1 and all("fontSize" in t for t in twins[1:])
    # Exactly one shows for each size.
    small = "!(@viewportProfile == @hubLandscapeSmall || @viewportProfile == @hubRoundSmall)"
    assert twins[0]["display"] == "${titleSize > 0 && %s ? 'none' : 'normal'}" % small
    for size, twin in enumerate(twins[1:], 1):
        assert twin["display"] == "${titleSize == %d && %s ? 'normal' : 'none'}" % (size, small)
    assert {"name": "titleSize", "type": "number", "value": "${primaryTextSize}"} in layout["item"][0]["bind"]
    assert document["resources"][0]["numbers"]["primarySongTextMaxLines"] == 2
    flip = json.dumps(layout)
    for twin in ("Audio_PrimaryTextLong", "Audio_PrimaryTextLonger"):
        assert '"componentId": "%s", "property": "text", "value": "${nextTitle}"' % twin in flip
    assert '"property": "titleSize", "value": "${nextTitleSize}"' in flip


@pytest.mark.parametrize("title, size", [("x" * 30, 0), ("x" * 31, 1), ("x" * 40, 1), ("x" * 41, 2), ("", 0)])
def test_the_page_starts_with_the_title_size_it_needs(store, title, size):
    store.save({"streamUrl": "https://ma.example/flow/1.mp3", "title": title})
    builder = _Builder()
    render.start(builder)
    assert builder.directives[0].document["mainTemplate"]["items"][0]["primaryTextSize"] == size


def test_artist_and_album_have_a_line_each_that_follows_track_changes():
    document = render.document()
    layout = document["layouts"]["AudioPlayer"]
    texts = {c.get("id"): c for c in render.components(layout) if c.get("type") == "Text"}
    assert texts["Audio_SecondaryText"]["maxLines"] == "@secondarySongTextMaxLines"
    assert all(r.get("numbers", {}).get("secondarySongTextMaxLines", 1) == 1 for r in document["resources"])
    album = texts["Audio_AlbumText"]
    assert album["text"] == "${album}" and album["maxLines"] == 1
    assert {"name": "album", "type": "string", "value": "${albumText}"} in layout["item"][0]["bind"]
    def nodes(node):
        if isinstance(node, dict):
            yield node
            yield from (n for v in node.values() for n in nodes(v))
        elif isinstance(node, list):
            yield from (n for v in node for n in nodes(v))
    flip = next(c for c in nodes(layout) if c.get("type") == "Sequential"
                and "videoProgressValue - trackOffset >= trackDuration" in c.get("when", ""))
    assert {"type": "SetValue", "property": "album", "value": "${nextAlbum}"} in flip["commands"]


def test_the_page_starts_with_the_album(store):
    store.save({"streamUrl": "https://ma.example/flow/1.mp3", "title": "Esti dal", "album": "Az én szívem"})
    builder = _Builder()
    render.start(builder)
    assert builder.directives[0].document["mainTemplate"]["items"][0]["albumText"] == "Az én szívem"


def test_a_track_with_no_artist_or_album_clears_the_last_ones():
    values = {(c["componentId"], c["property"]): c["value"] for c in render.metadata_commands(view.View(title="Radio"), {})}
    assert values[("Audio_SecondaryText", "text")] == "" and values[("AudioPlayerRoot", "album")] == ""


def test_quality_sits_left_of_the_time_and_follows_track_changes():
    document = render.document()
    layout = document["layouts"]["AudioPlayer"]
    texts = {c.get("id"): c for c in render.components(layout) if c.get("type") == "Text"}
    label = texts["Audio_QualityText"]
    assert label["text"] == "${quality}" and label["position"] == "absolute" and label["top"] == 0
    assert label["style"] == "@sliderTimeStampTestStyle"      # as AlexaSlider draws the time
    binds = layout["item"][0]["bind"]
    assert {"name": "quality", "type": "string", "value": ""} in binds
    assert {"name": "nextQuality", "type": "string", "value": ""} in binds
    assert '"property": "quality", "value": "${nextQuality}"' in json.dumps(layout)


def test_a_new_pages_first_update_does_not_set_its_images_again(store):
    _playing(store, "a.jpg")
    builder = _Builder()
    render.start(builder, "d")
    page_id = builder.directives[0].document["mainTemplate"]["items"][0]["bellPage"]
    assert ("AlexaBackground", "backgroundImageSource") not in _set(_song("a.jpg"), render.images("d", page_id))


def _track_offset(response):
    """The trackOffset an answer sets, or None."""
    for directive in response.get("directives", []):
        for command in directive.get("commands", []):
            if command.get("property") == "trackOffset":
                return command["value"]
    return None


@pytest.fixture
def den_page(skill, ma, store, echo, monkeypatch):
    """Echo d1 on Den shows the APL page; MA is 61.4 s into Song. Yields (Echo d1, its page id)."""
    from skill import devices
    monkeypatch.setenv("ENABLE_APL", "true")
    devices.set_player_for_device("d1", "Den")
    _playing(store, "a.jpg", playerId="Den")
    builder = _Builder()
    render.start(builder, "d1")
    return echo("d1", "apl"), builder.directives[0].document["mainTemplate"]["items"][0]["bellPage"]


def test_a_page_the_skill_doesnt_know_gets_its_background_once(skill, ma, store, echo, monkeypatch):
    from skill import devices
    monkeypatch.setenv("ENABLE_APL", "true")
    devices.set_player_for_device("d1", "Den")
    _playing(store, "a.jpg", playerId="Den")
    device = echo("d1", "apl")

    def background_set(response):
        return any(c.get("componentId") == "AlexaBackground" for d in response["directives"] for c in d["commands"])
    assert background_set(device.user_event("MetadataRefresh", 1, "${videoProgressValue}", "5f3a9c0e1b2d4e6f"))
    assert not background_set(device.user_event("MetadataRefresh", 2, "${videoProgressValue}", "5f3a9c0e1b2d4e6f"))


def test_a_page_the_skill_doesnt_know_keeps_its_next_image():
    render.images("d1", "5f3a9c0e1b2d4e6f")["next"] = "b.jpg"
    assert render.images("d1", "5f3a9c0e1b2d4e6f")["next"] == "b.jpg"
