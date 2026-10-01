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

# The page's refreshes come ~2.5 s apart: one of them falls within this
# of the queue's end, and plans the stop at the end.
_QUEUE_END_WINDOW_MS = 4_000
# Stop this much before the end: the Show asks for an ended stream again
# at once, and MA answers that by restarting the old flow.
_QUEUE_END_MARGIN_MS = 1_000

# A new page whose stream MA started further into the track than this
# started mid-track (MA's own resume); below it, it's startup delay.
_MID_TRACK_START_MS = 15_000


def track_key(info):
    """What identifies the playing track in the skill's metadata (data.info)."""
    return (info.get('audioSources') or '',
            info.get('primaryText') or '',
            info.get('secondaryText') or '')


def title_key(info):
    """The track by title and artist only: MA's resume gives it a new stream URL."""
    return track_key(info)[1:]


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


def page_start_offset_ms(position_ms, paused, paused_at_ms, elapsed_ms, stream_start_ms,
                         ma_paused_ms=None):
    """trackOffset for the first track of a new page.

    - Opened paused (a pause from MA reopens the page at the stream's start):
      show where playback stopped, the page's own pause position if known,
      else MA's elapsed time.
    - Opened by a resume: MA's new stream starts stream_start_ms into the
      track, so the video's 0 is that point of the track.
    - Opened by MA resuming on its own (play in MA while the page was
      closed): MA resumes where it paused, ma_paused_ms into the track, if
      the skill saw that pause; else, if MA's elapsed time is well ahead
      of the video, the stream started that far into the track. (Not for
      a replay of an old stream URL: MA restarts that flow where it first
      started while its clock runs on, so the skill never replays one.)
    - Otherwise the stream starts with the track: 0.
    """
    if paused:
        stopped_at = paused_at_ms if paused_at_ms is not None else elapsed_ms
        return (position_ms or 0) - (stopped_at or 0)
    if stream_start_ms is not None:
        return -stream_start_ms
    if ma_paused_ms is not None:
        return -ma_paused_ms
    if elapsed_ms is not None and elapsed_ms - (position_ms or 0) > _MID_TRACK_START_MS:
        return (position_ms or 0) - elapsed_ms
    return 0


def resume_position_s(requested_ms, elapsed_s, duration_s):
    """Where to resume, in whole seconds inside the track.

    The page's pause position if known, else MA's elapsed time; kept off
    the very end, where MA refuses a seek.
    """
    position = requested_ms / 1000 if requested_ms is not None else (elapsed_s or 0)
    if duration_s:
        position = min(position, duration_s - 1)
    return max(int(position), 0)


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
                                      "last": False, "previous_end": previous_end}
            self._seen.move_to_end(session_id)
            while len(self._seen) > self._max:
                self._seen.popitem(last=False)
            return True

    def previous_end(self, session_id):
        """Where the previous track of this session ended in the video (ms), or None."""
        with self._lock:
            entry = self._seen.get(session_id)
            return entry["previous_end"] if entry else None

    def forget(self, session_id):
        """The session's page has a new stream: its next track is a new page's first."""
        with self._lock:
            self._seen.pop(session_id, None)

    def record(self, session_id, offset_ms, duration_ms, last=False):
        """Remember where the current track lies, for the next change.

        last: nothing follows it in MA's queue.
        """
        with self._lock:
            entry = self._seen.get(session_id)
            if entry:
                entry["offset"], entry["duration"], entry["last"] = offset_ms, duration_ms, last

    def queue_end_ms(self, session_id):
        """Where MA's queue ends in this session's video (ms), or None if not on its last track."""
        with self._lock:
            entry = self._seen.get(session_id)
            if not entry or not entry["last"] or entry["offset"] is None or not entry["duration"]:
                return None
            return entry["offset"] + entry["duration"]


def queue_end_commands(position_ms, end_ms, refresh_event):
    """APL commands that stop the page at the queue's end, or None if it's not near.

    They wait out the rest of the last track, pause the video and send
    QueueEnded. A page paused meanwhile sends refresh_event instead and is
    left alone. They replace the refresh: the answer to either event goes on.
    """
    if end_ms is None or position_ms is None:
        return None
    remaining = end_ms - position_ms
    if remaining > _QUEUE_END_WINDOW_MS:
        return None
    return [
        {"type": "Idle", "delay": max(remaining - _QUEUE_END_MARGIN_MS, 0)},
        {"type": "Sequential", "when": "${!videoPlaying}", "commands": [refresh_event]},
        {"type": "Sequential", "when": "${videoPlaying}", "commands": [
            {"type": "ControlMedia", "componentId": "videoPlayer", "command": "pause"},
            {"type": "SendEvent", "arguments": ["QueueEnded"]},
        ]},
    ]


def set_track_time_commands(offset_ms, duration_ms, shown_ms=None):
    """APL SetValue commands for the page's trackOffset and trackDuration.

    offset_ms None leaves the offset as it is; duration_ms None or 0 shows
    no length (unknown, e.g. radio). shown_ms sets the slider's position
    directly, for a paused page: the slider only follows the video's time
    updates, and a paused video sends none.
    """
    commands = []
    if offset_ms is not None:
        commands.append({"type": "SetValue", "componentId": "AudioPlayerRoot",
                         "property": "trackOffset", "value": int(offset_ms)})
    commands.append({"type": "SetValue", "componentId": "AudioPlayerRoot",
                     "property": "trackDuration", "value": int(duration_ms or 0)})
    if shown_ms is not None:
        commands.append({"type": "SetValue", "componentId": "slider",
                         "property": "progressValue", "value": max(int(shown_ms), 0)})
    return commands
