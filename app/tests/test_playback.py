"""Playback as an Echo, its player page and MA see it, through the skill's public requests."""

import pytest

import json
from types import SimpleNamespace

import requests

from skill.page.html import channel
from skill import devices, handoff
from sse import next_chunk

pytest.importorskip("ask_sdk_core")

STREAM = "http://ma/flow/0.mp3"


@pytest.fixture
def den(ma, store, monkeypatch):
    """Echo d plays MA player Den, whose last stream the skill holds."""
    monkeypatch.setenv("ENABLE_APL", "true")
    monkeypatch.setenv("HTML_PAGE_URL", "https://skill.example/bell/html/")
    devices.set_player_for_device("d", "Den")
    store.save({"playerId": "Den", "streamUrl": STREAM, "title": "Kicsi, gyere velem", "artist": "Kaláka",
                "album": "Az én szívem"})
    yield
    handoff.closed("d")


def _page():
    return channel.start_state(channel.page_of("d"))


def _press(page_client, **action):
    return page_client.post(f"/bell/html/{channel.page_of('d')}/action", json=action).status_code


def _ma(ma):
    return [call for call in ma.calls if call[0] in ("seek", "play_index", "pause", "play", "stop", "next")]


def test_an_apl_page_starts_at_mas_place_in_the_track(den, echo):
    response = echo("d", "apl").intent("PlayAudio")
    [render] = response["directives"]
    item = render["document"]["mainTemplate"]["items"][0]
    assert (item["startOffset"], item["startDuration"]) == (-61000, 192000)
    assert item["audioSources"] == STREAM


def test_an_html_page_starts_at_mas_place_and_ma_resumes_at_the_whole_second(den, echo, ma):
    echo("d", "html").launch()
    assert (_page()["trackOffset"], _page()["trackDuration"]) == (-61000, 192000)
    assert _page()["autoplay"] is False
    assert _ma(ma) == [("seek", "den", 61)]


def test_a_renewed_html_page_starts_where_mas_new_stream_starts(den, echo, ma, clock):
    show = echo("d", "html")
    show.launch()
    clock.advance(0.5)
    show.html_message({"renew": channel.page_of("d")})
    assert _page()["trackOffset"] == -61000
    assert _ma(ma) == [("seek", "den", 61), ("seek", "den", 61)]


def test_the_open_html_page_takes_mas_new_stream(den, echo, ma, page_client):
    echo("d", "html").launch()
    assert ma.replies[-1]["pageLive"] is True
    response = page_client.get(f"/bell/html/{channel.page_of('d')}/events", buffered=False)
    chunks = iter(response.response)
    next(chunks)
    assert json.loads(next_chunk(chunks)[len(b"data: "):])["src"] == ma.pushed[-1]["streamUrl"]
    response.close()


def test_the_page_buttons_pause_play_and_seek_ma(den, echo, ma, page_client):
    echo("d", "html").launch()
    del ma.calls[:]
    assert _press(page_client, action="pause", position=70000) == 204
    assert _ma(ma) == [("pause", "Den")]
    _press(page_client, action="play", position=70000)
    _press(page_client, action="seek", position=100000)
    assert _ma(ma) == [("pause", "Den"), ("seek", "den", 70), ("seek", "den", 100)]


def test_a_page_opened_after_a_page_pause_shows_the_paused_place(den, echo, ma, page_client):
    show = echo("d", "html")
    show.launch()
    _press(page_client, action="pause", position=70000)
    del ma.calls[:]
    show.launch()
    assert _page()["trackOffset"] == -70000
    assert _ma(ma) == [("seek", "den", 70)]


def test_launch_on_a_screenless_echo_leaves_the_stream_to_ma(den, echo, ma):
    response = echo("d", "none").launch()
    assert response == {"shouldEndSession": True}
    assert _ma(ma) == [("seek", "den", 61)]
    assert ma.replies[-1]["pageLive"] is False


def test_mas_echo_of_a_voice_pause_is_not_sent_back(den, echo, ma, clock):
    dot = echo("d", "none")
    response = dot.intent("AMAZON.PauseIntent")
    assert response["directives"] == [{"type": "AudioPlayer.Stop"}]
    dot.controller("Pause")                     # MA's provider makes the Echo pause in turn
    assert _ma(ma) == [("pause", "Den")]
    clock.advance(9)
    dot.controller("Pause")
    assert _ma(ma) == [("pause", "Den"), ("pause", "Den")]


def test_echo_buttons_reach_only_the_echos_own_player(den, echo, ma):
    other = echo("unpaired", "none")
    assert other.controller("Next") == {}
    other.intent("AMAZON.PauseIntent")
    assert _ma(ma) == []
    echo("d", "none").controller("Next")
    assert _ma(ma) == [("next", "Den")]


def test_each_echo_keeps_its_own_stopped_place(den, echo, monkeypatch):
    monkeypatch.setenv("MA_HOSTNAME", "music.example")
    monkeypatch.setenv("SKIP_URL_VALIDATION", "true")
    unpaired = echo("unpaired", "none")
    unpaired.audio_event("PlaybackStopped", 5000, token=STREAM)
    echo("d", "none").audio_event("PlaybackStopped", 70000, token=STREAM)
    play = unpaired.intent("AMAZON.ResumeIntent")["directives"][0]
    assert play["audioItem"]["stream"]["offsetInMilliseconds"] == 5000


