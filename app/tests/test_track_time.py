from skill import track_time as tt


def test_video_position_from_event_arguments():
    assert tt.video_position_ms(["MetadataRefresh", 3, 125000]) == 125000
    assert tt.video_position_ms(["MetadataRefresh", 3, "1250.7"]) == 1250
    assert tt.video_position_ms(["MetadataRefresh", 3, -5]) == 0


def test_video_position_missing_or_garbage():
    assert tt.video_position_ms(["MetadataRefresh", 3]) is None
    assert tt.video_position_ms(["MetadataRefresh", 3, "${videoProgressValue}"]) is None
    assert tt.video_position_ms(None) is None


def test_offset_is_position_minus_elapsed():
    # third track of a flow stream: 400 s of stream played, 12 s into the track
    assert tt.track_offset_ms(400_000, 12_000) == 388_000


def test_offset_never_negative():
    # a fresh page: the video lags MA's clock slightly
    assert tt.track_offset_ms(300, 1_200) == 0


def test_tracker_reports_each_change_once_per_session():
    t = tt.TrackChangeTracker()
    a, b = ("u", "Song A", "X"), ("u", "Song B", "X")
    assert t.changed("s1", a) is True
    assert t.changed("s1", a) is False
    assert t.changed("s1", b) is True
    assert t.changed("s2", b) is True   # a new page is a change
    assert t.changed("s1", b) is False


def test_tracker_forgets_oldest_sessions():
    t = tt.TrackChangeTracker(max_sessions=2)
    k = ("u", "Song", "")
    t.changed("s1", k)
    t.changed("s2", k)
    t.changed("s3", k)
    assert t.changed("s1", k) is True


def test_track_key_uses_stream_and_titles():
    info = {"audioSources": "https://h/flow/x.mp3", "primaryText": "T", "secondaryText": "A - B"}
    assert tt.track_key(info) == ("https://h/flow/x.mp3", "T", "A - B")
    assert tt.track_key({}) == ("", "", "")


def test_set_track_time_commands():
    cmds = tt.set_track_time_commands(388_000, 215_000)
    assert [(c["property"], c["value"]) for c in cmds] == [("trackOffset", 388_000), ("trackDuration", 215_000)]
    assert all(c["componentId"] == "AudioPlayerRoot" for c in cmds)


def test_set_track_time_commands_unknown():
    cmds = tt.set_track_time_commands(None, None)
    assert [(c["property"], c["value"]) for c in cmds] == [("trackDuration", 0)]
