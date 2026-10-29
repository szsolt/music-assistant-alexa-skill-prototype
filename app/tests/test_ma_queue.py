import asyncio
from types import SimpleNamespace

from music_assistant_models.enums import MediaType, QueueOption, RepeatMode

from skill import ma_control

TITLES = ["Kicsi, gyere velem", "A bolond levele", "Nem hallod, üvöltök", "Altató", "Mi van a zsebedben?",
          "Esti dal", "Hét szem mazsola", "Ringató", "Dal a hóról", "Hajnali ének", "Altató",
          "Esti dal", "Kicsi, gyere velem", "A bolond levele", "Ringató"]


def _item(n):
    media = SimpleNamespace(name=TITLES[n], version=None, artist_str="Kaláka", uri=f"library://track/{n + 1}",
                            album=SimpleNamespace(name="Az én szívem", item_id="7", provider="library", media_type=MediaType.ALBUM,
                                                  uri="library://album/7"),
                            artists=[SimpleNamespace(name="Kaláka", uri="library://artist/3")],
                            duration=180 + n, to_dict=lambda: {"uri": f"library://track/{n + 1}"})
    details = SimpleNamespace(item_id=f"/music/Kaláka/{n + 1}.flac", provider="filesystem--abc")
    return SimpleNamespace(queue_item_id=f"q{n}", name=TITLES[n], media_item=media, duration=180 + n,
                           streamdetails=details)


def _media(kind, n, name, artist="Kaláka", duration=0, disc=1, favorite=False):
    return SimpleNamespace(media_type=MediaType(kind), uri=f"library://{kind}/{n}", item_id=str(n), provider="library",
                           name=name, version=None, artist_str=artist, duration=duration, disc_number=disc,
                           favorite=favorite)


ARTIST = _media("artist", 3, "Kaláka", artist="")
ALBUMS = [_media("album", 7, "Az én szívem", favorite=True), _media("album", 8, "Az én szívem (deluxe)")]
PLAYLIST = _media("playlist", 2, "Esti dal", artist="")
SPOTIFY_ARTIST = SimpleNamespace(ARTIST.__dict__ | {"uri": "spotify://artist/kalaka", "item_id": "kalaka", "provider": "spotify"})
SONGS = [_media("track", 12, "Esti dal", duration=180), _media("track", 13, "Kicsi, gyere velem", duration=181)]
for _album in ALBUMS:
    _album.artists, _album.year = [ARTIST], 2004
for _song in SONGS:
    _song.artists, _song.album = [ARTIST], ALBUMS[0]
DELUXE = SONGS + [_media("track", 14, "Esti dal", duration=182, disc=2)]


