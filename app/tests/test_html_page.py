"""The HTML page's routes: its start, its SSE stream, its reports and its page-only commands."""

import base64
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("ask_sdk_core")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")    # the skill makes AWS clients on import

from ask_sdk_core.response_helper import ResponseFactory  # noqa: E402
from ask_sdk_model.interfaces.alexa.presentation.html import StartDirective  # noqa: E402
from ask_sdk_model.interfaces.audioplayer import StopDirective  # noqa: E402

from skill import echo as echos  # noqa: E402
from skill import handoff, watch  # noqa: E402
from skill.ma import library, player  # noqa: E402
from skill.ma.errors import MAUnavailable  # noqa: E402
from skill.page.html import channel, routes  # noqa: E402
from sse import next_chunk  # noqa: E402

URL = "https://skill.example/bell/html/"


@pytest.fixture(autouse=True)
def fresh(monkeypatch, clock):
    monkeypatch.setenv("HTML_PAGE_URL", URL)
    clock.now = 0.0
    monkeypatch.setattr(watch, "ma_state", lambda device_id, page_id: None)   # no checks with MA
    yield
    for page in echos.open_pages("html"):
        echos.close_page(page.device_id, page.page_id)   # ends the page's SSE streams
    handoff.closed("d")


def _html_page(device_id="d"):
    page_id = echos.open_page(device_id, "html").page_id
    watch.new_page(page_id)
    return page_id


class _Stream:
    """The page's SSE stream, as its browser reads it."""

    def __init__(self, page_client, page):
        self.response = page_client.get(f"/bell/html/{page}/events", buffered=False)
        self.chunks = iter(self.response.response)
        assert next(self.chunks) == b"retry: 1000\n\n"

    def next(self):
        return json.loads(next_chunk(self.chunks)[len(b"data: "):])


def test_a_new_page_is_the_html_page(store):
    store.save({"streamUrl": "https://ma.example/flow.mp3", "title": "Kicsi, gyere velem", "artist": "Kaláka",
                "imageUrl": "https://ma.example/cover.jpg"})
    builder = ResponseFactory()
    channel.start(builder, "d", start_paused=True)
    [directive] = builder.response.directives
    page = channel.page_of("d")
    assert isinstance(directive, StartDirective)
    assert directive.request.uri == URL + page
    assert directive.configuration.timeout_in_seconds == 1800
    start = channel.start_state(page)
    assert start["title"] == "Kicsi, gyere velem" and start["artist"] == "Kaláka" and start["autoplay"] is False
    assert start["stream"] == "https://ma.example/flow.mp3"
    assert start["cover"] == "https://ma.example/cover.jpg"


