"""The open HTML page and MA: its hand-offs and news, through MA's requests, the page's routes and its Echo."""

import json
import logging
import queue
import threading
import time

import pytest

from music_assistant_models.enums import RepeatMode

from skill import echo as echos
from skill import actions, handoff, view
from skill.ma import client as ma_client
from skill.ma import events as ma_events
from skill.ma import player
from sse import next_chunk, up_to_mark

flask = pytest.importorskip("flask")
pytest.importorskip("ask_sdk_core")


@pytest.fixture
def den(ma, store, monkeypatch):
    """Echo d plays MA player Den, whose last stream the skill holds."""
    monkeypatch.setenv("ENABLE_APL", "true")
    monkeypatch.setenv("HTML_PAGE_URL", "https://skill.example/bell/html/")
    echos.get("d").pair("Den")
    store.save({"playerId": "Den", "streamUrl": "http://ma/flow/0.mp3", "title": "Kicsi, gyere velem",
                "artist": "Kaláka", "album": "Az én szívem"})
    yield
    for page in echos.open_pages("html"):
        echos.close_page(page.device_id, page.page_id)   # ends the page's SSE streams
    for reader in _readers:
        reader.join(1)
    del _readers[:]


@pytest.fixture
def ma_routes_client(app):
    """The skill's routes for MA's requests."""
    return app.test_client()


@pytest.fixture
def control(ma_routes_client):
    """MA's pause or resume for player Den, as MA sends it to the skill. Returns pageLive."""
    return lambda command: ma_routes_client.post("/ma/control", json={
        "playerId": "Den", "command": command, "canSkipSpeech": True}).get_json()["pageLive"]


_readers = []


def _message(chunk):
    """The message in an SSE chunk, or None for a comment such as a keepalive."""
    text = chunk.decode()
    return json.loads(text[len("data: "):]) if text.startswith("data: ") else None


class _Events:
    """The page's SSE stream, as its browser reads it."""

    def __init__(self, page_client, page_id):
        self.page_id = page_id
        self.response = page_client.get(f"/bell/html/{page_id}/events", buffered=False)
        self.messages = queue.Queue()
        self.chunks = iter(self.response.response)
        assert next(self.chunks) == b"retry: 1000\n\n"
        self.reader = None

    def next(self):
        """The next message, as soon as it comes."""
        return _message(next_chunk(self.chunks))

    def received(self):
        """The messages sent so far. The page reads nothing before the first call."""
        if self.reader is None:
            def read():
                for chunk in self.chunks:
                    if (message := _message(chunk)) is not None:
                        self.messages.put(message)
            self.reader = threading.Thread(target=read, daemon=True)
            self.reader.start()
            _readers.append(self.reader)
        return up_to_mark(self.page_id, self.messages)


class _Page:
    """The Echo's open HTML page: its SSE stream, and its report every second with the last answer it took in."""

    def __init__(self, page_client, clock, device_id="d"):
        self.client, self.clock = page_client, clock
        self.id = echos.get(device_id).page.page_id
        self.seen = 0
        self.events = _Events(page_client, self.id)

    def tell(self):
        """A second on, the page reports where it is."""
        self.clock.advance(1)
        assert self.client.post(f"/bell/html/{self.id}/state",
                                json={"seen": self.seen, "at": 5000, "playing": True}).status_code in (200, 204)

    def report(self, deaf=False):
        """A second on, the page reports; the messages that came. deaf: the page doesn't take them in."""
        self.tell()
        got = self.events.received()
        if not deaf:
            for message in got:
                self.seen = max(self.seen, message.get("set", {}).get("seen", self.seen))
        return got

    def seek(self, position):
        self.press({"action": "seek", "position": position})

    def press(self, action):
        assert self.client.post(f"/bell/html/{self.id}/action", json=action).status_code == 204


def _open(echo, page_client, clock, device_id="d"):
    """The Echo opens the HTML page, which takes MA's stream for it."""
    echo(device_id, "html").launch()
    page = _Page(page_client, clock, device_id)
    page.tell()
    first = page.events.next()
    assert first["src"].startswith("http://ma/flow/")
    page.seen = first["set"]["seen"]
    return page


def _sources(messages):
    return [m["src"] for m in messages if "src" in m]


