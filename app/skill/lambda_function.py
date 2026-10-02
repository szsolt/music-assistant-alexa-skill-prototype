# -*- coding: utf-8 -*-

import logging
import threading
import time
import gettext
import os
from ask_sdk.standard import StandardSkillBuilder
from ask_sdk_core.dispatch_components import (
    AbstractRequestHandler, AbstractExceptionHandler,
    AbstractRequestInterceptor, AbstractResponseInterceptor)
from ask_sdk_core.utils import is_request_type, is_intent_name
from ask_sdk_core.handler_input import HandlerInput
from ask_sdk_model import Response

from . import data, util, device_mapping, live_page, ma_control, track_time, bell, apl

sb = StandardSkillBuilder()
# sb = StandardSkillBuilder(
#     table_name=data.jingle["db_table"], auto_create_table=True)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class _ComponentFilter(logging.Filter):
    """Inject a `component` attribute based on logger name.

    This makes it easy to tell whether a message came from the
    API, the Alexa Skill code (Skill), or the UI/Web app.
    """
    def filter(self, record):
        name = (record.name or "")
        path = (getattr(record, 'pathname', '') or '')
        norm_path = path.replace(os.sep, '/') if path else ''
        if name.startswith('music_assistant_api') or name.startswith('ma_routes'):
            record.component = 'API'
        elif name.startswith('alexa') or name == 'lambda_function' or name.startswith('ask_sdk'):
            record.component = 'Skill'
        elif norm_path:
            if "/app/skill/" in norm_path:
                record.component = 'Skill'
            elif "/app/music_assistant_api/" in norm_path or "/app/alexa_api/" in norm_path:
                record.component = 'API'
            elif "/app/endpoints/" in norm_path or norm_path.endswith("/app.py"):
                record.component = 'UI/Web'
            else:
                record.component = 'UI/Web'
        else:
            record.component = 'UI/Web'
        return True


_filter = _ComponentFilter()
root_logger = logging.getLogger()
root_logger.addFilter(_filter)

# Ensure every LogRecord has a `component` attribute so formatters
# that reference %(component)s don't fail for third-party loggers
# (e.g. werkzeug) which may emit records before filters run.
_orig_log_record_factory = logging.getLogRecordFactory()

def _log_record_factory(*args, **kwargs):
    record = _orig_log_record_factory(*args, **kwargs)
    if not hasattr(record, 'component'):
        name = (getattr(record, 'name', '') or '')
        path = (getattr(record, 'pathname', '') or '')
        norm_path = path.replace(os.sep, '/') if path else ''
        if name.startswith('music_assistant_api') or name.startswith('ma_routes'):
            record.component = 'API'
        elif name.startswith('alexa') or name == 'lambda_function' or name.startswith('ask_sdk'):
            record.component = 'Skill'
        elif norm_path:
            if "/app/skill/" in norm_path:
                record.component = 'Skill'
            elif "/app/music_assistant_api/" in norm_path or "/app/alexa_api/" in norm_path:
                record.component = 'API'
            elif "/app/endpoints/" in norm_path or norm_path.endswith("/app.py"):
                record.component = 'UI/Web'
            else:
                record.component = 'UI/Web'
        else:
            record.component = 'UI/Web'
    return record

logging.setLogRecordFactory(_log_record_factory)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(component)s] %(name)s %(message)s",
    datefmt="%H:%M:%S %Y-%m-%d %z"
)


def _supports_apl(handler_input):
    """Whether the requesting device renders APL (set per request by the interceptor)."""
    return bool(handler_input.attributes_manager.request_attributes.get("supports_apl"))


def _get_stream_url(request):
    """Return (url, audio_data) where url is resolved from util.audio_data.

    Handles multiple shapes returned by util.audio_data and never raises.
    """
    try:
        audio = util.audio_data(request)
    except Exception:
        audio = None

    url = None
    if isinstance(audio, dict):
        url = (audio.get('url') or audio.get('audioSources') or
               audio.get('audio_sources') or audio.get('stream') or '')
    elif isinstance(audio, str):
        url = audio

    if url == '':
        url = None
    return url, audio

# ######################### INTENT HANDLERS #########################
# This section contains handlers for the built-in intents and generic
# request handlers like launch, session end, skill events etc.

class CheckAudioInterfaceHandler(AbstractRequestHandler):
    """Check if device supports audio play.

    This can be used as the first handler to be checked, before invoking
    other handlers, thus making the skill respond to unsupported devices
    without doing much processing.
    """
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        if (handler_input.request_envelope.context and 
            handler_input.request_envelope.context.system and 
            handler_input.request_envelope.context.system.device and
            handler_input.request_envelope.context.system.device.supported_interfaces):
            # Since skill events won't have device information
            return handler_input.request_envelope.context.system.device.supported_interfaces.audio_player is None
        else:
            return False

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In CheckAudioInterfaceHandler")
        _ = handler_input.attributes_manager.request_attributes["_"]
        handler_input.response_builder.speak(
            _(data.DEVICE_NOT_SUPPORTED)).set_should_end_session(True)
        return handler_input.response_builder.response


