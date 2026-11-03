"""What an Alexa request is about: its Echo, the page kind it gets, and the MA player it plays."""

from .. import config, devices, handoff
from ..page.html import channel


def device_id(handler_input):
    try:
        return handler_input.request_envelope.context.system.device.device_id
    except AttributeError:
        return None


def player_id(handler_input):
    return devices.get_player_for_device(device_id(handler_input))


def page_kind(handler_input):
    """The player page this request's Echo gets: "none", "apl" or "html" (set by the request interceptor)."""
    return handler_input.attributes_manager.request_attributes.get("page_kind", "none")


def page_open(handler_input):
    """Whether the Echo showed an open player page when this request came."""
    return bool(handler_input.attributes_manager.request_attributes.get("page_open"))


def takes_page(handler_input):
    """Whether this request's reply may carry a page: Alexa takes only AudioPlayer directives after a button or AudioPlayer event."""
    request_type = getattr(handler_input.request_envelope.request, "object_type", "") or ""
    return not request_type.startswith(("PlaybackController.", "AudioPlayer."))


def read(handler_input):
    """(page kind, page open) of a new request."""
    try:
        interfaces = handler_input.request_envelope.context.system.device.supported_interfaces
    except AttributeError:
        interfaces = None
    if getattr(interfaces, "alexa_presentation_apl", None) is None or not config.apl_enabled():
        return "none", False
    html = bool(channel.url()) and getattr(interfaces, "alexa_presentation_html", None) is not None
    kind = "html" if html else "apl"
    return kind, handoff.is_live(device_id(handler_input))
