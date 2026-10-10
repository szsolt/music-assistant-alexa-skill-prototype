import os
from types import SimpleNamespace as NS

import pytest

pytest.importorskip("ask_sdk_core")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")    # the skill makes AWS clients on import

from ask_sdk_core.response_helper import ResponseFactory

from skill import lambda_function as lf
from skill import live_page as lp


def _slot(value, resolved=None):
    resolutions = None
    if resolved:
        resolutions = NS(resolutions_per_authority=[NS(
            status=NS(code=NS(value="ER_SUCCESS_MATCH")), values=[NS(value=NS(name=resolved))])])
    return NS(value=value, resolutions=resolutions)


def _input(intent, slots=None):
    request = NS(object_type="IntentRequest", intent=NS(name=intent, slots=slots or {}))
    envelope = NS(request=request, context=NS(system=NS(device=NS(device_id="d"))))
    attributes = NS(request_attributes={"_": lambda s: s, "supports_apl": True})
    return NS(request_envelope=envelope, attributes_manager=attributes, response_builder=ResponseFactory())


@pytest.fixture
def page(monkeypatch):
    """An open player page on device d."""
    monkeypatch.setenv("ENABLE_APL", "true")
    monkeypatch.setattr(lf.bell, "has_page", lambda device_id: True)
    lp._last_event.clear()
    lp.heard_from("d")
    yield
    lp.closed("d")


def test_unknown_request_keeps_the_open_page_quietly(page):
    response = lf.UnhandledIntentHandler().handle(_input("AMAZON.FallbackIntent"))
    assert response.output_speech is None and response.should_end_session is None


def test_unknown_request_without_a_page_says_so():
    lp._last_event.clear()
    response = lf.UnhandledIntentHandler().handle(_input("AMAZON.FallbackIntent"))
    assert response.output_speech is not None and response.should_end_session is True


def test_forced_voice_command_does_nothing(page, monkeypatch):
    monkeypatch.setattr(lf.ma_voice, "find", lambda *a: pytest.fail("searched MA"))
    # "turn off lamp": Alexa's queue command, with no kind word.
    response = lf.VoicePlayHandler().handle(_input("Queue", {"kind": _slot(None), "name": _slot("off lamp")}))
    assert response.output_speech is None and response.should_end_session is None


def _session_ended(error_type=None):
    from ask_sdk_model.session_ended_error import SessionEndedError
    from ask_sdk_model.session_ended_error_type import SessionEndedErrorType
    error = SessionEndedError(message="x", object_type=SessionEndedErrorType(error_type)) if error_type else None
    handler_input = _input("unused")
    handler_input.request_envelope.request = NS(object_type="SessionEndedRequest", reason=None, error=error)
    return handler_input


@pytest.fixture
def reopen(monkeypatch):
    """MA plays on player p, paired with Echo d only; timers fire at once. Yields the resumed Echos."""
    monkeypatch.setenv("ENABLE_APL", "true")
    monkeypatch.setattr(lf.threading, "Timer", lambda delay, fn: NS(daemon=False, start=fn))
    monkeypatch.setattr(lf.device_mapping, "get_player_for_device", lambda device_id: "p")
    monkeypatch.setattr(lf.device_mapping, "get_devices_for_player", lambda player_id: ["d"])
    monkeypatch.setattr(lf.ma_control, "get_queue_state", lambda player_id: ("playing", 61000))
    monkeypatch.setattr(lf.bell, "has_page", lambda device_id: True)   # the closed page, until swept
    lp._last_event.clear()
    resumed = []
    monkeypatch.setattr(lf, "_resume_for", lambda device_id, position_ms: resumed.append((device_id, position_ms)) or "ok")
    lf._reopened_at.clear()
    yield resumed
    lf._reopened_at.clear()


def test_page_alexa_closed_comes_back_once_a_minute(reopen):
    lf.SkillEventHandler().handle(_session_ended("INTERNAL_SERVICE_ERROR"))
    assert reopen == [("d", 61000)]                                # where MA is now
    lf.SkillEventHandler().handle(_session_ended("INTERNAL_SERVICE_ERROR"))
    assert reopen == [("d", 61000)]


