"""The status page, as a browser gets it."""


def test_the_ma_check_shows_the_latest_stream_as_text(app, store):
    store.save({"streamUrl": "http://ma/flow/1.mp3", "title": "<b>Esti dal</b>", "artist": "Kodály Zoltán",
                "playerId": "Den"})
    html = app.test_client().get("/status/ma").get_json()["ma_api_html"]
    assert "reachable" in html and "http://ma/flow/1.mp3" in html
    assert "&lt;b&gt;Esti dal&lt;/b&gt;" in html and "<b>" not in html


def test_the_ma_check_says_no_stream_came_yet(app, store):
    html = app.test_client().get("/status/ma").get_json()["ma_api_html"]
    assert "404" in html and "No URL available" in html


def test_the_web_pages_list_no_past_requests(app):
    client = app.test_client()
    assert client.get("/invocations").status_code == 404
    assert "invocations" not in client.get("/status").get_data(as_text=True).lower()


def test_the_metadata_check_shows_its_error_as_text_without_query_strings(app, monkeypatch):
    from skill import view

    def fails(player_id):
        raise ConnectionError("<script>alert(1)</script> http://music.example/rest/ping.view?u=zenehallgato&t=abc&s=def")

    monkeypatch.setattr(view, "build", fails)
    html = app.test_client().get("/status/metadata").get_json()["metadata_html"]
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html and "<script>" not in html
    assert "music.example/rest/ping.view" in html and "t=abc" not in html
