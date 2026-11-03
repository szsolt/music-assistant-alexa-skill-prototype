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
import traceback
from urllib.parse import urlsplit

import aiohttp
from music_assistant_client import MusicAssistantClient
from music_assistant_client.exceptions import MusicAssistantClientException
from music_assistant_models.errors import MusicAssistantError

from env_secrets import get_env_secret

from .errors import MAFailed, MAUnavailable, safe_error

logger = logging.getLogger(__name__)
# The client logs every connect.
logging.getLogger("music_assistant_client").setLevel(logging.WARNING)

# After a lost connection: wait this long, doubling up to the second value.
RETRY_S = (5, 120)
# How long a call waits for the connection, e.g. right after the skill starts.
CONNECT_WAIT_S = 5
# Alexa waits 8 s for the skill's reply: a call on its path leaves 2 s for the rest.
REQUEST_TIMEOUT_S = 6
# Background work and the page's own queries have no Alexa deadline.
LONG_TIMEOUT_S = 120

_lock = threading.Lock()
_started = False
_connected = threading.Event()
_connection = None    # (client, the event loop it runs on), swapped as one so a call never sees half of a drop
_subscriptions = []   # (callback(client, event), event_type), for every connection
_request = threading.local()    # deadline: the Alexa request this thread answers shares one REQUEST_TIMEOUT_S


def subscribe(callback, event_type):
    """Have callback(client, event) hear MA's event_type. Before start()."""
    _subscriptions.append((callback, event_type))


def connected_client():
    """The connected client, whose copy of MA's players and queues follows MA's events; None while not connected."""
    connection = _connection
    return connection and connection[0]


def active_queue(player_id):
    """MA's active queue on player_id, as MA picks it, from the client's copy. None while not connected."""
    connection = _connection
    client = connection and connection[0]
    player = client and client.players.get(player_id)
    if not player:
        return None
    for leader in (player.synced_to, player.active_group):
        if leader and leader != player_id:
            return active_queue(leader)
    return client.player_queues.get(player.active_source or player_id)


_host = None   # the host of MA's base URL, kept while the connection is lost


def server_host():
    """The host of MA's base URL; while not connected, the last one known (None before any)."""
    global _host
    connection = _connection
    if connection:
        _host = urlsplit(connection[0].server_info.base_url).hostname
    return _host


def begin_request():
    """This thread's calls without a timeout share one REQUEST_TIMEOUT_S from now until end_request()."""
    _request.deadline = time.monotonic() + REQUEST_TIMEOUT_S


def end_request():
    _request.deadline = None


def request_deadline():
    """This thread's Alexa request deadline (time.monotonic()), or None (see begin_request)."""
    return getattr(_request, "deadline", None)


def call(fn, *args, timeout=None):
    """fn(client, *args)'s result, run on the connection. From any thread but the connection's.

    timeout: REQUEST_TIMEOUT_S if None, or what is left of the Alexa request's
    (see begin_request); counting the wait for the connection.
    Raises MAUnavailable when MA can't be reached or takes longer than timeout,
    MAFailed when MA refuses or fn fails. Their text is safe for logs.
    """
    if timeout is None:
        timeout = REQUEST_TIMEOUT_S
        deadline = request_deadline() or time.monotonic() + timeout
    else:
        deadline = time.monotonic() + timeout
    left = deadline - time.monotonic()
    if left <= 0:
        raise MAUnavailable("No time left to ask Music Assistant before Alexa gives up")
    _connected.wait(min(CONNECT_WAIT_S, left))
    connection = _connection
    if connection is None:
        raise MAUnavailable("Not connected to Music Assistant")
    client, loop = connection
    try:
        future = asyncio.run_coroutine_threadsafe(fn(client, *args), loop)
    except RuntimeError as e:    # the loop closed just now
        raise MAUnavailable("Lost the connection to Music Assistant") from e
    try:
        return future.result(max(0, deadline - time.monotonic()))
    except concurrent.futures.TimeoutError as e:
        future.cancel()
        raise MAUnavailable(f"Music Assistant took longer than {timeout} s") from e
    except concurrent.futures.CancelledError as e:    # the client cancels its open calls when the connection drops
        raise MAUnavailable("Lost the connection to Music Assistant") from e
    except MusicAssistantClientException as e:
        raise MAUnavailable(safe_error(e)) from None
    except MusicAssistantError as e:
        raise MAFailed(safe_error(e)) from None
    except Exception as e:
        # The frames without the error's own text, which may hold request URLs.
        logger.debug("%s in an MA call:\n%s", type(e).__name__, "".join(traceback.format_tb(e.__traceback__)))
        raise MAFailed(f"{type(e).__name__}: {safe_error(e)}") from None


def attach(client, loop):
    """Run calls on client, on loop; None: no connection."""
    global _connection
    if client is None:
        _connected.clear()
        _connection = None
        return
    _connection = (client, loop)
    _connected.set()


async def _connect(server_url, token):
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
                attach(client, asyncio.get_running_loop())
            try:
                await listening
            finally:
                attach(None, None)


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
            logger.warning("Lost the connection to MA: %s", safe_error(e))
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
