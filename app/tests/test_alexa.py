"""The Alexa side through Alexa's requests: what each Echo plays, and replies to an Echo with an open page."""

import json

import pytest

from skill import devices, handoff, watch
from skill import echo as echos
from skill.ma import client as ma_client

pytest.importorskip("ask_sdk_core")

DEN = "http://ma/flow/den/12.mp3"
KITCHEN = "http://ma/flow/kitchen/3.mp3"


@pytest.fixture
def two_players(ma, store, monkeypatch):
    """A screenless Echo d plays MA player Den; MA then sent Kitchen a stream. MA can't resume Den."""
    monkeypatch.setenv("MA_HOSTNAME", "music.example")
    monkeypatch.setenv("SKIP_URL_VALIDATION", "true")
    devices.set_player_for_device("d", "Den")
    devices.set_player_for_device("k", "Kitchen")
    store.save({"playerId": "Den", "streamUrl": DEN, "title": "Kicsi, gyere velem", "artist": "Kaláka",
                "album": "Az én szívem"})
    store.save({"playerId": "Kitchen", "streamUrl": KITCHEN, "title": "Ringató", "artist": "Kaláka",
                "album": "Az én szívem"})
    ma.queue.current_item = None


def _played(response):
    return [d["audioItem"]["stream"]["url"] for d in response.get("directives", []) if d["type"] == "AudioPlayer.Play"]


def test_a_screenless_echo_resumes_its_own_players_stream(two_players, echo):
    assert _played(echo("d", "none").intent("AMAZON.ResumeIntent")) == ["https://music.example/flow/den/12.mp3"]


def test_a_screenless_echos_play_button_plays_its_own_players_stream(two_players, echo):
    assert _played(echo("d", "none").controller("Play")) == ["https://music.example/flow/den/12.mp3"]


def test_a_screenless_echo_restarts_its_own_players_stream_after_a_failure(two_players, echo):
    response = echo("d", "none").audio_event("PlaybackFailed", token="https://music.example/flow/den/11.mp3",
                                             error={"type": "MEDIA_ERROR_UNKNOWN", "message": "test"})
    assert _played(response) == ["https://music.example/flow/den/12.mp3"]


def test_a_screenless_echo_queues_its_own_players_next_stream(two_players, echo):
    response = echo("d", "none").audio_event("PlaybackNearlyFinished",
                                             token="https://music.example/flow/den/11.mp3")
    assert _played(response) == ["https://music.example/flow/den/12.mp3"]
    assert "kitchen" not in json.dumps(response)


@pytest.fixture(params=["apl", "html"])
def show(request, ma, store, echo, page_client, monkeypatch):
    """Echo d shows its open player page (APL or HTML) while MA plays Den's album on it."""
    monkeypatch.setenv("ENABLE_APL", "true")
    if request.param == "html":
        monkeypatch.setenv("HTML_PAGE_URL", "https://skill.example/bell/html/")
    devices.set_player_for_device("d", "Den")
    store.save({"playerId": "Den", "streamUrl": "http://ma/flow/0.mp3", "title": "Kicsi, gyere velem",
                "artist": "Kaláka", "album": "Az én szívem"})
    show = echo("d", request.param)
    show.page = lambda: getattr(echos.get("d").page, "page_id", None)
    show.refresh = (lambda: show.user_event("MetadataRefresh", 1, 5000, show.page())) if request.param == "apl" \
        else (lambda: page_client.post(f"/bell/html/{show.page()}/state", json={"seen": 9, "at": 5000,
                                                                                  "playing": True}))
    if request.param == "apl":
        show.intent("PlayAudio")
    else:
        show.launch()
    show.refresh()
    yield show
    handoff.closed("d")