class SkillEventHandler(AbstractRequestHandler):
    """Close session for skill events or when session ends.

    Handler to handle session end or skill events (SkillEnabled,
    SkillDisabled etc.)
    """
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return (handler_input.request_envelope.request.object_type.startswith(
            "AlexaSkillEvent") or
                is_request_type("SessionEndedRequest")(handler_input))

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In SkillEventHandler")
        _page_heard_from(handler_input)
        # A response Alexa rejects (e.g. an invalid Play URL) only shows up here:
        # the Echo just says there was a problem with the skill's response.
        req = handler_input.request_envelope.request
        if getattr(req, 'reason', None) or getattr(req, 'error', None):
            logger.warning("Session ended: reason=%s error=%s",
                           getattr(req, 'reason', None), getattr(req, 'error', None))
        return handler_input.response_builder.response


class LaunchRequestOrPlayAudioHandler(AbstractRequestHandler):
    """Launch radio for skill launch or PlayAudio intent."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return (is_request_type("LaunchRequest")(handler_input) or
                is_intent_name("PlayAudio")(handler_input))

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In LaunchRequestOrPlayAudioHandler")

        _ = handler_input.attributes_manager.request_attributes["_"]
        device_id = _device_id_from(handler_input)
        device_mapping.pair_if_waiting(device_id)
        import shared_store
        if device_mapping.is_another_echos_stream(device_id, (shared_store._store or {}).get('playerId')):
            handler_input.response_builder.speak(
                _(data.UNPAIRED_ECHO_MSG)).set_should_end_session(True)
            return handler_input.response_builder.response
        # "Alexa, open music assistant": MA sends a new stream at its own
        # position, paused or playing. Replaying the stored URL makes MA
        # restart that flow where it first started it, while MA's clock (and
        # with it track time and the next title) runs on from the old start.
        # PlayAudio is MA itself sending a new stream: play that.
        if (is_request_type("LaunchRequest")(handler_input)
                and _resume_through_ma(handler_input) == "ok"):
            return handler_input.response_builder.set_should_end_session(True).response
        request = handler_input.request_envelope.request
        url, _audio = _get_stream_url(request)
        logger.info("URL from util.audio_data: %s", url)

        # FIX: Fallback to shared_store directly if util.audio_data is empty
        if not url:
            try:
                import shared_store
                if shared_store._store and shared_store._store.get('streamUrl'):
                    url = shared_store._store['streamUrl']
                    logger.info("URL from shared_store fallback: %s", url)
            except Exception as e:
                logger.warning("shared_store fallback failed: %s", e)

        if not url:
            logger.warning("No streamUrl available for Launch/Play request")
            handler_input.response_builder.speak(
                "Sorry, I could not retrieve the latest music stream from the API. Please check your setup.").set_should_end_session(True)
            return handler_input.response_builder.response

        logger.info("Playing URL: %s", url)
        started = time.monotonic()
        response = util.play(
            url=url,
            offset=0,
            text=data.WELCOME_MSG,
            response_builder=handler_input.response_builder,
            supports_apl=_supports_apl(handler_input),
            device_id=_device_id_from(handler_input)
        )
        # Alexa drops a response that takes over ~8 s: then the page never
        # shows, or shows without ever sending a refresh.
        logger.info("Player page response built in %d ms (APL: %s)",
                    (time.monotonic() - started) * 1000, _supports_apl(handler_input))
        # Only a page sends events that cancel the watchdog.
        if _supports_apl(handler_input) and util.apl_enabled():
            live_page.closed(_device_id_from(handler_input))
            _watch_page(_device_id_from(handler_input))
        return response


# A working player page sends its first refresh 2-4 s after it opens. After
# a few minutes of idle the Show's first page sometimes shows but neither
# plays nor sends a single event, until it closes ~30 s later. MA sending
# the stream again (as a skip in MA does) brings up a page that works.
_PAGE_WATCHDOG_S = 10
# At most one resend per this long per Echo, so a Show that keeps failing
# doesn't loop.
_PAGE_RESEND_PAUSE_S = 60
_page_watch = {}        # device_id -> Timer
_page_resent_at = {}    # device_id -> time.monotonic()
_page_watch_lock = threading.Lock()


def _watch_page(device_id):
    """Have MA resend the stream if the page just sent sends no event in time."""
    player_id = device_mapping.get_player_for_device(device_id)
    if not player_id:
        return

    def check():
        with _page_watch_lock:
            if _page_watch.get(device_id) is not timer:
                return
            del _page_watch[device_id]
            last = _page_resent_at.get(device_id)
            if last is not None and time.monotonic() - last < _PAGE_RESEND_PAUSE_S:
                logger.warning("Player page on %s sent no event in %d s; resent recently, leaving it",
                               player_id, _PAGE_WATCHDOG_S)
                return
            _page_resent_at[device_id] = time.monotonic()
        result = ma_control.get_current_track_time(player_id)
        if not result or result[2]:
            return   # MA has nothing to play, or is paused: a resend would play it
        logger.warning("Player page on %s sent no event in %d s: asking MA to resend the stream",
                       player_id, _PAGE_WATCHDOG_S)
        if ma_control.resume_at(player_id) is None:
            logger.warning("MA did not resend the stream for %s", player_id)

    timer = threading.Timer(_PAGE_WATCHDOG_S, check)
    timer.daemon = True
    with _page_watch_lock:
        old = _page_watch.pop(device_id, None)
        _page_watch[device_id] = timer
    if old:
        old.cancel()
    timer.start()


def _page_heard_from(handler_input):
    """The Echo's page sent an event or closed: no resend needed."""
    if not is_request_type("Alexa.Presentation.APL.UserEvent")(handler_input):
        # On a SessionEnded nothing else tells MA the page missed its hand-off.
        live_page.closed(_device_id_from(handler_input),
                         fall_back=is_request_type("SessionEndedRequest")(handler_input))
    with _page_watch_lock:
        timer = _page_watch.pop(_device_id_from(handler_input), None)
    if timer:
        timer.cancel()


