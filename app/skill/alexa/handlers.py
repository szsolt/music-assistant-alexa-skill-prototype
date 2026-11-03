"""The skill's request handlers, and their registration on the skill builder."""

import gettext
import json
import logging

from ask_sdk_core.dispatch_components import AbstractRequestHandler, AbstractExceptionHandler
from ask_sdk_core.serialize import DefaultSerializer
from ask_sdk_core.utils import is_request_type, is_intent_name

from .. import actions, clock, data, devices, handoff, store, track_time, sleep_timer, view, watch
from .. import echo as echos
from .. import page as pages
from ..ma import client as ma_client, library, player
from ..ma.errors import MAFailed, MAUnavailable, or_none, safe_error
from ..voice import commands as voice_commands, match as voice_match
from ..page.apl import render
from ..page.html import channel
from . import audio, context, interceptors, replies

logger = logging.getLogger(__name__)
_serializer = DefaultSerializer()


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
        if _alexa_broke_off(req) and context.page_kind(handler_input) != "none":
            watch.reopen_later(context.device_id(handler_input))
        return handler_input.response_builder.response


_NOTHING_TO_PLAY_MSG = ("Music Assistant has nothing to play on this device yet. "
                        "Start a song for it in Music Assistant.")


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
        device_id = context.device_id(handler_input)
        devices.pair_if_waiting(device_id)
        if devices.is_another_echos_stream(device_id, (store.for_player(None) or {}).get('playerId')):
            return replies.speak(handler_input, _(data.UNPAIRED_ECHO_MSG))
        # MA sends a new stream at its own position; the stored URL would restart its old flow.
        # PlayAudio is MA itself sending a new stream: play that.
        launch = is_request_type("LaunchRequest")(handler_input)
        # The HTML page takes MA's new stream over the LAN, so it goes out
        # first: MA then needn't make the Echo hear "play audio".
        html = launch and context.page_kind(handler_input) == "html"
        before = echos.get(device_id).page
        if html:
            pages.start(handler_input.response_builder, device_id, context.page_kind(handler_input), start_paused=True)
            handoff.closed(device_id)
            handoff.heard_from(device_id)
        resumed = launch and actions.run(device_id, actions.Play()).ok
        if resumed and html:
            watch.watch_page(device_id)
            return handler_input.response_builder.response
        if resumed:
            return replies.speak(handler_input, None)
        if html:
            pages.withdraw(handler_input.response_builder, device_id, before, context.page_open(handler_input))
        # MA had nothing to resume for this Echo's player. The stored stream
        # is then the latest of any player: another player's old stream
        # shows its album and plays nothing.
        if (is_request_type("LaunchRequest")(handler_input)
                and devices.is_other_players_stream(
                    device_id, (store.for_player(None) or {}).get('playerId'))):
            return replies.speak(handler_input, _NOTHING_TO_PLAY_MSG)
        # A spoken "play" while MA is paused resumes MA, as "resume" does.
        if not is_request_type("LaunchRequest")(handler_input) and _said_play(device_id):
            result = replies.stream_reply(handler_input, actions.Play())
            if not isinstance(result, actions.Result):
                return result
        url = view.stream_url(context.device_id(handler_input))
        if not url:
            logger.warning("No streamUrl available for Launch/Play request")
            return replies.speak(handler_input, _NOTHING_TO_PLAY_MSG)

        logger.info("Playing URL: %s", safe_error(url))
        started = clock.monotonic()
        response = audio.play(handler_input, url, text=data.WELCOME_MSG)
        # Alexa drops a response that takes over ~8 s: then the page never
        # shows, or shows without ever sending a refresh.
        logger.info("Player page response built in %d ms (page: %s)",
                    (clock.monotonic() - started) * 1000, context.page_kind(handler_input))
        # Only a page sends events that cancel the watchdog.
        if context.page_kind(handler_input) != "none":
            handoff.closed(context.device_id(handler_input))
            watch.watch_page(context.device_id(handler_input))
        return response


