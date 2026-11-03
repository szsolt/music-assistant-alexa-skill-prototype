import json
import os

import pytest

pytest.importorskip("ask_sdk_core")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")    # the skill makes AWS clients on import

from music_assistant_models.enums import PlaybackState

from skill import devices, sleep_timer
from skill.alexa import handlers
from skill.ma import library, player
from skill import echo as echos
from skill import handoff, watch
from skill.page.html import channel


@pytest.fixture
def page(echo, monkeypatch):
    """Echo d shows an open player page. Yields Echo d."""
    monkeypatch.setenv("ENABLE_APL", "true")
    handoff.heard_from("d")
    yield echo("d", "apl")
    handoff.closed("d")


def test_unknown_request_keeps_the_open_page_quietly(page):
    response = page.intent("AMAZON.FallbackIntent")
    assert "outputSpeech" not in response and "shouldEndSession" not in response


def test_unknown_request_without_a_page_says_so(echo):
    response = echo("d", "none").intent("AMAZON.FallbackIntent")
    assert "outputSpeech" in response and response["shouldEndSession"] is True


def test_forced_voice_command_does_nothing(page, monkeypatch):
    monkeypatch.setattr(library, "find", lambda *a: pytest.fail("searched MA"))
    # "turn off lamp": Alexa's queue command, with no kind word.
    response = page.intent("Queue", kind=None, name="off lamp")
    assert "outputSpeech" not in response and "shouldEndSession" not in response


@pytest.fixture
def reopen(ma, echo, monkeypatch):
    """MA plays on Den, paired with Echo d only. Yields Echo d, with a screen."""
    monkeypatch.setenv("ENABLE_APL", "true")
    devices.set_player_for_device("d", "Den")
    return echo("d", "apl")


def _resends(ma):
    return [call for call in ma.calls if call[0] == "seek"]


def test_page_alexa_closed_comes_back_once_a_minute(reopen, ma, clock):
    reopen.session_ended("INTERNAL_SERVICE_ERROR")
    assert _resends(ma) == []
    clock.advance(watch.REOPEN_DELAY_S)
    assert _resends(ma) == [("seek", "den", 61)]                # where MA is now
    reopen.session_ended("INTERNAL_SERVICE_ERROR")
    clock.advance(watch.REOPEN_DELAY_S)
    assert len(_resends(ma)) == 1


def test_page_closed_by_hand_or_by_a_skill_error_stays_closed(reopen, ma, clock):
    reopen.session_ended()
    reopen.session_ended("INVALID_RESPONSE")
    clock.advance(watch.REOPEN_DELAY_S)
    assert _resends(ma) == []


def test_page_alexa_closed_stays_closed_when_ma_has_stopped(reopen, ma, clock):
    ma.pause()
    reopen.session_ended("INTERNAL_SERVICE_ERROR")
    clock.advance(watch.REOPEN_DELAY_S)
    assert _resends(ma) == []
    # Not counted as a reopen: a later close while MA plays still reopens.
    ma.queue.state = PlaybackState.PLAYING
    reopen.session_ended("INTERNAL_SERVICE_ERROR")
    clock.advance(watch.REOPEN_DELAY_S)
    assert _resends(ma) == [("seek", "den", 61)]


def test_page_alexa_closed_stays_closed_for_a_shared_player_or_a_new_page(reopen, ma, clock):
    devices.set_player_for_device("e", "Den")
    reopen.session_ended("INTERNAL_SERVICE_ERROR")
    clock.advance(watch.REOPEN_DELAY_S)
    devices.save_mapping({"d": "Den"})
    reopen.session_ended("INTERNAL_SERVICE_ERROR")
    handoff.heard_from("d")                                            # a new page came meanwhile
    clock.advance(watch.REOPEN_DELAY_S)
    assert _resends(ma) == []


@pytest.fixture
def move(page, monkeypatch):
    """Echo d plays MA player p; the kitchen and a lamp can take the music. Yields the moves MA got."""
    devices.set_player_for_device("d", "p")
    monkeypatch.setattr(player, "players", lambda: [("p", "Here"), ("k", "Kitchen"), ("l", "Lamp")])
    moves = []
    monkeypatch.setattr(player, "move", lambda player_id, target_id: moves.append((player_id, target_id)))
    yield moves


def test_move_sends_the_music_on_and_closes_this_page(move, page):
    response = page.intent("MoveMusic", player=("kitchen", "Kitchen"))
    assert move == [("p", "k")]
    assert "Kitchen" in response["outputSpeech"]["ssml"] and response["shouldEndSession"] is True
    assert [d["type"] for d in response["directives"]] == ["AudioPlayer.Stop"]
    assert not handoff.is_live("d") and handoff.takes_silent_pause("d")  # MA's pause for d: no hand-off, no speech