def test_a_handoff_lost_with_its_sse_stream_reaches_the_reconnected_page(den, echo, ma, page_client, clock):
    page = _open(echo, page_client, clock)
    page.events.response.close()
    page.events = _Events(page_client, page.id)    # a stream the page will never read from
    page.seek(100000)
    stream = ma.pushed[-1]["streamUrl"]
    assert ma.replies[-1]["pageLive"] is True
    page.tell()                                    # the skill sends the page the new stream
    page.events.response.close()                   # the SSE stream drops before the page reads the stream
    page.events = _Events(page_client, page.id)    # EventSource reconnects by itself
    ma.flows_on()
    got = [m for _ in range(6) for m in page.report()]
    assert _sources(got) == [stream]
    assert "A bolond levele" in [m["track"]["title"] for m in got if "track" in m]
    del ma.calls[:]
    assert [m for _ in range(4) for m in page.report()] == []
    assert ma.calls == []


def test_a_stream_lost_with_its_sse_stream_comes_before_mas_pause(den, echo, ma, page_client, clock, control):
    page = _open(echo, page_client, clock)
    page.events.response.close()
    page.events = _Events(page_client, page.id)
    page.seek(100000)
    stream = ma.pushed[-1]["streamUrl"]
    page.tell()                                    # the skill sends the page the new stream
    page.events.response.close()                   # the SSE stream drops before the page reads the stream
    page.events = _Events(page_client, page.id)
    assert control("pause")
    got = page.report()
    assert _sources(got) == [stream]
    assert [m["media"] for m in got if "media" in m] == ["play", "pause"]


def test_a_stream_sent_again_on_a_new_sse_stream_goes_no_more(den, echo, ma, page_client, clock, control):
    page = _open(echo, page_client, clock)
    page.events.response.close()
    page.events = _Events(page_client, page.id)
    page.seek(100000)
    stream = ma.pushed[-1]["streamUrl"]
    page.tell()                                    # the skill sends the page the new stream
    page.events.response.close()                   # the SSE stream drops before the page reads the stream
    page.events = _Events(page_client, page.id)
    assert control("pause")
    got = [m for _ in range(4) for m in page.report(deaf=True)]
    assert _sources(got) == [stream]


def test_an_unconfirmed_handoff_goes_again_until_the_page_has_it(den, echo, ma, page_client, clock):
    page = _open(echo, page_client, clock)
    page.seek(100000)
    stream = ma.pushed[-1]["streamUrl"]
    assert _sources(page.report(deaf=True)) == [stream]
    assert [_sources(page.report(deaf=True)) for _ in range(2)] == [[], [stream]]
    page.seen += 2                                 # the page takes in the second one
    assert [m for _ in range(4) for m in page.report()] == []


def test_a_handoff_the_page_never_confirms_goes_once_more_then_ma_speaks_it(den, echo, ma, page_client, clock):
    page = _open(echo, page_client, clock)
    page.seek(100000)
    stream = ma.pushed[-1]["streamUrl"]
    got = [_sources(page.report(deaf=True)) for _ in range(3)]
    assert got == [[stream], [], [stream]]
    pushed = len(ma.pushed)
    got = [m for _ in range(4) for m in page.report(deaf=True)]
    assert _sources(got) == []
    assert len(ma.pushed) == pushed + 1            # MA sends the stream again, the spoken way
    assert ma.replies[-1]["pageLive"] is False


def test_a_press_after_a_quiet_spell_takes_mas_new_stream_on_the_page(den, echo, ma, page_client, clock):
    page = _open(echo, page_client, clock)
    page.report()
    clock.advance(handoff.LIVE_SECONDS + 1)
    page.seek(100000)
    assert ma.replies[-1]["pageLive"] is True


def _missed(page, ma, clock):
    """The page doesn't take MA's new stream in time: MA sends it the spoken way. The page hasn't reported since."""
    page.seek(100000)
    for _ in range(handoff.HANDOFF_SECONDS - 1):
        page.report(deaf=True)
    clock.advance(1)
    assert ma.replies[-1]["pageLive"] is False


def test_a_page_that_missed_a_handoff_is_asked_to_reopen_its_sse_stream(den, echo, ma, page_client, clock):
    page = _open(echo, page_client, clock)
    _missed(page, ma, clock)
    answer = page_client.post(f"/bell/html/{page.id}/state", json={"seen": page.seen, "at": 5000, "playing": True})
    assert answer.get_json() == {"reconnect": True}
    page.seek(50000)
    assert ma.replies[-1]["pageLive"] is False


def test_a_page_that_missed_a_handoff_stays_open_for_a_spoken_reply(den, echo, ma, page_client, clock):
    page = _open(echo, page_client, clock)
    _missed(page, ma, clock)
    assert "shouldEndSession" not in echo("d", "html").intent("AddFavorite", what="song")