class HelpIntentHandler(AbstractRequestHandler):
    """Handler for providing help information to user."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_intent_name("AMAZON.HelpIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In HelpIntentHandler")
        _ = handler_input.attributes_manager.request_attributes["_"]
        handler_input.response_builder.speak(
            _(data.HELP_MSG).format(_SKILL_NAME)
        ).set_should_end_session(False)
        return handler_input.response_builder.response


class UnhandledIntentHandler(AbstractRequestHandler):
    """Handler for fallback intent, for unmatched utterances.

    2018-July-12: AMAZON.FallbackIntent is currently available in all
    English locales. This handler will not be triggered except in that
    locale, so it can be safely deployed for any locale. More info
    on the fallback intent can be found here:
    https://developer.amazon.com/docs/custom-skills/standard-built-in-intents.html#fallback
    """
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_intent_name("AMAZON.FallbackIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In UnhandledIntentHandler")
        _ = handler_input.attributes_manager.request_attributes["_"]
        handler_input.response_builder.speak(
            _(data.UNHANDLED_MSG)).set_should_end_session(True)
        return handler_input.response_builder.response


def _device_id_from(handler_input):
    try:
        return handler_input.request_envelope.context.system.device.device_id
    except Exception:
        return None


def _sync_to_ma_unless_echo(handler_input, command, unless_paused=False):
    _sync_device_to_ma(_device_id_from(handler_input), command, unless_paused)


def _sync_device_to_ma(device_id, command, unless_paused=False):
    """Best-effort: forward pause/stop/resume to MA, unless this request is
    the echo of a command we ourselves just triggered on MA (see ma_control
    docstring for why that echo happens and must be suppressed once).

    Always lets the caller's normal Alexa-side action proceed regardless of
    outcome here - this is a secondary sync, not the primary response.

    unless_paused: leave a paused MA as it is (see CancelOrStopIntentHandler).
    """

    if ma_control.is_echo_of_ma_command(device_id, command):
        logger.info("Suppressing MA %s: echo of our own MA-triggered command for device_id=%s", command, device_id)
        return

    player_id = device_mapping.get_player_for_device(device_id)
    if not player_id:
        return
    if unless_paused and ma_control.is_paused(player_id):
        logger.info("Not sending %s to MA player %s: it is paused", command, player_id)
        return

    ma_control.mark_ma_triggered(device_id, command)
    if not ma_control.send_player_command(player_id, command):
        logger.warning("Failed to sync %s to MA player %s", command, player_id)


def _next_or_previous_to_ma(handler_input, command):
    return _next_or_previous_for(_device_id_from(handler_input), command)


def _next_or_previous_for(device_id, command):
    """Send next/previous to the MA player paired with this Echo.

    Returns "ok", "unmapped" (Echo not paired in /devices) or "failed".
    MA then pushes the new stream and relaunches the skill, as for any
    track change.
    """
    player_id = device_mapping.get_player_for_device(device_id)
    if not player_id:
        logger.warning("No MA player mapped for device_id=%s", device_id)
        return "unmapped"
    if not ma_control.send_player_command(player_id, command):
        return "failed"
    return "ok"


class NextOrPreviousIntentHandler(AbstractRequestHandler):
    """Handler for next or previous intents.

    Routed to Music Assistant (not Alexa's own AudioPlayer) since MA is
    the source of truth for what plays next in flow/radio mode. Requires
    the requesting device to be paired with an MA player_id via /devices.
    """
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return (is_intent_name("AMAZON.NextIntent")(handler_input) or
                is_intent_name("AMAZON.PreviousIntent")(handler_input))

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In NextOrPreviousIntentHandler")
        _ = handler_input.attributes_manager.request_attributes["_"]

        intent_name = handler_input.request_envelope.request.intent.name
        command = "next" if intent_name == "AMAZON.NextIntent" else "previous"

        result = _next_or_previous_to_ma(handler_input, command)
        if result == "unmapped":
            handler_input.response_builder.speak(
                _(data.DEVICE_NOT_MAPPED_MSG)).set_should_end_session(True)
            return handler_input.response_builder.response

        if result == "failed":
            handler_input.response_builder.speak(
                _(data.MA_COMMAND_FAILED_MSG)).set_should_end_session(True)
            return handler_input.response_builder.response

        handler_input.response_builder.set_should_end_session(True)
        return handler_input.response_builder.response


class CancelOrStopIntentHandler(AbstractRequestHandler):
    """Handler for cancel and stop intents."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return (is_intent_name("AMAZON.CancelIntent")(handler_input) or
                is_intent_name("AMAZON.StopIntent")(handler_input))

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In CancelOrStopIntentHandler")
        _page_heard_from(handler_input)
        _ = handler_input.attributes_manager.request_attributes["_"]
        # The Show closes a paused page after ~30 s and then sends this Stop.
        # MA stays paused: play in MA then resumes through us at the paused
        # position, where a stopped MA would start a stream we can't place.
        _sync_to_ma_unless_echo(handler_input, "stop", unless_paused=True)
        return util.stop(_(data.STOP_MSG), handler_input.response_builder, supports_apl=_supports_apl(handler_input))


