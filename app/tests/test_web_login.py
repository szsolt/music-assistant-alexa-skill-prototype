"""The web UI's login, as a browser and Alexa meet it."""

import base64
import json

import pytest

# One page of each part of the web UI; each answers 200 with the login.
PAGES = ["/setup/logs/download", "/status/ma", "/simulator/payload?intent=PlayIntent", "/devices", "/ma/latest-url"]


def _basic(user, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


@pytest.fixture
def client(app, store, monkeypatch):
    monkeypatch.setenv("APP_USERNAME", "zenehallgato")
    monkeypatch.setenv("APP_PASSWORD", "Bartok-1881")
    store.save({"streamUrl": "http://ma/flow/1.mp3", "title": "Esti dal", "artist": "Kodály Zoltán",
                "playerId": "Den"})
    return app.test_client()


@pytest.mark.parametrize("page", PAGES)
def test_the_right_login_opens_the_page(client, page):
    assert client.get(page, headers=_basic("zenehallgato", "Bartok-1881")).status_code == 200


@pytest.mark.parametrize("page, headers", [(page, {}) for page in PAGES]
                         + [(PAGES[0], _basic("vendeg", "Bartok-1881")), (PAGES[0], _basic("zenehallgato", "Liszt-1811"))])
def test_any_other_login_gets_401_and_asks_for_one(client, page, headers):
    answer = client.get(page, headers=headers)
    assert answer.status_code == 401
    assert answer.headers["WWW-Authenticate"].startswith("Basic ")


def test_mas_routes_answer_401_in_plain_text(client):
    assert client.get("/ma/latest-url").mimetype == "text/plain"


@pytest.mark.parametrize("method, page", [("GET", "/"), ("POST", "/bell/logo.png")])
def test_other_methods_on_the_open_paths_need_the_login(client, method, page):
    assert client.open(page, method=method).status_code == 401


@pytest.mark.parametrize("page", ["/bellhop", "/media"])
def test_paths_next_to_the_open_ones_need_the_login_and_answer_in_html(client, page):
    answer = client.get(page)
    assert answer.status_code == 401
    assert answer.mimetype == "text/html"


def test_the_echo_loads_the_player_page_and_its_logo_without_a_login(client):
    assert client.get("/bell/logo.png").status_code == 200
    assert client.get("/bell/html/a1b2c3").status_code != 401


def test_the_player_page_sends_its_events_without_a_login(client):
    assert client.post("/bell/html/a1b2c3/state", json={"position": 12}).status_code != 401
    assert client.post("/bell/html/a1b2c3/action", json={"action": "pause"}).status_code != 401


def test_alexa_needs_no_login_but_a_signature(client):
    envelope = {"version": "1.0",
                "context": {"System": {"application": {"applicationId": "amzn1.ask.skill.test"},
                                       "device": {"deviceId": "d"}}},
                "request": {"type": "LaunchRequest", "requestId": "r", "timestamp": "2026-10-30T20:00:00Z",
                            "locale": "en-US"}}
    assert client.post("/", data=json.dumps(envelope), content_type="application/json").status_code == 400
