"""Seams for the tests: a clock moved by hand, a fake MA, Echos that talk to the skill, and a page client."""

import importlib
import itertools
import json
import os
import sys
import types

import pytest

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")    # the skill makes AWS clients on import

from oscrypto.errors import LibraryNotFoundError  # noqa: E402

try:
    import certvalidator  # noqa: F401
except LibraryNotFoundError:
    # Without a libcrypto oscrypto can use, the Alexa endpoint's signature check can't load.
    # The tests send no signed requests, so its certificate check never runs.
    sys.modules["certvalidator"] = types.SimpleNamespace(CertificateValidator=None)
    sys.modules["certvalidator.errors"] = types.SimpleNamespace(ValidationError=Exception, PathError=Exception)

from skill import clock as skill_clock  # noqa: E402
from skill import echo as skill_echo  # noqa: E402
from skill import store as skill_store  # noqa: E402
from skill.ma import client as ma_client  # noqa: E402


class FakeClock:
    """Time that only moves when a test moves it. Due timers and background work run on the test's thread."""

    def __init__(self, now=0.0):
        self.now = now
        self.timers = []

    def monotonic(self):
        return self.now

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.advance(seconds)

    def after(self, seconds, fn, *args):
        timer = _Timer(self.now + seconds, seconds, fn, args)
        self.timers.append(timer)
        return timer

    def spawn(self, fn, *args, name=None):
        fn(*args)

    def advance(self, seconds):
        """Move time on by seconds, running the timers that fall due on the way, in order."""
        end = self.now + seconds
        while True:
            due = [t for t in self.timers if t.at <= end]
            if not due:
                break
            timer = min(due, key=lambda t: t.at)
            self.timers.remove(timer)
            self.now = max(self.now, timer.at)
            if not timer.cancelled:
                timer.fn(*timer.args)
        self.now = end

    def pending(self):
        """The waits of the timers still to run, in seconds as started."""
        return [t.seconds for t in self.timers if not t.cancelled]


class _Timer:
    def __init__(self, at, seconds, fn, args):
        self.at, self.seconds, self.fn, self.args = at, seconds, fn, args
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


# Each test starts a day after the one before, so what the skill keeps for a while has run out.
_test_days = itertools.count(0.0, 86400.0)


@pytest.fixture(autouse=True)
def clock():
    fake = FakeClock(next(_test_days))
    real = skill_clock.use(fake)
    yield fake
    skill_clock.use(real)


@pytest.fixture(autouse=True)
def no_ma_deadline_left():
    """Each test's Alexa requests end their MA deadline, as the web app ends them."""
    yield
    assert ma_client.request_deadline() is None


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    """The device mapping, MA's latest streams and the open pages live in tmp_path, fresh as after a first start."""
    monkeypatch.setenv("DEVICE_MAPPING_PATH", str(tmp_path / "device_players.json"))
    skill_echo.load()
    return importlib.reload(skill_store)