def test_a_page_that_missed_a_handoff_takes_them_again_on_a_new_sse_stream(den, echo, ma, page_client, clock):
    page = _open(echo, page_client, clock)
    _missed(page, ma, clock)
    page.events = _Events(page_client, page.id)    # EventSource reconnects
    page.report()
    page.seek(50000)
    assert ma.replies[-1]["pageLive"] is True
    assert _sources(page.report()) == [ma.pushed[-1]["streamUrl"]]


def test_two_echos_on_one_player_both_show_every_change(den, echo, ma, page_client, clock):
    echos.get("e").pair("Den")
    kitchen = _open(echo, page_client, clock, "e")
    den_page = _open(echo, page_client, clock, "d")
    pages = (den_page, kitchen)

    def shown():
        return [{k: v for m in page.report() for k, v in {**m.get("track", {}), **m.get("set", {})}.items()}
                for page in pages]
    ma.flows_on()
    assert [s.get("title") for s in shown()] == ["A bolond levele"] * 2
    den_page.press({"action": "shuffle", "on": True})
    assert ["shuffle" in s for s in shown()] == [True, True]
    den_page.press({"action": "favorite_song", "on": True})
    assert ["songFavorite" in s for s in shown()] == [True, True]
    ma.items[13:15] = ma.items[14:12:-1]            # a song moved up to play next
    ma.fire(ma_events.EventType.QUEUE_UPDATED, "den")
    assert ["nextTitle" in s for s in shown()] == [True, True]
    ma.player.sleep_timer_expires_at = clock.time() + 900   # MA's sleep timer, set in MA's app
    ma.fire(ma_events.EventType.PLAYER_UPDATED, "Den")
    assert [s.get("sleepAt") for s in shown()] == [int(1000 * ma.player.sleep_timer_expires_at)] * 2


def test_two_echos_on_one_player_both_play_mas_new_stream(den, echo, ma, page_client, clock):
    echos.get("e").pair("Den")
    kitchen = _open(echo, page_client, clock, "e")
    den_page = _open(echo, page_client, clock, "d")
    pages = (den_page, kitchen)
    for page in pages:
        page.report()
    den_page.seek(100000)
    stream = ma.pushed[-1]["streamUrl"]
    assert ma.replies[-1]["pageLive"] is True
    assert [_sources(page.report()) for page in pages] == [[stream], [stream]]


def test_two_echos_on_one_player_both_show_a_spoken_change(den, echo, ma, page_client, clock):
    echos.get("e").pair("Den")
    kitchen = _open(echo, page_client, clock, "e")
    den_page = _open(echo, page_client, clock, "d")
    pages = (den_page, kitchen)

    def shown():
        return [{k: v for m in page.report() for k, v in m.get("set", {}).items()} for page in pages]
    echo("d").intent("AddFavorite", what="song")
    assert ["songFavorite" in s for s in shown()] == [True, True]
    echo("d").intent("AMAZON.ShuffleOnIntent")
    assert [s.get("shuffle") for s in shown()] == [1, 1]
    echo("d").intent("AMAZON.LoopOnIntent")
    assert [s.get("repeat") for s in shown()] == [player.REPEAT_MODES.index("all")] * 2


def test_a_page_button_while_ma_is_down_says_so_and_the_page_stays(den, echo, ma, page_client, clock, monkeypatch):
    page = _open(echo, page_client, clock)
    monkeypatch.setattr(ma_client, "CONNECT_WAIT_S", 0)
    ma_client.attach(None, None)
    page.press({"action": "jump", "item": "q14"})
    got = page.report()
    assert [m.get("toast") for m in got if "toast" in m] == ["Music Assistant did not do it"]
    assert any(m.get("ready") for m in got)
    assert echos.get("d").page.page_id == page.id
    page.tell()


@pytest.mark.parametrize("action", [
    {"action": "move", "item": "q13", "shift": -1, "offset": 2, "limit": 110},
    {"action": "delete", "item": "q13", "offset": 2, "limit": 110},
    {"action": "autoplay", "on": True},
])
def test_a_playlist_or_options_edit_while_ma_is_down_says_so_and_the_page_stays(den, echo, ma, page_client,
                                                                               clock, monkeypatch, action):
    page = _open(echo, page_client, clock)
    page.report()
    monkeypatch.setattr(ma_client, "CONNECT_WAIT_S", 0)
    ma_client.attach(None, None)
    page.press(action)
    assert [m.get("toast") for m in page.report() if "toast" in m] == ["Music Assistant did not do it"]
    assert echos.get("d").page.page_id == page.id


