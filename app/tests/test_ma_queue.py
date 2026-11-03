from types import SimpleNamespace

import pytest
from music_assistant_models.enums import MediaType, QueueOption, RepeatMode

from fake_ma import PLAYLIST, TITLES, item
from skill.ma import library, player


def _window(ma, offset=None, limit=110, **queue):
    vars(ma.queue).update(queue)
    return player.queue_window("Den", offset, limit), ma.calls


def test_queue_window_starts_ten_songs_before_the_playing_one(ma):
    window, _ = _window(ma)
    assert (window["offset"], window["index"], window["locked"], window["total"]) == (2, 12, 13, 15)
    assert [row["item_id"] for row in window["items"]] == [f"q{n}" for n in range(2, 15)]
    assert window["items"][0]["title"] == "Nem hallod, üvöltök" and window["items"][0]["album"] == "Az én szívem"


def test_queue_window_counts_the_songs_after_the_playing_one(ma):
    window, _ = _window(ma)
    assert window["after"] == (193 + 194) * 1000


def test_queue_window_reads_the_whole_queue_in_pages(ma):
    window, calls = _window(ma, offset=5, limit=3)
    assert calls == [("items", 0, 500), ("items", 10, 500)]
    assert window["offset"] == 5 and [row["item_id"] for row in window["items"]] == ["q5", "q6", "q7"]


def test_queue_window_has_no_time_left_on_repeat_or_radio(ma):
    assert _window(ma, repeat_mode=RepeatMode.ALL)[0]["after"] == -1
    assert _window(ma, is_dynamic=True)[0]["after"] == -1


def _edit(ma, kind, item_id, shift=0):
    del ma.calls[:]
    done = player.edit_queue("Den", kind, item_id, shift)
    return done, [call for call in ma.calls if call[0] != "items"]


def test_each_queue_edit_is_one_ma_call(ma):
    assert _edit(ma, "move", "q14", -1) == (True, [("move", "den", "q14", -1)])
    assert _edit(ma, "play_next", "q14") == (True, [("move", "den", "q14", 0)])
    assert _edit(ma, "move_end", "q13") == (
        True, [("player_queues/move_item_end", {"queue_id": "den", "queue_item_id": "q13"})])
    assert _edit(ma, "delete", "q14") == (True, [("delete", "den", "q14")])
    assert _edit(ma, "clear", "") == (True, [("player_queues/clear", {"queue_id": "den"})])


def test_endless_mix_replaces_the_queue_with_a_mix_of_the_song(ma):
    assert _edit(ma, "mix", "q3") == (
        True, [("play_media", "den", "radio_playlist://playlist/library://track/4", QueueOption.REPLACE)])


def _wave(ma, item_id=None, **queue):
    vars(ma.queue).update(queue)
    return library.wave_form("Den", item_id), ma.calls


def test_wave_form_of_the_playing_song(ma):
    bars, calls = _wave(ma)
    assert calls == [("audio_analysis/wave_form", {"item_id": "/music/Kaláka/13.flac",
                                                    "provider_instance_id_or_domain": "filesystem--abc"})]
    assert len(bars) == 1800 and bars[0] == 0.0 and bars[900] == 0.5 and bars[-1] == 1.0


def test_wave_form_of_the_next_song(ma):
    _, calls = _wave(ma, "q13")
    assert calls[0][1]["item_id"] == "/music/Kaláka/14.flac"


def test_wave_form_needs_stream_details(ma):
    playing = item(12)
    playing.streamdetails = None
    assert _wave(ma, current_item=playing) == (None, [])


@pytest.fixture
def run(ma, clock):
    """fn("Den", *args) on the fake MA at 400 s: its result and the calls MA got."""
    clock.now = 400.0

    def run(fn, *args):
        del ma.calls[:]
        return fn("Den", *args), list(ma.calls)
    return run


def test_lyrics_of_the_playing_song(run):
    lyrics, calls = run(library.lyrics, None)
    assert lyrics == {"plain": "Esti dal", "lrc": "[00:01.50] Esti dal"}
    assert calls == [("metadata/get_track_lyrics", {"track": {"uri": "library://track/13"}})]


