"""MA's errors in the skill's logs: their text, without the query strings of the URLs in it."""

import pytest
from music_assistant_client.exceptions import MusicAssistantClientException
from music_assistant_models.errors import MusicAssistantError

from skill import devices
from skill.ma import player
from skill.ma.errors import MAFailed, or_none, safe_error

pytest.importorskip("ask_sdk_core")

URL = "https://music.example/rest/stream.view?u=kalaka&t=abc&s=def"


def test_safe_error_keeps_the_text_and_the_url_without_its_query():
    assert safe_error(MusicAssistantError(f"Could not play {URL} (404)")) == \
        "Could not play https://music.example/rest/stream.view (404)"


def test_safe_error_without_text_is_the_error_type():
    assert safe_error(MusicAssistantError()) == "MusicAssistantError"


@pytest.mark.parametrize("error", [MusicAssistantError, MusicAssistantClientException])
@pytest.mark.parametrize("said", [("AMAZON.NextIntent", "next_track"), ("AddFavorite", "add_item_to_favorites")])
def test_ma_errors_are_logged_without_query_strings(ma, echo, caplog, error, said):
    intent, call = said

    async def refused(*args, **kwargs):
        raise error(f"Could not fetch {URL}")

    devices.set_player_for_device("d", "Den")
    setattr(ma.players if call == "next_track" else ma.music, call, refused)
    caplog.set_level("INFO")
    echo("d", "none").intent(intent)
    assert "music.example/rest/stream.view" in caplog.text
    assert "t=abc" not in caplog.text and "s=def" not in caplog.text


def test_an_unexpected_error_in_an_ma_call_is_logged_without_query_strings(ma, caplog):
    async def broken(*args, **kwargs):
        raise RuntimeError(f"Could not read {URL}")

    ma.player_queues.get_active_queue = broken
    with pytest.raises(MAFailed) as failed:
        player.queue_window("Den")
    assert "t=abc" not in str(failed.value)
    assert or_none("read the queue", player.queue_window, "Den") is None
    assert "music.example/rest/stream.view" in caplog.text
    assert "t=abc" not in caplog.text and "s=def" not in caplog.text


def test_an_unexpected_error_in_an_ma_call_logs_where_it_happened_at_debug(ma, caplog):
    async def broken(*args, **kwargs):
        raise RuntimeError(f"Could not read {URL}")

    ma.player_queues.get_active_queue = broken
    caplog.set_level("DEBUG", logger="skill.ma.client")
    with pytest.raises(MAFailed):
        player.queue_window("Den")
    assert "in broken" in caplog.text
    assert "t=abc" not in caplog.text and "s=def" not in caplog.text
