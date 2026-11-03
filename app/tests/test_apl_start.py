import pytest

render = pytest.importorskip("skill.page.apl.render")


class _Builder:
    def __init__(self):
        self.directives = []

    def add_directive(self, directive):
        self.directives.append(directive)


def _start_values(device_id):
    """The start values of the page the skill builds now for device_id."""
    builder = _Builder()
    render.start(builder, device_id)
    item = builder.directives[0].document["mainTemplate"]["items"][0]
    return {key: value for key, value in item.items() if key.startswith("start")}


@pytest.fixture
def den(skill, ma, store):
    """Echo d plays MA player Den."""
    from skill import devices
    devices.set_player_for_device("d", "Den")
    store.save({"playerId": "Den", "streamUrl": "https://ma.example/flow/1.mp3", "title": "Kicsi, gyere velem"})


def test_a_new_page_starts_with_mas_track_time_and_button_states(den):
    assert _start_values("d") == {"startOffset": -61_000, "startDuration": 192_000, "startShuffleOn": 0,
                                  "startRepeatMode": 0, "startSongFavorite": 0, "startAlbumFavorite": 1}


def test_a_page_without_a_paired_player_has_no_start_values(den):
    assert _start_values("unpaired") == {}
    assert _start_values(None) == {}


def test_the_page_binds_its_buttons_to_the_start_values():
    document = render.document()
    layout = document["layouts"]["AudioPlayer"]
    params = {p["name"]: p for p in layout["parameters"]}
    binds = {b["name"]: b["value"] for b in layout["item"][0]["bind"]}
    for name in ("shuffleOn", "repeatMode", "songFavorite", "albumFavorite"):
        start = "start" + name[0].upper() + name[1:]
        assert params[start]["default"] == -1 and binds[name] == "${%s}" % start


def test_a_page_opens_without_start_values_when_reading_them_fails(den, monkeypatch):
    def broken(device_id):
        raise RuntimeError("positions")
    monkeypatch.setattr(render.view, "now", broken)
    assert _start_values("d") == {}