class _Client:
    def __init__(self, calls, **queue):
        state = dict(queue_id="office", items=len(TITLES), current_index=12, index_in_buffer=13,
                     repeat_mode=RepeatMode.OFF, is_dynamic=False, autoplay_enabled=False,
                     crossfade_enabled=True)
        items = [_item(n) for n in range(len(TITLES))]
        state.update(current_item=items[12], next_item=items[13])
        state.update(queue)
        q = SimpleNamespace(**state)

        async def get_active_queue(player_id):
            return q

        async def get_queue_items(queue_id, limit=500, offset=0):
            calls.append(("items", offset, limit))
            return items[offset:offset + min(limit, 10)]

        async def move_item(queue_id, queue_item_id, pos_shift=1):
            calls.append(("move", queue_id, queue_item_id, pos_shift))

        async def delete_item(queue_id, item_id_or_index):
            calls.append(("delete", queue_id, item_id_or_index))

        async def play_media(queue_id, media, option=None, start_item=None):
            calls.append(("play_media", queue_id, media, option) + ((start_item,) if start_item else ()))

        answers = {"audio_analysis/wave_form": [n / 1800 for n in range(1800)],
                   "metadata/get_track_lyrics": ["Esti dal", "[00:01.50] Esti dal"],
                   "players/sleep_timer/get": 1000.0,
                   "music/artists/similar_artists": [{"item_id": "4", "provider": "library", "name": "Kolinda",
                                                      "provider_mappings": []}]}

        async def send_command(command, **args):
            calls.append((command, args))
            return answers.get(command)

        async def get_album_tracks(item_id, provider):
            calls.append(("album_tracks", item_id, provider))
            if item_id == "8":
                return DELUXE
            return [SimpleNamespace(uri=f"library://track/{n + 1}", name=TITLES[n], version=None, track_number=n + 1,
                                    disc_number=1, duration=180 + n, media_type=MediaType.TRACK, favorite=n == 12)
                    for n in range(11, 14)]

        self.send_command = send_command
        async def search(text, media_types, limit=50):
            calls.append(("search", text, tuple(media_types), limit))
            return SimpleNamespace(artists=[ARTIST], albums=ALBUMS, playlists=[PLAYLIST], tracks=SONGS)

        async def get_item_by_uri(uri):
            return {i.uri: i for i in [ARTIST, SPOTIFY_ARTIST, PLAYLIST] + ALBUMS}[uri]

        async def get_library_item_by_prov_id(media_type, item_id, provider):
            return ARTIST if (item_id, provider) == ("kalaka", "spotify") else None

        async def get_item(media_type, item_id, provider):
            return {(i.media_type, i.item_id): i for i in [ARTIST, PLAYLIST] + ALBUMS}[(media_type, item_id)]

        async def add_item_to_favorites(uri):
            calls.append(("favorite", uri))

        async def remove_item_from_favorites(media_type, item_id):
            calls.append(("unfavorite", media_type, item_id))

        async def listing(name, *args, **kwargs):
            calls.append((name,) + args + tuple(sorted(kwargs.items())))
            return {"artist_albums": ALBUMS, "library_albums": ALBUMS, "library_playlists": [],
                    "library_artists": [ARTIST]}.get(name, SONGS)

        self.music = SimpleNamespace(get_album_tracks=get_album_tracks, search=search, get_item_by_uri=get_item_by_uri,
                                     get_item=get_item, get_library_item_by_prov_id=get_library_item_by_prov_id,
                                     add_item_to_favorites=add_item_to_favorites,
                                     remove_item_from_favorites=remove_item_from_favorites,
                                     **{f"get_{name}": (lambda name: lambda *a, **k: listing(name, *a, **k))(name)
                                        for name in ("artist_tracks", "artist_albums", "playlist_tracks",
                                                     "library_albums", "library_playlists", "library_artists",
                                                     "library_tracks")},
                                     recently_played=lambda **k: listing("recently_played", **k))
        self.player_queues = SimpleNamespace(get_active_queue=get_active_queue, get_queue_items=get_queue_items,
                                             move_item=move_item, delete_item=delete_item, play_media=play_media)
        self.music.get_media_item_image = lambda item, kind: SimpleNamespace(
            proxy_id=getattr(item, 'queue_item_id', item.uri), path="https://navidrome/img", remotely_accessible=True)
        self.server_info = SimpleNamespace(base_url="http://ma")


def _window(monkeypatch, offset=None, limit=110, **queue):
    calls = []
    return asyncio.run(ma_control._queue_window(_Client(calls, **queue), "Office", offset, limit)), calls


def test_queue_window_starts_ten_songs_before_the_playing_one(monkeypatch):
    window, _ = _window(monkeypatch)
    assert (window["offset"], window["index"], window["locked"], window["total"]) == (2, 12, 13, 15)
    assert [row["item_id"] for row in window["items"]] == [f"q{n}" for n in range(2, 15)]
    assert window["items"][0]["title"] == "Nem hallod, üvöltök" and window["items"][0]["album"] == "Az én szívem"


def test_queue_window_counts_the_songs_after_the_playing_one(monkeypatch):
    window, _ = _window(monkeypatch)
    assert window["after"] == (193 + 194) * 1000


def test_queue_window_reads_the_whole_queue_in_pages(monkeypatch):
    window, calls = _window(monkeypatch, offset=5, limit=3)
    assert calls == [("items", 0, 500), ("items", 10, 500)]
    assert window["offset"] == 5 and [row["item_id"] for row in window["items"]] == ["q5", "q6", "q7"]


def test_queue_window_has_no_time_left_on_repeat_or_radio(monkeypatch):
    assert _window(monkeypatch, repeat_mode=RepeatMode.ALL)[0]["after"] == -1
    assert _window(monkeypatch, is_dynamic=True)[0]["after"] == -1


def _edit(monkeypatch, kind, item_id, shift=0):
    calls = []
    done = asyncio.run(ma_control._edit_queue(_Client(calls), "Office", kind, item_id, shift))
    return done, [call for call in calls if call[0] != "items"]


def test_move_shifts_a_song_in_the_queue(monkeypatch):
    assert _edit(monkeypatch, "move", "q14", -1) == (True, [("move", "office", "q14", -1)])


def test_play_next_moves_a_song_after_the_playing_one(monkeypatch):
    assert _edit(monkeypatch, "play_next", "q14") == (True, [("move", "office", "q14", 0)])


def test_move_to_end_and_delete(monkeypatch):
    assert _edit(monkeypatch, "move_end", "q13") == (
        True, [("player_queues/move_item_end", {"queue_id": "office", "queue_item_id": "q13"})])
    assert _edit(monkeypatch, "delete", "q14") == (True, [("delete", "office", "q14")])


