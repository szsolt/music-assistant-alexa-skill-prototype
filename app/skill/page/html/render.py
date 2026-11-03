# -*- coding: utf-8 -*-
"""The View as the HTML page's fields: its start state, then {"track": fields} and {"set": fields} updates."""

import dataclasses

from ... import track_time, view
from ...ma import player


def fields(v, names=None):
    """The page's fields for v's View fields names (None: all of them)."""
    names = {f.name for f in dataclasses.fields(view.View)} if names is None else set(names)
    out = {}
    if names & {"title", "artist", "album", "cover"}:
        if v.title:
            out.update(title=v.title, titleSize=track_time.title_size(v.title), album=v.album)
        # with a new title even when empty, so a track with no artist doesn't keep the last one's
        if v.title or v.artist:
            out["artist"] = v.artist
        if v.cover:
            out.update(cover=v.cover, background=v.cover)
    if "item_id" in names:
        out["item"] = v.item_id or ""
    if "offset_ms" in names and v.offset_ms is not None:
        out["trackOffset"] = int(v.offset_ms)
    # The page's times are in its video's time; MA's speed stretches the track in it.
    if "duration_ms" in names:
        out["trackDuration"] = int((v.duration_ms or 0) / (v.speed or 1))
        out["speed"] = v.speed or -1
    if "quality" in names:
        out["quality"] = v.quality or ""
    if names & {"queue_end_ms", "last"}:
        out["queueEnd"] = int(v.queue_end_ms or 0)
    if "next" in names:
        upcoming = v.next or {}
        out.update(nextTitle=upcoming.get("title", ""), nextTitleSize=track_time.title_size(upcoming.get("title")),
                   nextArtist=upcoming.get("secondary", ""), nextAlbum=upcoming.get("album", ""),
                   nextQuality=upcoming.get("quality", ""), nextImage=upcoming.get("image", ""),
                   nextDuration=int((upcoming.get("duration_ms") or 0) / (v.speed or 1)) if upcoming else -1,
                   nextItem=upcoming.get("item_id", ""))
    if "favorites" in names:
        for kind in ("song", "album"):
            if kind in v.favorites:
                out[kind + "Favorite"] = -1 if v.favorites[kind] is None else int(v.favorites[kind])
    if "modes" in names and v.modes is not view.UNKNOWN:
        out["shuffle"] = -1 if v.modes is None else int(v.modes["shuffle"])
        out["repeat"] = -1 if v.modes is None else player.REPEAT_MODES.index(v.modes["repeat"])
        out["sleepAt"] = int(1000 * ((v.modes or {}).get("sleep_at") or 0))   # epoch ms, 0: off
    return out


# The fields of the playing track; the rest are its buttons.
TRACK = ("title", "artist", "album", "cover", "item_id", "offset_ms", "duration_ms", "quality", "last", "next")


def start(stream, v, autoplay):
    """The page's start state: its stream and v."""
    return {"stream": stream, "autoplay": autoplay, **fields(v)}
