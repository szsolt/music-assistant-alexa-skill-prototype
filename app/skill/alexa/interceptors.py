"""The request interceptors: the request's context, MA's time budget, localisation and the request and reply logs."""

import collections
import gettext
import logging

from ask_sdk_core.dispatch_components import AbstractRequestInterceptor, AbstractResponseInterceptor
from ask_sdk_core.utils import is_request_type

from ..ma import client as ma_client
from ..page.html import channel
from . import context

logger = logging.getLogger(__name__)

# The last replies sent, so a reply Alexa rejects (System.ExceptionEncountered) can be logged
_recent = collections.deque(maxlen=50)


def reply_to(request_id):
    """The reply sent to request_id, if it is among the last ones."""
    return next((reply for rid, reply in list(_recent) if rid == request_id), None)


def register(sb):
    sb.add_global_request_interceptor(MABudgetStart())
    sb.add_global_request_interceptor(RequestContext())
    sb.add_global_request_interceptor(RequestLogger())
    sb.add_global_request_interceptor(LocalizationInterceptor())
    sb.add_global_response_interceptor(WebAppResponseInterceptor())
    sb.add_global_response_interceptor(ResponseLogger())
    sb.add_global_response_interceptor(MABudgetEnd())


class RequestContext(AbstractRequestInterceptor):
    """Work out once per request what the Echo gets: its page kind and whether its page is open.

    Taken before the handler runs: a handler that starts new music closes the page's hand-off first.
    """
    def process(self, handler_input):
        kind, page_open = context.read(handler_input)
        attributes = handler_input.attributes_manager.request_attributes
        attributes["page_kind"], attributes["page_open"] = kind, page_open
        if kind == "apl" and channel.url() and is_request_type("LaunchRequest")(handler_input):
            logger.info("Echo has no HTML: it gets the APL page")


class WebAppResponseInterceptor(AbstractResponseInterceptor):
    """Keep AudioPlayer directives out of a reply to an Echo whose HTML page is open."""
    def process(self, handler_input, response):
        if response is not None and context.page_kind(handler_input) == "html" and channel.is_open(context.device_id(handler_input)):
            channel.drop_audio_player(response)

class MABudgetStart(AbstractRequestInterceptor):
    """All MA calls for one Alexa request share one deadline that ends before Alexa's."""
    def process(self, handler_input):
        ma_client.begin_request()


class MABudgetEnd(AbstractResponseInterceptor):
    def process(self, handler_input, response):
        ma_client.end_request()


class RequestLogger(AbstractRequestInterceptor):
    """Log the alexa requests."""
    def process(self, handler_input):
        # type: (HandlerInput) -> None
        request = handler_input.request_envelope.request
        try:
            req_type = getattr(request, 'object_type', type(request).__name__)
            # Skip noisy APL UserEvent logs.
            if req_type == "Alexa.Presentation.APL.UserEvent":
                return

            # If this is an IntentRequest, log intent name and slots
            if hasattr(request, 'intent') and request.intent:
                intent_name = getattr(request.intent, 'name', None)
                slots = {}
                intent_slots = getattr(request.intent, 'slots', None)
                if intent_slots:
                    for slot_key, slot_obj in intent_slots.items():
                        slots[slot_key] = getattr(slot_obj, 'value', None)

                logger.info("Incoming Intent: %s - Slots: %s", intent_name, slots)
            else:
                logger.info("Incoming Request Type: %s", req_type)
        except Exception:
            logger.exception("Failed to log incoming request details")

        # Keep a debug-level dump of the full request for deep troubleshooting
        logger.debug("Alexa Request: %s", request)


class LocalizationInterceptor(AbstractRequestInterceptor):
    """Process the locale in request and load localized strings for response.

    This interceptors processes the locale in request, and loads the locale
    specific localization strings for the function `_`, that is used during
    responses.
    """
    def process(self, handler_input):
        # type: (HandlerInput) -> None
        locale = getattr(handler_input.request_envelope.request, 'locale', None)
        if locale:
            parts = locale.split("-")
            lang = parts[0]
            region = parts[1] if len(parts) > 1 else None

            mapping = {
                "fr": "fr-CA" if region == "CA" else "fr-FR",
                "it": "it-IT",
                "es": "es-ES",
                "pt": "pt-BR",
                "de": "de-DE",
            }

            locale_file_name = mapping.get(lang, locale)

            i18n = gettext.translation(
                'data', localedir='locales', languages=[locale_file_name],
                fallback=True)
            handler_input.attributes_manager.request_attributes[
                "_"] = i18n.gettext
        else:
            handler_input.attributes_manager.request_attributes[
                "_"] = gettext.gettext


class ResponseLogger(AbstractResponseInterceptor):
    """Log the alexa responses."""
    def process(self, handler_input, response):
        # type: (HandlerInput, Response) -> None
        request = handler_input.request_envelope.request
        _recent.append((request.request_id, response))
        logger.debug("Alexa Response: %s", response)
        if response is None:
            logger.info("Reply to %s: none", request.object_type)
            return
        logger.info(
            "Reply to %s: directives=%s speech=%s end_session=%s",
            request.object_type, [d.object_type for d in response.directives or []],
            response.output_speech is not None, response.should_end_session)