class HtmlPageRenewHandler(AbstractRequestHandler):
    """The HTML page asks for a new page: Amazon closes it after 30 min without a touch or voice."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_request_type("Alexa.Presentation.HTML.Message")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        device_id = context.device_id(handler_input)
        message = getattr(handler_input.request_envelope.request, "message", None) or {}
        player_id = devices.get_player_for_device(device_id)
        # Without a page the skill holds, any page of this Echo may ask.
        page_id = channel.page_of(device_id)
        asking = message.get("renew") if isinstance(message, dict) else None
        if (not asking or (page_id and asking != page_id)
                or not player_id or or_none("read the track time", player.is_paused, player_id)):
            return handler_input.response_builder.response
        # As "Alexa, open music assistant": a new page, then MA's new stream for it.
        before = echos.get(device_id).page
        pages.start(handler_input.response_builder, device_id, context.page_kind(handler_input), start_paused=True)
        handoff.closed(device_id)
        handoff.heard_from(device_id)
        # The page names the next track when it asks just before it: then the new page starts there.
        item_id = message.get("item")
        if not actions.run(device_id, actions.Jump(item_id) if item_id else actions.Play()).ok:
            pages.withdraw(handler_input.response_builder, device_id, before, context.page_open(handler_input))
            return handler_input.response_builder.response
        logger.info("Page %s renewed%s", message["renew"], " at the next track" if message.get("item") else "")
        watch.watch_page(device_id)
        return handler_input.response_builder.response


# MA speaks "play audio" to the Echo about a second after sending its stream.
_MA_PLAY_SECONDS = 5


def _said_play(device_id):
    """A PlayAudio that isn't MA's: MA is paused and sent no stream just now."""
    player_id = devices.get_player_for_device(device_id)
    return bool(player_id) and not store.sent_since(player_id, _MA_PLAY_SECONDS) \
        and or_none("read the track time", player.is_paused, player_id)


def _alexa_broke_off(request):
    """True if Alexa ended the session with its own error, not one of the skill's."""
    error = getattr(request, 'error', None)
    kind = getattr(error, 'object_type', None) or getattr(error, 'type', None)
    return (getattr(request, 'object_type', None) == "SessionEndedRequest"
            and getattr(kind, 'value', kind) == "INTERNAL_SERVICE_ERROR")


def _page_heard_from(handler_input):
    """The Echo's page sent an event or closed: no resend needed."""
    if not is_request_type("Alexa.Presentation.APL.UserEvent")(handler_input):
        # On a SessionEnded nothing else tells MA the page missed its hand-off.
        handoff.closed(context.device_id(handler_input),
                         fall_back=is_request_type("SessionEndedRequest")(handler_input))
    watch.page_plays(context.device_id(handler_input))


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
        # With the player page open this was most likely meant for Alexa
        # ("turn off the light"): stay quiet and keep the page.
        return replies.ignore(handler_input)



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
        action = actions.Next() if intent_name == "AMAZON.NextIntent" else actions.Previous()

        result = replies.stream_reply(handler_input, action)
        if not isinstance(result, actions.Result):
            return result
        if result.status == "unpaired":
            return replies.speak(handler_input, _(data.DEVICE_NOT_MAPPED_MSG))
        return replies.speak(handler_input, _(data.MA_COMMAND_FAILED_MSG))


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
        device_id = context.device_id(handler_input)
        # MA stays paused, so a later play resumes at the paused position.
        actions.run(device_id, actions.Stop(unless_paused=True))
        # MA's stop comes as a new session, which leaves the open page playing on its own.
        channel.pause(device_id)
        return audio.stop(handler_input.response_builder, _(data.STOP_MSG))


