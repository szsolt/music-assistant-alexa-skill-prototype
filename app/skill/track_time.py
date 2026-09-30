# -*- coding: utf-8 -*-
"""Track time for the APL player page.

The page plays MA's flow stream in a Video component, which has no idea
where one track ends and the next begins: its position counts from the
start of the stream. The stream is the queue's tracks back to back, so
a track starts in the video where the previous one ended: offset =
previous offset + previous duration, and 0 for the first track of a page.
On a track change the skill asks MA once for the duration and sends the
page the offset; the page shows position - offset against the duration
and keeps counting on its own (also through a local pause).

Everything here is pure; the MA query lives in ma_control.
"""

import threading
from collections import OrderedDict

# Sessions remembered for change detection; old ones fall out.
_MAX_SESSIONS = 32


def track_key(info):
    """What identifies the playing track in the skill's metadata (data.info)."""
    return (info.get('audioSources') or '',
            info.get('primaryText') or '',
            info.get('secondaryText') or '')


def video_position_ms(arguments):
    """The page's video position from a MetadataRefresh event, or None.

    arguments: ["MetadataRefresh", refreshTick, videoProgressValue]; the
    third one is missing when the event came from an older page.
    """
    if not arguments or len(arguments) < 3:
        return None
    try:
        return max(int(float(arguments[2])), 0)
    except (TypeError, ValueError):
        return None


def track_offset_ms(position_ms, elapsed_ms):
    """Where the current track started in the page's video, in ms."""
    return max(int(position_ms) - int(elapsed_ms), 0)


def choose_offset_ms(previous_end_ms, position_ms, elapsed_ms):
    """Where the current track started in the page's video, in ms, or None.

    The end of the previous track when known; otherwise the video position
    minus MA's elapsed time, which needs the position from the page.
    """
    if previous_end_ms is not None:
        return previous_end_ms
    if position_ms is not None and elapsed_ms is not None:
        return track_offset_ms(position_ms, elapsed_ms)
    return None


class TrackChangeTracker:
    """Remembers, per APL session, the track seen last and where it lies in the video."""

    def __init__(self, max_sessions=_MAX_SESSIONS):
        self._seen = OrderedDict()
        self._max = max_sessions
        self._lock = threading.Lock()

    def changed(self, session_id, key):
        """Record key for session_id; True if it differs from the last one.

        Recording before the caller queries MA means concurrent refreshes
        of the same change query only once.
        """
        with self._lock:
            old = self._seen.get(session_id)
            if old is not None and old["key"] == key:
                self._seen.move_to_end(session_id)
                return False
            if old is None:
                previous_end = 0   # a new page: its stream starts with this track
            elif old["offset"] is not None and old["duration"]:
                previous_end = old["offset"] + old["duration"]
            else:
                previous_end = None
            self._seen[session_id] = {"key": key, "offset": None, "duration": None,
                                      "previous_end": previous_end}
            self._seen.move_to_end(session_id)
            while len(self._seen) > self._max:
                self._seen.popitem(last=False)
            return True

    def previous_end(self, session_id):
        """Where the previous track of this session ended in the video (ms), or None."""
        with self._lock:
            entry = self._seen.get(session_id)
            return entry["previous_end"] if entry else None

    def record(self, session_id, offset_ms, duration_ms):
        """Remember where the current track lies, for the next change."""
        with self._lock:
            entry = self._seen.get(session_id)
            if entry:
                entry["offset"], entry["duration"] = offset_ms, duration_ms


def set_track_time_commands(offset_ms, duration_ms):
    """APL SetValue commands for the page's trackOffset and trackDuration.

    offset_ms None leaves the offset as it is; duration_ms None or 0 hides
    the time display (unknown length, e.g. radio).
    """
    commands = []
    if offset_ms is not None:
        commands.append({"type": "SetValue", "componentId": "AudioPlayerRoot",
                         "property": "trackOffset", "value": int(offset_ms)})
    commands.append({"type": "SetValue", "componentId": "AudioPlayerRoot",
                     "property": "trackDuration", "value": int(duration_ms or 0)})
    return commands
