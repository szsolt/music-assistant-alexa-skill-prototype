import pytest

apl = pytest.importorskip("skill.apl")


def test_start_values_from_the_skill(monkeypatch):
    monkeypatch.setattr(apl, "_start_track_time", lambda device_id: (-65_000, 201_000))
    assert apl._start_values("d") == {"startOffset": -65_000, "startDuration": 201_000}


def test_no_start_values_without_device_or_answer(monkeypatch):
    monkeypatch.setattr(apl, "_start_track_time", lambda device_id: None)
    assert apl._start_values("d") == {}
    assert apl._start_values(None) == {}

    def broken(device_id):
        raise RuntimeError("MA down")
    monkeypatch.setattr(apl, "_start_track_time", broken)
    assert apl._start_values("d") == {}
