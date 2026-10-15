import logging
import os
from types import SimpleNamespace as NS

import pytest

pytest.importorskip("ask_sdk_core")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")    # the skill makes AWS clients on import

from ask_sdk_core.response_helper import ResponseFactory
from ask_sdk_model.interfaces.audioplayer import StopDirective

from skill import lambda_function as lf


def _input(request):
    return NS(request_envelope=NS(request=request), response_builder=ResponseFactory())


def test_rejected_reply_is_logged_with_amazons_error(caplog):
    """Every reply is logged; when Alexa rejects one, its error is logged with that reply."""
    reply = ResponseFactory().add_directive(StopDirective()).response
    caplog.set_level(logging.INFO, logger=lf.logger.name)
    lf.ResponseLogger().process(_input(NS(request_id="r1", object_type="PlaybackNearlyFinished")), reply)
    assert "Reply to PlaybackNearlyFinished: directives=['AudioPlayer.Stop']" in caplog.text

    rejection = NS(
        object_type="System.ExceptionEncountered",
        error=NS(object_type="INVALID_RESPONSE", message="bad reply"),
        cause=NS(request_id="r1"))
    lf.ExceptionEncounteredHandler().handle(_input(rejection))
    warning = caplog.records[-1]
    assert warning.levelno == logging.WARNING
    assert "r1: INVALID_RESPONSE bad reply" in warning.getMessage()
    assert '"type": "AudioPlayer.Stop"' in warning.getMessage()

    rejection.cause.request_id = "unknown"
    lf.ExceptionEncounteredHandler().handle(_input(rejection))
    assert caplog.records[-1].getMessage().endswith("Reply: not recorded")