class PauseIntentHandler(AbstractRequestHandler):
    """Handler for AMAZON.PauseIntent."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_intent_name("AMAZON.PauseIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In PauseIntentHandler")
        _ = handler_input.attributes_manager.request_attributes["_"]
        session_new = False
        if getattr(handler_input.request_envelope, 'session', None):
            session_new = bool(handler_input.request_envelope.session.new)

        _sync_to_ma_unless_echo(handler_input, "pause")

        return util.pause(text=None,
                  response_builder=handler_input.response_builder,
                  supports_apl=_supports_apl(handler_input),
                  session_new=session_new,
                  device_id=_device_id_from(handler_input))


class ResumeIntentHandler(AbstractRequestHandler):
    """Handler for resume intent."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_intent_name("AMAZON.ResumeIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In ResumeIntentHandler")
        request = handler_input.request_envelope.request
        _ = handler_input.attributes_manager.request_attributes["_"]

        # Voice "Alexa, resume", or play in MA's UI (which MA's provider
        # speaks as this intent): MA builds a new stream from the paused
        # position and relaunches us; the old URL would start the track over.
        if _resume_through_ma(handler_input) == "ok":
            return handler_input.response_builder.set_should_end_session(True).response

        url, _audio = _get_stream_url(request)
        if not url:
            logger.warning("No stream url available for Resume request")
            handler_input.response_builder.speak(
                "Sorry, I couldn't reach the stream right now.").set_should_end_session(True)
            return handler_input.response_builder.response

        offset = util.get_resume_offset(_device_id_from(handler_input), url)

        return util.play(
            url=url,
            offset=offset,
            text=data.WELCOME_MSG,
            response_builder=handler_input.response_builder,
            supports_apl=_supports_apl(handler_input),
            device_id=_device_id_from(handler_input)
        )


class StartOverIntentHandler(AbstractRequestHandler):
    """Handler for AMAZON.StartOverIntent: restart the current track.

    Routed to Music Assistant, same as Next/Previous. Distinct from
    Previous, which skips to the prior track.
    """
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_intent_name("AMAZON.StartOverIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In StartOverIntentHandler")
        _ = handler_input.attributes_manager.request_attributes["_"]

        device_id = _device_id_from(handler_input)
        player_id = device_mapping.get_player_for_device(device_id)
        if not player_id:
            logger.warning("No MA player mapped for device_id=%s", device_id)
            handler_input.response_builder.speak(
                _(data.DEVICE_NOT_MAPPED_MSG)).set_should_end_session(True)
            return handler_input.response_builder.response

        if not ma_control.send_player_command(player_id, "start_over"):
            handler_input.response_builder.speak(
                _(data.MA_COMMAND_FAILED_MSG)).set_should_end_session(True)
            return handler_input.response_builder.response

        handler_input.response_builder.set_should_end_session(True)
        return handler_input.response_builder.response


