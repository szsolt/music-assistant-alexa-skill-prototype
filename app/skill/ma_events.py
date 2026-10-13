# -*- coding: utf-8 -*-
"""MA's queue changes, passed on to the open player pages.

"Play next" or a rearranged queue in MA changes the song after the current
one. The page shows it as "Up next" and switches to it by itself at the
song's end, so it must hear of the change. The skill listens to MA's
events and has the page fetch its next song again, as after a shuffle.
"""

import asyncio
import logging
import threading
import time

import aiohttp
from music_assistant_client import MusicAssistantClient
from music_assistant_models.enums import EventType

from env_secrets import get_env_secret
from . import bell, device_mapping

logger = logging.getLogger(__name__)

# A rearranged queue sends a burst of events: the page fetches once they settle.
SETTLE_S = 1.0
# After a lost connection: wait this long, doubling up to the second value.
RETRY_S = (5, 120)

_lock = threading.Lock()
_pending = {}     # queue_id -> its Timer
_started = False


def queue_changed(queue_id, players=()):
    """The items of MA's queue_id changed: its pages fetch their next song, once it settles.

    An Echo's queue id is its MA player id, unless it plays another
    player's queue (a sync group): players are the ids that play it.
    """
    if not queue_id:
        return
    player_ids = {queue_id, *players}

    def send():
        with _lock:
            if _pending.get(queue_id) is not timer:
                return
            del _pending[queue_id]
        for device_id in {d for p in player_ids for d in device_mapping.get_devices_for_player(p)}:
            if bell.has_page(device_id):
                logger.info("MA's queue changed on %s: the page fetches its next song", queue_id)
                bell.send(device_id, ("modes", None))

    timer = threading.Timer(SETTLE_S, send)
    timer.daemon = True
    with _lock:
        old = _pending.get(queue_id)
        _pending[queue_id] = timer
    if old:
        old.cancel()
    timer.start()


def _players_of(client, queue_id):
    """The players that play queue_id although it isn't theirs: synced to it, or in its group."""
    return [player.player_id for player in client.players.players
            if queue_id in (player.synced_to, player.active_group, player.active_source)]


async def _listen(server_url, token):
    # Without a connect timeout, a host that drops packets holds the listener for minutes.
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(sock_connect=10)) as session:
        async with MusicAssistantClient(server_url, session, token=token) as client:
            client.subscribe(lambda event: queue_changed(event.object_id, _players_of(client, event.object_id)),
                             EventType.QUEUE_ITEMS_UPDATED)
            logger.info("Listening to MA's queue changes")
            await client.start_listening()


def _loop():
    wait = RETRY_S[0]
    while True:
        server_url = get_env_secret("MA_API_URL")
        if not server_url:
            logger.info("MA_API_URL is not set: not listening to MA's queue changes")
            return
        since = time.monotonic()
        try:
            asyncio.run(_listen(server_url, get_env_secret("MA_API_TOKEN")))
            logger.warning("MA closed the connection for queue changes")
        except Exception as e:
            logger.warning("Lost MA's queue changes: %s", e)
        if time.monotonic() - since > RETRY_S[1]:
            wait = RETRY_S[0]   # it was up for a while: a fresh failure
        time.sleep(wait)
        wait = min(wait * 2, RETRY_S[1])


def start():
    """Listen in the background. Once: later calls do nothing."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, name="ma-events", daemon=True).start()
