# -*- coding: utf-8 -*-
"""MA's queue changes, passed on to the open player pages.

"Play next" or a rearranged queue in MA changes the song after the current
one. The page shows it as "Up next" and switches to it by itself at the
song's end, so it must hear of the change. The skill listens to MA's
events (see ma_client.py) and has the page fetch its next song again,
as after a shuffle.
"""

import logging
import threading

from music_assistant_models.enums import EventType

from . import bell, device_mapping, ma_client

logger = logging.getLogger(__name__)

# A rearranged queue sends a burst of events: the page fetches once they settle.
SETTLE_S = 1.0

_lock = threading.Lock()
_pending = {}     # queue_id -> its Timer


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


def start():
    """Pass MA's queue changes on to the pages. Before ma_client.start()."""
    ma_client.subscribe(lambda client, event: queue_changed(event.object_id, _players_of(client, event.object_id)),
                        EventType.QUEUE_ITEMS_UPDATED)
