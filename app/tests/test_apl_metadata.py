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
    return {"primaryText": "Song", "secondaryText": "Artist",
            "coverImageSource": image, "backgroundImageSource": image}


def test_images_the_page_shows_are_not_set_again(monkeypatch):
    shown = {}
    assert ("AlexaBackground", "backgroundImageSource") in _set(_info("a.jpg"), shown, monkeypatch)
    assert _set(_info("a.jpg"), shown, monkeypatch) == [
        ("Audio_PrimaryText", "text"), ("Audio_PrimaryTextLong", "text"), ("AudioPlayerRoot", "titleLong"),
        ("Audio_SecondaryText", "text")]


def test_the_image_the_page_switched_to_itself_is_not_set_again(monkeypatch):
    shown = {"cover": "a.jpg", "background": "a.jpg", "next": "b.jpg"}
    assert ("Audio_CoverArt", "imageSource") not in _set(_info("b.jpg"), shown, monkeypatch)
    assert shown == {"cover": "b.jpg", "background": "b.jpg", "next": None}
    assert ("Audio_CoverArt", "imageSource") in _set(_info("c.jpg"), shown, monkeypatch)


def test_long_titles_have_a_smaller_twin_that_follows_track_changes():
    import json, pathlib
    document = json.loads((pathlib.Path(apl.__file__).parent / "apl_document.json").read_text())
    layout = document["layouts"]["AudioPlayer"]
    texts = {c.get("id"): c for c in apl._components(layout) if c.get("type") == "Text"}
    short, long = texts["Audio_PrimaryText"], texts["Audio_PrimaryTextLong"]
    assert short["maxLines"] == long["maxLines"] and "fontSize" in long
    # One shows when the other doesn't.
    assert short["display"].replace("'none' : 'normal'", "X") == long["display"].replace("'normal' : 'none'", "X")
    assert {"name": "titleLong", "type": "boolean", "value": "${primaryTextLong}"} in layout["item"][0]["bind"]
    flip = json.dumps(layout)
    assert '"componentId": "Audio_PrimaryTextLong", "property": "text", "value": "${nextTitle}"' in flip
    assert '"property": "titleLong", "value": "${nextTitleLong}"' in flip


@pytest.mark.parametrize("title, long", [("x" * 40, False), ("x" * 41, True), ("", False)])
def test_the_page_starts_with_the_title_size_it_needs(monkeypatch, title, long):
    monkeypatch.setattr(apl, "_get_metadata", lambda device_id=None: {"primaryText": title})
    builder = _Builder()
    apl.add_apl(builder)
    assert builder.directives[0].document["mainTemplate"]["items"][0]["primaryTextLong"] is long
