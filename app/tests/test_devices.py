import json

from skill import config
from skill import devices as dm


def test_unpaired_echo_pairs_with_the_waiting_player(clock):
    dm.wait_for_pairing("kitchen")
    clock.advance(5)
    assert dm.pair_if_waiting("echo-2") == "kitchen"
    assert dm.get_player_for_device("echo-2") == "kitchen"
    assert dm.pair_if_waiting("echo-3") is None   # taken once


def test_no_pairing_after_the_window(clock):
    dm.wait_for_pairing("kitchen")
    clock.advance(dm.PAIR_WINDOW_S + 1)
    assert dm.pair_if_waiting("echo-2") is None
    assert dm.get_player_for_device("echo-2") is None


def test_no_pairing_with_two_waiting_players(clock):
    dm.wait_for_pairing("kitchen")
    clock.advance(1)
    dm.wait_for_pairing("den")
    assert dm.pair_if_waiting("echo-2") is None


def test_a_paired_echo_keeps_its_player():
    dm.save_mapping({"echo-1": "den"})
    dm.wait_for_pairing("kitchen")
    assert dm.pair_if_waiting("echo-1") is None
    assert dm.get_player_for_device("echo-1") == "den"
    assert dm.pair_if_waiting("echo-2") == "kitchen"   # still waiting


def test_a_paired_player_waits_for_nothing():
    dm.save_mapping({"echo-1": "den"})
    dm.wait_for_pairing("den")
    assert dm.pair_if_waiting("echo-2") is None


def test_an_unpaired_echo_does_not_play_another_echos_stream():
    dm.save_mapping({"echo-1": "den"})
    assert dm.is_another_echos_stream("echo-2", "den")
    assert not dm.is_another_echos_stream("echo-1", "den")
    assert not dm.is_another_echos_stream("echo-2", "kitchen")   # no Echo yet: plays, as before
    assert not dm.is_another_echos_stream("echo-2", None)        # an MA that sends no playerId


def test_a_paired_echo_does_not_play_another_players_stream():
    dm.save_mapping({"echo-1": "den"})
    assert dm.is_other_players_stream("echo-1", "kitchen")
    assert not dm.is_other_players_stream("echo-1", "den")
    assert not dm.is_other_players_stream("echo-2", "kitchen")   # unpaired: is_another_echos_stream's case
    assert not dm.is_other_players_stream("echo-1", None)        # an MA that sends no playerId


def test_a_mapping_edited_in_its_file_is_read_again():
    dm.set_player_for_device("kitchen-echo", "Kitchen")
    assert dm.get_player_for_device("kitchen-echo") == "Kitchen"
    with open(config.device_mapping_path(), "w", encoding="utf-8") as f:
        json.dump({"kitchen-echo": "Living room"}, f)
    assert dm.get_player_for_device("kitchen-echo") == "Living room"