def test_the_echos_play_button_resumes_ma(den, echo, ma):
    ma.pause()
    assert echo("d", "none").controller("Play") == {}
    assert _ma(ma) == [("seek", "den", 61)]


def _report(page_client, at, playing=True):
    page_client.post(f"/bell/html/{channel.page_of('d')}/state", json={"seen": 0, "at": at, "playing": playing})


def test_alexa_pause_pauses_the_page_and_ma_as_its_button_does(den, echo, ma, page_client):
    show = echo("d", "html")
    show.launch()
    _report(page_client, 9000)                  # the page plays MA's stream from 61 s into the track
    del ma.calls[:]
    show.intent("AMAZON.PauseIntent")
    assert _ma(ma) == [("pause", "Den")]
    show.launch()
    assert _page()["trackOffset"] == -70000


def test_alexa_pause_keeps_the_apl_page_refreshing(den, echo):
    show = echo("d", "apl")
    show.intent("PlayAudio")
    [directive] = show.intent("AMAZON.PauseIntent")["directives"]
    assert directive["commands"][0]["command"] == "pause"
    assert directive["commands"][-1]["arguments"][0] == "MetadataRefresh"


def test_alexa_pause_counts_the_time_the_page_played_since_its_report(den, echo, ma, page_client, clock):
    show = echo("d", "html")
    show.launch()
    _report(page_client, 9000)
    clock.advance(1)
    show.intent("AMAZON.PauseIntent")
    del ma.calls[:]
    show.launch()
    assert _ma(ma) == [("seek", "den", 71)]


def test_alexa_pause_counts_the_time_since_the_report_up_to_the_tracks_end(den, echo, ma, page_client, clock):
    ma.queue.next_item = None
    show = echo("d", "html")
    show.launch()
    _report(page_client, 129000)                # 190 s into the 192 s track
    clock.advance(5)
    show.intent("AMAZON.PauseIntent")
    show.launch()
    assert _page()["trackOffset"] == -192000


def test_mas_echo_of_a_page_pause_keeps_the_buttons_place(den, echo, ma, page_client):
    show = echo("d", "html")
    show.launch()
    _report(page_client, 9000)
    _press(page_client, action="pause", position=72000)
    show.intent("AMAZON.PauseIntent")           # MA's provider makes the Echo hear "pause" in turn
    del ma.calls[:]
    show.launch()
    assert _ma(ma) == [("seek", "den", 72)]


def test_alexa_pause_after_the_page_closed_leaves_ma_its_own_place(den, echo, ma, page_client, clock):
    show = echo("d", "html")
    show.launch()
    clock.advance(0.5)
    _report(page_client, 9000)
    show.session_ended()
    show.intent("AMAZON.PauseIntent")
    del ma.calls[:]
    show.launch()
    assert _ma(ma) == [("seek", "den", 61)]


def test_alexa_resume_goes_on_where_the_echo_stopped_when_ma_cannot_resume(den, echo, ma, monkeypatch):
    monkeypatch.setenv("MA_HOSTNAME", "music.example")
    monkeypatch.setenv("SKIP_URL_VALIDATION", "true")
    ma.queue.current_item = None                # MA has no track to resume
    dot = echo("d", "none")
    token = dot.intent("AMAZON.ResumeIntent")["directives"][0]["audioItem"]["stream"]["token"]
    assert token.startswith("https://music.example/")
    dot.audio_event("PlaybackStopped", 70000, token=token)
    play = dot.intent("AMAZON.ResumeIntent")["directives"][0]
    assert play["audioItem"]["stream"]["offsetInMilliseconds"] == 70000


@pytest.mark.parametrize("check", ["skipped", "refused", "unreachable"])
def test_a_stream_url_is_logged_without_its_query(ma, store, echo, monkeypatch, caplog, check):
    monkeypatch.setenv("MA_HOSTNAME", "music.example")
    monkeypatch.setenv("SKIP_URL_VALIDATION", "true" if check == "skipped" else "false")

    def head(url, **kwargs):
        if check == "unreachable":
            raise requests.ConnectionError(f"Max retries exceeded with url: {url}")
        return SimpleNamespace(status_code=404)
    monkeypatch.setattr(requests, "head", head)
    monkeypatch.setattr(requests, "get", head)
    devices.set_player_for_device("d", "Den")
    store.save({"playerId": "Den", "streamUrl": "http://ma/flow/den/12.mp3?ts=1761800000&sig=abc",
                "title": "Kicsi, gyere velem", "artist": "Kaláka", "album": "Az én szívem"})
    ma.queue.current_item = None                # MA has no track to resume
    caplog.set_level("INFO")
    echo("d", "none").launch()
    assert "music.example/flow/den/12.mp3" in caplog.text
    assert "sig=abc" not in caplog.text