def test_a_next_press_answers_with_a_result(den, ma):
    assert actions.run("d", actions.Next()) == actions.OK


def _back_in_a_moment(ma, seconds=0.1):
    """MA drops and is back seconds later. Returns the thread that brings it back."""
    ma.detach()
    back = threading.Thread(target=lambda: time.sleep(seconds) or ma.attach(), daemon=True)
    back.start()
    return back


_PAGE_COMMANDS = [
    {"action": "queue", "offset": -1, "limit": 110},
    {"action": "move", "item": "q3", "shift": 1},
    {"action": "mix", "item": "q3"},
    {"action": "play_media", "uri": "library://album/7", "option": "add"},
    {"action": "add_to_playlist", "playlist": "2", "name": "Esti dal", "item": "q3"},
    {"action": "favorite", "uri": "library://album/7", "on": True},
    {"action": "transfer", "target": "kitchen", "name": "Kitchen"},
    {"action": "browse", "kind": "search", "text": "esti dal"},
]


@pytest.mark.parametrize("action", _PAGE_COMMANDS, ids=lambda a: a["action"])
def test_a_page_command_waits_for_a_slow_ma(den, echo, ma, page_client, clock, monkeypatch, caplog, action):
    page = _open(echo, page_client, clock)
    page.events.received()
    monkeypatch.setattr(ma_client, "REQUEST_TIMEOUT_S", 0.2)
    back = _back_in_a_moment(ma)
    page.press(action)
    back.join(3)
    assert "Music Assistant did not do it" not in [m.get("toast") for m in page.events.received()]
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_a_new_stream_replaces_an_unconfirmed_pause(den, echo, ma, page_client, clock, control):
    page = _open(echo, page_client, clock)
    assert control("pause")
    assert [m["media"] for m in page.report(deaf=True) if "media" in m] == ["pause"]
    page.seek(100000)
    stream = ma.pushed[-1]["streamUrl"]
    got = [m for _ in range(4) for m in page.report(deaf=True)]
    assert _sources(got) == [stream, stream]
    assert [m["media"] for m in got if "media" in m] == ["play", "play"]


def test_a_resume_is_not_sent_again(den, echo, ma, page_client, clock, control):
    page = _open(echo, page_client, clock)
    del ma.calls[:]
    assert control("resume")
    assert _sources(page.report()) == [ma.pushed[-1]["streamUrl"]]    # the skill has MA resume, with a new stream
    assert [c[0] for c in ma.calls if c[0] != "items"] == ["seek"]
    for _ in range(4):
        page.report()
    assert [c[0] for c in ma.calls if c[0] != "items"] == ["seek"]


def test_a_pause_after_a_new_stream_comes_after_it(den, echo, ma, page_client, clock, control):
    page = _open(echo, page_client, clock)
    page.seek(100000)
    stream = ma.pushed[-1]["streamUrl"]
    assert control("pause")
    got = page.report()
    assert _sources(got) == [stream]
    assert [m["media"] for m in got if "media" in m] == ["play", "pause"]
    assert [m for _ in range(4) for m in page.report()] == []


def test_mas_resume_goes_on_where_the_page_paused(den, echo, ma, page_client, clock, control):
    page = _open(echo, page_client, clock)
    assert page.client.post(f"/bell/html/{page.id}/action",
                            json={"action": "pause", "position": 70000}).status_code == 204
    del ma.calls[:]
    assert control("resume")
    assert [c for c in ma.calls if c[0] == "seek"] == [("seek", "den", 70)]


def test_a_missed_stream_goes_on_at_mas_place(den, echo, ma, page_client, clock, ma_routes_client):
    page = _open(echo, page_client, clock)
    assert page.client.post(f"/bell/html/{page.id}/action",
                            json={"action": "pause", "position": 70000}).status_code == 204
    assert ma_routes_client.post("/ma/push-url", json={
        "playerId": "Den", "streamUrl": "http://ma/flow/9.mp3", "title": "Kicsi, gyere velem",
        "artist": "Kaláka", "album": "Az én szívem",
        "canSkipSpeech": True}).get_json()["pageLive"]
    del ma.calls[:]
    clock.advance(handoff.HANDOFF_SECONDS + 1)
    assert [c for c in ma.calls if c[0] == "seek"] == [("seek", "den", 61)]


def test_mas_stop_pauses_the_open_page(den, echo, ma, page_client, clock):
    page = _open(echo, page_client, clock)
    echo("d").intent("AMAZON.StopIntent")          # MA's stop reaches the Echo as "stop"
    assert [m["media"] for m in page.report() if "media" in m] == ["pause"]


