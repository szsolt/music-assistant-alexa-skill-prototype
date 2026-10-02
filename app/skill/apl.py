# -*- coding: utf-8 -*-

import json
import logging
import os
import secrets
import sys
from ask_sdk_model.interfaces.alexa.presentation.apl import RenderDocumentDirective
from ask_sdk_core.response_helper import ResponseFactory
from . import bell, data, track_time

# Ensure /app/src is on the Python path so shared_store can be imported
_app_src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _app_src not in sys.path:
    sys.path.insert(0, _app_src)


def _load_apl_template():
    # type: () -> dict
    """Load the APL document template from JSON file."""
    template_path = os.path.join(os.path.dirname(__file__), 'apl_document.json')
    with open(template_path, 'r') as f:
        return json.load(f)


def _components(node):
    """Every component dict in an APL layout tree, depth first."""
    if isinstance(node, dict):
        if "type" in node:
            yield node
        for key in ("item", "items"):
            yield from _components(node.get(key))
    elif isinstance(node, list):
        for child in node:
            yield from _components(child)


# Set by the skill (set_start_track_time): start_track_time(device_id) is
# (track offset ms, track duration ms) for a new page, or None.
_start_track_time = None


def set_start_track_time(fn):
    global _start_track_time
    _start_track_time = fn


def _start_values(device_id):
    """The page's first trackOffset and trackDuration: the slider is right from the start."""
    if not _start_track_time or not device_id:
        return {}
    try:
        start = _start_track_time(device_id)
    except Exception:
        logging.exception("Could not get the track time for a new page")
        return {}
    if not start or start[0] is None:
        return {}
    return {"startOffset": int(start[0]), "startDuration": int(start[1] or 0)}


def add_apl(response_builder, start_paused=False, device_id=None):
    # type: (ResponseFactory, bool, str) -> None
    """Add the RenderDocumentDirective to the response with APL document.

    Every page gets a random id, which its events carry back. With device_id
    (and APL_BELL_URL) the page also gets a doorbell (see bell.py).
    """
    # Import here to avoid circular imports
    from .util import get_ma_hostname, replace_ip_in_url

    # Get metadata from shared_store (most reliable) or data.info as fallback
    metadata = _get_metadata()
    if not metadata:
        logging.warning("No metadata available for APL rendering")
        return

    # Replace MA-hosted image sources if MA_HOSTNAME is set
    try:
        hostname = get_ma_hostname(raise_on_http_scheme=False)
    except ValueError:
        hostname = ''

    cover_image = metadata.get("coverImageSource", "")
    background_image = metadata.get("backgroundImageSource", "")

    if hostname:
        cover_image = replace_ip_in_url(cover_image, hostname)
        background_image = replace_ip_in_url(background_image, hostname)

    # Load the APL document template
    apl_document = _load_apl_template()

    # Set the dynamic autoplay value based on start_paused
    autoplay = not start_paused

    # Update autoplay in the Video component, found by type: its place in
    # the layout changes with the page design. The play/pause button
    # follows the video's own play and pause events.
    for component in _components(apl_document["layouts"]["AudioPlayer"]):
        if component.get("type") == "Video":
            component["autoplay"] = autoplay

    # Update mainTemplate with metadata values
    try:
        main_template_item = apl_document["mainTemplate"]["items"][0]
        main_template_item.update({
            "audioSources": metadata.get("audioSources", ""),
            "backgroundImageSource": background_image,
            "coverImageSource": cover_image,
            "headerAttributionImage": metadata.get("headerAttributionImage", ""),
            "headerTitle": metadata.get("headerTitle", ""),
            "headerSubtitle": metadata.get("headerSubtitle", ""),
            "primaryText": metadata.get("primaryText", ""),
            "secondaryText": metadata.get("secondaryText", ""),
        })
        # The page's doorbell (see bell.py): where the Show finds the skill on the LAN
        bell_page = bell.new_page(device_id)
        page_id = bell_page or secrets.token_hex(8)
        track_time.pages.started(page_id)   # its stream starts with the current track
        main_template_item.update({
            "bellUrl": bell.base_url() if bell_page else "",
            "bellPage": page_id,
        })
        main_template_item.update(_start_values(device_id))
    except (KeyError, IndexError):
        logging.warning("Could not update mainTemplate in APL document")

    response_builder.add_directive(
        RenderDocumentDirective(
            token="playbackToken",
            document=apl_document,
            datasources={}
        )
    )


def _get_metadata():
    """Get metadata from shared_store or data.info.

    shared_store is the primary source (set by MA push-url).
    data.info is the fallback (set by data.get_latest()).
    """
    # Priority 1: shared_store (most reliable, set by MA)
    try:
        import shared_store
        if shared_store._store:
            store = shared_store._store
            return {
                "audioSources": store.get("streamUrl", ""),
                "backgroundImageSource": store.get("imageUrl", ""),
                "coverImageSource": store.get("imageUrl", ""),
                "headerAttributionImage": "",
                "headerTitle": "",
                "headerSubtitle": "",
                "primaryText": store.get("title", ""),
                "secondaryText": _build_secondary_text(store)
            }
    except Exception as e:
        logging.debug("shared_store read failed in APL: %s", e)

    # Priority 2: data.info (fallback)
    try:
        if data.info and data.info.get("audioSources"):
            return data.info
    except Exception:
        pass

    return None


def _build_secondary_text(store):
    """Build secondary text from artist and album."""
    artist = store.get("artist", "")
    album = store.get("album", "")
    if artist and album:
        return f"{artist} - {album}"
    elif artist:
        return artist
    elif album:
        return album
    return ""