class PauseIntentHandler(AbstractRequestHandler):
    """Handler for AMAZON.PauseIntent."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_intent_name("AMAZON.PauseIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In PauseIntentHandler")
        device_id = context.device_id(handler_input)
        screen = context.page_kind(handler_input) != "none"
        # As the page's pause button: MA resumes where the page paused, and the reply pauses the page.
        actions.run(device_id, actions.Pause(_page_position(device_id), page_paused=screen))
        builder = handler_input.response_builder
        if not screen:
            return audio.stop(builder)
        session = getattr(handler_input.request_envelope, 'session', None)
        if session is not None and session.new:
            try:
                pages.start(builder, device_id, context.page_kind(handler_input), start_paused=True)
            except Exception:
                logger.exception('Failed to re-render APL on session new')
        elif channel.has_page(device_id):
            channel.pause(device_id)
        else:
            APLUserEventHandler._schedule_refresh(handler_input, [_PAUSE_PAGE])
        # Unset, not False: False opens the mic (as for the refresh).
        return builder.set_should_end_session(None).response


def _page_position(device_id):
    """Where the Echo's open page is in its current track, by its last report; None if unknown.

    A page Alexa closed stays on the Echo's record for a while: only a live one counts.
    """
    echo = echos.find(device_id)
    page = echo.page if echo is not None else None
    if page is None or page.report is None or not handoff.is_live(device_id):
        return None
    at, playing, when = page.report
    if at is None:
        return None
    if playing:
        at += int((clock.monotonic() - when) * 1000)
    track = page.track or {}
    if "trackOffset" not in track:
        return None
    position = max(at - track["trackOffset"], 0)
    duration = track.get("trackDuration")
    # the page may have played past the track's end since its report
    return min(position, duration) if duration else position


class ResumeIntentHandler(AbstractRequestHandler):
    """Handler for resume intent."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_intent_name("AMAZON.ResumeIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In ResumeIntentHandler")
        _ = handler_input.attributes_manager.request_attributes["_"]

        # MA builds a new stream from the paused position; the old URL starts the track over.
        result = replies.stream_reply(handler_input, actions.Play())
        if not isinstance(result, actions.Result):
            return result

        url = view.stream_url(context.device_id(handler_input))
        if not url:
            logger.warning("No stream url available for Resume request")
            return replies.speak(handler_input, "Sorry, I couldn't reach the stream right now.")

        offset = actions.resume_offset(context.device_id(handler_input), url)

        return audio.play(handler_input, url, offset, data.WELCOME_MSG)


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

        result = actions.run(context.device_id(handler_input), actions.StartOver())
        if not result.ok:
            message = data.DEVICE_NOT_MAPPED_MSG if result.status == "unpaired" else data.MA_COMMAND_FAILED_MSG
            return replies.speak(handler_input, _(message))
        return replies.speak(handler_input, None)


class LoopOrShuffleIntentHandler(AbstractRequestHandler):
    """Shuffle on/off and repeat (loop) on/off, on the paired MA player's queue."""
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
        name = handler_input.request_envelope.request.intent.name
        on = name.endswith("OnIntent")
        if "Shuffle" in name:
            action, speech = actions.Shuffle(on), data.SHUFFLE_ON_MSG if on else data.SHUFFLE_OFF_MSG
        else:
            action, speech = actions.Repeat("all" if on else "off"), data.REPEAT_ON_MSG if on else data.REPEAT_OFF_MSG
        device_id = context.device_id(handler_input)
        result = actions.run(device_id, action)
        if result.status != "unpaired":
            # also when it failed: the pages show MA's buttons again
            clock.spawn(channel.player_news, device_id, ("modes",), name="news")
        return replies.speak(handler_input, _(speech if result.ok else replies.failed_msg(result)))