@pytest.fixture
def app(monkeypatch):
    """The skill's Flask app, as app.py serves it, without a web login set."""
    monkeypatch.delenv("APP_USERNAME", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    from web import create_app
    return create_app()


@pytest.fixture
def ma(app, monkeypatch):
    """A fake MA behind the MA client. Its new streams reach the skill's /ma/push-url, and its events the skill's
    subscriptions, as MA sends them."""
    from fake_ma import FakeMA
    from skill.ma import events as ma_events
    monkeypatch.setattr(ma_client, "_subscriptions", [])
    ma_events.start()
    pushes = app.test_client()
    fake = FakeMA(push=lambda body: pushes.post("/ma/push-url", json=body).get_json())
    fake.attach()
    yield fake
    fake.detach()


class Echo:
    """An Echo that sends Alexa's requests to the skill. screen: "none", "apl" or "html"."""

    def __init__(self, skill, device_id, screen):
        self.skill, self.device_id, self.screen = skill, device_id, screen

    def send(self, request, new=False, playing=None):
        """The skill's answer to request (the envelope's "request" part), as Alexa gets it.

        playing: the token of the stream the Echo plays, which Alexa sends along.
        """
        from ask_sdk_model import RequestEnvelope
        interfaces = {"AudioPlayer": {}}
        if self.screen in ("apl", "html"):
            interfaces["Alexa.Presentation.APL"] = {}
        if self.screen == "html":
            interfaces["Alexa.Presentation.HTML"] = {}
        app = {"applicationId": self.skill.skill_id or ""}
        payload = {
            "version": "1.0",
            "session": {"new": new, "sessionId": "SessionId.test", "application": app, "user": {"userId": "user"}},
            "context": {"System": {"application": app, "user": {"userId": "user"},
                                   "device": {"deviceId": self.device_id, "supportedInterfaces": interfaces}},
                        **({"AudioPlayer": {"token": playing, "playerActivity": "PLAYING"}} if playing else {})},
            "request": {"requestId": "EdwRequestId.test", "timestamp": "2026-10-30T20:00:00Z", "locale": "en-US",
                        **request},
        }
        envelope = self.skill.serializer.deserialize(json.dumps(payload), RequestEnvelope)
        try:
            response = self.skill.serializer.serialize(self.skill.invoke(request_envelope=envelope, context=None))
        finally:
            ma_client.end_request()     # as the web app does after each request
        response = response["response"]
        if request["type"].startswith(("PlaybackController.", "AudioPlayer.")):
            # Alexa rejects any other directive in a reply to these
            assert all(d["type"].startswith("AudioPlayer.") for d in response.get("directives", [])), response
        return response

    def launch(self):
        return self.send({"type": "LaunchRequest"}, new=True)

    def intent(self, intent_name, **slots):
        """Intent intent_name; a slot value is its spoken text, or (spoken, resolved) for a matched slot type."""
        def slot(key, value):
            spoken, resolved = value if isinstance(value, tuple) else (value, None)
            slot = {"name": key, "value": spoken}
            if resolved:
                slot["resolutions"] = {"resolutionsPerAuthority": [{
                    "authority": "test", "status": {"code": "ER_SUCCESS_MATCH"},
                    "values": [{"value": {"name": resolved, "id": resolved}}]}]}
            return slot
        return self.send({"type": "IntentRequest", "intent": {
            "name": intent_name, "confirmationStatus": "NONE",
            "slots": {key: slot(key, value) for key, value in slots.items()}}})

    def audio_event(self, kind, offset_ms=0, token="playbackToken", **more):
        """AudioPlayer.<kind>, e.g. "PlaybackStopped", for the stream token the Echo plays."""
        return self.send({"type": f"AudioPlayer.{kind}", "token": token, "offsetInMilliseconds": offset_ms, **more},
                         playing=token)

    def controller(self, command):
        """PlaybackController.<command>CommandIssued: a button on the Echo, e.g. "Pause"."""
        return self.send({"type": f"PlaybackController.{command}CommandIssued"})

    def user_event(self, *arguments):
        """An APL UserEvent from the player page."""
        return self.send({"type": "Alexa.Presentation.APL.UserEvent", "token": "playbackToken",
                          "arguments": list(arguments)})

    def html_message(self, message):
        return self.send({"type": "Alexa.Presentation.HTML.Message", "message": message})

    def session_ended(self, error_type=None):
        request = {"type": "SessionEndedRequest", "reason": "ERROR" if error_type else "USER_INITIATED"}
        if error_type:
            request["error"] = {"type": error_type, "message": "test"}
        return self.send(request)


@pytest.fixture(scope="session")
def skill():
    from skill import lambda_function
    return lambda_function.sb.create()


@pytest.fixture
def echo(skill):
    """Make an Echo: echo(device_id, screen="html")."""
    return lambda device_id, screen="html": Echo(skill, device_id, screen)


@pytest.fixture
def page_client(app):
    """The player page's side of the skill: its HTTP routes, as the Echo's browser calls them."""
    return app.test_client()
