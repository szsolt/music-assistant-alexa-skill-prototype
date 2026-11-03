# -*- coding: utf-8 -*-
"""MA's events, passed on to the open player pages, as MA's own app follows them.

MA tells its clients when a player or a queue changed: its track, its
place, its shuffle and repeat, its items. The client's copy is up to date
by then, and each page of the players concerned gets what it shows from it.
"""

import logging

from music_assistant_models.enums import EventType

from .. import clock, devices
from ..page.html import channel
from . import client as ma_client

logger = logging.getLogger(__name__)

EVENTS = (EventType.PLAYER_UPDATED, EventType.QUEUE_UPDATED, EventType.QUEUE_TIME_UPDATED)


def changed(client, event):
    """MA's player or queue event.object_id changed: its pages sync, off the client's loop."""
    player_ids = {event.object_id, *_players_of(client, event.object_id)}
    device_ids = {d for p in player_ids if p for d in devices.get_devices_for_player(p)}
    if any(channel.has_page(d) for d in device_ids):
        _log(client, event, player_ids)
        clock.spawn(channel.news, device_ids, name="ma-event")


def _log(client, event, player_ids):
    """MA's event and the client's copy of each player and queue it touched; ages in s before now."""
    now = clock.time()
    age = lambda t: None if t is None else round(now - t, 2)  # noqa: E731
    for player_id in player_ids:
        player, queue = client.players.get(player_id), ma_client.active_queue(player_id)
        if player is None or queue is None:
            continue
        logger.info("MA %s %s: player %s elapsed=%s (%s s ago) | queue %s item=%s elapsed=%s (%s s ago)",
                    event.event, event.object_id, player.playback_state, player.elapsed_time,
                    age(player.elapsed_time_last_updated), queue.state,
                    queue.current_item and queue.current_item.queue_item_id[:8], queue.elapsed_time,
                    age(queue.elapsed_time_last_updated))


def _players_of(client, queue_id):
    """The players that play queue_id although it isn't theirs: synced to it, or in its group."""
    return [player.player_id for player in client.players.players
            if queue_id in (player.synced_to, player.active_group, player.active_source)]


def start():
    """Pass MA's events on to the pages. Before client.start()."""
    for event_type in EVENTS:
        ma_client.subscribe(changed, event_type)
