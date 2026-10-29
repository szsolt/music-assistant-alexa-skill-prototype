import json
import os
import time

import pytest

pytest.importorskip("ask_sdk_core")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")    # the skill makes AWS clients on import

from ask_sdk_core.response_helper import ResponseFactory
from ask_sdk_core.skill_builder import SkillBuilder
from ask_sdk_core.utils import is_request_type
from ask_sdk_model.interfaces.alexa.presentation.apl import (
    ExecuteCommandsDirective, ControlMediaCommand, MediaCommandType)
from ask_sdk_model.interfaces.alexa.presentation.html import StartDirective
from ask_sdk_model.interfaces.audioplayer import StopDirective

from skill import apl, bell, device_mapping, html_page, ma_control, ma_voice
from skill import live_page as lp

URL = "https://skill.example/bell/html/"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setenv("APL_BELL_URL", "")
    monkeypatch.setenv("HTML_PAGE_URL", URL)
    bell._pages.clear()
    bell._page_of.clear()
    html_page._started.clear()
    html_page._queues.clear()
    html_page._presses.clear()
    html_page._waiting.clear()
    monkeypatch.setattr(time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(bell, "_handlers", {})
    monkeypatch.setattr(bell, "_run", lambda fn, *args: fn(*args))
    html_page.want(True)
    yield
    html_page.want(False)
    lp.closed("d")


def _html_page(device_id="d"):
    page = bell.new_page(device_id, always=True)
    html_page.started(page, {"primaryText": "Song"}, True)
    return page


def test_html_page_gets_a_bell_page_without_the_apl_bell():
    assert bell.new_page("d") == ""
    page = bell.new_page("d", always=True)
    assert page and bell.device_of(page) == "d" and bell.page_of("d") == page


def test_add_apl_sends_the_html_page(monkeypatch):
    monkeypatch.setattr(apl, "_get_metadata", lambda device_id: {
        "audioSources": "https://ma.example/flow.mp3", "primaryText": "Song", "secondaryText": "Artist",
        "coverImageSource": "https://ma.example/cover.jpg"})
    monkeypatch.setattr(apl, "_start_track_time", None)
    builder = ResponseFactory()
    apl.add_apl(builder, start_paused=True, device_id="d")
    [directive] = builder.response.directives
    page = bell.page_of("d")
    assert isinstance(directive, StartDirective)
    assert directive.request.uri == URL + page
    assert directive.configuration.timeout_in_seconds == 1800
    start = html_page.start_state(page)
    assert start["title"] == "Song" and start["artist"] == "Artist" and start["autoplay"] is False
    assert start["stream"] == "https://ma.example/flow.mp3"
    assert start["cover"] == "https://ma.example/cover.jpg"


def test_unknown_page_has_no_start_state():
    assert html_page.start_state("nope") is None


def test_reply_commands_become_one_message():
    assert html_page.message([
        {"type": "SetValue", "componentId": "AudioPlayerRoot", "property": "bellSeen", "value": 2},
        {"type": "Parallel", "commands": [
            {"type": "SetValue", "property": "nextSecondary", "value": "Artist"},
            {"type": "SetValue", "componentId": "Audio_PrimaryTextLong", "property": "text", "value": "Song"},
            {"type": "SetValue", "componentId": "Audio_CoverArt", "property": "imageSource", "value": "c.jpg"},
            {"type": "SetValue", "componentId": "Busy_Overlay", "property": "display", "value": "none"}]},
        {"type": "PlayMedia", "source": [{"url": "https://ma.example/flow2.mp3"}]},
    ]) == {"set": {"seen": 2, "nextArtist": "Artist", "title": "Song", "cover": "c.jpg"},
           "ready": True, "src": "https://ma.example/flow2.mp3", "media": "play"}
    assert html_page.message([{"type": "ControlMedia", "command": "pause"}]) == {"media": "pause"}
    assert html_page.message([{"type": "Idle"}]) is None


def test_reply_commands_go_to_the_html_page():
    page = _html_page()
    stream = html_page.stream(page)
    assert next(stream) == "retry: 1000\n\n"
    response = ResponseFactory().add_directive(StopDirective()).add_directive(ExecuteCommandsDirective(
        commands=[ControlMediaCommand(command=MediaCommandType.pause, component_id="videoPlayer"),
                  {"type": "SetValue", "componentId": "AudioPlayerRoot", "property": "bellSeen", "value": 2}],
        token="playbackToken")).response
    html_page.take_commands("d", response)
    assert [type(d) for d in response.directives] == [StopDirective]
    assert json.loads(next(stream)[len("data: "):]) == {"media": "pause", "set": {"seen": 2}}
    stream.close()
    assert page not in html_page._queues


def test_reply_commands_wait_for_the_page_to_connect():
    page = _html_page()
    response = ResponseFactory().add_directive(ExecuteCommandsDirective(commands=[
        {"type": "PlayMedia", "componentId": "videoPlayer", "source": "https://ma.example/flow2.mp3"}],
        token="playbackToken")).response
    html_page.take_commands("d", response)
    stream = html_page.stream(page)
    next(stream)
    assert json.loads(next(stream)[len("data: "):]) == {"src": "https://ma.example/flow2.mp3", "media": "play"}
    stream.close()


def test_reply_commands_stay_for_an_apl_page(monkeypatch):
    monkeypatch.setenv("APL_BELL_URL", "https://bell.example/bell/")
    bell.new_page("d")
    response = ResponseFactory().add_directive(ExecuteCommandsDirective(commands=[], token="t")).response
    html_page.take_commands("d", response)
    assert len(response.directives) == 1


def test_stream_ends_when_the_page_is_replaced():
    page = _html_page()
    stream = html_page.stream(page)
    next(stream)
    bell.new_page("d", always=True)
    assert list(stream) == []


def _skill(seen):
    sb = SkillBuilder()
    sb.skill_id = "amzn1.ask.skill.test"

    @sb.request_handler(can_handle_func=is_request_type("Alexa.Presentation.APL.UserEvent"))
    def event(handler_input):
        envelope = handler_input.request_envelope
        seen.append((envelope.context.system.device.device_id, envelope.request.arguments,
                     envelope.context.system.device.supported_interfaces.alexa_presentation_html is not None))
        return handler_input.response_builder.response
    return sb.create()


def test_page_buttons_run_as_apl_user_events_from_its_echo():
    page = _html_page()
    seen = []
    skill = _skill(seen)
    assert html_page.act(skill, page, {"action": "next"})
    assert html_page.act(skill, page, {"action": "seek", "position": 61_000})
    assert html_page.act(skill, page, {"action": "shuffle", "on": True})
    assert seen == [("d", ["Next", 1], True), ("d", ["Seek", 61_000, 2], True), ("d", ["Shuffle", 3, 1], True)]
    assert html_page.act(skill, "nope", {"action": "next"}) is None
    with pytest.raises(ValueError):
        html_page.act(skill, page, {"action": "seek"})
    with pytest.raises(ValueError):
        html_page.act(skill, page, {"action": "dance"})
    assert len(seen) == 3


def test_page_jump_runs_as_an_apl_user_event():
    page = _html_page()
    seen = []
    assert html_page.act(_skill(seen), page, {"action": "jump", "item": "q7"})
    assert seen == [("d", ["Jump", 1, "q7"], True)]


def _playlist(monkeypatch):
    calls = []
    monkeypatch.setattr(device_mapping, "get_player_for_device", lambda device_id: "Office")
    monkeypatch.setattr(ma_control, "queue_window",
                        lambda player_id, offset=None, limit=110: calls.append(("window", player_id, offset, limit))
                        or {"offset": offset or 0, "items": []})
    monkeypatch.setattr(ma_control, "edit_queue",
                        lambda player_id, kind, item_id, shift=0: calls.append((kind, player_id, item_id, shift)) or True)
    monkeypatch.setattr(ma_control, "wave_form",
                        lambda player_id, item_id=None: calls.append(("wave", player_id, item_id)) or [0.5, 1.0])
    monkeypatch.setattr(ma_control, "options", lambda player_id, kind=None, value=0:
                        calls.append(("options", kind, value)) or {"sleep_left": 0, "autoplay": True})
    monkeypatch.setattr(ma_control, "play_media", lambda player_id, uri, option="replace", start=None:
                        calls.append(("play_media", uri, option, start)) or True)
    monkeypatch.setattr(ma_control, "browse", lambda player_id, kind, text="", item_id=None:
                        calls.append(("browse", kind, text, item_id)) or {"title": text, "sections": []})
    monkeypatch.setattr(ma_control, "now_playing", lambda player_id, item_id=None:
                        calls.append(("now", item_id)) or ["library://track/14", "library://album/7"])
    monkeypatch.setattr(ma_control, "set_favorite", lambda player_id, uri, on:
                        calls.append(("favorite", uri, on)) or uri != "library://album/9")
    page = _html_page()
    return page, html_page.stream(page), calls


def test_page_asks_for_its_playlist(monkeypatch):
    page, stream, calls = _playlist(monkeypatch)
    next(stream)
    assert html_page.act(None, page, {"action": "queue", "offset": -1, "limit": 110})
    assert calls == [("window", "Office", None, 110)]
    assert json.loads(next(stream)[len("data: "):]) == {"queue": {"offset": 0, "items": []}}


def test_page_moves_a_song_then_gets_its_playlist_again(monkeypatch):
    page, stream, calls = _playlist(monkeypatch)
    next(stream)
    assert html_page.act(None, page, {"action": "move", "item": "q14", "shift": -2, "offset": 2, "limit": 110})
    assert calls == [("move", "Office", "q14", -2), ("window", "Office", 2, 110)]
    assert "queue" in json.loads(next(stream)[len("data: "):])


def test_page_deletes_a_song_then_gets_its_playlist_again(monkeypatch):
    page, stream, calls = _playlist(monkeypatch)
    next(stream)
    assert html_page.act(None, page, {"action": "delete", "item": "q14", "offset": 2, "limit": 110})
    assert calls == [("delete", "Office", "q14", 0), ("window", "Office", 2, 110)]


def test_page_endless_mix_waits_for_the_new_stream(monkeypatch):
    page, stream, calls = _playlist(monkeypatch)
    next(stream)
    assert html_page.act(None, page, {"action": "mix", "item": "q3"})
    assert calls == [("mix", "Office", "q3", 0)]


def test_page_endless_mix_that_fails_clears_the_wait(monkeypatch):
    page, stream, _ = _playlist(monkeypatch)
    monkeypatch.setattr(ma_control, "edit_queue", lambda *a, **k: False)
    next(stream)
    assert html_page.act(None, page, {"action": "mix", "item": "q3"})
    assert json.loads(next(stream)[len("data: "):]) == {"ready": True}


def test_page_gets_the_loudness_graph(monkeypatch):
    page, stream, calls = _playlist(monkeypatch)
    next(stream)
    assert html_page.act(None, page, {"action": "wave", "item": ""})
    assert calls == [("wave", "Office", None)]
    assert json.loads(next(stream)[len("data: "):]) == {"wave": {"item": "", "points": [0.5, 1.0]}}


def test_page_sets_the_sleep_timer_and_autoplay(monkeypatch):
    page, stream, calls = _playlist(monkeypatch)
    next(stream)
    assert html_page.act(None, page, {"action": "sleep", "minutes": 30})
    assert html_page.act(None, page, {"action": "autoplay", "on": False})
    assert html_page.act(None, page, {"action": "options"})
    assert calls == [("options", "sleep", 30), ("options", "autoplay", False), ("options", None, False)]
    assert json.loads(next(stream)[len("data: "):]) == {"options": {"sleep_left": 0, "autoplay": True}}


def test_page_switches_crossfade(monkeypatch):
    page, stream, calls = _playlist(monkeypatch)
    next(stream)
    assert html_page.act(None, page, {"action": "crossfade", "on": True})
    assert calls == [("options", "crossfade", True)]


def test_page_clears_the_queue_then_gets_its_playlist_again(monkeypatch):
    page, stream, calls = _playlist(monkeypatch)
    next(stream)
    assert html_page.act(None, page, {"action": "clear", "offset": -1, "limit": 110})
    assert calls == [("clear", "Office", "", 0), ("window", "Office", None, 110)]


def test_page_lists_the_other_players(monkeypatch):
    page, stream, _ = _playlist(monkeypatch)
    monkeypatch.setattr(ma_voice, "players", lambda: [("Office", "Office"), ("kitchen", "Kitchen")])
    next(stream)
    assert html_page.act(None, page, {"action": "players"})
    assert json.loads(next(stream)[len("data: "):]) == {"players": [["kitchen", "Kitchen"]]}


def test_page_moves_the_music_to_another_player(monkeypatch):
    page, stream, _ = _playlist(monkeypatch)
    moved = []
    monkeypatch.setattr(ma_voice, "move", lambda player_id, target_id: moved.append((player_id, target_id)))
    next(stream)
    assert html_page.act(None, page, {"action": "transfer", "target": "kitchen", "name": "Kitchen"})
    assert moved == [("Office", "kitchen")]
    assert json.loads(next(stream)[len("data: "):]) == {"toast": "Moved to Kitchen"}


def test_page_move_that_fails_says_so(monkeypatch):
    page, stream, _ = _playlist(monkeypatch)

    def move(player_id, target_id):
        raise ma_voice.MAUnavailable("down")
    monkeypatch.setattr(ma_voice, "move", move)
    next(stream)
    assert html_page.act(None, page, {"action": "transfer", "target": "kitchen", "name": "Kitchen"})
    assert json.loads(next(stream)[len("data: "):]) == {"toast": "Music Assistant did not do it"}


def test_page_sleep_timer_needs_minutes(monkeypatch):
    page, _, calls = _playlist(monkeypatch)
    for action in ({"action": "sleep"}, {"action": "sleep", "minutes": -5}):
        with pytest.raises(ValueError):
            html_page.act(None, page, action)
    assert calls == []


def test_page_without_lyrics_gets_empty_ones(monkeypatch):
    page, stream, _ = _playlist(monkeypatch)
    monkeypatch.setattr(ma_control, "lyrics", lambda player_id, item_id=None: None)
    next(stream)
    assert html_page.act(None, page, {"action": "lyrics", "item": "q13"})
    assert json.loads(next(stream)[len("data: "):]) == {"lyrics": {"item": "q13", "plain": "", "lrc": ""}}


def test_page_plays_an_album_from_a_track(monkeypatch):
    page, stream, calls = _playlist(monkeypatch)
    next(stream)
    assert html_page.act(None, page, {"action": "play_media", "uri": "library://album/7", "item": "library://track/12"})
    assert calls == [("play_media", "library://album/7", "replace", "library://track/12")]


def test_page_adds_a_song_to_the_queue(monkeypatch):
    page, stream, calls = _playlist(monkeypatch)
    next(stream)
    assert html_page.act(None, page, {"action": "play_media", "uri": "library://track/12", "option": "add"})
    assert calls == [("play_media", "library://track/12", "add", None)]
    assert json.loads(next(stream)[len("data: "):]) == {"toast": "Added to the queue"}


def test_page_searches(monkeypatch):
    page, stream, calls = _playlist(monkeypatch)
    next(stream)
    assert html_page.act(None, page, {"action": "browse", "kind": "search", "text": "esti dal"})
    assert calls == [("browse", "search", "esti dal", None)]
    assert json.loads(next(stream)[len("data: "):]) == {"browse": {"title": "esti dal", "sections": []}}


def test_page_favourites_an_album(monkeypatch):
    page, stream, calls = _playlist(monkeypatch)
    next(stream)
    assert html_page.act(None, page, {"action": "favorite", "uri": "library://album/7", "on": True})
    assert html_page.act(None, page, {"action": "favorite", "uri": "library://album/9", "on": False})
    assert calls == [("favorite", "library://album/7", True), ("favorite", "library://album/9", False)]
    assert json.loads(next(stream)[len("data: "):]) == {"toast": "Music Assistant did not do it"}


def test_page_hears_what_plays_now(monkeypatch):
    page, stream, calls = _playlist(monkeypatch)
    next(stream)
    assert html_page.act(None, page, {"action": "now", "item": "q13"})
    assert calls == [("now", "q13")]
    assert json.loads(next(stream)[len("data: "):]) == {
        "now": {"item": "q13", "uris": ["library://track/14", "library://album/7"]}}


def test_page_browse_and_play_need_known_options(monkeypatch):
    page, _, calls = _playlist(monkeypatch)
    for action in ({"action": "browse", "kind": "delete"}, {"action": "play_media", "uri": "x", "option": "now"},
                   {"action": "play_media"}, {"action": "favorite", "on": True}):
        with pytest.raises(ValueError):
            html_page.act(None, page, action)
    assert calls == []


def test_page_move_needs_a_shift(monkeypatch):
    page, _, calls = _playlist(monkeypatch)
    for action in ({"action": "move", "item": "q14", "shift": 0}, {"action": "move", "shift": 1}):
        with pytest.raises(ValueError):
            html_page.act(None, page, action)
    assert calls == []


def test_state_report_pulls_only_when_behind():
    page = _html_page()
    seen = []
    skill = _skill(seen)
    assert html_page.report(skill, page, 0, 5_000, True)
    assert seen == [("d", ["Pull", 0, 5_000, True, page], True)]
    assert html_page.report(skill, page, 1, 6_000, True)
    assert len(seen) == 1
    assert lp.is_live("d")
    assert not html_page.report(skill, "nope", 0, 0, False)


def test_replies_to_the_html_page_carry_no_audio_player_directives():
    response = ResponseFactory().add_directive(StopDirective()).add_directive(
        html_page.start_directive("p1")).response
    html_page.drop_audio_player(response)
    assert [type(d) for d in response.directives] == [StartDirective]
    response = ResponseFactory().add_directive(StopDirective()).response
    html_page.drop_audio_player(response)
    assert response.directives is None


def test_the_page_is_open_while_it_holds_its_stream():
    page = _html_page()
    assert not html_page.is_open("d")
    stream = html_page.stream(page)
    next(stream)
    assert html_page.is_open("d")
    stream.close()
    assert not html_page.is_open("d")
