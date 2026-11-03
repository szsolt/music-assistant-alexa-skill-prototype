# -*- coding: utf-8 -*-
"""The HTML page's buttons: the player's actions (see actions.py) and the page-only commands.

The page-only commands run on MA directly: the queue edits, and the
playlist, browse, lyrics, wave, players, playlists and options it reads.
Their answers go to the page as {"queue"}, {"browse"}, {"lyrics"},
{"wave"}, {"now"}, {"options"}, {"players"}, {"playlists"} or {"toast"}.
"""

import logging

from ... import actions, handoff, watch
from ... import echo as echos
from ...ma import library, player
from ...ma.client import LONG_TIMEOUT_S
from ...ma.errors import MAFailed, MAUnavailable, or_none, safe_error
from . import channel, visualizer

logger = logging.getLogger(__name__)

# A press on the page -> its action, or None if there is nothing to do.
_PRESSES = {
    "next": lambda a: actions.Next(),
    "previous": lambda a: actions.Previous(),
    "play": lambda a: actions.Play(max(int(a["position"]), 0)),
    "pause": lambda a: actions.Pause(max(int(a["position"]), 0), page_paused=True),
    "seek": lambda a: actions.Seek(max(int(a["position"]), 0)),
    "favorite_song": lambda a: actions.FavoriteSong(bool(a["on"])),
    "favorite_album": lambda a: actions.FavoriteAlbum(bool(a["on"])),
    "shuffle": lambda a: actions.Shuffle(bool(a["on"])),
    "repeat": lambda a: actions.Repeat(player.REPEAT_MODES[int(a["mode"]) % len(player.REPEAT_MODES)]),
    "queue_ended": lambda a: actions.QueueEnded(),
    "jump": lambda a: actions.Jump(str(a["item"])) if str(a["item"]) else None,
}
# Presses that wait for MA's new stream: the page undoes them when none comes.
_STREAM_PRESSES = ("play", "seek", "next", "previous", "jump")
# What the page shows again after a press, also when MA refused it: it flipped the button already.
_ASK = {"favorite_song": "favorites", "favorite_album": "favorites", "shuffle": "modes", "repeat": "modes"}


def run(page_id, action):
    """A button on the page, e.g. {"action": "seek", "position": 61000}.

    False for a page the skill doesn't hold; raises ValueError for a bad action.
    """
    found = channel.page(page_id)
    if found is None:
        return False
    kind = action.get("action") if isinstance(action, dict) else None
    if kind == "viz":
        visualizer.show(found, bool(action.get("on")))
        return True
    if kind == "viz_slow":
        visualizer.slow(found, action.get("presets"))
        return True
    if kind in _COMMANDS:
        _command(found, kind, _parse(kind, action))
        return True
    try:
        press = _PRESSES[kind](action)
    except (KeyError, TypeError, ValueError):
        raise ValueError("bad action")
    _press(found, kind, press)
    return True


def _press(found, kind, press):
    """Run a press's action; the page hears what MA did through its news."""
    device_id = found.device_id
    watch.page_plays(device_id)
    handoff.heard_from(device_id)
    if kind == "queue_ended":
        # MA's stop closes the page; a paused MA would restart its old flow.
        logger.info("Queue ended on the page")
        actions.run(device_id, press)
        return
    watch.quiet(device_id)
    logger.info("Page button: %s", press)
    result = actions.run(device_id, press) if press is not None else None
    if result is not None and result.status == "failed":
        channel.send(found.page_id, {"toast": "Music Assistant did not do it"})
    if kind == "pause":
        channel.send(found.page_id, {"media": "pause"})
    elif result is None or (kind in _STREAM_PRESSES and not result.ok):
        # MA won't send a new stream: undo the page's pause and overlay (play: play the page's own stream)
        channel.send(found.page_id, {"media": "play", "ready": True})
    elif not result.ok:
        channel.send(found.page_id, {"ready": True})
    if kind in _ASK:
        # after the pressing page's answer: each page's news can wait on MA
        channel.player_news(device_id, (_ASK[kind],))


_BROWSE_KINDS = ("favorites", "library", "recent", "search", "open", "playing")
_PLAY_OPTIONS = {"replace": "", "next": "Playing next", "add": "Added to the queue"}
_NOT_DONE = {"toast": "Music Assistant did not do it"}


class _Args:
    """A page-only command's checked arguments."""


def _parse(kind, action):
    """kind's arguments from the page's action; raises ValueError for bad ones."""
    a = _Args()
    try:
        a.offset, a.limit = int(action.get("offset", -1)), min(int(action.get("limit", 110)), 500)
        a.item = (str(action["item"]) if kind in ("move", "play_next", "move_end", "delete", "mix")
                  else str(action.get("item") or ""))
        a.shift = int(action["shift"]) if kind == "move" else 0
        if kind == "move" and not a.shift:
            raise ValueError("no shift")    # MA reads 0 as "play next"
        a.minutes = int(action["minutes"]) if kind == "sleep" else 0
        a.speed = float(action["speed"]) if kind == "speed" else 1.0
        if not 0.5 <= a.speed <= 3:
            raise ValueError("bad speed")
        if not 0 <= a.minutes <= 720:
            raise ValueError("bad minutes")
        a.uri = str(action["uri"]) if kind in ("play_media", "favorite") else ""
        a.option = str(action.get("option", "replace")) if kind == "play_media" else "replace"
        a.browse = str(action.get("kind")) if kind == "browse" else "favorites"
        a.text = str(action.get("text") or "")[:200]
        a.target, a.name = (str(action["target"]), str(action.get("name") or "")[:100]) if kind == "transfer" else ("", "")
        a.on, a.queue, a.playlist = bool(action.get("on")), bool(action.get("queue")), ""
        if kind == "add_to_playlist":
            a.playlist, a.name = str(action.get("playlist") or ""), str(action.get("name") or "").strip()[:100]
            a.uri = str(action.get("uri") or "")[:500]
            if not a.name:
                raise ValueError("no playlist name")
        if a.option not in _PLAY_OPTIONS or a.browse not in _BROWSE_KINDS or (kind == "play_media" and not a.uri):
            raise ValueError("bad option")
    except (KeyError, TypeError, ValueError):
        raise ValueError("bad action")
    return a