def test_clear_empties_the_queue(monkeypatch):
    assert _edit(monkeypatch, "clear", "") == (True, [("player_queues/clear", {"queue_id": "office"})])


def test_endless_mix_replaces_the_queue_with_a_mix_of_the_song(monkeypatch):
    assert _edit(monkeypatch, "mix", "q3") == (
        True, [("play_media", "office", "radio_playlist://playlist/library://track/4", QueueOption.REPLACE)])


def _wave(monkeypatch, item_id=None, **queue):
    calls = []
    return asyncio.run(ma_control._wave_form(_Client(calls, **queue), "Office", item_id)), calls


def test_wave_form_of_the_playing_song(monkeypatch):
    bars, calls = _wave(monkeypatch)
    assert calls == [("audio_analysis/wave_form", {"item_id": "/music/Kaláka/13.flac",
                                                    "provider_instance_id_or_domain": "filesystem--abc"})]
    assert len(bars) == 1800 and bars[0] == 0.0 and bars[900] == 0.5 and bars[-1] == 1.0


def test_wave_form_of_the_next_song(monkeypatch):
    _, calls = _wave(monkeypatch, "q13")
    assert calls[0][1]["item_id"] == "/music/Kaláka/14.flac"


def test_wave_form_needs_stream_details(monkeypatch):
    item = _item(12)
    item.streamdetails = None
    assert _wave(monkeypatch, current_item=item) == (None, [])


def _run(monkeypatch, fn, *args):
    calls = []
    monkeypatch.setattr(ma_control.time, "time", lambda: 400.0)
    return asyncio.run(fn(_Client(calls), "Office", *args)), calls


def test_lyrics_of_the_playing_song(monkeypatch):
    lyrics, calls = _run(monkeypatch, ma_control._lyrics, None)
    assert lyrics == {"plain": "Esti dal", "lrc": "[00:01.50] Esti dal"}
    assert calls == [("metadata/get_track_lyrics", {"track": {"uri": "library://track/13"}})]


def test_options_read_the_sleep_timer_and_autoplay(monkeypatch):
    assert _run(monkeypatch, ma_control._options, None, 0) == (
        {"sleep_left": 600, "autoplay": False, "crossfade": True}, [("players/sleep_timer/get", {"player_id": "Office"})])


def test_sleep_timer_is_set_in_minutes_and_cleared_with_zero(monkeypatch):
    _, calls = _run(monkeypatch, ma_control._options, "sleep", 30)
    assert calls[0] == ("players/sleep_timer/set", {"player_id": "Office", "seconds": 1800})
    _, calls = _run(monkeypatch, ma_control._options, "sleep", 0)
    assert calls[0] == ("players/sleep_timer/clear", {"player_id": "Office"})


def test_autoplay_is_switched_on_the_queue(monkeypatch):
    options, calls = _run(monkeypatch, ma_control._options, "autoplay", True)
    assert calls[0] == ("player_queues/autoplay", {"queue_id": "office", "autoplay_enabled": True})
    assert options["autoplay"] is True


def test_crossfade_is_switched_on_the_queue(monkeypatch):
    options, calls = _run(monkeypatch, ma_control._options, "crossfade", False)
    assert calls[0] == ("player_queues/crossfade", {"queue_id": "office", "crossfade_enabled": False})
    assert options["crossfade"] is False


def test_the_playing_songs_album_opens_at_the_song(monkeypatch):
    found, _ = _run(monkeypatch, ma_control._browse, "playing", "")
    assert (found["type"], found["item"], found["uri"]) == ("album", "library://album/7", "library://album/7")
    assert found["now"] == ["library://track/13", "library://album/7", "library://artist/3"]
    assert [r["name"] for r in found["sections"][0]["rows"]] == ["Esti dal", "Kicsi, gyere velem", "A bolond levele"]


def test_the_playing_songs_artist_opens(monkeypatch):
    found, calls = _run(monkeypatch, ma_control._browse, "playing", "artist")
    assert (found["type"], found["item"], found["title"]) == ("artist", "library://artist/3", "Kaláka")
    assert [s["name"] for s in found["sections"]] == ["Albums", "Songs", "Similar artists"]


def test_play_album_from_a_track(monkeypatch):
    assert _run(monkeypatch, ma_control._play_media, "library://album/7", "replace", "library://track/12") == (
        True, [("play_media", "office", "library://album/7", QueueOption.REPLACE, "library://track/12")])


def test_add_a_song_to_the_queue(monkeypatch):
    assert _run(monkeypatch, ma_control._play_media, "library://track/12", "add", None) == (
        True, [("play_media", "office", "library://track/12", QueueOption.ADD)])


