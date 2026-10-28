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

from skill import apl, bell, html_page
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
