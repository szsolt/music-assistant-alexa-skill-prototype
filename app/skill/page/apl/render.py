# -*- coding: utf-8 -*-
"""The View as the basic APL page: its document at the start, then SetValue commands.

Every page gets a random id, which its events carry back.
"""

import dataclasses
import json
import logging
import os
import secrets
from urllib.parse import urlsplit

from ask_sdk_model.interfaces.alexa.presentation.apl import ExecuteCommandsDirective, RenderDocumentDirective

from ... import actions, config, devices, track_time, view
from ... import echo as echos
from ...ma import player

logger = logging.getLogger(__name__)

DOCUMENT_PATH = os.path.join(os.path.dirname(__file__), "document.json")


def document():
    with open(DOCUMENT_PATH, encoding="utf-8") as f:
        return json.load(f)


def components(node):
    """Every component dict in an APL layout tree, depth first."""
    if isinstance(node, dict):
        if "type" in node:
            yield node
        for key in ("item", "items"):
            yield from components(node.get(key))
    elif isinstance(node, list):
        for child in node:
            yield from components(child)


def start(builder, device_id=None, start_paused=False):
    """A new page for device_id with its stream, else the latest of any player's; nothing without one.

    It starts with MA's track time and button states, not after its first refresh.
    """
    payload = view.stream_of(device_id, any_player=True)
    if not payload:
        logger.warning("No metadata available for APL rendering")
        return
    v = view.of_stream(payload)
    player_id = devices.get_player_for_device(device_id) if device_id else None
    apl_document = document()
    autoplay = not start_paused
    # Found by type: its place in the layout changes with the page design. The
    # play/pause button follows the video's own play and pause events.
    for component in components(apl_document["layouts"]["AudioPlayer"]):
        if component.get("type") == "Video":
            component["autoplay"] = autoplay
    item = apl_document["mainTemplate"]["items"][0]
    item.update({
        "audioSources": payload["streamUrl"],
        "backgroundImageSource": v.cover,
        "coverImageSource": v.cover,
        "headerTitle": "",
        "headerSubtitle": "",
        "primaryText": v.title,
        "secondaryText": v.artist,
        "albumText": v.album,
        "primaryTextSize": track_time.title_size(v.title),
    })
    page = echos.open_page(device_id, "apl") if device_id else None
    page_id = page.page_id if page is not None else secrets.token_hex(8)
    # its first update must not set these again: a new background flashes
    images(device_id, page_id).update(cover=v.cover, background=v.cover)
    item["bellPage"] = page_id
    if page is not None:
        page.stream = payload["streamUrl"]
        try:
            now, fresh = view.now(device_id, payload["streamUrl"], 0, paused_at=actions.page_paused(device_id))
            if fresh and now.offset_ms is not None and now.offset_ms <= 0:
                now = dataclasses.replace(now, favorites=view.favorites(player_id) if player_id else {})
                item.update(_start_values(now))
        except Exception:
            logger.exception("Could not read the page's start values")
    builder.add_directive(RenderDocumentDirective(token="playbackToken", document=apl_document, datasources={}))


def _start_values(v):
    """The page's first button states and track time, as its start parameters."""
    values = {"start" + c["property"][0].upper() + c["property"][1:]: int(c["value"])
              for c in mode_commands(v.modes) + favorite_commands(v.favorites)}
    values.update(startOffset=int(v.offset_ms), startDuration=int(v.duration_ms or 0))
    return values


def _track(stream, v):
    """The page record's track: what the page was sent of v."""
    return {"stream": stream, "item": v.item_id, "trackOffset": int(v.offset_ms),
            "trackDuration": int(v.duration_ms or 0), "queueEnd": int(v.queue_end_ms or 0),
            "next": (v.next or {}).get("item_id"), "modes": v.modes}


def images(device_id, page_id):
    """The images page_id shows ({"cover", "background", "next"}), kept on its page record.

    An APL page the skill doesn't know becomes device_id's open page.
    """
    if not device_id or not page_id or page_id == device_id:    # a page without an id has no record
        return {}
    page = echos.adopt_page(device_id, page_id, "apl")
    return page.images if page is not None else {}


def show_metadata(builder, v, device_id, page_id):
    """Send the page v's title, artist and album, and the images it doesn't show yet."""
    commands = metadata_commands(v, images(device_id, page_id))
    if not commands:
        logger.warning("No SetValue commands generated - no metadata to update")
    execute(builder, commands)


def _image_key(url):
    """An MA image's id, the same from MA's web and stream servers; other images by URL."""
    path = urlsplit(url or "").path
    return path if "/imageproxy/" in path else url


