import asyncio
from types import SimpleNamespace

from skill import ma_control


class _Client:
    def __init__(self, calls):
        queue = SimpleNamespace(queue_id="office", corrected_elapsed_time=61.4,
                                current_item=SimpleNamespace(duration=215))

        async def get_active_queue(player_id):
            return queue

        async def play_index(queue_id, index):
            calls.append(("play_index", queue_id, index))

        async def seek(queue_id, position):
            calls.append(("seek", queue_id, position))

        self.player_queues = SimpleNamespace(get_active_queue=get_active_queue, play_index=play_index, seek=seek)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _resume(monkeypatch, **kwargs):
    calls = []
    monkeypatch.setattr(ma_control, "MusicAssistantClient", lambda *a, **k: _Client(calls))
    used = asyncio.run(ma_control._resume_at("http://ma", "t", "Office", kwargs.get("position_ms"),
                                             kwargs.get("item_id")))
    return used, calls


def test_resume_seeks_the_current_track(monkeypatch):
    used, calls = _resume(monkeypatch, position_ms=61_000)
    assert calls == [("seek", "office", 61)] and used == 61_000


def test_resume_at_an_item_plays_it_from_the_start(monkeypatch):
    used, calls = _resume(monkeypatch, item_id="q2")
    assert calls == [("play_index", "office", "q2")] and used == 0