def test_mas_pause_reaches_the_page_and_stays_done(den, echo, ma, page_client, clock, control):
    page = _open(echo, page_client, clock)
    assert control("pause")
    assert [m["media"] for m in page.report() if "media" in m] == ["pause"]
    del ma.calls[:]
    for _ in range(handoff.HANDOFF_SECONDS + 1):
        page.report()
    assert ma.calls == []


def test_after_a_missed_stream_alexa_resume_goes_on_at_mas_place(den, echo, ma, page_client, clock, ma_routes_client):
    page = _open(echo, page_client, clock)
    assert page.client.post(f"/bell/html/{page.id}/action",
                            json={"action": "pause", "position": 70000}).status_code == 204
    assert ma_routes_client.post("/ma/push-url", json={
        "playerId": "Den", "streamUrl": "http://ma/flow/9.mp3", "title": "Kicsi, gyere velem",
        "artist": "Kaláka", "album": "Az én szívem", "canSkipSpeech": True}).get_json()["pageLive"]
    clock.advance(handoff.HANDOFF_SECONDS + 1)
    speaker = echo("d")
    speaker.intent("AMAZON.PauseIntent")
    del ma.calls[:]
    speaker.intent("AMAZON.ResumeIntent")
    assert [c for c in ma.calls if c[0] == "seek"] == [("seek", "den", 61)]


def _news(page):
    return {k: v for m in page.report() for k, v in {**m.get("track", {}), **m.get("set", {})}.items()}


def test_a_mode_change_on_the_last_track_ends_the_queue_with_it(den, echo, ma, page_client, clock):
    page = _open(echo, page_client, clock)
    _news(page)
    ma.queue.repeat_mode = RepeatMode.ALL
    del ma.items[13:]
    page.press({"action": "repeat", "mode": 0})
    news = _news(page)
    assert news["queueEnd"] == 192_000 - 61_000    # the page's stream starts at MA's place in the song
    assert news["nextDuration"] == -1


def test_a_mode_change_leaves_the_hearts_alone(den, echo, ma, page_client, clock, monkeypatch):
    page = _open(echo, page_client, clock)
    _news(page)
    asked = []
    monkeypatch.setattr(view, "favorites", lambda player_id: asked.append(player_id) or {})
    page.press({"action": "shuffle", "on": True})
    assert _news(page)["shuffle"] == 1
    assert asked == []


def _apl_open(echo):
    """Echo d opens the APL page, whose first refresh shows it is open."""
    show = echo("d", "apl")
    page = show.intent("PlayAudio")["directives"][0]["document"]["mainTemplate"]["items"][0]["bellPage"]
    show.user_event("MetadataRefresh", 1, 0, page)
    return show, page


def _apl_media(response):
    """The ControlMedia commands in an answer to the APL page."""
    def walk(commands):
        for c in commands:
            if c.get("type") == "ControlMedia":
                yield c["command"]
            yield from walk(c.get("commands", []))
    return [m for d in response.get("directives", []) for m in walk(d.get("commands", []))]


def test_an_apl_page_pause_takes_mas_pause_handoff(den, echo, ma, control):
    show, page = _apl_open(echo)
    assert control("pause")
    show.user_event("Pause", 70000)
    assert _apl_media(show.user_event("MetadataRefresh", 1, 70000, page)) == []


def test_a_missed_resume_goes_on_where_the_page_paused(den, echo, ma, control, clock):
    show, page = _apl_open(echo)
    show.user_event("Pause", 70000)
    assert control("resume")
    del ma.calls[:]
    clock.advance(handoff.HANDOFF_SECONDS + 1)
    assert [c for c in ma.calls if c[0] == "seek"] == [("seek", "den", 70)]


def _tracks(messages):
    return [(m["track"]["item"], m["track"]["trackOffset"]) for m in messages if "track" in m]


def test_a_new_page_shows_mas_track_where_its_stream_starts(den, echo, ma, page_client, clock):
    page = _open(echo, page_client, clock)
    assert _tracks(page.report()) == [("q12", -61_000)]


def test_a_jump_shows_the_new_track_after_its_stream(den, echo, ma, page_client, clock):
    page = _open(echo, page_client, clock)
    page.report()
    page.press({"action": "jump", "item": "q14"})
    got = page.report()
    assert _sources(got) == [ma.pushed[-1]["streamUrl"]]
    assert _tracks(got) == [("q14", 0)]
    assert next(i for i, m in enumerate(got) if "src" in m) < next(i for i, m in enumerate(got) if "track" in m)
