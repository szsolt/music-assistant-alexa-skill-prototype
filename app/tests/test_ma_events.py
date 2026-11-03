from types import SimpleNamespace as NS

import pytest

pytest.importorskip("music_assistant_client")

from skill.ma import events as ma_events  # noqa: E402


def test_players_of_a_queue():
    players = [NS(player_id="g", synced_to="leader", active_group=None, active_source=None),
               NS(player_id="h", synced_to=None, active_group=None, active_source="h"),
               NS(player_id="i", synced_to=None, active_group="leader", active_source=None)]
    client = NS(players=NS(players=players))
    assert ma_events._players_of(client, "leader") == ["g", "i"]

