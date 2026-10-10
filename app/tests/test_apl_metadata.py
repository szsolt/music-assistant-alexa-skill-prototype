import pytest

pytest.importorskip("ask_sdk_model")

from skill import apl, util  # noqa: E402


class _Builder:
    def __init__(self):
        self.directives = []

    def add_directive(self, directive):
        self.directives.append(directive)


def _set(info, shown, monkeypatch):
    monkeypatch.setattr(util, "apl_enabled", lambda: True)
    monkeypatch.setattr(util, "get_ma_hostname", lambda **kw: "")
    builder = _Builder()
    util.update_apl_metadata(builder, info, shown)
    return [(c["componentId"], c["property"]) for d in builder.directives for c in d.commands]


def _info(image):
    return {"primaryText": "Song", "secondaryText": "Artist", "albumText": "Album",
            "coverImageSource": image, "backgroundImageSource": image}


def test_images_the_page_shows_are_not_set_again(monkeypatch):
    shown = {}
    assert ("AlexaBackground", "backgroundImageSource") in _set(_info("a.jpg"), shown, monkeypatch)
    assert _set(_info("a.jpg"), shown, monkeypatch) == [
        ("Audio_PrimaryText", "text"), ("Audio_PrimaryTextLong", "text"), ("Audio_SecondaryText", "text"),
        ("Audio_PrimaryTextLonger", "text"), ("AudioPlayerRoot", "titleSize"), ("AudioPlayerRoot", "album")]


def test_the_image_the_page_switched_to_itself_is_not_set_again(monkeypatch):
    shown = {"cover": "a.jpg", "background": "a.jpg", "next": "b.jpg"}
    assert ("Audio_CoverArt", "imageSource") not in _set(_info("b.jpg"), shown, monkeypatch)
    assert shown == {"cover": "b.jpg", "background": "b.jpg", "next": None}
    assert ("Audio_CoverArt", "imageSource") in _set(_info("c.jpg"), shown, monkeypatch)


def test_long_titles_have_smaller_twins_that_follow_track_changes():
    import json, pathlib
    document = json.loads((pathlib.Path(apl.__file__).parent / "apl_document.json").read_text())
    layout = document["layouts"]["AudioPlayer"]
    texts = {c.get("id"): c for c in apl._components(layout) if c.get("type") == "Text"}
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
def test_the_page_starts_with_the_title_size_it_needs(monkeypatch, title, size):
    monkeypatch.setattr(apl, "_get_metadata", lambda device_id=None: {"primaryText": title})
    builder = _Builder()
    apl.add_apl(builder)
    assert builder.directives[0].document["mainTemplate"]["items"][0]["primaryTextSize"] == size


def test_artist_and_album_have_a_line_each_that_follows_track_changes():
    import json, pathlib
    document = json.loads((pathlib.Path(apl.__file__).parent / "apl_document.json").read_text())
    layout = document["layouts"]["AudioPlayer"]
    texts = {c.get("id"): c for c in apl._components(layout) if c.get("type") == "Text"}
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


def test_the_page_starts_with_the_album(monkeypatch):
    monkeypatch.setattr(apl, "_get_metadata", lambda device_id=None: {"primaryText": "T", "albumText": "Alb"})
    builder = _Builder()
    apl.add_apl(builder)
    assert builder.directives[0].document["mainTemplate"]["items"][0]["albumText"] == "Alb"


def test_a_track_with_no_artist_or_album_clears_the_last_ones(monkeypatch):
    builder = _Builder()
    monkeypatch.setattr(util, "apl_enabled", lambda: True)
    monkeypatch.setattr(util, "get_ma_hostname", lambda **kw: "")
    util.update_apl_metadata(builder, {"primaryText": "Radio"}, {})
    values = {(c["componentId"], c["property"]): c["value"] for d in builder.directives for c in d.commands}
    assert values[("Audio_SecondaryText", "text")] == "" and values[("AudioPlayerRoot", "album")] == ""


def test_quality_sits_left_of_the_time_and_follows_track_changes():
    import json, pathlib
    document = json.loads((pathlib.Path(apl.__file__).parent / "apl_document.json").read_text())
    layout = document["layouts"]["AudioPlayer"]
    texts = {c.get("id"): c for c in apl._components(layout) if c.get("type") == "Text"}
    label = texts["Audio_QualityText"]
    assert label["text"] == "${quality}" and label["position"] == "absolute" and label["top"] == 0
    assert label["style"] == "@sliderTimeStampTestStyle"      # as AlexaSlider draws the time
    binds = layout["item"][0]["bind"]
    assert {"name": "quality", "type": "string", "value": ""} in binds
    assert {"name": "nextQuality", "type": "string", "value": ""} in binds
    assert '"property": "quality", "value": "${nextQuality}"' in json.dumps(layout)


def test_a_new_pages_first_update_does_not_set_its_images_again(monkeypatch):
    monkeypatch.setattr(apl, "_get_metadata", lambda device_id=None: _info("a.jpg"))
    monkeypatch.setattr(util, "get_ma_hostname", lambda **kw: "")
    builder = _Builder()
    apl.add_apl(builder)
    page_id = builder.directives[0].document["mainTemplate"]["items"][0]["bellPage"]
    assert ("AlexaBackground", "backgroundImageSource") not in _set(_info("a.jpg"), apl.images_on(page_id), monkeypatch)