def test_page_closed_by_hand_or_by_a_skill_error_stays_closed(reopen):
    lf.SkillEventHandler().handle(_session_ended())
    lf.SkillEventHandler().handle(_session_ended("INVALID_RESPONSE"))
    assert reopen == []


def test_page_alexa_closed_stays_closed_when_ma_has_stopped(reopen, monkeypatch):
    monkeypatch.setattr(lf.ma_control, "get_queue_state", lambda player_id: ("paused", 0))
    lf.SkillEventHandler().handle(_session_ended("INTERNAL_SERVICE_ERROR"))
    assert reopen == []
    # Not counted as a reopen: a later close while MA plays still reopens.
    monkeypatch.setattr(lf.ma_control, "get_queue_state", lambda player_id: ("playing", 0))
    lf.SkillEventHandler().handle(_session_ended("INTERNAL_SERVICE_ERROR"))
    assert reopen == [("d", 0)]


def test_page_alexa_closed_stays_closed_for_a_shared_player_or_a_new_page(reopen, monkeypatch):
    monkeypatch.setattr(lf.device_mapping, "get_devices_for_player", lambda player_id: ["d", "e"])
    lf.SkillEventHandler().handle(_session_ended("INTERNAL_SERVICE_ERROR"))
    monkeypatch.setattr(lf.device_mapping, "get_devices_for_player", lambda player_id: ["d"])
    monkeypatch.setattr(lf.threading, "Timer", lambda delay, fn: NS(daemon=False, start=lambda: (lp.heard_from("d"), fn())))
    lf.SkillEventHandler().handle(_session_ended("INTERNAL_SERVICE_ERROR"))
    lp._last_event.clear()
    assert reopen == []


@pytest.fixture
def move(page, monkeypatch):
    """Echo d plays MA player p; the kitchen and a lamp can take the music. Yields the moves MA got."""
    monkeypatch.setattr(lf.device_mapping, "get_player_for_device", lambda device_id: "p")
    monkeypatch.setattr(lf.ma_voice, "players", lambda: [("p", "Here"), ("k", "Kitchen"), ("l", "Lamp")])
    moves = []
    monkeypatch.setattr(lf.ma_voice, "move", lambda player_id, target_id: moves.append((player_id, target_id)))
    yield moves


def test_move_sends_the_music_on_and_closes_this_page(move):
    response = lf.MoveMusicHandler().handle(_input("MoveMusic", {"player": _slot("kitchen", "Kitchen")}))
    assert move == [("p", "k")]
    assert "Kitchen" in response.output_speech.ssml and response.should_end_session is True
    assert [d.object_type for d in response.directives] == ["AudioPlayer.Stop"]
    assert not lp.is_live("d") and lp.takes_silent_pause("d")  # MA's pause for d: no hand-off, no speech


def test_move_to_an_unknown_player_keeps_playing_here(move):
    response = lf.MoveMusicHandler().handle(_input("MoveMusic", {"player": _slot("garage")}))
    assert move == []
    assert "garage" in response.output_speech.ssml and response.should_end_session is not True


def test_move_to_the_player_already_playing_says_so(move):
    response = lf.MoveMusicHandler().handle(_input("MoveMusic", {"player": _slot("here", "Here")}))
    assert move == [] and "already" in response.output_speech.ssml


def test_like_something_other_than_song_album_artist_does_nothing(page, monkeypatch):
    monkeypatch.setattr(lf.ma_voice, "set_favorite", lambda *a: pytest.fail("changed a favourite"))
    response = lf.FavoriteHandler().handle(_input("AddFavorite", {"what": _slot("Queen")}))
    assert response.output_speech is None and response.should_end_session is None


def test_a_new_page_gets_the_button_states_ma_gives_in_time(monkeypatch):
    import threading
    monkeypatch.setattr(lf.device_mapping, "get_player_for_device", lambda device_id: "p")
    monkeypatch.setattr(lf.ma_voice, "modes", lambda player_id: {"shuffle": True, "repeat": "one"})
    monkeypatch.setattr(lf.ma_voice, "favorites", lambda player_id: {"song": True, "album": None})
    assert lf._start_buttons("d") == {"shuffleOn": 1, "repeatMode": 2, "songFavorite": 1, "albumFavorite": -1}
    # MA slow with the favourites: the page gets the modes now, the hearts at its first refresh.
    slow = threading.Event()
    monkeypatch.setattr(lf, "START_BUTTONS_S", 0.05)
    monkeypatch.setattr(lf.ma_voice, "favorites", lambda player_id: slow.wait(2) or {})
    try:
        assert lf._start_buttons("d") == {"shuffleOn": 1, "repeatMode": 2}
    finally:
        slow.set()


