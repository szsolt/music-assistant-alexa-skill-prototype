"""The AudioPlayer: its directives (play, enqueue, stop) and its playback events."""

import logging
import os

import requests
from ask_sdk_core.dispatch_components import AbstractRequestHandler
from ask_sdk_core.utils import is_request_type
from ask_sdk_model.interfaces.audioplayer import (
    PlayDirective, PlayBehavior, AudioItem, Stream, StopDirective)

from .. import actions, config, view
from .. import page
from ..ma.errors import safe_error
from . import context

logger = logging.getLogger(__name__)


def play(handler_input, url, offset=0, text=None):
    """Play url on the Echo, or show the player page on an Echo that gets one."""
    response_builder = handler_input.response_builder
    kind = context.page_kind(handler_input)
    if kind != "none":
        if context.takes_page(handler_input):
            page.start(response_builder, context.device_id(handler_input), kind)
    else:
        try:
            hostname = config.ma_hostname(raise_on_http_scheme=True)
        except ValueError:
            response_builder.speak(
                "The domain uses an unsupported scheme (http). Please check your environment variable MA_HOSTNAME.").set_should_end_session(True)
            return response_builder.response

        if not hostname:
            response_builder.speak(
                "You did not specify a valid hostname. Please check your environment variable MA_HOSTNAME.").set_should_end_session(True)
            return response_builder.response

        url = view.public_url(url)

        skip_validation = os.environ.get('SKIP_URL_VALIDATION', 'false').lower() in ('true', '1', 'yes')

        if skip_validation:
            logger.info('Stream URL (validation skipped via SKIP_URL_VALIDATION): %s', safe_error(url))
        else:
            try:
                head_resp = requests.head(url, allow_redirects=True, timeout=5)
                resp = head_resp
                if head_resp.status_code >= 400:
                    resp = requests.get(url, stream=True, allow_redirects=True, timeout=5)

                if resp.status_code >= 400:
                    logger.error('Audio URL returned HTTP %s: %s', resp.status_code, safe_error(url))
                    response_builder.speak(
                        "Sorry, I can't reach the audio file. Please check that your stream URL is internet accessible via HTTPS at the MA_HOSTNAME variable you provided.")
                    response_builder.set_should_end_session(True)
                    return response_builder.response
            except requests.RequestException as e:
                logger.error('Play Function URL: %s: %s', safe_error(url), safe_error(e))
                response_builder.speak(
                    "Sorry, I can't reach the audio file. Please check that your stream URL is internet accessible via HTTPS at the MA_HOSTNAME variable you provided.")
                response_builder.set_should_end_session(True)
                return response_builder.response

        response_builder.add_directive(
            PlayDirective(
                play_behavior=PlayBehavior.REPLACE_ALL,
                audio_item=AudioItem(
                    stream=Stream(
                        token=url,
                        url=url,
                        offset_in_milliseconds=offset,
                        expected_previous_token=None
                    )
                )
            )
        )
        response_builder.set_should_end_session(True)

    if text:
        response_builder.speak(text)

    return response_builder.response


def play_later(response_builder, url, expected_previous_token=None):
    """Enqueue the next stream without interrupting the one now playing.

    Called from AudioPlayer.PlaybackNearlyFinished.

    https://developer.amazon.com/docs/custom-skills/audioplayer-interface-reference.html#play
    ENQUEUE: Add the specified stream to the end of the current queue. This
    does not impact the currently playing stream.

    Two constraints make this different from play():

    1. Alexa rejects a PlaybackNearlyFinished response that carries
       outputSpeech, a reprompt, or shouldEndSession, answering with
       System.ExceptionEncountered / INVALID_RESPONSE. So this only ever adds
       a directive, and never speaks or ends the session.
    2. ENQUEUE requires expectedPreviousToken to match the token of the stream
       that is currently playing. Without it the directive is discarded.
    """
    try:
        hostname = config.ma_hostname(raise_on_http_scheme=True)
    except ValueError:
        logger.error(
            'MA_HOSTNAME uses an unsupported scheme (http); cannot enqueue next stream.')
        return response_builder.response

    if not hostname:
        logger.error('MA_HOSTNAME is not set; cannot enqueue next stream.')
        return response_builder.response

    url = view.public_url(url)

    if not expected_previous_token:
        # Nothing to chain from. Enqueueing anyway would be dropped by Alexa.
        logger.warning(
            'No current playback token available; skipping enqueue of next stream.')
        return response_builder.response

    if url == expected_previous_token:
        # The API is still serving the stream that is playing. In flow mode the
        # queue is a single continuous stream, so re-enqueueing it would restart
        # playback in a loop rather than advance. Let it finish instead.
        logger.info(
            'Next stream URL matches the current one; nothing to enqueue.')
        return response_builder.response

    response_builder.add_directive(
        PlayDirective(
            play_behavior=PlayBehavior.ENQUEUE,
            audio_item=AudioItem(
                stream=Stream(
                    token=url,
                    url=url,
                    offset_in_milliseconds=0,
                    expected_previous_token=expected_previous_token
                )
            )
        )
    )

    return response_builder.response