ASKED = {
    "open": lambda e: e.launch(),
    "play audio": lambda e: e.intent("PlayAudio"),
    "help": lambda e: e.intent("AMAZON.HelpIntent"),
    "unknown words": lambda e: e.intent("AMAZON.FallbackIntent"),
    "an intent the skill has no handler for": lambda e: e.intent("AMAZON.NavigateHomeIntent"),
    "next": lambda e: e.intent("AMAZON.NextIntent"),
    "previous": lambda e: e.intent("AMAZON.PreviousIntent"),
    "pause": lambda e: e.intent("AMAZON.PauseIntent"),
    "resume": lambda e: e.intent("AMAZON.ResumeIntent"),
    "start over": lambda e: e.intent("AMAZON.StartOverIntent"),
    "shuffle on": lambda e: e.intent("AMAZON.ShuffleOnIntent"),
    "repeat off": lambda e: e.intent("AMAZON.LoopOffIntent"),
    "like this song": lambda e: e.intent("AddFavorite"),
    "play an album": lambda e: e.intent("PlayAlbum", album="az en szivem", mode=("play", "play")),
    "queue an album": lambda e: e.intent("Queue", kind=("album", "album"), name="az en szivem"),
    "play something": lambda e: e.intent("PlayAlbum", mode=("play", "play")),
    "shuffle my library": lambda e: e.intent("PlayRandom"),
    "what's playing": lambda e: e.intent("WhatsPlaying"),
    "move the music to a player that isn't there": lambda e: e.intent("MoveMusic", player="garage"),
    "move the music where it plays": lambda e: e.intent("MoveMusic", player="den"),
    "sleep timer": lambda e: e.intent("SleepTimer", duration="PT30M"),
    "sleep timer without a length": lambda e: e.intent("SleepTimer"),
    "cancel the sleep timer": lambda e: e.intent("CancelSleepTimer"),
    "the echo's play button": lambda e: e.controller("Play"),
    "the echo's next button": lambda e: e.controller("Next"),
    "the echo's previous button": lambda e: e.controller("Previous"),
    "the echo's pause button": lambda e: e.controller("Pause"),
    "a page button": lambda e: e.user_event("Next"),
    "the page asks for a new page": lambda e: e.html_message({"renew": e.page()}),
    "playback started": lambda e: e.audio_event("PlaybackStarted"),
    "playback stopped": lambda e: e.audio_event("PlaybackStopped"),
    "playback nearly finished": lambda e: e.audio_event("PlaybackNearlyFinished"),
    "playback finished": lambda e: e.audio_event("PlaybackFinished"),
    "playback failed": lambda e: e.audio_event("PlaybackFailed",
                                               error={"type": "MEDIA_ERROR_UNKNOWN", "message": "test"}),
    "alexa reports an error in a reply": lambda e: e.send({
        "type": "System.ExceptionEncountered", "error": {"type": "INVALID_RESPONSE", "message": "test"},
        "cause": {"requestId": "amzn1.echo-api.request.0"}}),
}
# One of each kind of request: with MA down, they share one error path.
ASKED_OF_MA = ["open", "next", "pause", "play an album", "what's playing", "shuffle on", "like this song", "sleep timer",
               "the echo's play button", "a page button", "playback nearly finished", "playback failed"]


def _shown(response, before):
    """The page the Echo shows after response: a new one it was sent, else the one it had."""
    for directive in response.get("directives", []):
        if directive["type"] == "Alexa.Presentation.HTML.Start":
            return directive["request"]["uri"].rsplit("/", 1)[-1]
        if directive["type"] == "Alexa.Presentation.APL.RenderDocument":
            return directive["document"]["mainTemplate"]["items"][0]["bellPage"]
    return before


def _keeps_page(show, asked):
    """The reply leaves the Echo's page open, and the skill holds the page the Echo shows."""
    before = show.page()
    response = ASKED[asked](show)
    assert response.get("shouldEndSession") is not True
    assert show.page() == _shown(response, before)


@pytest.mark.parametrize("show", ["html"], indirect=True)
@pytest.mark.parametrize("asked", ASKED)
def test_a_reply_keeps_the_open_page(show, asked):
    _keeps_page(show, asked)


@pytest.mark.parametrize("show", ["html"], indirect=True)
@pytest.mark.parametrize("asked", ASKED_OF_MA)
def test_a_reply_keeps_the_open_page_when_ma_is_down(show, ma, asked, monkeypatch):
    monkeypatch.setattr(ma_client, "CONNECT_WAIT_S", 0)
    ma_client.attach(None, None)
    _keeps_page(show, asked)


