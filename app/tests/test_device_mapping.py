from skill import device_mapping as dm


def setup_function():
    dm._waiting.clear()


def _use_tmp_mapping(tmp_path, monkeypatch, mapping=None):
    monkeypatch.setenv("DEVICE_MAPPING_PATH", str(tmp_path / "device_players.json"))
    if mapping:
        dm.save_mapping(mapping)


def test_unpaired_echo_pairs_with_the_waiting_player(tmp_path, monkeypatch):
    _use_tmp_mapping(tmp_path, monkeypatch)
    dm.wait_for_pairing("kitchen", now=100)
    assert dm.pair_if_waiting("echo-2", now=105) == "kitchen"
    assert dm.get_player_for_device("echo-2") == "kitchen"
    assert dm.pair_if_waiting("echo-3", now=106) is None   # taken once


def test_no_pairing_after_the_window(tmp_path, monkeypatch):
    _use_tmp_mapping(tmp_path, monkeypatch)
    dm.wait_for_pairing("kitchen", now=100)
    assert dm.pair_if_waiting("echo-2", now=101 + dm.PAIR_WINDOW_S) is None
    assert dm.get_player_for_device("echo-2") is None


def test_no_pairing_with_two_waiting_players(tmp_path, monkeypatch):
    _use_tmp_mapping(tmp_path, monkeypatch)
    dm.wait_for_pairing("kitchen", now=100)
    dm.wait_for_pairing("office", now=101)
    assert dm.pair_if_waiting("echo-2", now=102) is None


def test_a_paired_echo_keeps_its_player(tmp_path, monkeypatch):
    _use_tmp_mapping(tmp_path, monkeypatch, {"echo-1": "office"})
    dm.wait_for_pairing("kitchen", now=100)
    assert dm.pair_if_waiting("echo-1", now=101) is None
    assert dm.get_player_for_device("echo-1") == "office"
    assert dm.pair_if_waiting("echo-2", now=102) == "kitchen"   # still waiting


def test_a_paired_player_waits_for_nothing(tmp_path, monkeypatch):
    _use_tmp_mapping(tmp_path, monkeypatch, {"echo-1": "office"})
    dm.wait_for_pairing("office", now=100)
    assert dm.pair_if_waiting("echo-2", now=101) is None


def test_an_unpaired_echo_does_not_play_another_echos_stream(tmp_path, monkeypatch):
    _use_tmp_mapping(tmp_path, monkeypatch, {"echo-1": "office"})
    assert dm.is_another_echos_stream("echo-2", "office")
    assert not dm.is_another_echos_stream("echo-1", "office")
    assert not dm.is_another_echos_stream("echo-2", "kitchen")   # no Echo yet: plays, as before
    assert not dm.is_another_echos_stream("echo-2", None)        # an MA that sends no playerId