class FavoriteHandler(AbstractRequestHandler):
    """"Like this song", "remove this album from my favourites"."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        request = handler_input.request_envelope.request
        return (is_request_type("IntentRequest")(handler_input)
                and request.intent.name in voice_commands.FAVORITE_INTENTS)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        _ = handler_input.attributes_manager.request_attributes["_"]
        intent = handler_input.request_envelope.request.intent
        kind = voice_commands.favorite_kind(intent.slots)
        if kind is None:
            return UnhandledIntentHandler().handle(handler_input)
        on = voice_commands.FAVORITE_INTENTS[intent.name]
        logger.info("Voice command %s: %s", intent.name, kind)
        device_id = context.device_id(handler_input)
        action = {"song": actions.FavoriteSong, "album": actions.FavoriteAlbum, "artist": actions.FavoriteArtist}[kind]
        result = actions.run(device_id, action(on))
        if result.status == "unpaired":
            return replies.speak(handler_input, _(data.DEVICE_NOT_MAPPED_MSG))
        clock.spawn(channel.player_news, device_id, ("favorites",), name="news")
        if not result.ok:
            return replies.speak(handler_input, _(data.MA_COMMAND_FAILED_MSG))
        if result.name is None:
            return replies.speak(handler_input, _(data.NOTHING_TO_FAVORITE_MSG))
        message = data.FAVORITE_ADDED_MSG if on else data.FAVORITE_REMOVED_MSG
        # An HTML page has its hearts from its news; an APL page takes them with the reply.
        commands = () if channel.has_page(device_id) else render.favorite_commands(
            view.favorites(devices.get_player_for_device(device_id)))
        return replies.speak(handler_input, _(message).format(result.name), commands)



class VoicePlayHandler(AbstractRequestHandler):
    """Play artist/album/song/playlist/anything, radio, queue and play next."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        request = handler_input.request_envelope.request
        return (is_request_type("IntentRequest")(handler_input)
                and request.intent.name in voice_commands.INTENTS)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        started = clock.monotonic()
        _ = handler_input.attributes_manager.request_attributes["_"]
        intent = handler_input.request_envelope.request.intent
        wanted = voice_commands.request_of(intent.name, intent.slots)
        logger.info("Voice command %s: heard %r, by %r", intent.name, wanted["heard"], wanted["artist"])
        if not wanted["meant"]:
            # No "play"/"shuffle" or kind word: Alexa forced another request
            # ("set volume to 2") into this one.
            logger.info("Voice command %s: no play or kind word, ignoring it", intent.name)
            return replies.ignore(handler_input)
        if not wanted["heard"]:
            # Asked once: the session stays open for the answer.
            return (handler_input.response_builder.speak(_(data.WHAT_TO_PLAY_MSG))
                    .ask(_(data.WHAT_TO_PLAY_MSG)).response)

        def run(player_id):
            match, score = library.find(wanted["heard"], wanted["kinds"], wanted["artist"])
            if not match:
                logger.info("Voice command: no match for %r (best score %.2f)", wanted["heard"], score)
                return _(data.NOT_FOUND_MSG).format(wanted["heard"])
            logger.info("Voice command: %r -> %s %r by %r (score %.2f, %s)", wanted["heard"], match["kind"],
                        match["name"], match["artist"], score,
                        "library" if match["from_library"] else "providers")
            speech = voice_commands.answer(_, wanted, match)

            def start():
                try:
                    player.play(player_id, match["uri"], wanted["option"], wanted["shuffle"], wanted["radio"])
                except MAFailed as e:
                    raise replies.PlayFailed(_(data.PLAY_FAILED_MSG).format(match["name"])) from e
            if wanted["option"] != "replace":
                start()
                return speech
            return replies.play_and_answer(handler_input, started, start, speech)
        return replies.voice_reply(handler_input, run)


class PlayRandomHandler(AbstractRequestHandler):
    """Shuffle my library / play something random."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_intent_name("PlayRandom")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In PlayRandomHandler")
        started = clock.monotonic()
        _ = handler_input.attributes_manager.request_attributes["_"]

        def run(player_id):
            def start():
                if not player.play_random(player_id):
                    raise replies.PlayFailed(_(data.EMPTY_LIBRARY_MSG))
            return replies.play_and_answer(handler_input, started, start, _(data.RANDOM_MSG))
        return replies.voice_reply(handler_input, run)


class WhatsPlayingHandler(AbstractRequestHandler):
    """What's playing: title and artist from MA."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_intent_name("WhatsPlaying")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("In WhatsPlayingHandler")
        _ = handler_input.attributes_manager.request_attributes["_"]

        def run(player_id):
            playing = library.now_playing(player_id, current_only=True)
            if not playing:
                return _(data.NOTHING_PLAYING_MSG)
            title, artist = playing["title"], playing["artist"]
            return _(data.NOW_PLAYING_MSG).format(_(data.BY_MSG).format(title, artist) if artist else title)
        return replies.voice_reply(handler_input, run)

