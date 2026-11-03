# -*- coding: utf-8 -*-
"""The skill's one connection to Music Assistant.

A background thread keeps one client connected and listening. Any thread
runs its MA calls on it with call(). The client matches each answer to its
request, so calls may overlap, and its copy of MA's players and queues
stays up to date.
"""

import asyncio
import concurrent.futures
import logging
import threading
import time

import aiohttp
from music_assistant_client import MusicAssistantClient
from music_assistant_client.exceptions import MusicAssistantClientException

from env_secrets import get_env_secret

logger = logging.getLogger(__name__)
# The client logs every connect.
logging.getLogger("music_assistant_client").setLevel(logging.WARNING)

# After a lost connection: wait this long, doubling up to the second value.
RETRY_S = (5, 120)
# How long a call waits for the connection, e.g. right after the skill starts.
CONNECT_WAIT_S = 5
# A call that takes longer gives up.
CALL_TIMEOUT_S = 120

_lock = threading.Lock()
_started = False
_connected = threading.Event()
_client = None    # the connected client
_loop = None      # the event loop it runs on
_subscriptions = []   # (callback(client, event), event_type), for every connection


class Unavailable(Exception):
    """MA can't be reached, or didn't answer in time."""


def subscribe(callback, event_type):
    """Have callback(client, event) hear MA's event_type. Before start()."""
    _subscriptions.append((callback, event_type))


def active_queue(player_id):
    """MA's active queue on player_id, as MA picks it, from the client's copy. None while not connected."""
    client = _client
    player = client and client.players.get(player_id)
    if not player:
        return None
    for leader in (player.synced_to, player.active_group):
        if leader and leader != player_id:
            return active_queue(leader)
    return client.player_queues.get(player.active_source or player_id)


def call(fn, *args, timeout=CALL_TIMEOUT_S):
    """fn(client, *args)'s result, run on the connection. From any thread but the connection's.

    Raises Unavailable when MA can't be reached or takes longer than timeout;
    MA's own refusals come through as MusicAssistantError.
    """
    if not _connected.wait(CONNECT_WAIT_S):
        raise Unavailable("Not connected to Music Assistant")
    try:
        future = asyncio.run_coroutine_threadsafe(fn(_client, *args), _loop)
    except RuntimeError as e:    # the loop closed just now
        raise Unavailable("Lost the connection to Music Assistant") from e
    try:
        return future.result(timeout)
    except concurrent.futures.TimeoutError as e:
        future.cancel()
        raise Unavailable(f"Music Assistant took longer than {timeout} s") from e
    except concurrent.futures.CancelledError as e:    # the client cancels its open calls when the connection drops
        raise Unavailable("Lost the connection to Music Assistant") from e
    except MusicAssistantClientException as e:
        raise Unavailable(str(e)) from e


async def _connect(server_url, token):
    global _client, _loop
    # Without a connect timeout, a host that drops packets holds the connection for minutes.
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(sock_connect=10)) as session:
        async with MusicAssistantClient(server_url, session, token=token) as client:
            for callback, event_type in _subscriptions:
                client.subscribe(lambda event, callback=callback: callback(client, event), event_type)
            ready = asyncio.Event()
            listening = asyncio.create_task(client.start_listening(ready))
            waiting = asyncio.create_task(ready.wait())
            await asyncio.wait((listening, waiting), return_when=asyncio.FIRST_COMPLETED)
            waiting.cancel()
            if ready.is_set():
                logger.info("Connected to MA")
                _client, _loop = client, asyncio.get_running_loop()
                _connected.set()
            try:
                await listening
            finally:
                _connected.clear()
                _client = None


def _keep_connected():
    wait = RETRY_S[0]
    while True:
        server_url = get_env_secret("MA_API_URL")
        if not server_url:
            logger.error("MA_API_URL is not set: no connection to MA")
            return
        since = time.monotonic()
        try:
            asyncio.run(_connect(server_url, get_env_secret("MA_API_TOKEN")))
            logger.warning("MA closed the connection")
        except Exception as e:
            logger.warning("Lost the connection to MA: %s", e)
        if time.monotonic() - since > RETRY_S[1]:
            wait = RETRY_S[0]   # it was up for a while: a fresh failure
        time.sleep(wait)
        wait = min(wait * 2, RETRY_S[1])


def start():
    """Connect in the background, and reconnect. Once: later calls do nothing."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_keep_connected, name="ma-client", daemon=True).start()
