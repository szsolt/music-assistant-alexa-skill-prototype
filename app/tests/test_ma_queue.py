import asyncio
from types import SimpleNamespace

from music_assistant_models.enums import QueueOption, RepeatMode

from skill import ma_control

TITLES = ["Kicsi, gyere velem", "A bolond levele", "Nem hallod, üvöltök", "Altató", "Mi van a zsebedben?",
          "Esti dal", "Hét szem mazsola", "Ringató", "Dal a hóról", "Hajnali ének", "Altató",
          "Esti dal", "Kicsi, gyere velem", "A bolond levele", "Ringató"]


def _item(n):
    media = SimpleNamespace(name=TITLES[n], version=None, artist_str="Kaláka", uri=f"library://track/{n + 1}",
                            album=SimpleNamespace(name="Az én szívem"), duration=180 + n)
    details = SimpleNamespace(item_id=f"/music/Kaláka/{n + 1}.flac", provider="filesystem--abc")
    return SimpleNamespace(queue_item_id=f"q{n}", name=TITLES[n], media_item=media, duration=180 + n,
                           streamdetails=details)


class _Client:
    def __init__(self, calls, **queue):
        state = dict(queue_id="office", items=len(TITLES), current_index=12, index_in_buffer=13,
                     repeat_mode=RepeatMode.OFF, is_dynamic=False)
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

        async def play_media(queue_id, media, option=None):
            calls.append(("play_media", queue_id, media, option))

        async def send_command(command, **args):
            calls.append((command, args))
            return [n / 1800 for n in range(1800)] if command == "audio_analysis/wave_form" else None

        self.send_command = send_command
        self.player_queues = SimpleNamespace(get_active_queue=get_active_queue, get_queue_items=get_queue_items,
                                             move_item=move_item, delete_item=delete_item, play_media=play_media)
        self.get_media_item_image_url = lambda item, size=512: f"img/{item.queue_item_id}"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _window(monkeypatch, offset=None, limit=110, **queue):
    calls = []
    monkeypatch.setattr(ma_control, "MusicAssistantClient", lambda *a, **k: _Client(calls, **queue))
    return asyncio.run(ma_control._queue_window("http://ma", "t", "Office", offset, limit)), calls


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
    monkeypatch.setattr(ma_control, "MusicAssistantClient", lambda *a, **k: _Client(calls))
    done = asyncio.run(ma_control._edit_queue("http://ma", "t", "Office", kind, item_id, shift))
    return done, [call for call in calls if call[0] != "items"]


def test_move_shifts_a_song_in_the_queue(monkeypatch):
    assert _edit(monkeypatch, "move", "q14", -1) == (True, [("move", "office", "q14", -1)])


def test_play_next_moves_a_song_after_the_playing_one(monkeypatch):
    assert _edit(monkeypatch, "play_next", "q14") == (True, [("move", "office", "q14", 0)])


def test_move_to_end_and_delete(monkeypatch):
    assert _edit(monkeypatch, "move_end", "q13") == (
        True, [("player_queues/move_item_end", {"queue_id": "office", "queue_item_id": "q13"})])
    assert _edit(monkeypatch, "delete", "q14") == (True, [("delete", "office", "q14")])


def test_endless_mix_replaces_the_queue_with_a_mix_of_the_song(monkeypatch):
    assert _edit(monkeypatch, "mix", "q3") == (
        True, [("play_media", "office", "radio_playlist://playlist/library://track/4", QueueOption.REPLACE)])


def _wave(monkeypatch, item_id=None, **queue):
    calls = []
    monkeypatch.setattr(ma_control, "MusicAssistantClient", lambda *a, **k: _Client(calls, **queue))
    return asyncio.run(ma_control._wave_form("http://ma", "t", "Office", item_id)), calls


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