class MoveMusicHandler(AbstractRequestHandler):
    """Move the music to another MA player: "move the music to the kitchen"."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_intent_name("MoveMusic")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        _ = handler_input.attributes_manager.request_attributes["_"]
        intent = handler_input.request_envelope.request.intent
        heard = voice_commands.slot_text(intent.slots, "player")
        logger.info("Move the music: heard %r", heard)
        if not heard:
            return replies.ignore(handler_input)
        device_id = context.device_id(handler_input)

        def run(player_id):
            players = {name: target for target, name in player.players()}
            name = heard if heard in players else voice_match.closest(heard, list(players))
            if not name:
                logger.info("Move the music: no player like %r", heard)
                return _(data.NO_PLAYER_MSG).format(heard)
            if players[name] == player_id:
                return _(data.ALREADY_THERE_MSG).format(name)
            logger.info("Move the music: %s -> %s (%r)", player_id, players[name], name)
            # Close the page first: MA's pause for this Echo must not go to
            # it as a hand-off, nor be spoken. This Echo stops its stream itself.
            handoff.closed(device_id)
            handoff.expect_silent_pause(device_id)
            player.move(player_id, players[name])
            return audio.stop(handler_input.response_builder, _(data.MOVING_MSG).format(name))
        return replies.voice_reply(handler_input, run)

class SleepTimerHandler(AbstractRequestHandler):
    """Pause MA after a while: "stop in 30 minutes"."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_intent_name("SleepTimer")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        _ = handler_input.attributes_manager.request_attributes["_"]
        slot = (handler_input.request_envelope.request.intent.slots or {}).get("duration")
        seconds = sleep_timer.parse_duration(slot and slot.value)
        logger.info("Sleep timer: heard %r", slot and slot.value)
        if not seconds:
            return replies.speak(handler_input, _(data.SLEEP_HOW_LONG_MSG))
        device_id = context.device_id(handler_input)

        def run(player_id):
            sleep_timer.start(device_id, seconds, lambda device_id: actions.run(device_id, actions.Pause()))
            return _(data.SLEEP_SET_MSG).format(sleep_timer.spoken(seconds))
        return replies.voice_reply(handler_input, run)


class CancelSleepTimerHandler(AbstractRequestHandler):
    """"Cancel the sleep timer"."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return is_intent_name("CancelSleepTimer")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        _ = handler_input.attributes_manager.request_attributes["_"]
        cancelled = sleep_timer.cancel(context.device_id(handler_input))
        return replies.speak(handler_input, _(data.SLEEP_CANCELLED_MSG if cancelled else data.NO_SLEEP_TIMER_MSG))


class ExceptionEncounteredHandler(AbstractRequestHandler):
    """Handler to handle exceptions from responses sent by AudioPlayer
    request.
    """
    def can_handle(self, handler_input):
        # type; (HandlerInput) -> bool
        return is_request_type("System.ExceptionEncountered")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        request = handler_input.request_envelope.request
        cause_id = getattr(request.cause, "request_id", None)
        reply = interceptors.reply_to(cause_id)
        logger.warning(
            "Alexa rejected the reply to %s: %s %s. Reply: %s",
            cause_id, getattr(request.error, "object_type", None),
            getattr(request.error, "message", None),
            json.dumps(_serializer.serialize(reply), default=str) if reply else "not recorded")
        return handler_input.response_builder.response

_APL_EVENTS = ("MetadataRefresh", "Next", "Previous", "Pause", "Play", "Seek", "QueueEnded",
               "FavSong", "FavAlbum", "Shuffle", "Repeat", "PlayAlbum", "Jump")
# The page's button events.
_BUTTON_EVENTS = ("Next", "Previous", "Play", "Pause", "Seek", "FavSong", "FavAlbum", "Shuffle", "Repeat",
                  "PlayAlbum", "Jump")


def _press_value(arguments):
    """The state a favourite or mode button asks for: ["FavSong", press number, 1 to mark or 0 to unmark]."""
    return int(float(arguments[2])) if len(arguments) > 2 else 0


def _seek(arguments):
    position_ms = track_time.video_position_ms(arguments, index=1)
    return actions.Seek(position_ms) if position_ms is not None else None


# A press on the page -> its action, or None if there is nothing to do.
# Pause, Play, Seek: [name, shown track position]; Jump: [name, press number, queue item id];
# Shuffle 1/0; Repeat 0 off, 1 all, 2 one.
_PRESSES = {
    "pause": lambda a: actions.Pause(track_time.video_position_ms(a, index=1), page_paused=True),
    "play": lambda a: actions.Play(track_time.video_position_ms(a, index=1)),
    "seek": _seek,
    "next": lambda a: actions.Next(),
    "previous": lambda a: actions.Previous(),
    "jump": lambda a: actions.Jump(str(a[2])) if len(a) > 2 and str(a[2]) else None,
    "favsong": lambda a: actions.FavoriteSong(bool(_press_value(a))),
    "favalbum": lambda a: actions.FavoriteAlbum(bool(_press_value(a))),
    "shuffle": lambda a: actions.Shuffle(bool(_press_value(a))),
    "repeat": lambda a: actions.Repeat(player.REPEAT_MODES[_press_value(a) % len(player.REPEAT_MODES)]),
}
# Presses that wait for MA's new stream: the page undoes them when none comes.
_STREAM_PRESSES = ("play", "seek", "next", "previous", "jump", "playalbum")
_PAUSE_PAGE = {"type": "ControlMedia", "componentId": "videoPlayer", "command": "pause"}
_UNDO_BUTTON_PRESS = [
    {"type": "ControlMedia", "componentId": "videoPlayer", "command": "play"},
    {"type": "SetValue", "componentId": "Busy_Overlay", "property": "opacity", "value": 0},
    {"type": "SetValue", "componentId": "Busy_Overlay", "property": "display", "value": "none"},
]


_SKILL_NAME = "Music Assistant"

def _apl_event_arguments(handler_input):
    try:
        return list(getattr(handler_input.request_envelope.request, 'arguments', None) or [])
    except Exception:
        return []


# Where a page's events carry its id (the skill gives every page one, see page/apl/render.py).
_PAGE_ID_AT = {"MetadataRefresh": 3}


def _page_key(arguments):
    """The id of the page an event comes from, or "" if it carries none."""
    index = _PAGE_ID_AT.get(arguments[0]) if arguments else None
    return str(arguments[index]) if index is not None and len(arguments) > index and arguments[index] else ""


def _play_album_for(device_id):
    """A tap on the cover: play its whole album. False if MA won't (nothing to play)."""
    player_id = devices.get_player_for_device(device_id)
    if not player_id:
        return False
    try:
        name = player.play_playing_album(player_id)
    except (MAUnavailable, MAFailed) as e:
        logger.warning("MA didn't play the album: %s", safe_error(e))
        return False
    logger.info("APL cover tap: playing album %s", name)
    return name is not None