class LoopOrShuffleIntentHandler(AbstractRequestHandler):
    """Handler for loop on/off, shuffle on/off intent."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return (is_intent_name("AMAZON.LoopOnIntent")(handler_input) or
                is_intent_name("AMAZON.LoopOffIntent")(handler_input) or
                is_intent_name("AMAZON.ShuffleOnIntent")(handler_input) or
                is_intent_name("AMAZON.ShuffleOffIntent")(handler_input))

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In LoopOrShuffleIntentHandler")

        _ = handler_input.attributes_manager.request_attributes["_"]
        speech = _(data.NOT_POSSIBLE_MSG)
        return handler_input.response_builder.speak(speech).response

# ###################################################################

# ########## AUDIOPLAYER INTERFACE HANDLERS #########################
# This section contains handlers related to Audioplayer interface

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
            util.record_stopped_position(
                _device_id_from(handler_input),
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
        request = handler_input.request_envelope.request
        url, _audio = _get_stream_url(request)
        if not url:
            logger.warning("No stream url available for PlaybackNearlyFinished")
            return handler_input.response_builder.response

        # ENQUEUE must chain from the stream that is currently playing.
        current_token = None
        try:
            context = handler_input.request_envelope.context
            if context and context.audio_player:
                current_token = context.audio_player.token
        except AttributeError:
            current_token = None

        return util.play_later(
            url=url,
            response_builder=handler_input.response_builder,
            expected_previous_token=current_token
        )


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
        url, _audio = _get_stream_url(request)
        if not url:
            logger.warning("No stream url available for PlaybackFailed; skipping restart")
            return handler_input.response_builder.response

        return util.play(
            url=url, 
            offset=0, 
            text=None,
            response_builder=handler_input.response_builder,
            supports_apl=_supports_apl(handler_input),
            device_id=_device_id_from(handler_input)
        )


class ExceptionEncounteredHandler(AbstractRequestHandler):
    """Handler to handle exceptions from responses sent by AudioPlayer
    request.
    """
    def can_handle(self, handler_input):
        # type; (HandlerInput) -> bool
        return is_request_type("System.ExceptionEncountered")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("\n**************** EXCEPTION *******************")
        logger.info(handler_input.request_envelope)
        return handler_input.response_builder.response

# ###################################################################

# ########## APL INTERFACE HANDLERS #################################
# This section contains handlers related to APL interface

_APL_EVENTS = ("MetadataRefresh", "Next", "Previous", "Pause", "Play", "Seek", "QueueEnded", "Pull")
# Where a button's event carries its press number (bell.pressed).
_PRESS_NUMBER_AT = {"Next": 1, "Previous": 1, "Play": 2, "Pause": 2, "Seek": 2}
_UNDO_BUTTON_PRESS = [
    {"type": "ControlMedia", "componentId": "videoPlayer", "command": "play"},
    {"type": "SetValue", "componentId": "Busy_Overlay", "property": "opacity", "value": 0},
    {"type": "SetValue", "componentId": "Busy_Overlay", "property": "display", "value": "none"},
]
_track_changes = track_time.pages

_SKILL_NAME = "Music Assistant"

# Per Echo, each (title key, ms into the track): where the page was paused,
# and where MA paused (its elapsed time on the paused page). Where the
# stream of our last resume starts: (ms into the track, valid until).
_STREAM_START_TTL_SECONDS = 60
_paused_at = {}
_ma_paused_at = {}
_stream_start = {}


def _remember(store, device_id, position_ms):
    if device_id and position_ms is not None:
        store[device_id] = (track_time.title_key(data.info), position_ms)


def _recall(store, device_id):
    """The position kept in store for this Echo, if it is for the current track."""
    entry = store.get(device_id)
    if entry and entry[0] == track_time.title_key(data.info):
        return entry[1]
    return None


def _take_stream_start(device_id, keep=False):
    start, valid_until = (_stream_start.get if keep else _stream_start.pop)(device_id, (None, 0))
    return start if valid_until >= time.time() else None


def _resume_through_ma(handler_input, position_ms=None):
    return _resume_for(_device_id_from(handler_input), position_ms)


def _resume_for(device_id, position_ms=None):
    """Resume MA at position_ms, else where the page or MA paused.

    Returns "ok", "unmapped" or "failed". MA pushes a new stream from that
    position and relaunches the skill, as for next/previous.
    """
    player_id = device_mapping.get_player_for_device(device_id)
    if not player_id:
        logger.info("No MA player mapped for device_id=%s", (device_id or '')[-8:])
        return "unmapped"
    if position_ms is None:
        position_ms = _recall(_paused_at, device_id)
    start = ma_control.resume_at(player_id, position_ms)
    if start is None:
        return "failed"
    _paused_at.pop(device_id, None)
    _ma_paused_at.pop(device_id, None)
    _stream_start[device_id] = (start, time.time() + _STREAM_START_TTL_SECONDS)
    logger.info("Resuming %s at %s ms", player_id, start)
    return "ok"


def _apl_event_arguments(handler_input):
    try:
        return list(getattr(handler_input.request_envelope.request, 'arguments', None) or [])
    except Exception:
        return []


def _session_id_from(handler_input):
    session = getattr(handler_input.request_envelope, 'session', None)
    return getattr(session, 'session_id', None) or _device_id_from(handler_input)


# Where a page's events carry its id (the skill gives every page one, see apl.py).
_PAGE_ID_AT = {"Pull": 4, "MetadataRefresh": 3}


def _page_key(handler_input, arguments):
    """The page an event comes from: its id, or (a page from before page ids) the session."""
    index = _PAGE_ID_AT.get(arguments[0]) if arguments else None
    if index is not None and len(arguments) > index and arguments[index]:
        return str(arguments[index])
    return _session_id_from(handler_input)


def _track_time_commands(handler_input, arguments, page_key):
    """SetValue commands for trackOffset/trackDuration, or [] if unneeded.

    Only on a track change (or the first refresh of a new page): MA is
    asked once, and the page counts from its own video position after that.
    Kept per page (page_key), so a new page always starts afresh.
    """
    key = track_time.track_key(data.info)
    if not _track_changes.changed(page_key, key):
        return []
    session_id = page_key
    player_id = device_mapping.get_player_for_device(_device_id_from(handler_input))
    if not player_id:
        return []
    device_id = _device_id_from(handler_input)
    result = ma_control.get_current_track_time(player_id)
    duration_ms, elapsed_ms, paused, last, upcoming = result if result else (None, None, False, False, None)
    position_ms = track_time.video_position_ms(arguments)
    previous_end = _track_changes.previous_end(session_id)
    if previous_end == 0:   # a new page
        offset = track_time.page_start_offset_ms(position_ms, paused, _recall(_paused_at, device_id),
                                                 elapsed_ms, _take_stream_start(device_id),
                                                 _recall(_ma_paused_at, device_id))
        if paused:
            _remember(_ma_paused_at, device_id, elapsed_ms)
        else:
            _paused_at.pop(device_id, None)
            _ma_paused_at.pop(device_id, None)
    else:
        offset = track_time.choose_offset_ms(previous_end, position_ms, elapsed_ms)
    _track_changes.record(session_id, offset, duration_ms, last)
    logger.info("Track time for %s: duration=%s ms elapsed=%s ms paused=%s offset=%s ms last=%s next=%s",
                player_id, duration_ms, elapsed_ms, paused, offset, last,
                upcoming and (upcoming["duration_ms"], bool(upcoming["image"])))
    shown = (position_ms or 0) - offset if paused and offset is not None else None
    return track_time.set_track_time_commands(offset, duration_ms, shown,
                                              _track_changes.queue_end_ms(session_id),
                                              _with_ma_hostname(upcoming))


def _with_ma_hostname(upcoming):
    """upcoming with its image on MA's public hostname, as the current track's (util)."""
    if not upcoming or not upcoming.get("image"):
        return upcoming
    try:
        hostname = util.get_ma_hostname(raise_on_http_scheme=False)
    except ValueError:
        hostname = ''
    if not hostname:
        return upcoming
    return dict(upcoming, image=util.replace_ip_in_url(upcoming["image"], hostname))


def _start_track_time(device_id):
    """(offset ms, duration ms) for a page the skill builds now, or None.

    As the page's first refresh works it out (_track_time_commands), with
    the video at 0. The refresh still does, and sets the same values.
    """
    player_id = device_mapping.get_player_for_device(device_id)
    result = ma_control.get_current_track_time(player_id) if player_id else None
    if not result:
        return None
    duration_ms, elapsed_ms, paused = result[:3]
    offset = track_time.page_start_offset_ms(0, paused, _recall(_paused_at, device_id), elapsed_ms,
                                             _take_stream_start(device_id, keep=True),
                                             _recall(_ma_paused_at, device_id))
    return offset, duration_ms


apl.set_start_track_time(_start_track_time)


def _press(device_id, kind, position_ms=None):
    """Do what the page's button asks (pause, play, next, previous, seek), from either path.

    False if the page should undo its press: MA won't send a new stream.
    Pause: remember where, and pause MA. The page pauses itself; MA's pause
    hand-off isn't needed (or, unpatched, MA makes the Echo hear "pause",
    which reopens the page paused).
    """
    if kind == "pause":
        logger.info("APL pause button at %s ms", position_ms)
        _remember(_paused_at, device_id, position_ms)
        _sync_device_to_ma(device_id, "pause")
        live_page.take(device_id, "pause")
        return True
    if kind == "play":
        logger.info("APL play button at %s ms", position_ms)
        return _resume_for(device_id, position_ms) == "ok"
    if kind == "seek":
        # MA plays from there, also when it was paused: like a play at that position.
        logger.info("APL seek to %s ms", position_ms)
        return position_ms is not None and _resume_for(device_id, position_ms) == "ok"
    logger.info("APL %s button", kind)
    return _next_or_previous_for(device_id, kind) == "ok"


def _lan_press(device_id, kind, position_ms):
    """A button press that came over the LAN, through the page's bell (bell.py)."""
    if kind in ("pause", "play", "next", "previous", "seek") and not _press(device_id, kind, position_ms):
        bell.undo(device_id)


