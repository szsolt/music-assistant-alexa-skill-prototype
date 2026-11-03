# -*- coding: utf-8 -*-
"""MA's visualiser on the HTML page.

MA's MilkDrop visualizer plugin taps a player's audio and sends its waveform
over a WebSocket, each frame stamped with when it plays. While a page shows
the visualiser, a thread reads that socket for the page's player and passes its
frames on over the page's message stream.
"""

import asyncio
import base64
import json
import logging
import threading
import time
from urllib.parse import urlencode, urlsplit, urlunsplit

import aiohttp

from env_secrets import get_env_secret

from ... import echo as echos
from . import channel

logger = logging.getLogger(__name__)

WAVE = 22           # MA's waveform frame: [type][play-at µs, MA's monotonic clock][1024 samples, 0x80 = 0]
BATCH_S = 0.25      # frames go to the page in batches this often
RETRY_S = (2, 60)   # after a lost socket: wait this long, doubling up to the second value


def show(found, on):
    """Start or stop relaying MA's visualiser to the page."""
    with found.lock:
        if not on:
            found.viz = None
        elif found.viz is None or not found.viz.is_alive():
            found.viz = threading.Thread(target=_run, args=(found,), name="visualizer", daemon=True)
            found.viz.start()


def slow(found, presets):
    """Log the MilkDrop presets the page skips as too slow for its Echo."""
    if not isinstance(presets, list) or not all(isinstance(p, str) for p in presets):
        raise ValueError("bad presets")
    logger.info("Page on %s skips slow MilkDrop presets: %s", found.device_id[-8:],
                "; ".join(p[:120] for p in presets[:100]))


def _wanted(found):
    return found.viz is threading.current_thread() and channel.held(found.page_id)


def _run(found):
    wait = RETRY_S[0]
    while _wanted(found):
        try:
            asyncio.run(_relay(found))
        except Exception as e:
            logger.warning("Lost MA's visualiser for page %s: %s", found.page_id, type(e).__name__)
        end = time.monotonic() + wait
        while time.monotonic() < end and _wanted(found):
            time.sleep(0.5)
        wait = min(wait * 2, RETRY_S[1])


def _url(player_id):
    base = urlsplit(get_env_secret("MA_API_URL") or "")
    scheme = "wss" if base.scheme in ("https", "wss") else "ws"
    return urlunsplit((scheme, base.netloc, "/milkdrop_visualizer", urlencode({"player": player_id}), ""))


async def _relay(found):
    player_id = echos.get(found.device_id).player_id
    if not player_id:
        return
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(sock_connect=10)) as session:
        async with session.ws_connect(_url(player_id), heartbeat=30) as ws:
            await ws.send_json({"type": "auth", "token": get_env_secret("MA_API_TOKEN")})
            frames, sent = [], time.monotonic()
            while _wanted(found):
                try:
                    msg = await ws.receive(timeout=BATCH_S)
                except asyncio.TimeoutError:
                    msg = None
                if msg is None:
                    pass
                elif msg.type == aiohttp.WSMsgType.BINARY and msg.data[:1] == bytes([WAVE]):
                    frames.append((int.from_bytes(msg.data[1:9], "big", signed=True) // 1000,
                                   base64.b64encode(msg.data[9:]).decode()))
                elif msg.type == aiohttp.WSMsgType.TEXT:
                    kind = json.loads(msg.data).get("type")
                    if kind == "error":
                        logger.warning("MA has no visualiser for player %s", player_id)
                        return
                    if kind == "stream/clear":
                        frames = []
                        channel.send(found.page_id, {"viz": {"clear": True}})
                elif msg.type != aiohttp.WSMsgType.BINARY:
                    return  # closed
                now = time.monotonic()
                if frames and now - sent >= BATCH_S:
                    # MA and the skill share a host, so its play-at times are on this clock.
                    now_ms = int(now * 1000)
                    channel.send(found.page_id, {"viz": {"frames": [[at - now_ms, wave] for at, wave in frames]}})
                    frames, sent = [], now
            await ws.send_json({"type": "client/goodbye"})