def test_queue_options_are_read_and_switched_in_ma(run):
    assert run(player.options, None, 0) == ({"autoplay": False, "crossfade": True}, [])
    _, calls = run(player.options, "sleep", 30)
    assert calls[0] == ("players/sleep_timer/set", {"player_id": "Den", "seconds": 1800})
    _, calls = run(player.options, "sleep", 0)
    assert calls[0] == ("players/sleep_timer/clear", {"player_id": "Den"})
    options, calls = run(player.options, "autoplay", True)
    assert calls[0] == ("player_queues/autoplay", {"queue_id": "den", "autoplay_enabled": True})
    assert options["autoplay"] is True
    options, calls = run(player.options, "crossfade", False)
    assert calls[0] == ("player_queues/crossfade", {"queue_id": "den", "crossfade_enabled": False})
    assert options["crossfade"] is False


def test_the_playing_songs_album_opens_at_the_song(run):
    found, _ = run(library.browse, "playing", "")
    assert (found["type"], found["item"], found["uri"]) == ("album", "library://album/7", "library://album/7")
    assert found["now"] == ["library://track/13", "library://album/7", "library://artist/3"]
    assert [r["name"] for r in found["sections"][0]["rows"]] == ["Esti dal", "Kicsi, gyere velem", "A bolond levele"]


def test_the_playing_songs_artist_opens(run):
    found, calls = run(library.browse, "playing", "artist")
    assert (found["type"], found["item"], found["title"]) == ("artist", "library://artist/3", "Kaláka")
    assert [s["name"] for s in found["sections"]] == ["Albums", "Songs", "Similar artists"]


def test_play_an_album_from_a_track_or_add_a_song(run):
    assert run(player.play_media, "library://album/7", "replace", "library://track/12") == (
        True, [("play_media", "den", "library://album/7", QueueOption.REPLACE, "library://track/12")])
    assert run(player.play_media, "library://track/12", "add", None) == (
        True, [("play_media", "den", "library://track/12", QueueOption.ADD)])


def test_search_shows_artists_albums_playlists_songs_podcasts_and_audiobooks(run):
    found, calls = run(library.browse, "search", "esti dal")
    assert calls == [("search", "esti dal", (MediaType.ARTIST, MediaType.ALBUM, MediaType.PLAYLIST, MediaType.TRACK,
                                             MediaType.PODCAST, MediaType.AUDIOBOOK), 10)]
    assert found["title"] == "esti dal"
    assert [(s["name"], [r["name"] for r in s["rows"]]) for s in found["sections"]] == [
        ("Artists", ["Kaláka"]), ("Albums", ["Az én szívem", "Az én szívem (deluxe)"]), ("Playlists", ["Esti dal"]),
        ("Songs", ["Esti dal", "Kicsi, gyere velem"]), ("Podcasts", ["Esti mese"]), ("Audiobooks", ["A Pál utcai fiúk"])]
    assert found["sections"][3]["rows"][0] == {"uri": "library://track/12", "type": "track", "name": "Esti dal",
                                               "sub": "Kaláka", "image": "http://ma/imageproxy/library://track/12?size=256", "duration_ms": 180000,
                                               "disc": 1, "favorite": False, "played": "",
                                               "go": [["artist", "Kaláka", "library://artist/3"],
                                                      ["album", "Az én szívem", "library://album/7"]]}
    assert found["sections"][1]["rows"][0]["go"] == [["artist", "Kaláka", "library://artist/3"]]


def test_an_artist_opens_with_songs_and_albums(run):
    found, calls = run(library.browse, "open", "library://artist/3")
    assert calls[:2] == [("artist_albums", "3", "library"), ("artist_tracks", "3", "library")]
    assert (found["title"], found["uri"], [s["name"] for s in found["sections"]]) == (
        "Kaláka", "", ["Albums", "Songs", "Similar artists"])
    assert [(r["type"], r["name"], r["uri"]) for r in found["sections"][2]["rows"]] == [
        ("artist", "Kolinda", "library://artist/4")]
    assert found["header"] == {"image": "http://ma/imageproxy/library://artist/3?size=512", "artist": "", "artist_uri": "",
                               "year": 0}


def test_a_streaming_artist_gets_similar_artists_from_its_library_copy(run):
    found, calls = run(library.browse, "open", "spotify://artist/kalaka")
    assert ("music/artists/similar_artists", {"item_id": "3", "provider_instance_id_or_domain": "library", "limit": 20}) in calls
    assert [s["name"] for s in found["sections"]] == ["Albums", "Songs", "Similar artists"]


