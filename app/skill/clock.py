# -*- coding: utf-8 -*-
"""The skill's time and timers, in one place so tests can swap in a clock they move by hand."""

import threading
import time as _time


class Clock:
    """The real time: timers and background work run on daemon threads."""

    def monotonic(self):
        return _time.monotonic()

    def time(self):
        return _time.time()

    def sleep(self, seconds):
        _time.sleep(seconds)

    def after(self, seconds, fn, *args):
        """Run fn(*args) in seconds, on its own thread. Returns a timer with cancel()."""
        timer = threading.Timer(seconds, fn, args)
        timer.daemon = True
        timer.start()
        return timer

    def spawn(self, fn, *args, name=None):
        """Run fn(*args) now, on its own thread."""
        threading.Thread(target=fn, args=args, name=name, daemon=True).start()


_clock = Clock()


def use(clock):
    """Make clock the skill's clock. Returns the one it replaces."""
    global _clock
    old, _clock = _clock, clock
    return old


def monotonic():
    return _clock.monotonic()


def time():
    return _clock.time()


def sleep(seconds):
    _clock.sleep(seconds)


def after(seconds, fn, *args):
    return _clock.after(seconds, fn, *args)


def spawn(fn, *args, name=None):
    _clock.spawn(fn, *args, name=name)