def test_move_to_an_unknown_player_keeps_playing_here(move, page):
    response = page.intent("MoveMusic", player="garage")
    assert move == []
    assert "garage" in response["outputSpeech"]["ssml"] and response.get("shouldEndSession") is not True


def test_move_to_the_player_already_playing_says_so(move, page):
    response = page.intent("MoveMusic", player=("here", "Here"))
    assert move == [] and "already" in response["outputSpeech"]["ssml"]


def test_like_something_other_than_song_album_artist_does_nothing(page, monkeypatch):
    monkeypatch.setattr(library, "favorite_playing", lambda *a: pytest.fail("changed a favourite"))
    response = page.intent("AddFavorite", what="Queen")
    assert "outputSpeech" not in response and "shouldEndSession" not in response


@pytest.fixture
def sleep(monkeypatch):
    """Echo d plays MA player p. Yields the timers the skill started."""
    devices.set_player_for_device("d", "p")
    started = []
    monkeypatch.setattr(sleep_timer, "start", lambda device_id, seconds, on_due: started.append((device_id, seconds)))
    yield started


def test_sleep_timer_starts_and_says_when(sleep, echo):
    response = echo("d", "none").intent("SleepTimer", duration="PT1H30M")
    assert sleep == [("d", 5400)]
    assert "1 hour and 30 minutes" in response["outputSpeech"]["ssml"]


def test_sleep_timer_without_a_length_asks_for_one(sleep, echo):
    response = echo("d", "none").intent("SleepTimer", duration=None)
    assert sleep == [] and "How long" in response["outputSpeech"]["ssml"]


def test_cancel_sleep_timer_says_if_there_was_none(monkeypatch, echo):
    monkeypatch.setattr(sleep_timer, "cancel", lambda device_id: False)
    response = echo("d", "none").intent("CancelSleepTimer")
    assert "no sleep timer" in response["outputSpeech"]["ssml"]


@pytest.mark.parametrize("sent, paused", [(True, True), (False, False)])
def test_play_leaves_ma_alone_when_it_sent_a_stream_or_plays(ma, echo, monkeypatch, sent, paused):
    monkeypatch.setenv("ENABLE_APL", "true")
    devices.set_player_for_device("d", "Den")
    if paused:
        ma.pause()
    from skill import store
    monkeypatch.setattr(store, "sent_since", lambda player_id, seconds: sent)
    handoff.heard_from("d")
    echo("d", "apl").intent("PlayAudio")
    handoff.closed("d")
    assert not [call for call in ma.calls if call[0] in ("seek", "play", "resume")]


def test_said_play_resumes_ma_on_the_open_page(ma, echo, monkeypatch):
    monkeypatch.setenv("ENABLE_APL", "true")
    devices.set_player_for_device("d", "Den")
    ma.pause()
    handoff.heard_from("d")
    response = echo("d", "apl").intent("PlayAudio")
    assert [call for call in ma.calls if call[0] == "seek"] == [("seek", "den", 61)]
    assert [d["type"] for d in response["directives"]] == ["Alexa.Presentation.APL.RenderDocument"]
    assert ma.pushed[-1]["streamUrl"] in json.dumps(response)
    handoff.closed("d")


def test_open_does_not_play_another_players_stream(ma, echo, store):
    ma.queue.current_item = None
    devices.set_player_for_device("d", "Den")
    store.save({"playerId": "Kitchen", "streamUrl": "http://ma/flow/kitchen.mp3"})
    response = echo("d", "none").launch()
    assert not response.get("directives") and "nothing to play" in response["outputSpeech"]["ssml"]


def test_open_does_not_play_the_stream_another_echo_played(ma, echo, store, monkeypatch):
    monkeypatch.setenv("MA_HOSTNAME", "music.example")
    monkeypatch.setenv("SKIP_URL_VALIDATION", "true")
    ma.queue.current_item = None
    devices.set_player_for_device("d", "Den")
    devices.set_player_for_device("k", "Kitchen")
    store.save({"playerId": "Kitchen", "streamUrl": "http://ma/flow/kitchen.mp3"})
    assert "kitchen.mp3" in json.dumps(echo("k", "none").launch())
    response = echo("d", "none").launch()
    assert not response.get("directives") and "nothing to play" in response["outputSpeech"]["ssml"]


