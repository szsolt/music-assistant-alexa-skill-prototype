import pytest

apl = pytest.importorskip("skill.apl")


@pytest.fixture(autouse=True)
def no_buttons(monkeypatch):
    monkeypatch.setattr(apl, "_start_buttons", None)


def test_start_values_from_the_skill(monkeypatch):
    monkeypatch.setattr(apl, "_start_track_time", lambda device_id: (-65_000, 201_000))
    assert apl._start_values("d") == {"startOffset": -65_000, "startDuration": 201_000}


def test_no_start_values_without_device_or_answer(monkeypatch):
    monkeypatch.setattr(apl, "_start_track_time", lambda device_id: None)
    assert apl._start_values("d") == {}
    assert apl._start_values(None) == {}

    def broken(device_id):
        raise RuntimeError("MA down")
    monkeypatch.setattr(apl, "_start_track_time", broken)
    assert apl._start_values("d") == {}


def test_start_values_carry_the_button_states(monkeypatch):
    monkeypatch.setattr(apl, "_start_track_time", lambda device_id: None)
    monkeypatch.setattr(apl, "_start_buttons", lambda device_id: {"shuffleOn": 1, "repeatMode": 2, "songFavorite": True})
    assert apl._start_values("d") == {"startShuffleOn": 1, "startRepeatMode": 2, "startSongFavorite": 1}

    def broken(device_id):
        raise RuntimeError("MA down")
    monkeypatch.setattr(apl, "_start_buttons", broken)
    monkeypatch.setattr(apl, "_start_track_time", lambda device_id: (0, 1_000))
    assert apl._start_values("d") == {"startOffset": 0, "startDuration": 1_000}


def test_the_page_binds_its_buttons_to_the_start_values():
    import json, pathlib
    document = json.loads((pathlib.Path(apl.__file__).parent / "apl_document.json").read_text())
    layout = document["layouts"]["AudioPlayer"]
    params = {p["name"]: p for p in layout["parameters"]}
    binds = {b["name"]: b["value"] for b in layout["item"][0]["bind"]}
    for name in ("shuffleOn", "repeatMode", "songFavorite", "albumFavorite"):
        start = "start" + name[0].upper() + name[1:]
        assert params[start]["default"] == -1 and binds[name] == "${%s}" % start