def test_the_page_comes_with_every_part_of_its_script_and_style_inline(store, page_client):
    store.save({"streamUrl": "https://ma.example/flow.mp3", "title": "Kicsi, gyere velem", "artist": "Kaláka"})
    channel.start(ResponseFactory(), "d", start_paused=True)
    html = page_client.get(f"/bell/html/{channel.page_of('d')}").get_data(as_text=True)
    folder = Path(routes.__file__).parent
    for part in [*folder.glob("*.js"), *folder.glob("*.css")]:
        assert part.read_text() in html, part.name
    assert "<!--include:" not in html and '"title": "Kicsi, gyere velem"' in html


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_served_page_runs_in_a_stub_browser(store, page_client, tmp_path):
    store.save({"streamUrl": "https://ma.example/flow.mp3", "title": "Kicsi, gyere velem", "artist": "Kaláka"})
    channel.start(ResponseFactory(), "d", start_paused=True)
    html = page_client.get(f"/bell/html/{channel.page_of('d')}").get_data(as_text=True)
    served = tmp_path / "page.js"
    served.write_text(re.search(r"<script>(.*?)</script>", html, re.S).group(1))
    run = subprocess.run(["node", "--test", str(Path(__file__).parent / "js" / "page.test.js")],
                         env={**os.environ, "PAGE_SCRIPT": str(served)}, capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stdout[-3000:]


def test_an_unknown_page_is_not_found(page_client):
    assert page_client.get("/bell/html/nope").status_code == 404
    assert page_client.get("/bell/html/nope/events").status_code == 404
    assert page_client.post("/bell/html/nope/state", json={"seen": 0, "at": 0, "playing": True}).status_code == 404
    assert page_client.post("/bell/html/nope/action", json={"action": "next"}).status_code == 404


def test_a_bad_report_or_button_is_refused(page_client):
    page = _html_page()
    assert page_client.post(f"/bell/html/{page}/state", json={"seen": 0, "at": 0}).status_code == 400
    for action in ({"action": "seek"}, {"action": "dance"}, None):
        assert page_client.post(f"/bell/html/{page}/action", json=action).status_code == 400


def test_a_report_shows_the_page_is_open(page_client):
    page = _html_page()
    assert not handoff.is_live("d")
    assert page_client.post(f"/bell/html/{page}/state", json={"seen": 0, "at": 0, "playing": True}).status_code == 204
    assert handoff.is_live("d")


def test_a_new_page_replaces_the_old_one(page_client):
    old = _html_page()
    stream = _Stream(page_client, old)
    new = _html_page()
    assert list(stream.chunks) == []
    assert page_client.post(f"/bell/html/{old}/state", json={"seen": 0, "at": 0, "playing": True}).status_code == 404
    assert page_client.post(f"/bell/html/{new}/state", json={"seen": 0, "at": 0, "playing": True}).status_code == 204


def test_replies_to_the_html_page_carry_no_audio_player_directives():
    start = StartDirective()
    response = ResponseFactory().add_directive(StopDirective()).add_directive(start).response
    channel.drop_audio_player(response)
    assert response.directives == [start]
    response = ResponseFactory().add_directive(StopDirective()).response
    channel.drop_audio_player(response)
    assert response.directives is None


def test_the_page_is_open_while_it_holds_its_stream(page_client):
    page = _html_page()
    assert not channel.is_open("d")
    stream = _Stream(page_client, page)
    assert channel.is_open("d")
    stream.response.close()
    assert not channel.is_open("d")


def test_the_page_buttons_run_the_players_actions(page_client, ma):
    echos.get("d").pair("Den")
    page = _html_page()
    for action in ({"action": "next"}, {"action": "seek", "position": 61_000}, {"action": "jump", "item": "q7"}):
        assert page_client.post(f"/bell/html/{page}/action", json=action).status_code == 204
    assert [c for c in ma.calls if c[0] in ("next", "seek", "play_index")] == [
        ("next", "Den"), ("seek", "den", 61), ("play_index", "den", "q7")]


def test_a_jump_to_nothing_clears_the_wait(page_client):
    page = _html_page()
    stream = _Stream(page_client, page)
    assert page_client.post(f"/bell/html/{page}/action", json={"action": "jump", "item": ""}).status_code == 204
    assert stream.next() == {"media": "play", "ready": True}


def _playlist(monkeypatch, page_client):
    """Echo d's open page on Den, MA's player and library faked. (page, its stream, the MA calls)."""
    calls = []
    echos.get("d").pair("Den")
    monkeypatch.setattr(player, "queue_window",
                        lambda player_id, offset=None, limit=110: calls.append(("window", player_id, offset, limit))
                        or {"offset": offset or 0, "items": []})
    monkeypatch.setattr(player, "edit_queue",
                        lambda player_id, kind, item_id, shift=0: calls.append((kind, player_id, item_id, shift)) or True)
    monkeypatch.setattr(library, "wave_form",
                        lambda player_id, item_id=None: calls.append(("wave", player_id, item_id)) or [0.5, 1.0])
    monkeypatch.setattr(player, "options", lambda player_id, kind=None, value=0:
                        calls.append(("options", kind, value)) or {"autoplay": True})
    monkeypatch.setattr(player, "play_media", lambda player_id, uri, option="replace", start=None:
                        calls.append(("play_media", uri, option, start)) or True)
    monkeypatch.setattr(library, "browse", lambda player_id, kind, text="", item_id=None:
                        calls.append(("browse", kind, text, item_id)) or {"title": text, "sections": []})
    monkeypatch.setattr(library, "now_playing", lambda player_id, item_id=None, timeout=None:
                        calls.append(("now", item_id)) or {"uris": ["library://track/14", "library://album/7"]})
    monkeypatch.setattr(library, "set_favorite", lambda uri, on:
                        calls.append(("favorite", uri, on)) or uri != "library://album/9")
    monkeypatch.setattr(library, "playlists", lambda: [["2", "Esti dal"]])
    monkeypatch.setattr(library, "add_to_playlist",
                        lambda player_id, playlist_id, name, uri="", item_id="", queue=False:
                        calls.append(("add", playlist_id, name, uri, item_id, queue)) or (name if name != "Ringató" else None))
    monkeypatch.setattr(player, "players", lambda timeout=None: [("Den", "Den"), ("kitchen", "Kitchen")])
    monkeypatch.setattr(player, "move", lambda player_id, target_id, timeout=None:
                        calls.append(("transfer", player_id, target_id)))
    monkeypatch.setattr(library, "lyrics", lambda player_id, item_id=None: None)
    page = _html_page()
    return page, _Stream(page_client, page), calls


def _act(page_client, page, action):
    return page_client.post(f"/bell/html/{page}/action", json=action).status_code


# (what the page asks, the MA calls it makes, the page's first message back; None: none is checked)
_ACTIONS = [
    ({"action": "queue", "offset": -1, "limit": 110}, [("window", "Den", None, 110)],
     {"queue": {"offset": 0, "items": []}}),
    ({"action": "move", "item": "q14", "shift": -2, "offset": 2, "limit": 110},
     [("move", "Den", "q14", -2), ("window", "Den", 2, 110)], {"queue": {"offset": 2, "items": []}}),
    ({"action": "delete", "item": "q14", "offset": 2, "limit": 110},
     [("delete", "Den", "q14", 0), ("window", "Den", 2, 110)], None),
    ({"action": "clear", "offset": -1, "limit": 110}, [("clear", "Den", "", 0), ("window", "Den", None, 110)], None),
    ({"action": "mix", "item": "q3"}, [("mix", "Den", "q3", 0)], None),
    ({"action": "wave", "item": ""}, [("wave", "Den", None)], {"wave": {"item": "", "points": [0.5, 1.0]}}),
    ({"action": "sleep", "minutes": 30}, [("options", "sleep", 30)], None),
    ({"action": "autoplay", "on": False}, [("options", "autoplay", False)], None),
    ({"action": "crossfade", "on": True}, [("options", "crossfade", True)], None),
    ({"action": "options"}, [("options", None, False)], {"options": {"autoplay": True}}),
    ({"action": "players"}, [], {"players": [["kitchen", "Kitchen"]]}),
    ({"action": "transfer", "target": "kitchen", "name": "Kitchen"}, [("transfer", "Den", "kitchen")],
     {"toast": "Moved to Kitchen"}),
    ({"action": "lyrics", "item": "q13"}, [], {"lyrics": {"item": "q13", "plain": "", "lrc": ""}}),
    ({"action": "play_media", "uri": "library://album/7", "item": "library://track/12"},
     [("play_media", "library://album/7", "replace", "library://track/12")], None),
    ({"action": "play_media", "uri": "library://track/12", "option": "add"},
     [("play_media", "library://track/12", "add", None)], {"toast": "Added to the queue"}),
    ({"action": "browse", "kind": "search", "text": "esti dal"}, [("browse", "search", "esti dal", None)],
     {"browse": {"title": "esti dal", "sections": []}}),
    ({"action": "favorite", "uri": "library://album/7", "on": True}, [("favorite", "library://album/7", True)], None),
    ({"action": "now", "item": "q13"}, [("now", "q13")],
     {"now": {"item": "q13", "uris": ["library://track/14", "library://album/7"]}}),
    ({"action": "playlists"}, [], {"playlists": [["2", "Esti dal"]]}),
    ({"action": "add_to_playlist", "playlist": "2", "name": "Esti dal", "uri": "library://album/7"},
     [("add", "2", "Esti dal", "library://album/7", "", False)], {"toast": "Added to Esti dal"}),
    ({"action": "add_to_playlist", "playlist": "", "name": "Esti mix", "queue": True},
     [("add", "", "Esti mix", "", "", True)], {"toast": "Added to Esti mix"}),
]


def test_each_page_action_asks_ma_and_answers_the_page(monkeypatch, page_client):
    for action, made, answer in _ACTIONS:
        page, stream, calls = _playlist(monkeypatch, page_client)
        assert _act(page_client, page, action) == 204, action
        assert calls == made, action
        if answer is not None:
            assert stream.next() == answer, action


def test_a_page_action_ma_did_not_do_says_so(monkeypatch, page_client):
    page, stream, _ = _playlist(monkeypatch, page_client)
    assert _act(page_client, page, {"action": "favorite", "uri": "library://album/9", "on": False}) == 204
    assert stream.next() == {"toast": "Music Assistant did not do it"}
    assert _act(page_client, page, {"action": "add_to_playlist", "playlist": "3", "name": "Ringató", "item": "q3"}) == 204
    assert stream.next() == {"toast": "Music Assistant did not do it"}

    def move(player_id, target_id, timeout=None):
        raise MAUnavailable("down")
    monkeypatch.setattr(player, "move", move)
    assert _act(page_client, page, {"action": "transfer", "target": "kitchen", "name": "Kitchen"}) == 204
    assert stream.next() == {"toast": "Music Assistant did not do it"}
    monkeypatch.setattr(player, "edit_queue", lambda *a, **k: False)
    assert _act(page_client, page, {"action": "mix", "item": "q3"}) == 204
    assert stream.next() == {"toast": "Music Assistant did not do it", "ready": True}


def test_a_page_action_without_what_it_needs_is_refused(monkeypatch, page_client):
    page, _, calls = _playlist(monkeypatch, page_client)
    for action in ({"action": "sleep"}, {"action": "sleep", "minutes": -5},
                   {"action": "browse", "kind": "delete"}, {"action": "play_media", "uri": "x", "option": "now"},
                   {"action": "play_media"}, {"action": "favorite", "on": True},
                   {"action": "move", "item": "q14", "shift": 0}, {"action": "move", "shift": 1},
                   {"action": "add_to_playlist", "playlist": "", "name": " ", "queue": True}):
        assert _act(page_client, page, action) == 400, action
    assert calls == []


def _ma_visualizer(monkeypatch, frame):
    """MA's visualiser socket on a local port: it sends frame to a viewer with the right token."""
    import asyncio
    from aiohttp import web

    async def viewer(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        auth = await ws.receive_json()
        if auth == {"type": "auth", "token": "ma-token"} and request.query.get("player") == "Den":
            await ws.send_str('{"type": "auth_ok"}')
            await ws.send_bytes(frame())
        async for _ in ws:
            pass
        return ws

    loop, ready = asyncio.new_event_loop(), threading.Event()
    port = []

    async def serve():
        app = web.Application()
        app.router.add_get("/milkdrop_visualizer", viewer)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port.append(site._server.sockets[0].getsockname()[1])
        ready.set()

    threading.Thread(target=lambda: (loop.run_until_complete(serve()), loop.run_forever()), daemon=True).start()
    ready.wait(5)
    monkeypatch.setenv("MA_API_URL", f"http://127.0.0.1:{port[0]}")
    monkeypatch.setenv("MA_API_TOKEN", "ma-token")


def test_page_gets_mas_visualiser_frames(monkeypatch, page_client):
    page, stream, _ = _playlist(monkeypatch, page_client)
    samples = bytes(range(256)) * 4
    _ma_visualizer(monkeypatch, lambda: bytes([22]) + int((time.monotonic() + 2) * 1e6).to_bytes(8, "big") + samples)
    assert _act(page_client, page, {"action": "viz", "on": True}) == 204
    [[ahead, wave]] = stream.next()["viz"]["frames"]
    assert 1500 < ahead <= 2000
    assert base64.b64decode(wave) == samples
    assert _act(page_client, page, {"action": "viz", "on": False}) == 204