class APLUserEventHandler(AbstractRequestHandler):
    """Handler for APL UserEvent requests from the player page.

    - MetadataRefresh: the APL page sends it every couple of
      seconds. It updates title, artist and images, takes MA's hand-offs,
      and on a track change sets the track time (see track_time).
    - Next / Previous / Play / Pause / Seek / Jump: the page's own buttons,
      seek bar and playlist (see _PRESSES).
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
        handoff.heard_from(context.device_id(handler_input))
        arguments = _apl_event_arguments(handler_input)
        if arguments[0] in _BUTTON_EVENTS:
            kind = arguments[0].lower()
            device_id = context.device_id(handler_input)
            watch.quiet(device_id)
            if kind == "playalbum":
                done = _play_album_for(device_id)
            else:
                action = _PRESSES[kind](arguments)
                logger.info("APL button: %s", action)
                done = action is not None and (actions.run(device_id, action).ok or kind not in _STREAM_PRESSES)
            # A press stops the page's running command sequence, and with it a
            # refresh chain: restart it (an HTML page has no chain).
            if kind == "pause":
                self._schedule_refresh(handler_input, [_PAUSE_PAGE])
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
            actions.run(context.device_id(handler_input), actions.QueueEnded())
            return handler_input.response_builder.set_should_end_session(None).response

        handed = handoff.take(context.device_id(handler_input))
        return self._update_page(handler_input, arguments, [handed] if handed else [])

    def _update_page(self, handler_input, arguments, handoffs):
        """The answer to a MetadataRefresh: the latest metadata and track time, the hand-offs, and the next refresh."""
        # One ExecuteCommands directive for the whole answer: each new one
        # cancels the commands still running from the one before (a loading
        # PlayMedia, and the commands after it).
        device_id = context.device_id(handler_input)
        page_id = _page_key(arguments)
        page_key = page_id or device_id
        page = echos.find_page(page_id) if arguments[0] == "MetadataRefresh" and page_id else None
        if page is not None:
            page.reported(track_time.video_position_ms(arguments), None, clock.monotonic())
        commands, media = [], []
        for handed in handoffs:
            more, more_media = self._take_handoff(handler_input, page_key, *handed)
            commands += more
            media += more_media
            if handed[0] == "stream":
                # the video starts the new stream at 0
                arguments = [arguments[0], arguments[1] if len(arguments) > 1 else 0, 0]

        stream = view.stream_of(device_id)
        if not stream:
            logger.warning("No audio sources available for metadata refresh")
        else:
            v = view.of_stream(stream)
            try:
                render.show_metadata(handler_input.response_builder, v, device_id, page_id)
            except Exception:
                logger.exception("Failed to update APL metadata")
            try:
                commands += render.track_time_commands(device_id, page_key, arguments, stream["streamUrl"])
            except Exception:
                logger.exception("Failed to update APL track time")

        # The media command runs next to the refresh timer: whether or not it
        # completes before the stream plays, the refreshes go on.
        refresh = render.refresh_commands()
        if media:
            refresh = [{"type": "Parallel",
                        "commands": media + [{"type": "Sequential", "commands": refresh}]}]
        render.execute(handler_input.response_builder, commands + refresh)

        # Unset, not False: False opens the mic on every refresh and ducks the music;
        # the APL page keeps the session alive by itself.
        return handler_input.response_builder.set_should_end_session(None).response

    @staticmethod
    def _take_handoff(handler_input, page_key, command, value):
        """(APL commands, media commands) for what MA (or the skill) hands this open page."""
        logger.info("Open page takes %s from MA", command)
        if command == "stream":
            page = echos.find_page(page_key)
            if page is not None:
                page.track, page.stream = None, value
            # a new stream: the page won't switch to the next image by itself
            render.images(context.device_id(handler_input), page_key)["next"] = None
            return ([{"type": "SetValue", "componentId": "AudioPlayerRoot",
                      "property": "videoProgressValue", "value": 0}] + _UNDO_BUTTON_PRESS[1:],
                    [{"type": "PlayMedia", "componentId": "videoPlayer", "source": value,
                      "audioTrack": "background"}])
        if command == "pause":
            return [], [{"type": "ControlMedia", "componentId": "videoPlayer", "command": "pause"}]
        # resume: MA sends a new stream from the paused position, handed over the same way
        if not actions.run(context.device_id(handler_input), actions.Play()).ok:
            return [], [{"type": "ControlMedia", "componentId": "videoPlayer", "command": "play"}]
        return [], []

    @staticmethod
    def _schedule_refresh(handler_input, commands=()):
        # An APL page: always schedule the next refresh so polling
        # continues; other commands go in the same directive (a later one
        # would cancel them). An HTML page gets no APL commands.
        if channel.has_page(context.device_id(handler_input)):
            return
        try:
            render.execute(handler_input.response_builder,
                                      list(commands) + render.refresh_commands())
        except Exception:
            logger.exception("Failed to schedule APL refresh")

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
        if actions.run(context.device_id(handler_input), actions.Play()).ok:
            return handler_input.response_builder.response
        url = view.stream_url(context.device_id(handler_input))
        if not url:
            if context.page_open(handler_input):
                return handler_input.response_builder.response
            logger.warning("No stream url available for PlayCommand; notifying user")
            handler_input.response_builder.speak(
                "Sorry, I couldn't reach the stream right now.").set_should_end_session(True)
            return handler_input.response_builder.response

        return audio.play(handler_input, url)


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
        actions.run(context.device_id(handler_input), actions.Next() if "Next" in req_type else actions.Previous())
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
        actions.run(context.device_id(handler_input), actions.Pause())
        # MA's pause reaches an open page as a hand-off.
        if context.page_open(handler_input):
            return handler_input.response_builder.response
        return audio.stop(handler_input.response_builder)

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
        return replies.speak(handler_input, _(data.UNHANDLED_MSG))


def register(sb):
    """Add the handlers and interceptors to the skill builder sb, in the order Alexa's requests try them."""
    for handler in (CheckAudioInterfaceHandler, SkillEventHandler, LaunchRequestOrPlayAudioHandler,
                    HtmlPageRenewHandler, PlayCommandHandler, HelpIntentHandler, ExceptionEncounteredHandler,
                    APLUserEventHandler, UnhandledIntentHandler, NextOrPreviousIntentHandler,
                    NextOrPreviousCommandHandler, PauseIntentHandler, CancelOrStopIntentHandler, PauseCommandHandler,
                    ResumeIntentHandler, StartOverIntentHandler, VoicePlayHandler, FavoriteHandler, PlayRandomHandler,
                    WhatsPlayingHandler, MoveMusicHandler, SleepTimerHandler, CancelSleepTimerHandler,
                    LoopOrShuffleIntentHandler, *audio.HANDLERS):
        sb.add_request_handler(handler())
    sb.add_exception_handler(CatchAllExceptionHandler())
    interceptors.register(sb)