def test_a_failed_button_state_leaves_the_others(monkeypatch):
    monkeypatch.setattr(lf.device_mapping, "get_player_for_device", lambda device_id: "p")
    monkeypatch.setattr(lf.ma_voice, "modes", lambda player_id: {"shuffle": False, "repeat": "off"})

    def broken(player_id):
        raise KeyError("song")
    monkeypatch.setattr(lf.ma_voice, "favorites", broken)
    assert lf._start_buttons("d") == {"shuffleOn": 0, "repeatMode": 0}


@pytest.fixture
def sleep(monkeypatch):
    """Echo d plays MA player p. Yields the timers the skill started."""
    monkeypatch.setattr(lf.device_mapping, "get_player_for_device", lambda device_id: "p")
    started = []
    monkeypatch.setattr(lf.sleep_timer, "start", lambda device_id, seconds, on_due: started.append((device_id, seconds)))
    yield started


def test_sleep_timer_starts_and_says_when(sleep):
    response = lf.SleepTimerHandler().handle(_input("SleepTimer", {"duration": _slot("PT1H30M")}))
    assert sleep == [("d", 5400)]
    assert "1 hour and 30 minutes" in response.output_speech.ssml


def test_sleep_timer_without_a_length_asks_for_one(sleep):
    response = lf.SleepTimerHandler().handle(_input("SleepTimer", {"duration": _slot(None)}))
    assert sleep == [] and "How long" in response.output_speech.ssml


def test_cancel_sleep_timer_says_if_there_was_none(monkeypatch):
    monkeypatch.setattr(lf.sleep_timer, "cancel", lambda device_id: False)
    response = lf.CancelSleepTimerHandler().handle(_input("CancelSleepTimer"))
    assert "no sleep timer" in response.output_speech.ssml


@pytest.mark.parametrize("sent, paused, said", [(False, True, True), (True, True, False), (False, False, False)])
def test_play_is_someones_only_when_ma_is_paused_and_sent_nothing(monkeypatch, sent, paused, said):
    monkeypatch.setattr(lf.device_mapping, "get_player_for_device", lambda device_id: "p")
    monkeypatch.setattr(lf.ma_control, "is_paused", lambda player_id: paused)
    import shared_store
    monkeypatch.setattr(shared_store, "sent_since", lambda player_id, seconds: sent)
    assert lf._said_play("d") is said


def test_said_play_resumes_ma_on_the_open_page(monkeypatch):
    monkeypatch.setattr(lf.device_mapping, "is_another_echos_stream", lambda *a: False)
    monkeypatch.setattr(lf, "_said_play", lambda device_id: True)
    resumed = object()
    monkeypatch.setattr(lf, "_stream_reply", lambda handler_input, send: resumed)
    assert lf.LaunchRequestOrPlayAudioHandler().handle(_input("PlayAudio")) is resumed


def test_open_does_not_play_another_players_stream(monkeypatch):
    monkeypatch.setattr(lf.device_mapping, "is_another_echos_stream", lambda *a: False)
    monkeypatch.setattr(lf, "_resume_through_ma", lambda handler_input: "failed")
    monkeypatch.setattr(lf.device_mapping, "get_player_for_device", lambda device_id: "office")
    import shared_store
    monkeypatch.setattr(shared_store, "_store", {"playerId": "kitchen", "streamUrl": "https://x/kitchen.mp3"})
    played = []
    monkeypatch.setattr(lf.util, "play", lambda **kwargs: played.append(kwargs))
    launch = _input(None)
    launch.request_envelope.request.object_type = "LaunchRequest"
    response = lf.LaunchRequestOrPlayAudioHandler().handle(launch)
    assert played == [] and "nothing to play" in response.output_speech.ssml
