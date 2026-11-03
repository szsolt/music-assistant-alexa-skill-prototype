from skill import track_time as tt


def test_video_position_from_event_arguments():
    assert tt.video_position_ms(["MetadataRefresh", 3, 125000]) == 125000
    assert tt.video_position_ms(["MetadataRefresh", 3, "1250.7"]) == 1250
    assert tt.video_position_ms(["MetadataRefresh", 3, -5]) == 0
    assert tt.video_position_ms(["Pause", 61000.4], index=1) == 61000
    assert tt.video_position_ms(["MetadataRefresh", 3]) is None
    assert tt.video_position_ms(["MetadataRefresh", 3, "${videoProgressValue}"]) is None
    assert tt.video_position_ms(None) is None
    assert tt.video_position_ms(["Play"], index=1) is None


def test_set_track_time_commands():
    cmds = tt.set_track_time_commands(388_000, 215_000)
    assert [(c["property"], c["value"]) for c in cmds] == [("queueEnd", 0), ("trackOffset", 388_000), ("trackDuration", 215_000),
        ("nextTitle", ""), ("nextSecondary", ""), ("nextImage", ""), ("nextDuration", -1), ("nextTitleSize", 0), ("nextAlbum", ""),
        ("nextQuality", ""), ("nextItem", ""), ("quality", "")]
    assert all(c["componentId"] == "AudioPlayerRoot" for c in cmds)


def test_commands_carry_the_next_track():
    upcoming = {"title": "B", "secondary": "Artist", "album": "Album", "image": "https://i/b", "duration_ms": 180_000,
                "quality": "MP3 192k", "item_id": "q2"}
    cmds = tt.set_track_time_commands(0, 215_000, upcoming=upcoming, quality="FLAC 16/44.1")
    assert [(c["property"], c["value"]) for c in cmds][-9:] == [
        ("nextTitle", "B"), ("nextSecondary", "Artist"), ("nextImage", "https://i/b"),
        ("nextDuration", 180_000), ("nextTitleSize", 0), ("nextAlbum", "Album"),
        ("nextQuality", "MP3 192k"), ("nextItem", "q2"), ("quality", "FLAC 16/44.1")]
    unknown_length = dict(upcoming, duration_ms=0)
    assert {"type": "SetValue", "componentId": "AudioPlayerRoot", "property": "nextDuration", "value": 0} in \
        tt.set_track_time_commands(0, 215_000, upcoming=unknown_length)


def test_set_track_time_commands_unknown():
    cmds = tt.set_track_time_commands(None, None)
    assert [(c["property"], c["value"]) for c in cmds] == [("queueEnd", 0), ("trackDuration", 0),
                                                                 ("nextTitle", ""), ("nextSecondary", ""), ("nextImage", ""), ("nextDuration", -1), ("nextTitleSize", 0), ("nextAlbum", ""),
                                                                 ("nextQuality", ""), ("nextItem", ""), ("quality", "")]


def test_resume_position():
    assert tt.resume_position_s(61_900, 75.0, 229) == 61
    assert tt.resume_position_s(None, 75.6, 229) == 75
    assert tt.resume_position_s(None, None, 229) == 0
    assert tt.resume_position_s(500_000, 75.0, 229) == 228
    assert tt.resume_position_s(61_000, 75.0, None) == 61


def test_commands_set_slider_when_paused():
    cmds = tt.set_track_time_commands(-34_760, 326_000, shown_ms=34_760)
    assert cmds[-6] == {"type": "SetValue", "componentId": "slider",
                        "property": "progressValue", "value": 34_760}
    assert len(tt.set_track_time_commands(-34_760, 326_000)) == 12


def test_set_track_time_commands_queue_end():
    commands = tt.set_track_time_commands(0, 300_000, queue_end_ms=290_000)
    assert {"type": "SetValue", "componentId": "AudioPlayerRoot",
            "property": "queueEnd", "value": 290_000} in commands
    # not on the last track: cleared, so a page that was on it doesn't stop
    assert tt.set_track_time_commands(0, 300_000)[0]["value"] == 0


def test_upcoming_commands_after_repeat_or_shuffle():
    upcoming = {"title": "B", "secondary": "Artist", "album": "Album", "image": "https://i/b", "duration_ms": 180_000,
                "quality": "FLAC 16/44.1"}
    values = {c["property"]: c["value"] for c in tt.upcoming_commands(None, upcoming)}
    assert values == {"queueEnd": 0, "nextTitle": "B", "nextTitleSize": 0, "nextSecondary": "Artist",
                      "nextImage": "https://i/b", "nextDuration": 180_000, "nextAlbum": "Album",
                      "nextQuality": "FLAC 16/44.1", "nextItem": ""}
    long = dict(upcoming, title="Transit Blues I. (Budapest - Rotterdam), live")
    assert {c["property"]: c["value"] for c in tt.upcoming_commands(None, long)}["nextTitleSize"] == 2
    # nothing follows: the page stops at the queue's end, and shows no next song
    values = {c["property"]: c["value"] for c in tt.upcoming_commands(290_000, None)}
    assert values["queueEnd"] == 290_000 and values["nextDuration"] == -1


def test_commands_older_pages_lack_come_last():
    cmds = tt.new_page_only_last(tt.set_track_time_commands(0, 215_000, shown_ms=0)
                                 + [{"type": "SetValue", "componentId": "AudioPlayerRoot", "property": "shuffleOn", "value": 1}])
    assert [c["property"] for c in cmds][-6:] == ["shuffleOn", "nextTitleSize", "nextAlbum",
                                                  "nextQuality", "nextItem", "quality"]


SINCE = 1_762_140_795.15    # when MA sent the stream


def test_a_seek_starts_at_the_skills_place():
    # repeat one: MA's clock puts the seeked track at 0 when the stream starts
    assert tt.stream_offset_ms(200_000, SINCE + 0.02, SINCE + 0.01, SINCE, 1) == -200_000
    assert tt.stream_offset_ms(200_000, SINCE - 200.01, SINCE + 0.01, SINCE, 1) == -200_000
    assert tt.stream_offset_ms(60_000, None, None, SINCE, 1.5) == -40_000


def test_the_next_track_in_the_stream_starts_at_mas_place():
    # repeat one wraps 31 s into a stream that started 200 s into a 231 s track
    assert tt.stream_offset_ms(200_000, SINCE + 31.01, SINCE + 31.02, SINCE, 1) == 31_000
    assert tt.stream_offset_ms(0, SINCE + 231.0, SINCE + 231.01, SINCE, 1) == 231_000


def test_a_stream_ma_started_follows_mas_clock():
    assert tt.stream_offset_ms(None, SINCE - 0.01, SINCE + 0.01, SINCE, 1) == 0
    assert tt.stream_offset_ms(None, SINCE - 0.01, SINCE - 5, SINCE, 1) is None
    assert tt.stream_offset_ms(None, None, None, SINCE, 1) is None
