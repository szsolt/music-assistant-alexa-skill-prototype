# -*- coding: utf-8 -*-
"""Track time on the player pages.

A page plays MA's flow stream, the queue's tracks back to back. MA says
where its current track starts in that stream (offset), and the page
shows its position - offset against the track's duration.

Everything here is pure.
"""


def video_position_ms(arguments, index=2):
    """A position in ms from an APL event's arguments, or None.

    MetadataRefresh: ["MetadataRefresh", refreshTick, videoProgressValue];
    the third one is missing when the event came from an older page.
    Pause/Play: [name, shown track position], index 1.
    """
    if not arguments or len(arguments) <= index:
        return None
    try:
        return max(int(float(arguments[index])), 0)
    except (TypeError, ValueError):
        return None


def resume_position_s(requested_ms, elapsed_s, duration_s):
    """Where to resume, in whole seconds inside the track.

    The page's pause position if known, else MA's elapsed time; kept off
    the very end, where MA refuses a seek.
    """
    position = requested_ms / 1000 if requested_ms is not None else (elapsed_s or 0)
    if duration_s:
        position = min(position, duration_s - 1)
    return max(int(position), 0)


# MA counts a track as started in its stream after this long; the skill never asks for less of one.
SAME_TRACK_S = 1


def stream_offset_ms(start_ms, track_at, anchored_at, since, speed):
    """Where MA's current track starts in its stream, in ms of the stream; None: MA doesn't say yet.

    since: when MA sent the stream (clock.time()). start_ms: where the skill
    asked MA to start the stream's first track, or None if MA started it.
    The stream's first track starts at the skill's place: MA's clock is off
    there after a seek with repeat one. A later track starts where MA's
    queue clock puts it (track_at: when the track was at 0), once MA has
    updated it (anchored_at) for this stream.
    """
    if start_ms is not None and (track_at is None or track_at - since < SAME_TRACK_S):
        return round(-start_ms / (speed or 1))
    if track_at is not None and anchored_at >= since:
        return 1000 * round(track_at - since)
    return None


TITLE_SIZE_CHARS = (30, 40)


def title_size(title):
    """0 for a short title, 1 for a long one, 2 for a longer one."""
    return sum(len(title or "") > chars for chars in TITLE_SIZE_CHARS)


def upcoming_commands(queue_end_ms, upcoming):
    """APL SetValue commands for what follows the current track: queueEnd and the next track.

    Also on their own, when shuffle or repeat changes what comes next.
    """
    commands = [{"type": "SetValue", "componentId": "AudioPlayerRoot",
                 "property": "queueEnd", "value": int(queue_end_ms or 0)}]
    upcoming = upcoming or {}
    for prop, value in (("nextTitle", upcoming.get("title", "")),
                        ("nextSecondary", upcoming.get("secondary", "")),
                        ("nextImage", upcoming.get("image", "")),
                        ("nextDuration", int(upcoming.get("duration_ms") or 0) if upcoming else -1),
                        ("nextTitleSize", title_size(upcoming.get("title"))),
                        ("nextAlbum", upcoming.get("album", "")),
                        ("nextQuality", upcoming.get("quality", "")),
                        ("nextItem", upcoming.get("item_id", ""))):
        commands.append({"type": "SetValue", "componentId": "AudioPlayerRoot",
                         "property": prop, "value": value})
    return commands


# Pages opened before the third title size, the album line, the quality or the next item lack these.
_NEW_PAGE_ONLY = ("nextTitleSize", "nextAlbum", "quality", "nextQuality", "nextItem")


def new_page_only_last(commands):
    """commands, with the ones older open pages lack moved to the end.

    Those pages should still take every other command of the update.
    """
    new_only = [c for c in commands if c.get("property") in _NEW_PAGE_ONLY]
    return [c for c in commands if c not in new_only] + new_only


def set_track_time_commands(offset_ms, duration_ms, shown_ms=None, queue_end_ms=None,
                            upcoming=None, quality=""):
    """APL SetValue commands for the page's trackOffset and trackDuration.

    offset_ms None leaves the offset as it is; duration_ms None or 0 shows
    no length (unknown, e.g. radio). shown_ms sets the slider's position
    directly, for a paused page: the slider only follows the video's time
    updates, and a paused video sends none. queue_end_ms: where MA's queue
    ends in the video, on its last track (the page stops there); else 0.
    upcoming: the next track (title, secondary, album, image, duration_ms,
    quality), which the page shows by itself at the track's end, before the
    skill's next update arrives; None: nothing to switch to. quality: the
    current track's file, e.g. "FLAC 16/44.1"; "" shows none.
    """
    queue_end, *following = upcoming_commands(queue_end_ms, upcoming)
    commands = [queue_end]
    if offset_ms is not None:
        commands.append({"type": "SetValue", "componentId": "AudioPlayerRoot",
                         "property": "trackOffset", "value": int(offset_ms)})
    commands.append({"type": "SetValue", "componentId": "AudioPlayerRoot",
                     "property": "trackDuration", "value": int(duration_ms or 0)})
    commands += following
    commands.append({"type": "SetValue", "componentId": "AudioPlayerRoot",
                     "property": "quality", "value": quality or ""})
    if shown_ms is not None:
        commands.append({"type": "SetValue", "componentId": "slider",
                         "property": "progressValue", "value": max(int(shown_ms), 0)})
    return new_page_only_last(commands)