def _ma_state_of(device_id, page_id):
    """(MA state, MA elapsed ms, the page's track offset ms) for the bell's check, or None."""
    player_id = device_mapping.get_player_for_device(device_id)
    state = ma_control.get_queue_state(player_id) if player_id else None
    if not state:
        return None
    return state[0], state[1], _track_changes.offset(page_id)


bell.set_handlers(press=_lan_press, ma_state=_ma_state_of,
                  pause_ma=lambda device_id: _sync_device_to_ma(device_id, "pause"))


class APLUserEventHandler(AbstractRequestHandler):
    """Handler for APL UserEvent requests from the player page.

    - Pull: the page's doorbell (see bell.py) says there's news; the
      answer has it, and the new state number for the page.
    - MetadataRefresh: a page without a doorbell sends it every couple of
      seconds. Both update title, artist and images, take MA's hand-offs,
      and on a track change set the track time (see track_time).
    - Next / Previous / Play / Pause / Seek: the page's own buttons and
      seek bar (see _press).
      They go to MA like "Alexa, next" does; the page's video must not skip
      by itself, since it only has the one flow stream the page was opened with.
    """
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        if not is_request_type("Alexa.Presentation.APL.UserEvent")(handler_input):
            return False
        arguments = _apl_event_arguments(handler_input)
        return bool(arguments) and arguments[0] in _APL_EVENTS

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        _page_heard_from(handler_input)
        live_page.heard_from(_device_id_from(handler_input))
        arguments = _apl_event_arguments(handler_input)
        if arguments[0] in _PRESS_NUMBER_AT:
            # The page's buttons. The same press may have come over the LAN
            # already (bell.py): then the skill acted on it, and this answer is empty.
            kind = arguments[0].lower()
            index = _PRESS_NUMBER_AT[arguments[0]]
            number = arguments[index] if len(arguments) > index else None
            if not bell.pressed(_device_id_from(handler_input), number, kind):
                return handler_input.response_builder.set_should_end_session(None).response
            position_ms = (track_time.video_position_ms(arguments, index=1)
                           if kind in ("pause", "play", "seek") else None)
            done = _press(_device_id_from(handler_input), kind, position_ms)
            # A press stops the page's running command sequence, and with it a
            # refresh chain: restart it (a page with a doorbell has no chain).
            if kind == "pause":
                self._schedule_refresh(handler_input, [
                    {"type": "ControlMedia", "componentId": "videoPlayer", "command": "pause"}])
            elif not done:
                # MA won't send a new stream: undo the page's pause and overlay
                # (play: play the page's own stream).
                self._schedule_refresh(handler_input, _UNDO_BUTTON_PRESS)
            else:
                self._schedule_refresh(handler_input)
            return handler_input.response_builder.set_should_end_session(None).response
        if arguments[0] == "QueueEnded":
            # The page paused at the end of MA's last track. Stop MA, or it
            # restarts the old flow when the Show asks for the stream again;
            # MA's stop then closes the page.
            logger.info("Queue ended on the page")
            _sync_to_ma_unless_echo(handler_input, "stop")
            return handler_input.response_builder.set_should_end_session(None).response

        if arguments[0] == "Pull":
            # ["Pull", seen state number, video position, playing, page id]
            device_id = _device_id_from(handler_input)
            try:
                seen = int(float(arguments[1]))
            except (IndexError, TypeError, ValueError):
                seen = 0
            page_id = str(arguments[4]) if len(arguments) > 4 else ""
            answer = bell.pull(page_id, device_id, seen)
            if answer is None:
                return handler_input.response_builder.set_should_end_session(None).response
            state, handoffs = answer
            logger.info("Page on %s pulls state %s", (device_id or "")[-8:], state)
            return self._update_page(handler_input, arguments, handoffs, [
                {"type": "SetValue", "componentId": "AudioPlayerRoot",
                 "property": "bellSeen", "value": state}], refresh=False)

        handed = live_page.take(_device_id_from(handler_input))
        return self._update_page(handler_input, arguments, [handed] if handed else [], [],
                                 refresh=True)

    def _update_page(self, handler_input, arguments, handoffs, commands, refresh):
        """The answer to a Pull or MetadataRefresh: the latest metadata and track time, and the hand-offs.

        refresh: ask for the next MetadataRefresh (a page without a doorbell).
        """
        # One ExecuteCommands directive for the whole answer: each new one
        # cancels the commands still running from the one before (a loading
        # PlayMedia, and the commands after it).
        page_key = _page_key(handler_input, arguments)
        commands, media = list(commands), []
        for handed in handoffs:
            more, more_media = self._take_handoff(handler_input, page_key, *handed)
            commands += more
            media += more_media
            if handed[0] == "stream":
                # the video starts the new stream at 0
                arguments = [arguments[0], arguments[1] if len(arguments) > 1 else 0, 0]

        # Fetch latest metadata from Music Assistant
        changed = False
        try:
            result = data.get_latest()
            changed = bool(result and result.get('changed'))
            if changed:
                logger.info("Metadata changed")
            else:
                logger.debug("Metadata unchanged, skipping update")
        except Exception:
            logger.exception("Failed to fetch latest metadata")

        # Check if we have valid metadata
        if not data.info.get('audioSources'):
            logger.warning("No audio sources available for metadata refresh")
        else:
            # Send updated APL document with new metadata
            if changed:
                try:
                    util.update_apl_metadata(handler_input.response_builder)
                    logger.info("APL metadata update directive added to response")
                except Exception:
                    logger.exception("Failed to update APL metadata")
            try:
                commands += _track_time_commands(handler_input, arguments, page_key)
            except Exception:
                logger.exception("Failed to update APL track time")

        if not refresh:
            util.execute_apl_commands(handler_input.response_builder, commands + media)
            return handler_input.response_builder.set_should_end_session(None).response
        # The media command runs next to the refresh timer: whether or not it
        # completes before the stream plays, the refreshes go on.
        refresh = util.apl_refresh_commands()
        if media:
            refresh = [{"type": "Parallel",
                        "commands": media + [{"type": "Sequential", "commands": refresh}]}]
        util.execute_apl_commands(handler_input.response_builder, commands + refresh)

        # Unset, not False: False opens the mic on every refresh and ducks the music;
        # the APL page keeps the session alive by itself.
        return handler_input.response_builder.set_should_end_session(None).response

    @staticmethod
    def _take_handoff(handler_input, page_key, command, value):
        """(APL commands, media commands) for what MA (or the skill) hands this open page."""
        if command == "undo":
            # The skill couldn't do what a press over the LAN asked (bell.py).
            logger.info("Page undoes its button press")
            return list(_UNDO_BUTTON_PRESS), []
        logger.info("Open page takes %s from MA", command)
        if command == "stream":
            _track_changes.started(page_key)
            return ([{"type": "SetValue", "componentId": "AudioPlayerRoot",
                      "property": "videoProgressValue", "value": 0}] + _UNDO_BUTTON_PRESS[1:],
                    [{"type": "PlayMedia", "componentId": "videoPlayer", "source": value,
                      "audioTrack": "background"}])
        if command == "pause":
            return [], [{"type": "ControlMedia", "componentId": "videoPlayer", "command": "pause"}]
        # resume: MA sends a new stream from the paused position, handed over the same way
        if _resume_through_ma(handler_input) != "ok":
            return [], [{"type": "ControlMedia", "componentId": "videoPlayer", "command": "play"}]
        return [], []

    @staticmethod
    def _schedule_refresh(handler_input, commands=()):
        # Without a doorbell, always schedule the next refresh so polling
        # continues; other commands go in the same directive (a later one
        # would cancel them). A page with a doorbell needs no refresh.
        if bell.has_page(_device_id_from(handler_input)):
            util.execute_apl_commands(handler_input.response_builder, list(commands))
            return
        try:
            util.execute_apl_commands(handler_input.response_builder,
                                      list(commands) + util.apl_refresh_commands())
        except Exception:
            logger.exception("Failed to schedule APL refresh")