def test_search_shows_artists_albums_playlists_and_songs(monkeypatch):
    found, calls = _run(monkeypatch, ma_control._browse, "search", "esti dal")
    assert calls == [("search", "esti dal", (MediaType.ARTIST, MediaType.ALBUM, MediaType.PLAYLIST, MediaType.TRACK), 10)]
    assert found["title"] == "esti dal"
    assert [(s["name"], [r["name"] for r in s["rows"]]) for s in found["sections"]] == [
        ("Artists", ["Kaláka"]), ("Albums", ["Az én szívem", "Az én szívem (deluxe)"]), ("Playlists", ["Esti dal"]),
        ("Songs", ["Esti dal", "Kicsi, gyere velem"])]
    assert found["sections"][3]["rows"][0] == {"uri": "library://track/12", "type": "track", "name": "Esti dal",
                                               "sub": "Kaláka", "image": "http://ma/imageproxy/library://track/12?size=256", "duration_ms": 180000,
                                               "disc": 1, "favorite": False,
                                               "go": [["artist", "Kaláka", "library://artist/3"],
                                                      ["album", "Az én szívem", "library://album/7"]]}
    assert found["sections"][1]["rows"][0]["go"] == [["artist", "Kaláka", "library://artist/3"]]


def test_an_artist_opens_with_songs_and_albums(monkeypatch):
    found, calls = _run(monkeypatch, ma_control._browse, "open", "library://artist/3")
    assert calls[:2] == [("artist_albums", "3", "library"), ("artist_tracks", "3", "library")]
    assert (found["title"], found["uri"], [s["name"] for s in found["sections"]]) == (
        "Kaláka", "", ["Albums", "Songs", "Similar artists"])
    assert [(r["type"], r["name"], r["uri"]) for r in found["sections"][2]["rows"]] == [
        ("artist", "Kolinda", "library://artist/4")]
    assert found["header"] == {"image": "http://ma/imageproxy/library://artist/3?size=512", "artist": "", "artist_uri": "",
                               "year": 0}


def test_a_streaming_artist_gets_similar_artists_from_its_library_copy(monkeypatch):
    found, calls = _run(monkeypatch, ma_control._browse, "open", "spotify://artist/kalaka")
    assert ("music/artists/similar_artists", {"item_id": "3", "provider_instance_id_or_domain": "library", "limit": 20}) in calls
    assert [s["name"] for s in found["sections"]] == ["Albums", "Songs", "Similar artists"]


def test_a_playlist_opens_with_its_songs(monkeypatch):
    found, calls = _run(monkeypatch, ma_control._browse, "open", "library://playlist/2")
    assert calls == [("playlist_tracks", "2", "library", ("limit", 500))]
    assert found["uri"] == "library://playlist/2"
    assert [r["name"] for r in found["sections"][0]["rows"]] == ["Esti dal", "Kicsi, gyere velem"]


def test_browse_marks_the_playing_song_album_and_artist(monkeypatch):
    found, _ = _run(monkeypatch, ma_control._browse, "favorites", "")
    assert found["now"] == ["library://track/13", "library://album/7", "library://artist/3"]


def test_what_plays_next(monkeypatch):
    assert _run(monkeypatch, ma_control._now, "q13")[0] == ["library://track/14", "library://album/7", "library://artist/3"]


def test_favourites_skip_empty_sections(monkeypatch):
    found, _ = _run(monkeypatch, ma_control._browse, "favorites", "")
    assert [s["name"] for s in found["sections"]] == ["Albums", "Artists", "Songs"]


def test_an_album_opens_with_its_discs_and_favourite(monkeypatch):
    found, _ = _run(monkeypatch, ma_control._browse, "open", "library://album/8")
    assert (found["type"], found["item"], found["favorite"]) == ("album", "library://album/8", False)
    assert [r["disc"] for r in found["sections"][0]["rows"]] == [1, 1, 2]
    assert found["header"] == {"image": "http://ma/imageproxy/library://album/8?size=512", "artist": "Kaláka",
                               "artist_uri": "library://artist/3", "year": 2004}
    assert _run(monkeypatch, ma_control._browse, "open", "library://album/7")[0]["favorite"] is True


def test_favourite_an_album(monkeypatch):
    assert _run(monkeypatch, ma_control._set_favorite, "library://album/8", True) == (
        True, [("favorite", "library://album/8")])


def test_unfavourite_an_album(monkeypatch):
    assert _run(monkeypatch, ma_control._set_favorite, "library://album/7", False) == (
        True, [("unfavorite", MediaType.ALBUM, "7")])
