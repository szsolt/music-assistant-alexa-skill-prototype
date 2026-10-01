# -*- coding: utf-8 -*-
"""Doorbell test: can the player page hear the skill over the LAN, without Amazon?

The page reloads a hidden image from the skill about once a second, each time
with a new tick in the URL so nothing caches it. The skill answers with an
image (nothing new) or an error (news). This test only answers errors on a
fixed pattern, to learn how the Show behaves:
- does it really load every new URL (the request log shows each tick)?
- does the image's onFail get the HTTP status as its errorCode?
- can onFail send an event to the skill (the BellFail event)?
"""

import base64
import logging
import threading
import time

logger = logging.getLogger(__name__)

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")

REPORT_EVERY_S = 10

_last_request = None
_last_report = 0.0
_lock = threading.Lock()


def status_for(tick):
    """The HTTP status the bell answers for this tick: mostly OK, two errors in every ten."""
    if tick % 10 == 5:
        return 404
    if tick % 10 == 8:
        return 410
    return 200


def rang(tick, now=None):
    """Log a bell request and return (status, seconds since the one before)."""
    global _last_request
    now = time.monotonic() if now is None else now
    with _lock:
        gap = None if _last_request is None else now - _last_request
        _last_request = now
    status = status_for(tick)
    logger.info("Bell tick %s -> %s (%s s after the last one)", tick, status,
                "?" if gap is None else "%.2f" % gap)
    return status, gap


def page_counts(arguments, now=None):
    """Log the page's own load/fail counts (MetadataRefresh arguments 3 and 4) every REPORT_EVERY_S."""
    global _last_report
    if not arguments or len(arguments) < 5:
        return False
    now = time.monotonic() if now is None else now
    with _lock:
        if now - _last_report < REPORT_EVERY_S:
            return False
        _last_report = now
    logger.info("Bell on the page: %s loads, %s fails", arguments[3], arguments[4])
    return True


def failed(arguments):
    """Log a BellFail event: [BellFail, errorCode, error, tick]."""
    code, error, tick = (list(arguments[1:4]) + [None] * 3)[:3]
    logger.info("Bell fail on the page: tick %s, errorCode %r, error %r (expected %s)",
                tick, code, error, _expected(tick))


def _expected(tick):
    try:
        return status_for(int(float(tick)))
    except (TypeError, ValueError):
        return "?"
