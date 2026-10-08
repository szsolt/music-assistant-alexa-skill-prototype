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
