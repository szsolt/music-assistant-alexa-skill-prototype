"""The skill's web server: its start, and what each request leaves behind."""

import importlib
import logging
import signal

import pytest

from skill import echo, watch
from skill.ma import client as ma_client
from skill.ma import events as ma_events
from skill.voice import lists as voice_lists


def test_a_failed_skill_request_leaves_no_ma_deadline(app):
    app.test_client().post("/simulator/send", json={"intent": "NoSuchIntent"})
    assert ma_client.request_deadline() is None


@pytest.fixture
def starts(monkeypatch):
    """Each background job's start, counted instead of run."""
    counted = []
    for module, name in ((voice_lists, "start"), (echo, "load"), (watch, "start_sweeping"),
                         (ma_events, "start"), (ma_client, "start")):
        monkeypatch.setattr(module, name, lambda name=f"{module.__name__}.{name}": counted.append(name))
    return counted


def test_the_background_work_starts_once_per_process(starts, monkeypatch):
    monkeypatch.delenv("APP_USERNAME", raising=False)
    import web
    web = importlib.reload(web)
    web.create_app()
    web.start_background()
    web.create_app()
    web.start_background()
    assert sorted(starts) == ["skill.echo.load", "skill.ma.client.start", "skill.ma.events.start",
                              "skill.voice.lists.start", "skill.watch.start_sweeping"]


@pytest.fixture
def main(monkeypatch):
    """app.py's main, up to the server's run: returns how often it started the background work."""
    import flask
    from ask_sdk_webservice_support.verifier import RequestVerifier
    import app as app_py
    import web
    monkeypatch.setenv("SKILL_ID", "amzn1.ask.skill.test")
    monkeypatch.setenv("FLASK_DEBUG", "1")
    monkeypatch.setenv("FLASK_RELOADER", "1")
    monkeypatch.delenv("WERKZEUG_RUN_MAIN", raising=False)
    monkeypatch.setattr(flask.Flask, "run", lambda self, **kwargs: None)
    monkeypatch.setattr(RequestVerifier, "_validate_cert_chain", RequestVerifier._validate_cert_chain)
    started = []
    monkeypatch.setattr(web, "start_background", lambda: started.append(1))
    handlers = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    record_factory = logging.getLogRecordFactory()
    root_handlers = list(logging.getLogger().handlers)

    def run():
        app_py.main()
        return len(started)
    yield run
    for s, handler in handlers.items():
        signal.signal(s, handler)
    logging.setLogRecordFactory(record_factory)
    logging.getLogger().handlers[:] = root_handlers


def test_the_reloaders_watching_process_starts_no_background_work(main):
    assert main() == 0


def test_the_process_the_reloader_runs_starts_the_background_work(main, monkeypatch):
    monkeypatch.setenv("WERKZEUG_RUN_MAIN", "true")
    assert main() == 1


def test_without_the_reloader_the_server_starts_the_background_work(main, monkeypatch):
    monkeypatch.setenv("FLASK_RELOADER", "0")
    assert main() == 1