def metadata_commands(v, shown=None):
    """SetValue commands for v's texts and images, without a new document (that restarts the audio).

    shown: the page's images ({"cover", "background", "next"}), updated
    here. Images the page already shows aren't set again: setting the
    background rebuilds it, and the screen flashes. "next" is the image the
    page switches to by itself when the track ends. The texts go on every
    refresh: they mend a wrong switch the page made by itself.
    """
    cover = background = v.cover
    if shown is not None:
        on_page = {_image_key(shown.get("cover")), _image_key(shown.get("next"))}
        if _image_key(cover) in on_page:
            cover = ""
        if _image_key(background) in on_page | {_image_key(shown.get("background"))}:
            background = ""
        shown.update(cover=v.cover or shown.get("cover"), background=v.cover or shown.get("background"), next=None)

    def set_value(component, prop, value):
        return {"type": "SetValue", "componentId": component, "property": prop, "value": value}

    commands = []
    if v.title:
        commands += [set_value(c, "text", v.title) for c in ("Audio_PrimaryText", "Audio_PrimaryTextLong")]
    # With a new title even when empty, so a track with no artist doesn't keep the last one's.
    if v.title or v.artist:
        commands.append(set_value("Audio_SecondaryText", "text", v.artist))
    # The bound data too, so conditional rendering refreshes.
    if cover:
        commands += [set_value("AudioPlayerRoot", "coverImageSource", cover),
                     set_value("Audio_CoverArt", "imageSource", cover)]
    if background:
        commands += [set_value("AudioPlayerRoot", "backgroundImageSource", background),
                     set_value("AlexaBackground", "backgroundImageSource", background)]
    # Last: pages opened before the third title size or the album line
    # lack these, and should still take the updates above.
    if v.title:
        commands += [set_value("Audio_PrimaryTextLonger", "text", v.title),
                     set_value("AudioPlayerRoot", "titleSize", track_time.title_size(v.title)),
                     # even when empty: a track with no album hides the last one's
                     set_value("AudioPlayerRoot", "album", v.album)]
    return commands


def track_time_commands(device_id, page_key, arguments, stream):
    """SetValue commands for MA's track on the page: its time, what comes next and the buttons; [] if unchanged.

    The page shows MA's track once its video reaches the track's start.
    """
    page = echos.find_page(page_key) if device_id else None
    if page is None:
        return []
    stream = page.stream or stream
    position_ms = track_time.video_position_ms(arguments)
    known = (page.track or {}).get("item")
    v, fresh = view.now(device_id, stream, position_ms, known, actions.page_paused(device_id))
    if not fresh or v.offset_ms is None or (position_ms is not None and v.offset_ms > position_ms):
        return []
    track = view.keep_offset(page.track, _track(stream, v))
    if track == page.track:
        return []
    offset = track["trackOffset"]
    logger.info("Page on %s plays %s %r: offset=%s ms duration=%s ms paused=%s last=%s next=%s",
                device_id[-8:], v.item_id, v.title, offset, v.duration_ms, v.paused, v.last, track["next"])
    commands = []
    if v.item_id != known:
        player_id = devices.get_player_for_device(device_id)
        commands = favorite_commands(view.favorites(player_id) if player_id else {})
    page.track = track
    shown = (position_ms or 0) - offset if v.paused else None
    images(device_id, page_key)["next"] = (v.next or {}).get("image") or None
    return track_time.new_page_only_last(
        track_time.set_track_time_commands(offset, v.duration_ms, shown, v.queue_end_ms, v.next, v.quality)
        + commands + mode_commands(v.modes))


def favorite_commands(state):
    """SetValue commands for the page's hearts: 1 a favourite, 0 not, -1 hidden (can't be one)."""
    return [{"type": "SetValue", "componentId": "AudioPlayerRoot", "property": f"{kind}Favorite",
             "value": -1 if state[kind] is None else int(state[kind])}
            for kind in ("song", "album") if kind in state]


def mode_commands(modes):
    """SetValue commands for the shuffle and repeat buttons: -1 hidden; shuffle 0/1; repeat 0 off, 1 all, 2 one."""
    if modes is view.UNKNOWN:
        return []
    return [{"type": "SetValue", "componentId": "AudioPlayerRoot", "property": "shuffleOn",
             "value": -1 if modes is None else int(modes["shuffle"])},
            {"type": "SetValue", "componentId": "AudioPlayerRoot", "property": "repeatMode",
             "value": -1 if modes is None else player.REPEAT_MODES.index(modes["repeat"])}]


def refresh_commands(delay_ms=1000):
    """APL commands that ask for the next metadata refresh after delay_ms."""
    return [{"type": "Idle", "delay": int(delay_ms)},
            {"type": "SendEvent", "arguments": ["MetadataRefresh", "${refreshTick}", "${videoProgressValue}",
                                                "${bellPage}"]}]


def execute(builder, commands):
    """Send APL commands to the player page, if there are any."""
    if commands and config.apl_enabled():
        builder.add_directive(ExecuteCommandsDirective(commands=commands, token="playbackToken"))
