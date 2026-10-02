import pytest

pytest.importorskip("ask_sdk_model")

from skill import util  # noqa: E402


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
    assert _set(_info("a.jpg"), shown, monkeypatch) == [("Audio_PrimaryText", "text"),
                                                       ("Audio_SecondaryText", "text")]


def test_the_image_the_page_switched_to_itself_is_not_set_again(monkeypatch):
    shown = {"cover": "a.jpg", "background": "a.jpg", "next": "b.jpg"}
    assert ("Audio_CoverArt", "imageSource") not in _set(_info("b.jpg"), shown, monkeypatch)
    assert shown == {"cover": "b.jpg", "background": "b.jpg", "next": None}
    assert ("Audio_CoverArt", "imageSource") in _set(_info("c.jpg"), shown, monkeypatch)