def test_a_playlist_opens_with_its_songs(run):
    found, calls = run(library.browse, "open", "library://playlist/2")
    assert calls == [("playlist_tracks", "2", "library", ("limit", 500))]
    assert found["uri"] == "library://playlist/2"
    assert [r["name"] for r in found["sections"][0]["rows"]] == ["Esti dal", "Kicsi, gyere velem"]


def test_browse_marks_the_playing_song_album_and_artist(run):
    found, _ = run(library.browse, "favorites", "")
    assert found["now"] == ["library://track/13", "library://album/7", "library://artist/3"]


def test_what_plays_next(run):
    assert run(library.now_playing, "q13")[0]["uris"] == ["library://track/14", "library://album/7", "library://artist/3"]


def test_favourites_skip_empty_sections(run):
    found, _ = run(library.browse, "favorites", "")
    assert [s["name"] for s in found["sections"]] == ["Albums", "Artists", "Songs", "Podcasts"]


def test_the_library_lists_everything_but_songs(run):
    found, calls = run(library.browse, "library", "")
    assert found["title"] == "Library"
    assert [s["name"] for s in found["sections"]] == ["Albums", "Artists", "Podcasts"]
    assert ("library_albums", ("limit", 1000)) in calls


def test_an_album_opens_with_its_discs_and_favourite(run):
    found, _ = run(library.browse, "open", "library://album/8")
    assert (found["type"], found["item"], found["favorite"]) == ("album", "library://album/8", False)
    assert [r["disc"] for r in found["sections"][0]["rows"]] == [1, 1, 2]
    assert found["header"] == {"image": "http://ma/imageproxy/library://album/8?size=512", "artist": "Kaláka",
                               "artist_uri": "library://artist/3", "year": 2004}
    assert run(library.browse, "open", "library://album/7")[0]["favorite"] is True


def test_favourite_and_unfavourite_an_album(ma):
    assert library.set_favorite("library://album/8", True) is True
    assert library.set_favorite("library://album/7", False) is True
    assert ma.calls == [("favorite", "library://album/8"), ("unfavorite", MediaType.ALBUM, "7")]


def test_playlists_lists_the_ones_that_take_songs(ma):
    async def get_library_playlists(**kwargs):
        return [SimpleNamespace(PLAYLIST.__dict__ | {"is_editable": True}),
                SimpleNamespace(PLAYLIST.__dict__ | {"item_id": "5", "name": "Daily Mix 1", "is_editable": False})]
    ma.music.get_library_playlists = get_library_playlists
    assert library.playlists() == [["2", "Esti dal"]]


def test_add_songs_or_the_queue_to_a_playlist(run):
    assert run(library.add_to_playlist, "2", "Esti dal", "library://track/12", "", False) == (
        "Esti dal", [("add_tracks", "2", ["library://track/12"])])
    name, calls = run(library.add_to_playlist, "2", "Esti dal", "", "q3", False)
    assert (name, calls[-1]) == ("Esti dal", ("add_tracks", "2", ["library://track/4"]))
    name, calls = run(library.add_to_playlist, "", "Esti mix", "", "", True)
    assert name == "Esti mix"
    assert ("create_playlist", "Esti mix") in calls
    assert calls[-1] == ("add_tracks", "21", [f"library://track/{n + 1}" for n in range(len(TITLES))])


def test_podcast_and_audiobook_rows_show_publisher_and_authors(run):
    found, _ = run(library.browse, "search", "esti")
    assert [s["rows"][0]["sub"] for s in found["sections"][-2:]] == ["Kossuth Rádió", "Molnár Ferenc"]


def test_a_podcast_opens_with_its_episodes_newest_first(run):
    found, _ = run(library.browse, "open", "library://podcast/5")
    assert (found["type"], found["title"], found["uri"]) == ("podcast", "Esti mese", "")
    assert found["header"] == {"image": "http://ma/imageproxy/library://podcast/5?size=512", "artist": "Kossuth Rádió",
                               "artist_uri": "", "year": 0}
    assert found["sections"][0]["name"] == "Episodes"
    assert [(r["name"], r["sub"], r["duration_ms"], r["played"]) for r in found["sections"][0]["rows"]] == [
        ("Esti mese 3", "29 Oct 2026", 903000, ""), ("Esti mese 2", "28 Oct 2026", 902000, "part"),
        ("Esti mese 1", "27 Oct 2026", 901000, "done")]
