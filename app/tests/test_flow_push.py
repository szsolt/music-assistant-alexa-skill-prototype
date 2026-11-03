import pytest

from skill import store

pytest.importorskip("ask_sdk_model")

from skill import devices, handoff  # noqa: E402
from skill.page.apl import render  # noqa: E402
from skill.page.html import channel  # noqa: E402
from fake_ma import QUEUE  # noqa: E402
from music_assistant_models.enums import EventType  # noqa: E402
from sse import MARK, MARK_CHUNK, next_chunk  # noqa: E402

DEN_FLOW = "http://192.0.2.10:8097/flow/den/1.mp3"


class _Builder:
    def __init__(self):
        self.directives = []

    def add_directive(self, directive):
        self.directives.append(directive)


@pytest.fixture
def two_players(ma):
    """Echo d plays MA player Den, Echo k plays Kitchen; each has its stream."""
    devices.set_player_for_device("d", "Den")
    devices.set_player_for_device("k", "Kitchen")
    ma.push({"streamUrl": DEN_FLOW, "playerId": "Den", "title": "Altató", "artist": "Kaláka"})
    ma.push({"streamUrl": "http://192.0.2.10:8097/flow/kitchen/1.mp3", "playerId": "Kitchen", "title": "Ringató"})
    yield
    handoff.closed("d")
    handoff.closed("k")


def _report(page_client, page):
    assert page_client.post(f"/bell/html/{page}/state", json={"seen": 0, "at": 5000, "playing": True}).status_code == 204


def _events(page_client, page):
    """The page's SSE stream, as its browser reads it."""
    response = page_client.get(f"/bell/html/{page}/events", buffered=False)
    chunks = iter(response.response)
    assert next(chunks) == b"retry: 1000\n\n"
    return response, chunks


def _shown(chunks, page):
    """What the skill sent the page so far, as its SSE stream brings it."""
    channel.send(page, MARK)
    seen = b""
    while (chunk := next_chunk(chunks)) != MARK_CHUNK:
        seen += chunk
    return seen


def _next_track_in_dens_flow(ma):
    """Den's flow reaches its next track: MA pushes it with no player id and the same stream URL. Returns its title."""
    ma.plays(ma.queue.current_index + 1, 0.5)
    title = ma.queue.current_item.name
    ma.push({"streamUrl": store.for_player("Den")["streamUrl"], "title": title, "artist": "Kaláka", "album": "Az én szívem"})
    ma.later(EventType.QUEUE_UPDATED, QUEUE)
    return title


def test_the_next_track_in_a_flow_reaches_only_its_players_html_pages(two_players, ma, page_client, clock,
                                                                    monkeypatch):
    monkeypatch.setenv("ENABLE_APL", "true")
    monkeypatch.setenv("HTML_PAGE_URL", "https://skill.example/bell/html/")
    streams = {}
    try:
        for device_id in ("d", "k"):
            channel.start(_Builder(), device_id, start_paused=True)
            page = channel.page_of(device_id)
            streams[device_id] = (page, *_events(page_client, page))
            _report(page_client, page)
            assert b'"track"' in _shown(streams[device_id][2], page)
        title = _next_track_in_dens_flow(ma)
        assert title.encode() in _shown(streams["d"][2], streams["d"][0])
        assert _shown(streams["k"][2], streams["k"][0]) == b""
    finally:
        for _, response, _ in streams.values():
            response.close()


def test_the_next_track_in_a_flow_shows_only_on_its_players_apl_pages(two_players, ma, echo, monkeypatch):
    monkeypatch.setenv("ENABLE_APL", "true")
    pages = {}
    for device_id in ("d", "k"):
        builder = _Builder()
        render.start(builder, device_id)
        pages[device_id] = builder.directives[0].document["mainTemplate"]["items"][0]["bellPage"]
    den = _next_track_in_dens_flow(ma)

    def title(response):
        return next(c["value"] for d in response["directives"] for c in d.get("commands", [])
                    if c.get("componentId") == "Audio_PrimaryText")
    shown = {d: title(echo(d, "apl").user_event("MetadataRefresh", 1, "${videoProgressValue}", page))
             for d, page in pages.items()}
    assert shown == {"d": den, "k": "Ringató"}


def test_a_stream_of_no_known_player_reaches_the_page_of_an_echo_without_one(two_players, ma, page_client, clock,
                                                                          monkeypatch):
    monkeypatch.setenv("HTML_PAGE_URL", "https://skill.example/bell/html/")
    channel.start(_Builder(), "u", start_paused=True)
    page = channel.page_of("u")
    response, chunks = _events(page_client, page)
    try:
        _report(page_client, page)
        assert b'"track"' in _shown(chunks, page)    # what it shows first
        ma.push({"streamUrl": "http://192.0.2.10:8097/flow/garden/1.mp3", "title": "Esti dal", "artist": "Kaláka"})
        assert b"Esti dal" in _shown(chunks, page)
    finally:
        response.close()
        handoff.closed("u")