def test_watchdog_waits_longer_for_an_html_page_and_stops_when_it_plays(ma, clock, echo):
    # After an idle night the Show can hold an HTML page's loads for ~10 s
    # while its video already plays.
    for device_id in ("apl-show", "html-show"):
        devices.set_player_for_device(device_id, "Den")
    page = echos.open_page("html-show", "html").page_id
    watch.new_page(page)
    watch.watch_page("apl-show")
    watch.watch_page("html-show")
    assert clock.pending() == [watch.PAGE_WATCHDOG_S, watch.HTML_PAGE_WATCHDOG_S]
    channel.report(page, 0, 0, False)
    assert clock.pending() == [watch.PAGE_WATCHDOG_S, watch.HTML_PAGE_WATCHDOG_S]
    channel.report(page, 0, 700, True)                                 # its report says it plays
    assert clock.pending() == [watch.PAGE_WATCHDOG_S]
    echo("apl-show", "apl").session_ended()                            # its page closed
    assert clock.pending() == []


@pytest.fixture
def html_launch(ma, echo, store, monkeypatch):
    """Echo d, paired with Den, gets the HTML page. MA pushed Den's song before. Yields Echo d."""
    monkeypatch.setenv("ENABLE_APL", "true")
    monkeypatch.setenv("HTML_PAGE_URL", "https://skill.example/bell/html/")
    devices.set_player_for_device("d", "Den")
    store.save({"playerId": "Den", "streamUrl": "http://ma/flow/0.mp3", "title": "Kicsi, gyere velem",
                "artist": "Kaláka", "album": "Az én szívem"})
    yield echo("d", "html")
    handoff.closed("d")


def _resumes(ma):
    return [call for call in ma.calls if call[0] in ("seek", "play_index")]


def test_open_sends_the_html_page_and_ma_hands_its_stream_to_it(html_launch, ma):
    response = html_launch.launch()
    assert [d["type"] for d in response["directives"]] == ["Alexa.Presentation.HTML.Start"]
    assert "shouldEndSession" not in response
    assert _resumes(ma) == [("seek", "den", 61)]


def test_open_without_ma_resume_drops_the_html_page(html_launch, ma, store):
    ma.queue.current_item = None
    store.save({"playerId": "Kitchen", "streamUrl": "http://ma/flow/kitchen.mp3"})
    response = html_launch.launch()
    assert not response.get("directives") and "nothing to play" in response["outputSpeech"]["ssml"]
    assert not handoff.is_live("d")


@pytest.fixture
def renew(html_launch, ma, clock):
    """Echo d shows an HTML page while MA plays on Den. Yields its page id."""
    html_launch.launch()
    clock.advance(0.5)
    del ma.calls[:]
    return channel.page_of("d")


def test_a_playing_page_is_replaced_by_a_new_one_when_it_asks(renew, html_launch, ma):
    response = html_launch.html_message({"renew": renew})
    assert [d["type"] for d in response["directives"]] == ["Alexa.Presentation.HTML.Start"]
    assert "shouldEndSession" not in response
    assert _resumes(ma) == [("seek", "den", 61)]


def test_a_page_replaced_at_a_track_end_starts_the_next_track(renew, html_launch, ma):
    html_launch.html_message({"renew": renew, "item": "q13"})
    assert _resumes(ma) == [("play_index", "den", "q13")]


def test_a_paused_page_is_not_replaced(renew, html_launch, ma):
    ma.pause()
    response = html_launch.html_message({"renew": renew})
    assert _resumes(ma) == [] and not response.get("directives")


def test_an_old_page_is_not_replaced(renew, html_launch, ma):
    response = html_launch.html_message({"renew": "p-old"})
    assert _resumes(ma) == [] and not response.get("directives")


def test_a_page_the_skill_lost_on_a_restart_is_replaced(renew, html_launch, ma, monkeypatch):
    monkeypatch.setattr(channel, "page_of", lambda device_id: None)
    html_launch.html_message({"renew": "p-old"})
    assert _resumes(ma) == [("seek", "den", 61)]


def test_a_tapped_playlist_song_plays_from_its_start(renew, ma, page_client):
    assert page_client.post(f"/bell/html/{renew}/action", json={"action": "jump", "item": "q7"}).status_code < 300
    assert _resumes(ma) == [("play_index", "den", "q7")]


def test_whats_playing_names_only_the_current_song(ma, echo):
    from skill import devices
    devices.set_player_for_device("d", "Den")
    ma.queue.current_item = None
    said = echo("d", "none").intent("WhatsPlaying")["outputSpeech"]["ssml"]
    assert "Nothing is playing" in said