@pytest.mark.parametrize("show", ["html"], indirect=True)
@pytest.mark.parametrize("asked", ["the echo's play button", "the echo's pause button", "the echo's next button",
                                   "playback failed"])
def test_the_echos_buttons_leave_the_page_on_screen_working(show, asked, page_client, monkeypatch):
    shown = show.page()
    monkeypatch.setattr(ma_client, "CONNECT_WAIT_S", 0)
    ma_client.attach(None, None)
    ASKED[asked](show)
    assert page_client.post(f"/bell/html/{shown}/state", json={"seen": 9, "at": 6000, "playing": True}).status_code == 204
    assert page_client.post(f"/bell/html/{shown}/action", json={"action": "shuffle", "on": True}).status_code == 204


@pytest.mark.parametrize("show", ["apl"], indirect=True)
def test_the_apl_pages_refresh_keeps_it_open_and_updates_it(show):
    response = show.refresh()
    assert response.get("shouldEndSession") is not True
    commands = [command["type"] for directive in response["directives"] for command in directive["commands"]]
    assert "SetValue" in commands and commands[-1] == "SendEvent"   # its values, then its next refresh


@pytest.mark.parametrize("show", ["html"], indirect=True)
@pytest.mark.parametrize("asked", ["next", "start over", "resume", "play an album", "shuffle my library"])
def test_a_reply_keeps_the_open_page_when_mas_new_stream_comes_late(show, ma, asked):
    ma.push = None
    _keeps_page(show, asked)


def test_a_reply_keeps_the_open_page_when_ma_has_nothing_for_it(show, ma, store):
    ma.queue.current_item = None
    store.save({"playerId": "Kitchen", "streamUrl": KITCHEN})
    _keeps_page(show, "open")
    if show.screen == "html":
        assert show.refresh().status_code == 204


@pytest.mark.parametrize("show", ["html"], indirect=True)
@pytest.mark.parametrize("asked", ["open", "next", "start over", "like this song", "what's playing"])
def test_a_reply_keeps_the_open_page_when_the_echo_lost_its_player(show, asked):
    devices.save_mapping({"k": "Den"})
    _keeps_page(show, asked)


@pytest.mark.parametrize("asked", ["AMAZON.StopIntent", "AMAZON.CancelIntent"])
def test_stop_and_cancel_close_the_open_page(show, asked):
    assert show.intent(asked)["shouldEndSession"] is True


def _transport(ma):
    return [call for call in ma.calls if call[0] in ("play", "pause", "stop", "next", "play_index", "seek")]


def test_the_end_of_the_queue_stops_ma_and_mas_stop_closes_the_page(show, ma, page_client):
    del ma.calls[:]
    if show.screen == "apl":
        assert show.user_event("QueueEnded").get("shouldEndSession") is not True
    else:
        assert page_client.post(f"/bell/html/{show.page()}/action", json={"action": "queue_ended"}).status_code == 204
    assert _transport(ma) == [("stop", "Den")]
    # MA's stop reaches the Echo as "stop"
    assert show.intent("AMAZON.StopIntent")["shouldEndSession"] is True
    assert _transport(ma) == [("stop", "Den")]


@pytest.mark.parametrize("show", ["html"], indirect=True)
@pytest.mark.parametrize("held", [False, True])
def test_a_page_whose_renew_ma_could_not_play_renews_once_ma_plays_again(clock, show, ma, held, page_client,
                                                                         monkeypatch):
    shown = show.page()
    monkeypatch.setattr(ma_client, "CONNECT_WAIT_S", 0)
    ma_client.attach(None, None)
    clock.advance(watch.GONE_SECONDS)
    if not held:
        watch.sweep()
    assert "directives" not in show.html_message({"renew": shown})
    ma_client.attach(ma, ma._loop)
    renewed = show.html_message({"renew": shown})
    assert [d["type"] for d in renewed["directives"]] == ["Alexa.Presentation.HTML.Start"]
    new = _shown(renewed, shown)
    assert page_client.post(f"/bell/html/{new}/state", json={"seen": 0, "at": 0, "playing": True}).status_code == 204
    assert page_client.post(f"/bell/html/{new}/action", json={"action": "shuffle", "on": True}).status_code == 204