# ###################################################################

# ########## PLAYBACK CONTROLLER INTERFACE HANDLERS #################
# This section contains handlers related to Playback Controller interface
# https://developer.amazon.com/docs/custom-skills/playback-controller-interface-reference.html#requests

class PlayCommandHandler(AbstractRequestHandler):
    """Handler for Play command from hardware buttons or touch control.

    This handler handles the play command sent through hardware buttons such
    as remote control or the play control from Alexa-devices with a screen.
    """
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_request_type(
            "PlaybackController.PlayCommandIssued")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In PlayCommandHandler")
        _ = handler_input.attributes_manager.request_attributes["_"]
        # As "Alexa, resume": MA sends a new stream from the paused position.
        if _resume_through_ma(handler_input) == "ok":
            return handler_input.response_builder.response
        request = handler_input.request_envelope.request
        url, _audio = _get_stream_url(request)
        if not url:
            logger.warning("No stream url available for PlayCommand; notifying user")
            handler_input.response_builder.speak(
                "Sorry, I couldn't reach the stream right now.").set_should_end_session(True)
            return handler_input.response_builder.response

        return util.play(
            url=url,
            offset=0,
            text=None,
            response_builder=handler_input.response_builder,
            supports_apl=_supports_apl(handler_input),
            device_id=_device_id_from(handler_input)
        )