def stop(response_builder, text=None):
    response_builder.add_directive(StopDirective())

    if text:
        response_builder.speak(text)

    response_builder.set_should_end_session(True)

    return response_builder.response


class PlaybackStartedHandler(AbstractRequestHandler):
    """AudioPlayer.PlaybackStarted Directive received.

    Confirming that the requested audio file began playing.
    Do not send any specific response.
    """
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_request_type("AudioPlayer.PlaybackStarted")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In PlaybackStartedHandler")
        logger.info("Playback started")
        return handler_input.response_builder.response

class PlaybackFinishedHandler(AbstractRequestHandler):
    """AudioPlayer.PlaybackFinished Directive received.

    Confirming that the requested audio file completed playing.
    Do not send any specific response.
    """
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_request_type("AudioPlayer.PlaybackFinished")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In PlaybackFinishedHandler")
        logger.info("Playback finished")
        return handler_input.response_builder.response


class PlaybackStoppedHandler(AbstractRequestHandler):
    """AudioPlayer.PlaybackStopped Directive received.

    Confirming that the requested audio file stopped playing.
    Do not send any specific response.
    """
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_request_type("AudioPlayer.PlaybackStopped")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In PlaybackStoppedHandler")
        logger.info("Playback stopped")
        try:
            request = handler_input.request_envelope.request
            actions.stopped(
                context.device_id(handler_input),
                getattr(request, 'token', None),
                getattr(request, 'offset_in_milliseconds', None))
        except Exception:
            logger.exception("Failed to record stopped playback position")
        return handler_input.response_builder.response


class PlaybackNearlyFinishedHandler(AbstractRequestHandler):
    """AudioPlayer.PlaybackNearlyFinished Directive received.

    Replacing queue with the URL again. This should not happen on live streams.
    """
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_request_type("AudioPlayer.PlaybackNearlyFinished")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In PlaybackNearlyFinishedHandler")
        logger.info("Playback nearly finished")
        url = view.stream_url(context.device_id(handler_input))
        if not url:
            logger.warning("No stream url available for PlaybackNearlyFinished")
            return handler_input.response_builder.response

        # ENQUEUE must chain from the stream that is currently playing.
        current_token = None
        try:
            envelope_context = handler_input.request_envelope.context
            if envelope_context and envelope_context.audio_player:
                current_token = envelope_context.audio_player.token
        except AttributeError:
            current_token = None

        return play_later(handler_input.response_builder, url, current_token)


class PlaybackFailedHandler(AbstractRequestHandler):
    """AudioPlayer.PlaybackFailed Directive received.

    Logging the error and restarting playing with no output speech and card.
    """
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_request_type("AudioPlayer.PlaybackFailed")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In PlaybackFailedHandler")
        request = handler_input.request_envelope.request
        logger.info("Playback failed: {}".format(request.error))
        url = view.stream_url(context.device_id(handler_input))
        if not url:
            logger.warning("No stream url available for PlaybackFailed; skipping restart")
            return handler_input.response_builder.response

        return play(handler_input, url)


HANDLERS = (PlaybackStartedHandler, PlaybackFinishedHandler, PlaybackStoppedHandler, PlaybackNearlyFinishedHandler,
            PlaybackFailedHandler)
