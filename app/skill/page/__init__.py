"""The player pages: the HTML page (html/) and the basic APL page (apl/)."""

from .. import echo as echos, handoff
from .apl import render
from .html import channel


def start(builder, device_id, kind, start_paused=False):
    """A new page of kind ("apl" or "html") for device_id."""
    if kind == "html" and device_id:
        channel.start(builder, device_id, start_paused)
    else:
        render.start(builder, device_id, start_paused)


def withdraw(builder, device_id, before, was_open):
    """Drop the new page from the reply: device_id's page is before again (None: none). was_open: before was live."""
    builder.response.directives = None
    handoff.closed(device_id)
    echos.set_page(device_id, before)
    if was_open:
        handoff.heard_from(device_id)
