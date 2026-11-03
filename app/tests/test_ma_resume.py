from skill.ma import player


def test_resume_seeks_the_current_track(ma):
    assert player.resume_at("Den", 61_000) == 61_000
    assert ma.calls == [("seek", "den", 61)]


def test_resume_at_an_item_plays_it_from_the_start(ma):
    assert player.resume_at("Den", item_id="q2") == 0
    assert ma.calls == [("play_index", "den", "q2")]