class NextOrPreviousCommandHandler(AbstractRequestHandler):
    """Handler for Next or Previous command from hardware buttons or touch
    control.

    This handler handles the next/previous command sent through hardware
    buttons such as remote control or the next/previous control from
    Alexa-devices with a screen.
    """
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return (is_request_type(
            "PlaybackController.NextCommandIssued")(handler_input) or
                is_request_type(
                    "PlaybackController.PreviousCommandIssued")(handler_input))

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In NextOrPreviousCommandHandler")
        req_type = handler_input.request_envelope.request.object_type
        command = "next" if "Next" in req_type else "previous"

        _next_or_previous_to_ma(handler_input, command)
        return handler_input.response_builder.response


class PauseCommandHandler(AbstractRequestHandler):
    """Handler for Pause command from hardware buttons or touch control.

    This handler handles the pause command sent through hardware
    buttons such as remote control or the pause control from
    Alexa-devices with a screen.
    """
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_request_type("PlaybackController.PauseCommandIssued")(
            handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In PauseCommandHandler")
        _sync_to_ma_unless_echo(handler_input, "pause")
        return util.stop(text=None,
                         response_builder=handler_input.response_builder,
                         supports_apl=_supports_apl(handler_input))

# ###################################################################

# ################## EXCEPTION HANDLERS #############################
class CatchAllExceptionHandler(AbstractExceptionHandler):
    """Catch all exception handler, log exception and
    respond with custom message.
    """
    def can_handle(self, handler_input, exception):
        # type: (HandlerInput, Exception) -> bool
        return True

    def handle(self, handler_input, exception):
        # type: (HandlerInput, Exception) -> Response
        logger.info("In CatchAllExceptionHandler")
        logger.error(exception, exc_info=True)
        # Page events and player requests run during playback, and Alexa
        # rejects speech in AudioPlayer/PlaybackController responses: stay silent.
        req_type = getattr(handler_input.request_envelope.request, 'object_type', '') or ''
        if req_type.startswith(("Alexa.Presentation.APL.", "AudioPlayer.", "PlaybackController.")):
            return handler_input.response_builder.response
        _ = handler_input.attributes_manager.request_attributes.get("_", gettext.gettext)
        handler_input.response_builder.speak(_(data.UNHANDLED_MSG)).set_should_end_session(True)
        return handler_input.response_builder.response

# ###################################################################

# ############# REQUEST / RESPONSE INTERCEPTORS #####################

class APLSupportRequestInterceptor(AbstractRequestInterceptor):
    """Record per request whether the device supports APL.

    A request attribute, not a module global: requests from different
    Echos are handled concurrently.
    """
    def process(self, handler_input):
        try:
            supported_interfaces = getattr(
                handler_input.request_envelope.context.system.device.supported_interfaces,
                'alexa_presentation_apl', None)
        except AttributeError:
            supported_interfaces = None
        handler_input.attributes_manager.request_attributes["supports_apl"] = (
            supported_interfaces is not None)

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
        logger.debug("Alexa Response: {}".format(response))

# ###################################################################


# ############# REGISTER HANDLERS #####################
# Request Handlers
sb.add_request_handler(CheckAudioInterfaceHandler())
sb.add_request_handler(SkillEventHandler())
sb.add_request_handler(LaunchRequestOrPlayAudioHandler())
sb.add_request_handler(PlayCommandHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(ExceptionEncounteredHandler())
sb.add_request_handler(APLUserEventHandler())
sb.add_request_handler(UnhandledIntentHandler())
sb.add_request_handler(NextOrPreviousIntentHandler())
sb.add_request_handler(NextOrPreviousCommandHandler())
sb.add_request_handler(PauseIntentHandler())
sb.add_request_handler(CancelOrStopIntentHandler())
sb.add_request_handler(PauseCommandHandler())
sb.add_request_handler(ResumeIntentHandler())
sb.add_request_handler(StartOverIntentHandler())
sb.add_request_handler(LoopOrShuffleIntentHandler())
sb.add_request_handler(PlaybackStartedHandler())
sb.add_request_handler(PlaybackFinishedHandler())
sb.add_request_handler(PlaybackStoppedHandler())
sb.add_request_handler(PlaybackNearlyFinishedHandler())
sb.add_request_handler(PlaybackFailedHandler())

# Exception handlers
sb.add_exception_handler(CatchAllExceptionHandler())

# Interceptors
sb.add_global_request_interceptor(APLSupportRequestInterceptor())
sb.add_global_request_interceptor(RequestLogger())
sb.add_global_request_interceptor(LocalizationInterceptor())
sb.add_global_response_interceptor(ResponseLogger())

# AWS Lambda handler
lambda_handler = sb.lambda_handler()
