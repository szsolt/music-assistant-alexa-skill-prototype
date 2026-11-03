"""The Echo record: its pairing and its open page, kept across a restart of the skill."""

import importlib

import pytest

from skill import store
from skill import echo as echos
from skill import handoff
from sse import next_chunk

pytest.importorskip("ask_sdk_core")

STREAM = "http://ma/flow/0.mp3"


@pytest.fixture
def den(ma, store, monkeypatch):
    """Echos d and k play MA player Den, whose last stream the skill holds."""
    monkeypatch.setenv("ENABLE_APL", "true")
    monkeypatch.setenv("HTML_PAGE_URL", "https://skill.example/bell/html/")
    echos.get("d").pair("Den")
    echos.get("k").pair("Den")
    store.save({"playerId": "Den", "streamUrl": STREAM, "title": "Kicsi, gyere velem", "artist": "Kaláka",
                "album": "Az én szívem"})
    yield
    handoff.closed("d")
    handoff.closed("k")


def _page_id(device_id):
    return echos.get(device_id).page.page_id


def _restart():
    importlib.reload(store)
    store.load()
    echos.load()


def _report(page_client, page_id, seen=1):
    return page_client.post(f"/bell/html/{page_id}/state", json={"seen": seen, "at": 5000, "playing": True}).status_code


def _events(page_client, page_id):
    response = page_client.get(f"/bell/html/{page_id}/events", buffered=False)
    status = response.status_code
    response.close()
    return status


def test_the_pairing_is_on_the_echo_and_in_the_device_file(store):
    echos.get("d").pair("Den")
    echos.load()
    assert echos.get("d").player_id == "Den"
    assert [e.device_id for e in echos.for_player("Den")] == ["d"]
    echos.get("d").pair(None)
    assert echos.get("d").player_id is None


def test_an_open_page_survives_a_restart(den, echo, page_client, ma, clock):
    echo("d", "html").launch()
    clock.advance(0.5)
    page = _page_id("d")
    _restart()
    assert echos.by_page(page).device_id == "d"
    assert _report(page_client, page, seen=7) == 204
    response = page_client.get(f"/bell/html/{page}/events", buffered=False)
    assert response.status_code == 200
    chunks = iter(response.response)
    assert next(chunks) == b"retry: 1000\n\n"
    assert b'"title": "Kicsi, gyere velem"' in next_chunk(chunks)
    response.close()


def test_a_page_kept_from_before_a_restart_loads_with_what_plays_now(den, echo, page_client, ma):
    echo("d", "html").launch()
    page = _page_id("d")
    _restart()
    response = page_client.get(f"/bell/html/{page}")
    assert response.status_code == 200
    stream = store.for_player("Den")["streamUrl"]
    assert f'"stream": "{stream}"'.encode() in response.data
    assert b'"autoplay": false' in response.data


def test_after_a_restart_a_newer_page_replaces_the_saved_one(den, echo, page_client, ma):
    show = echo("d", "html")
    show.launch()
    old = _page_id("d")
    _restart()
    show.launch()
    new = _page_id("d")
    assert new != old
    assert _report(page_client, old) == 404
    assert _events(page_client, old) == 404
    assert show.html_message({"renew": old}) == {}            # the old page can't take the Echo back
    assert _report(page_client, new) == 204
    assert _events(page_client, new) == 200


def test_a_restart_brings_back_only_the_newest_page_of_an_echo(den, echo, page_client, ma):
    show = echo("d", "html")
    show.launch()
    old = _page_id("d")
    show.launch()
    new = _page_id("d")
    _restart()
    assert _report(page_client, old) == 404
    assert _report(page_client, new) == 204


def test_a_closed_page_is_not_brought_back(den, echo, page_client, ma):
    echo("d", "html").launch()
    page = _page_id("d")
    echos.close_page("d", page)
    _restart()
    assert _report(page_client, page) == 404


def test_an_unreadable_pages_file_opens_no_pages(store, tmp_path):
    (tmp_path / "open_pages.json").write_text("{not json")
    echos.load()
    assert echos.get("d").page is None
