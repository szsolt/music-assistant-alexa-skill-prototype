# -*- coding: utf-8 -*-
"""Sleep timer: "ask Music Assistant to stop in 30 minutes".

Alexa keeps "stop music in 30 minutes" for its own sleep timer, which
only stops Alexa's music, not the player page. So the skill runs its own:
one timer per Echo, which pauses MA when it runs out. The page follows MA.

Timers live in memory: a restart of the skill drops them.
"""

import logging
import re
import threading

logger = logging.getLogger(__name__)

MAX_SECONDS = 24 * 3600
_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")

_timers = {}    # device_id -> threading.Timer
_lock = threading.Lock()


def parse_duration(value):
    """Seconds in an AMAZON.DURATION value ("PT1H30M"), or None if unusable."""
    match = _DURATION.match(value or "")
    if not match:
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    total = ((days * 24 + hours) * 60 + minutes) * 60 + seconds
    return total if 0 < total <= MAX_SECONDS else None


def spoken(seconds):
    """seconds as words: "1 hour and 30 minutes"."""
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [f"{n} {unit}{'' if n == 1 else 's'}"
             for n, unit in ((hours, "hour"), (minutes, "minute"), (secs, "second")) if n]
    return " and ".join(parts) if len(parts) < 3 else f"{parts[0]}, {parts[1]} and {parts[2]}"


def start(device_id, seconds, on_due):
    """Run on_due(device_id) in seconds, replacing the device's timer."""
    timer = threading.Timer(seconds, _due, (device_id, on_due))
    timer.daemon = True
    with _lock:
        old = _timers.pop(device_id, None)
        _timers[device_id] = timer
    if old:
        old.cancel()
    timer.start()


def cancel(device_id):
    """Cancel the device's timer. False if it had none."""
    with _lock:
        timer = _timers.pop(device_id, None)
    if timer:
        timer.cancel()
    return timer is not None


def _due(device_id, on_due):
    with _lock:
        if _timers.get(device_id) is not threading.current_thread():
            return
        del _timers[device_id]
    logger.info("Sleep timer on %s ran out: pausing MA", device_id[-8:])
    try:
        on_due(device_id)
    except Exception:
        logger.exception("Sleep timer on %s: could not pause MA", device_id[-8:])
