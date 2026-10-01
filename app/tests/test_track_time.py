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


def test_offset_prefers_previous_track_end():
    assert tt.choose_offset_ms(215_000, None, 3_000) == 215_000
    assert tt.choose_offset_ms(215_000, 400_000, 3_000) == 215_000


def test_offset_falls_back_to_position_then_none():
    assert tt.choose_offset_ms(None, 400_000, 12_000) == 388_000
    assert tt.choose_offset_ms(None, None, 12_000) is None
    assert tt.choose_offset_ms(None, 400_000, None) is None


def test_tracker_chains_track_ends_within_a_page():
    t = tt.TrackChangeTracker()
    t.changed("s1", ("u", "Rain", ""))
    assert t.previous_end("s1") == 0          # first track of a page
    t.record("s1", 0, 10_000)
    t.changed("s1", ("u", "Mercy Street", ""))
    assert t.previous_end("s1") == 10_000
    t.record("s1", 10_000, 376_000)
    t.changed("s1", ("u", "Next one", ""))
    assert t.previous_end("s1") == 386_000


def test_tracker_unknown_duration_breaks_the_chain():
    t = tt.TrackChangeTracker()
    t.changed("s1", ("u", "Radio", ""))
    t.record("s1", 0, None)
    t.changed("s1", ("u", "Song", ""))
    assert t.previous_end("s1") is None
    assert t.previous_end("nope") is None


def test_event_position_at_index():
    assert tt.video_position_ms(["Pause", 61000.4], index=1) == 61000
    assert tt.video_position_ms(["Play"], index=1) is None


def test_page_start_offset():
    # normal new page: the stream starts with the track
    assert tt.page_start_offset_ms(300, False, None, 4_000, None) == 0
    # reopened by our resume at 61 s: video 0 is 61 s into the track
    assert tt.page_start_offset_ms(300, False, None, 4_000, 61_000) == -61_000
    # reopened by MA's own resume at 2:34: the stream starts mid-track
    assert tt.page_start_offset_ms(1_000, False, None, 154_000, None) == -153_000
    assert tt.page_start_offset_ms(None, False, None, 154_000, None) == -154_000
    # ... at 9.5 s, where the skill saw MA pause: below the elapsed-time guess
    assert tt.page_start_offset_ms(1_000, False, None, 12_300, None, 9_569) == -9_569
    # our own resume wins over MA's pause position
    assert tt.page_start_offset_ms(300, False, None, 4_000, 61_000, 9_569) == -61_000


def test_title_key_ignores_stream_url():
    a = {"audioSources": "https://h/flow/a/x.mp3", "primaryText": "T", "secondaryText": "A"}
    b = dict(a, audioSources="https://h/flow/b/x.mp3")
    assert tt.title_key(a) == tt.title_key(b) == ("T", "A")
    # reopened paused after a screen pause at 61 s
    assert tt.page_start_offset_ms(0, True, 61_000, 75_000, None) == -61_000
    # reopened paused by a pause from MA: MA's elapsed time
    assert tt.page_start_offset_ms(None, True, None, 75_000, None) == -75_000


def test_resume_position():
    assert tt.resume_position_s(61_900, 75.0, 229) == 61
    assert tt.resume_position_s(None, 75.6, 229) == 75
    assert tt.resume_position_s(None, None, 229) == 0
    assert tt.resume_position_s(500_000, 75.0, 229) == 228
    assert tt.resume_position_s(61_000, 75.0, None) == 61


def test_commands_set_slider_when_paused():
    cmds = tt.set_track_time_commands(-34_760, 326_000, shown_ms=34_760)
    assert cmds[-1] == {"type": "SetValue", "componentId": "slider",
                        "property": "progressValue", "value": 34_760}
    assert len(tt.set_track_time_commands(-34_760, 326_000)) == 2


def test_queue_end_only_on_the_last_track():
    tracker = tt.TrackChangeTracker()
    tracker.changed("s", ("u", "a", "x"))
    tracker.record("s", 0, 200_000)
    assert tracker.queue_end_ms("s") is None
    tracker.changed("s", ("u", "b", "x"))
    tracker.record("s", 200_000, 300_000, last=True)
    assert tracker.queue_end_ms("s") == 500_000
    tracker.changed("s", ("u", "c", "x"))   # the queue grew: no longer known
    assert tracker.queue_end_ms("s") is None
    assert tracker.queue_end_ms("other") is None


def test_queue_end_unknown_duration():
    tracker = tt.TrackChangeTracker()
    tracker.changed("s", ("u", "radio", ""))
    tracker.record("s", 0, None, last=True)
    assert tracker.queue_end_ms("s") is None


def test_queue_end_commands():
    refresh = {"type": "SendEvent", "arguments": ["MetadataRefresh"]}
    assert tt.queue_end_commands(100_000, 500_000, refresh) is None
    assert tt.queue_end_commands(None, 500_000, refresh) is None
    assert tt.queue_end_commands(497_000, None, refresh) is None
    commands = tt.queue_end_commands(497_000, 500_000, refresh)
    assert commands[0] == {"type": "Idle", "delay": 2_000}
    assert commands[1]["when"] == "${!videoPlaying}" and commands[1]["commands"] == [refresh]
    assert commands[2]["when"] == "${videoPlaying}"
    assert commands[2]["commands"][-1] == {"type": "SendEvent", "arguments": ["QueueEnded"]}
    # past the end (late refresh): stop at once
    assert tt.queue_end_commands(501_000, 500_000, refresh)[0]["delay"] == 0
