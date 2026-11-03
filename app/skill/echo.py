# -*- coding: utf-8 -*-
"""One record per Echo (its Alexa device id): its MA player and its open page.

A new page for an Echo replaces its old one. The open pages are kept in
open_pages.json in the data directory, so a restart of the skill keeps
them open. The pairing stays in the device mapping file.

Each part of the skill keeps what it needs about a page on the page itself.
The registry never calls MA and has no timers.
"""

import json
import logging
import os
import secrets
import threading

from . import config, devices

logger = logging.getLogger(__name__)

KINDS = ("html", "apl")

_lock = threading.Lock()
_echos = {}    # device_id -> Echo
_pages = {}    # page id -> Echo, for the open pages


class Page:
    """An open page of the Echo device_id. kind: "html" or "apl"."""

    def __init__(self, device_id, kind, page_id):
        self.device_id, self.kind, self.page_id = device_id, kind, page_id
        self.start = None       # html: what it shows first, until it loads
        self.streams = []       # html: a message queue per open SSE stream
        self.waiting = []       # html: messages sent before its SSE stream opened
        self.stream = None      # the stream its video plays
        self.track = None       # html and apl: MA's track it was sent last (its fields); None: none yet
        self.buttons = {}       # html: its favourite, shuffle and repeat buttons as sent last
        self.version = None     # html: the number of its latest hand-off; None: not known since a restart
        self.deaf = False       # html: it missed a hand-off and takes none until its SSE stream reopens (see handoff; its _lock)
        self.lock = threading.RLock()   # html: one update at a time; never held while asking MA
        self.news_lock = threading.Lock()   # html: one read of its news from MA at a time
        self.deliver_lock = threading.Lock()   # html: MA's hand-offs taken and sent one at a time
        self.images = {}        # the images it shows: {"cover", "background", "next"}
        self.handed = []        # html: MA's hand-offs sent to it, not yet confirmed (handoff.py)
        self.check = None       # html: its check with MA (watch.py)
        self.viz = None         # html: the thread relaying MA's visualiser to it (visualizer.py); None: off
        self.report = None      # its last report: (video position ms, playing or None, monotonic time)

    def reported(self, at, playing, now):
        """Its report at monotonic time now; at or playing None keeps the last one known."""
        last_at, last_playing, _ = self.report or (None, None, None)
        self.report = (last_at if at is None else at, last_playing if playing is None else playing, now)


class Echo:
    def __init__(self, device_id):
        self.device_id = device_id
        self.page = None
        self.handoffs = None    # MA's hand-offs waiting for its page (handoff.py)
        self.positions = None   # where it paused (actions.py)
        self.sent = {}          # pause or stop sent to MA -> until when MA's echo of it is dropped (actions.py)
        self.watch = None       # the checks of its pages (watch.py)

    @property
    def player_id(self):
        """Its MA player, or None."""
        return devices.get_player_for_device(self.device_id)

    def pair(self, player_id):
        """Pair it with player_id (None: unpair). False if the mapping could not be saved."""
        return devices.set_player_for_device(self.device_id, player_id)


def get(device_id):
    """The Echo's record, made on first use."""
    with _lock:
        return _get(device_id)


def find(device_id):
    """The Echo's record, or None if it has none yet."""
    with _lock:
        return _echos.get(device_id)


def _get(device_id):
    echo = _echos.get(device_id)
    if echo is None:
        echo = _echos[device_id] = Echo(device_id)
    return echo


def every():
    """Every Echo with a record."""
    with _lock:
        return list(_echos.values())


def by_page(page_id):
    """The Echo whose open page page_id is, or None."""
    with _lock:
        return _pages.get(page_id)


def find_page(page_id):
    """The open page page_id, or None."""
    with _lock:
        echo = _pages.get(page_id)
        return echo.page if echo else None


def for_player(player_id):
    """The Echos paired with player_id."""
    return [get(device_id) for device_id in devices.get_devices_for_player(player_id)]


def open_pages(kind):
    """Every open page of that kind."""
    with _lock:
        return [echo.page for echo in _pages.values() if echo.page.kind == kind]


def open_page(device_id, kind):
    """A new page for device_id, which replaces its old one."""
    page = Page(device_id, kind, secrets.token_hex(8))
    if kind == "html":
        page.version = 0
    set_page(device_id, page)
    return page


def set_page(device_id, page):
    """page is device_id's open page, in place of its old one; None: it has none."""
    with _lock:
        echo = _get(device_id)
        if echo.page is not None:
            _pages.pop(echo.page.page_id, None)
        echo.page = page
        if page is not None:
            _pages[page.page_id] = echo
        _save()


def adopt_page(device_id, page_id, kind):
    """device_id's open page page_id, or None. An Echo without an open page takes page_id as its open page."""
    with _lock:
        echo = _get(device_id)
        if echo.page is None and page_id not in _pages:
            echo.page = Page(device_id, kind, page_id)
            _pages[page_id] = echo
            _save()
        return echo.page if echo.page is not None and echo.page.page_id == page_id else None


def close_page(device_id, page_id):
    """Close device_id's page page_id, if it is still its open page."""
    with _lock:
        echo = _echos.get(device_id)
        if echo is None or echo.page is None or echo.page.page_id != page_id:
            return
        echo.page = None
        _pages.pop(page_id, None)
        _save()


def _save():
    """Keep the open pages for the next start. Hold _lock."""
    path = config.data_path("open_pages.json")
    kept = {echo.device_id: {"page": echo.page.page_id, "kind": echo.page.kind} for echo in _pages.values()}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(kept, f, indent=2, sort_keys=True)
        os.replace(path + ".tmp", path)
    except Exception:
        logger.exception("Failed to keep the open pages in %s", path)


def load():
    """Forget every record, then bring back the open pages kept by the last start."""
    path = config.data_path("open_pages.json")
    try:
        with open(path, encoding="utf-8") as f:
            kept = json.load(f)
    except FileNotFoundError:
        kept = {}
    except Exception:
        logger.exception("Failed to read the open pages from %s", path)
        kept = {}
    with _lock:
        _echos.clear()
        _pages.clear()
        for device_id, page in (kept.items() if isinstance(kept, dict) else ()):
            if isinstance(page, dict) and page.get("kind") in KINDS and isinstance(page.get("page"), str):
                echo = _get(device_id)
                echo.page = Page(device_id, page["kind"], page["page"])
                _pages[echo.page.page_id] = echo