def _command(found, kind, a):
    """Run a page-only command on MA and send the page its answer."""
    player_id = echos.get(found.device_id).player_id
    if not player_id:
        return
    result, update = _COMMANDS[kind](player_id, kind, a)
    if update:
        channel.send(found.page_id, update)
    elif not result.ok:
        channel.send(found.page_id, _NOT_DONE)


def _queue(player_id, kind, a):
    """The page's playlist rows (offset -1: around the playing song), after an edit of kind.

    MA's answer replaces what the page shows, also when MA refuses the edit.
    """
    if kind != "queue":
        or_none(f"{kind} a queue item", player.edit_queue, player_id, kind, a.item, a.shift)
    window = or_none("read the queue", player.queue_window, player_id, a.offset if a.offset >= 0 else None, a.limit)
    return (actions.OK, {"queue": window}) if window else (actions.FAILED, None)


def _replace(player_id, kind, a):
    """MA replaces the queue: the page gets its stream as for any track change."""
    if (or_none("mix a queue item", player.edit_queue, player_id, kind, a.item) if kind == "mix"
            else or_none("play media", player.play_media, player_id, a.uri, "replace", a.item or None)):
        return actions.OK, None
    return actions.FAILED, dict(_NOT_DONE, ready=True)


def _play_media(player_id, kind, a):
    if a.option == "replace":
        return _replace(player_id, kind, a)
    done = or_none("play media", player.play_media, player_id, a.uri, a.option)
    return (actions.OK, {"toast": _PLAY_OPTIONS[a.option]}) if done else (actions.FAILED, _NOT_DONE)


def _options(player_id, kind, a):
    """The sleep timer, speed, Autoplay and crossfade, after setting kind."""
    value = {"sleep": a.minutes, "speed": a.speed}.get(kind, a.on)
    options = or_none("set the player options", player.options, player_id, None if kind == "options" else kind, value)
    return (actions.OK, {"options": options}) if options else (actions.FAILED, None)


def _players(player_id, kind, a):
    """The other players the music can move to."""
    try:
        return actions.OK, {"players": [[pid, pname] for pid, pname in player.players(timeout=LONG_TIMEOUT_S)
                                          if pid != player_id]}
    except (MAUnavailable, MAFailed) as e:
        logger.warning("Could not list the players for player %s: %s", player_id, safe_error(e))
        return actions.FAILED, _NOT_DONE


def _transfer(player_id, kind, a):
    """Move the queue to a.target, as the voice command."""
    try:
        player.move(player_id, a.target, timeout=LONG_TIMEOUT_S)    # MA then pauses this Echo, which reaches the page
        return actions.OK, {"toast": "Moved to " + a.name}
    except (MAUnavailable, MAFailed) as e:
        logger.warning("Could not move the music for player %s: %s", player_id, safe_error(e))
        return actions.FAILED, _NOT_DONE


def _add_to_playlist(player_id, kind, a):
    """Add a.uri, a.item or the queue to a.playlist (empty: a new one named a.name)."""
    added = or_none("add to a playlist", library.add_to_playlist, player_id, a.playlist, a.name, a.uri, a.item, a.queue)
    return (actions.OK, {"toast": "Added to " + added}) if added else (actions.FAILED, _NOT_DONE)


def _playlists(player_id, kind, a):
    """The playlists songs can be added to."""
    playlists = or_none("list the playlists", library.playlists)
    return (actions.OK, {"playlists": playlists}) if playlists is not None else (actions.FAILED, _NOT_DONE)


def _favorite(player_id, kind, a):
    """Mark a.uri a favourite or not; the page hears only of a refusal."""
    done = or_none("change a favourite", library.set_favorite, a.uri, a.on)
    return (actions.OK, None) if done else (actions.FAILED, _NOT_DONE)


# kind -> fn(player_id, kind, args): (actions.Result, the page's answer or None)
_COMMANDS = {
    "queue": _queue, "move": _queue, "play_next": _queue, "move_end": _queue, "delete": _queue, "clear": _queue,
    "mix": _replace,
    "play_media": _play_media,
    "options": _options, "sleep": _options, "speed": _options, "autoplay": _options, "crossfade": _options,
    "players": _players,
    "transfer": _transfer,
    "add_to_playlist": _add_to_playlist,
    "favorite": _favorite,
    "wave": lambda p, k, a: (actions.OK, {"wave": {"item": a.item, "points": or_none(
        "read the loudness graph", library.wave_form, p, a.item or None)}}),
    "lyrics": lambda p, k, a: (actions.OK, {"lyrics": {"item": a.item, **(or_none(
        "read the lyrics", library.lyrics, p, a.item or None) or {"plain": "", "lrc": ""})}}),
    "playlists": _playlists,
    "browse": lambda p, k, a: (actions.OK, {"browse": or_none(
        "browse the music", library.browse, p, a.browse, a.text, a.item or None) or {"title": "", "sections": []}}),
    "now": lambda p, k, a: (actions.OK, {"now": {"item": a.item, "uris": (or_none(
        "read what plays", library.now_playing, p, a.item or None, timeout=LONG_TIMEOUT_S) or {"uris": []})["uris"]}}),
}
