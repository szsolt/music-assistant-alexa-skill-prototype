"""The skill's replies: spoken answers that keep an open player page, and answers that play MA's new stream."""

import logging

from .. import actions, clock, data, handoff, watch
from ..ma.errors import MAFailed, MAUnavailable, safe_error
from ..page.apl import render
from ..page.html import channel
from . import audio, context

logger = logging.getLogger(__name__)


def ignore(handler_input):
    """Says nothing with the player page open, or that it didn't understand without one."""
    _ = handler_input.attributes_manager.request_attributes["_"]
    return speak(handler_input, None, if_closed=_(data.UNHANDLED_MSG))


def failed_msg(result):
    """What to say when MA didn't do an action."""
    if result.status == "unpaired":
        return data.UNPAIRED_ECHO_MSG
    return data.MA_UNAVAILABLE_MSG if result.unreachable else data.MA_COMMAND_FAILED_MSG


# How long a voice command waits for MA's new stream, to answer with it
# in one reply. Alexa drops a reply that takes over ~8 s.
_VOICE_WAIT_S = 5
_VOICE_DEADLINE_S = 6.5


class PlayFailed(Exception):
    """MA refused to play it; carries what to say."""


def voice_reply(handler_input, run):
    """The reply for run(player_id): words to say, or a finished reply.

    MA's errors become their own answers.
    """
    _ = handler_input.attributes_manager.request_attributes["_"]
    player_id = context.player_id(handler_input)
    if not player_id:
        speech = _(data.UNPAIRED_ECHO_MSG)
    else:
        try:
            speech = run(player_id)
        except PlayFailed as e:
            logger.warning("Voice command: MA refused to play it: %s", safe_error(e.__cause__))
            speech = str(e)
        except MAUnavailable as e:
            logger.warning("Voice command: MA unreachable: %s", safe_error(e))
            speech = _(data.MA_UNAVAILABLE_MSG)
        except MAFailed as e:
            logger.warning("Voice command: MA refused it: %s", safe_error(e))
            speech = _(data.MA_COMMAND_FAILED_MSG)
    if not isinstance(speech, str):
        return speech
    return speak(handler_input, speech)


def speak(handler_input, speech, commands=(), if_closed=None):
    """A reply, spoken or silent, that keeps an open player page.

    Ending the session closes the page (or leaves it stuck), so with the
    page open the session stays as it is, as for next and previous, and
    an APL page gets its next refresh. speech None says
    nothing; if_closed is said instead when no page is open.
    """
    builder = handler_input.response_builder
    device_id = context.device_id(handler_input)
    if not context.page_open(handler_input):
        if speech or if_closed:
            builder.speak(speech or if_closed)
        return builder.set_should_end_session(True).response
    if speech:
        builder.speak(speech)
    commands = list(commands)
    if not channel.has_page(device_id):
        commands += render.refresh_commands()
    render.execute(builder, commands)
    return builder.set_should_end_session(None).response


def wait_for_stream(device_id, seconds):
    """MA's new stream for device_id's Echo, handed over within seconds, or None."""
    deadline = clock.monotonic() + seconds
    while clock.monotonic() < deadline:
        taken = handoff.take(device_id, "stream", for_reply=True)
        if taken:
            return taken[1]
        clock.sleep(0.1)
    return None


def play_and_answer(handler_input, started, start, speech):
    """Start new music in MA and answer with its stream, in one reply.

    MA's new stream is claimed for this reply, so an open page doesn't take it.
    If MA is too slow, MA sends it the spoken way and the reply only speaks.
    """
    device_id = context.device_id(handler_input)
    handoff.closed(device_id)
    handoff.heard_from(device_id)
    handoff.claim(device_id)
    try:
        start()
    except Exception:
        handoff.closed(device_id)
        raise
    url = wait_for_stream(device_id, min(_VOICE_WAIT_S, _VOICE_DEADLINE_S - (clock.monotonic() - started)))
    if url is None:
        logger.info("No stream from MA in time, MA starts it the spoken way")
        handoff.closed(device_id, fall_back=True)
        return speak(handler_input, speech)
    response = audio.play(handler_input, url, text=speech)
    if context.page_kind(handler_input) != "none":
        handoff.closed(device_id)
        watch.watch_page(device_id)
    return response



class _NotStarted(Exception):
    """MA didn't take the command (stream_reply)."""


def stream_reply(handler_input, action):
    """Have MA do action, which brings a new stream, and answer with it in one reply.

    As for voice commands: an open page plays the new stream instead of
    being closed and opened again. The actions.Result if MA didn't take
    it, else the response.
    """
    result = []

    def start():
        result.append(actions.run(context.device_id(handler_input), action))
        if not result[-1].ok:
            raise _NotStarted

    try:
        return play_and_answer(handler_input, clock.monotonic(), start, None)
    except _NotStarted:
        return result[-1]
