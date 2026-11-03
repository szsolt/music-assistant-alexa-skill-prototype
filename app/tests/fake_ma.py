"""Music Assistant as the skill's client sees it: one player and its queue, a small catalog, and the calls it got.

attach() puts it behind skill.ma.client, on an event loop of its own, as the real connection runs.
"""

import asyncio
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

from music_assistant_models.enums import EventType, MediaType, PlaybackState, QueueOption, RepeatMode

from skill import clock
from skill.ma import client as ma_client

PLAYER = "Den"
QUEUE = "den"

TITLES = ["Kicsi, gyere velem", "A bolond levele", "Nem hallod, üvöltök", "Altató", "Mi van a zsebedben?",
          "Esti dal", "Hét szem mazsola", "Ringató", "Dal a hóról", "Hajnali ének", "Altató",
          "Esti dal", "Kicsi, gyere velem", "A bolond levele", "Ringató"]


def item(n):
    """Queue item q<n>: song n of TITLES."""
    media = SimpleNamespace(media_type=MediaType.TRACK, item_id=str(n + 1), provider="library",
                            name=TITLES[n], version=None, artist_str="Kaláka", uri=f"library://track/{n + 1}",
                            album=SimpleNamespace(name="Az én szívem", item_id="7", provider="library",
                                                  media_type=MediaType.ALBUM, uri="library://album/7"),
                            artists=[SimpleNamespace(name="Kaláka", uri="library://artist/3")],
                            duration=180 + n, to_dict=lambda: {"uri": f"library://track/{n + 1}"})
    details = SimpleNamespace(item_id=f"/music/Kaláka/{n + 1}.flac", provider="filesystem--abc")
    return SimpleNamespace(queue_item_id=f"q{n}", name=TITLES[n], media_item=media, media_type=MediaType.TRACK, duration=180 + n,
                           streamdetails=details)


def _media(kind, n, name, artist="Kaláka", duration=0, disc=1, favorite=False):
    return SimpleNamespace(media_type=MediaType(kind), uri=f"library://{kind}/{n}", item_id=str(n), provider="library",
                           name=name, version=None, artist_str=artist, duration=duration, disc_number=disc,
                           favorite=favorite)


ARTIST = _media("artist", 3, "Kaláka", artist="")
ALBUMS = [_media("album", 7, "Az én szívem", favorite=True), _media("album", 8, "Az én szívem (deluxe)")]
PLAYLIST = _media("playlist", 2, "Esti dal", artist="")
SPOTIFY_ARTIST = SimpleNamespace(ARTIST.__dict__ | {"uri": "spotify://artist/kalaka", "item_id": "kalaka",
                                                    "provider": "spotify"})
SONGS = [_media("track", 12, "Esti dal", duration=180), _media("track", 13, "Kicsi, gyere velem", duration=181)]
SINGLE = SimpleNamespace(_media("album", 9, "Az én szívem").__dict__ | {"album_type": "single"})
SPOTIFY_ALBUM = SimpleNamespace(ALBUMS[0].__dict__ | {"uri": "spotify://album/az-en-szivem", "item_id": "az-en-szivem",
                                                      "provider": "spotify"})
for _album in ALBUMS + [SINGLE]:
    _album.artists, _album.year = [ARTIST], 2004
SPOTIFY_ALBUM.artists, SPOTIFY_ALBUM.year = [SPOTIFY_ARTIST], 2004
for _song in SONGS:
    _song.artists, _song.album = [ARTIST], ALBUMS[0]
PODCAST = SimpleNamespace(_media("podcast", 5, "Esti mese", artist="").__dict__ | {"publisher": "Kossuth Rádió"})
EPISODES = [SimpleNamespace(_media("podcast_episode", 50 + n, f"Esti mese {n}", artist="", duration=900 + n).__dict__ | {
    "position": n, "fully_played": n == 1, "resume_position_ms": 60000 if n == 2 else None,
    "metadata": SimpleNamespace(release_date=datetime(2026, 10, 26 + n, tzinfo=timezone.utc))}) for n in (2, 1, 3)]
AUDIOBOOK = SimpleNamespace(_media("audiobook", 6, "A Pál utcai fiúk", artist="").__dict__ | {
    "publisher": "Móra", "authors": [SimpleNamespace(name="Molnár Ferenc")]})
DELUXE = SONGS + [_media("track", 14, "Esti dal", duration=182, disc=2)]


class _Queue(SimpleNamespace):
    """MA's queue. Its elapsed time is MA's at its last update."""

    @property
    def corrected_elapsed_time(self):
        return self.elapsed_time

    @corrected_elapsed_time.setter
    def corrected_elapsed_time(self, seconds):
        self.elapsed_time, self.elapsed_time_last_updated = float(seconds), clock.time()


def close(loop):
    """Close a stopped loop once the calls still on it are cancelled."""
    tasks = asyncio.all_tasks(loop)
    for task in tasks:
        task.cancel()
    if tasks:
        loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
    loop.close()


class FakeMA:
    """MA playing song 13 of 15 on player Den, its queue den. calls lists what the skill asked, in order."""

    def __init__(self, push=None):
        self.calls = []
        calls = self.calls
        self.push = push      # push(body): MA's POST to the skill's /push-url, returns its JSON answer
        self.pushed = []
        self.replies = []     # the skill's answers to the pushes
        self.items = [item(n) for n in range(len(TITLES))]
        self.queue = q = _Queue(
            queue_id=QUEUE, items=len(TITLES), current_index=12, index_in_buffer=13, repeat_mode=RepeatMode.OFF,
            shuffle_enabled=False, is_dynamic=False, autoplay_enabled=False, crossfade_enabled=True, flow_mode=True,
            state=PlaybackState.PLAYING, current_item=self.items[12], next_item=self.items[13])
        q.corrected_elapsed_time = 61.4
        # MA's stream started just now, 61.4 s into the track.
        self.player = SimpleNamespace(player_id=PLAYER, name=PLAYER, synced_to=None, active_group=None,
                                      active_source=QUEUE, available=True, enabled=True, hide_in_ui=False,
                                      playback_state=PlaybackState.PLAYING, elapsed_time=0.0,
                                      elapsed_time_last_updated=clock.time(), sleep_timer_expires_at=None)
        self.starts = None    # (index, seconds): where MA's next stream starts
        items = self.items
        favorites = set()

        async def get_active_queue(player_id):
            return q if player_id == PLAYER else None

        async def get_queue_items(queue_id, limit=500, offset=0):
            calls.append(("items", offset, limit))
            return items[offset:offset + min(limit, 10)]

        async def move_item(queue_id, queue_item_id, pos_shift=1):
            calls.append(("move", queue_id, queue_item_id, pos_shift))

        async def delete_item(queue_id, item_id_or_index):
            calls.append(("delete", queue_id, item_id_or_index))

        async def transfer(source_queue_id, target_queue_id, auto_play=True):
            calls.append(("transfer", source_queue_id, target_queue_id))

        async def stream():
            """MA's Alexa provider sends the Echo a new stream, off its loop: the skill may call MA meanwhile.

            MA sets its queue's place first, then its player's once the push is answered; their events come a
            moment later. MA sends no time update when the place moved less than 2 s.
            """
            index, seconds = self.starts or (q.current_index, q.elapsed_time)
            self.starts = None
            was = (q.current_index, q.elapsed_time + clock.time() - q.elapsed_time_last_updated)
            self.plays(index, seconds)
            q.elapsed_time_last_updated -= 0.2
            if self.push:
                body = {"streamUrl": f"http://ma/flow/{len(self.pushed) + 1}.mp3", "playerId": PLAYER,
                        "canSkipSpeech": True, "title": items[index].name, "artist": "Kaláka",
                        "album": "Az én szívem", "imageUrl": "http://ma/cover.jpg"}
                self.pushed.append(body)
                self.replies.append(await asyncio.get_running_loop().run_in_executor(None, self.push, body))
            self.player.elapsed_time, self.player.elapsed_time_last_updated = 0.0, clock.time()
            self.player.playback_state = PlaybackState.PLAYING
            self.later(EventType.PLAYER_UPDATED, PLAYER)
            self.later(EventType.QUEUE_UPDATED, QUEUE)
            if was[0] != index or abs(was[1] - seconds) > 2:
                clock.after(0.5, self.fetched, seconds)

        async def play_media(queue_id, media, option=None, start_item=None):
            calls.append(("play_media", queue_id, media, option) + ((start_item,) if start_item else ()))
            if option in (None, QueueOption.REPLACE, QueueOption.PLAY):
                await stream()

        async def play_index(queue_id, index):
            calls.append(("play_index", queue_id, index))
            self.starts = (int(index[1:]) if isinstance(index, str) else index, 0.0)
            await stream()

        async def shuffle(queue_id, shuffle_enabled):
            calls.append(("shuffle", queue_id, shuffle_enabled))
            q.shuffle_enabled = shuffle_enabled

        async def repeat(queue_id, repeat_mode):
            calls.append(("repeat", queue_id, repeat_mode))
            q.repeat_mode = repeat_mode

        async def seek(queue_id, position):
            calls.append(("seek", queue_id, position))
            self.starts = (q.current_index, float(position))
            await stream()

        def player_command(name, state=None):
            async def command(player_id):
                calls.append((name, player_id))
                if state:
                    q.state = self.player.playback_state = state
                    self.later(EventType.QUEUE_UPDATED, QUEUE)
            return command

        answers = {"audio_analysis/wave_form": [n / 1800 for n in range(1800)],
                   "metadata/get_track_lyrics": ["Esti dal", "[00:01.50] Esti dal"],
                   "players/sleep_timer/get": 1000.0,
                   "music/artists/similar_artists": [{"item_id": "4", "provider": "library", "name": "Kolinda",
                                                      "provider_mappings": []}]}

        async def send_command(command, **args):
            calls.append((command, args))
            return answers.get(command)

        async def get_album_tracks(item_id, provider):
            calls.append(("album_tracks", item_id, provider))
            if item_id == "8":
                return DELUXE
            return [SimpleNamespace(uri=f"library://track/{n + 1}", name=TITLES[n], version=None, track_number=n + 1,
                                    disc_number=1, duration=180 + n, media_type=MediaType.TRACK, favorite=n == 12)
                    for n in range(11, 14)]

        async def search(text, media_types, limit=50):
            calls.append(("search", text, tuple(media_types), limit))
            return SimpleNamespace(artists=[ARTIST], albums=ALBUMS, playlists=[PLAYLIST], tracks=SONGS,
                                   podcasts=[PODCAST], audiobooks=[AUDIOBOOK])

        async def get_item_by_uri(uri):
            return {i.uri: i for i in [ARTIST, SPOTIFY_ARTIST, PLAYLIST, PODCAST, SINGLE, SPOTIFY_ALBUM] + ALBUMS}[uri]

        async def get_library_item_by_prov_id(media_type, item_id, provider):
            return {("kalaka", "spotify"): ARTIST, ("az-en-szivem", "spotify"): ALBUMS[0]}.get((item_id, provider))

        async def get_item(media_type, item_id, provider):
            if media_type == MediaType.TRACK:
                return SimpleNamespace(item_id=item_id, favorite=f"library://track/{item_id}" in favorites)
            return {(i.media_type, i.item_id): i for i in [ARTIST, PLAYLIST, PODCAST, SINGLE] + ALBUMS}[(media_type, item_id)]

        async def get_podcast_episodes(item_id, provider):
            calls.append(("episodes", item_id, provider))
            return EPISODES

        async def add_item_to_favorites(uri):
            calls.append(("favorite", uri))
            favorites.add(uri)

        async def remove_item_from_favorites(media_type, item_id):
            calls.append(("unfavorite", media_type, item_id))

        async def create_playlist(name):
            calls.append(("create_playlist", name))
            return SimpleNamespace(item_id="21", name=name)

        async def add_playlist_tracks(db_playlist_id, uris):
            calls.append(("add_tracks", db_playlist_id, uris))

        async def listing(name, *args, **kwargs):
            calls.append((name,) + args + tuple(sorted(kwargs.items())))
            return {"artist_albums": ALBUMS, "library_albums": ALBUMS, "library_playlists": [],
                    "library_artists": [ARTIST], "library_podcasts": [PODCAST], "library_audiobooks": []}.get(name, SONGS)

        self.send_command = send_command
        self.music = SimpleNamespace(
            get_album_tracks=get_album_tracks, search=search, get_item_by_uri=get_item_by_uri, get_item=get_item,
            get_library_item_by_prov_id=get_library_item_by_prov_id, add_item_to_favorites=add_item_to_favorites,
            remove_item_from_favorites=remove_item_from_favorites, get_podcast_episodes=get_podcast_episodes,
            create_playlist=create_playlist, add_playlist_tracks=add_playlist_tracks,
            **{f"get_{name}": (lambda name: lambda *a, **k: listing(name, *a, **k))(name)
               for name in ("artist_tracks", "artist_albums", "playlist_tracks", "library_albums",
                            "library_playlists", "library_artists", "library_tracks", "library_podcasts",
                            "library_audiobooks")},
            recently_played=lambda **k: listing("recently_played", **k))
        self.music.get_media_item_image = lambda media, kind: SimpleNamespace(
            proxy_id=getattr(media, 'queue_item_id', media.uri), path="https://navidrome/img", remotely_accessible=True)
        self.player_queues = SimpleNamespace(
            get=lambda queue_id: q if queue_id == QUEUE else None, get_active_queue=get_active_queue,
            get_queue_items=get_queue_items, move_item=move_item, delete_item=delete_item, play_media=play_media,
            play_index=play_index, seek=seek, shuffle=shuffle, repeat=repeat, transfer=transfer)
        self.players = SimpleNamespace(
            get=lambda player_id: self.player if player_id == PLAYER else None, players=[self.player],
            pause=player_command("pause", PlaybackState.PAUSED), play=player_command("play", PlaybackState.PLAYING),
            stop=player_command("stop", PlaybackState.IDLE), next_track=player_command("next"))
        self.server_info = SimpleNamespace(base_url="http://ma")

    def pause(self):
        """MA paused by hand, in its own app."""
        self.queue.state = self.player.playback_state = PlaybackState.PAUSED
        self.later(EventType.QUEUE_UPDATED, QUEUE)

    def plays(self, index, seconds=0.0):
        """MA's queue plays its item index from seconds on, as MA says once its player started it."""
        q = self.queue
        q.current_index, q.current_item = index, self.items[index]
        q.next_item = self.items[index + 1] if index + 1 < len(self.items) else None
        q.index_in_buffer = index + 1
        q.state = PlaybackState.PLAYING
        q.corrected_elapsed_time = seconds

    def fetched(self, seconds):
        """The Echo fetched MA's new stream, which starts seconds into its track: MA's next time update says where it is."""
        self.queue.corrected_elapsed_time = seconds + clock.time() - self.player.elapsed_time_last_updated
        self.fire(EventType.QUEUE_TIME_UPDATED, QUEUE)

    def flows_on(self):
        """MA's flow stream reaches the queue's next track: MA pushes its names without a player, and its queue follows."""
        self.plays(self.queue.current_index + 1, 0.5)
        if self.push:
            body = {"streamUrl": self.pushed[-1]["streamUrl"], "title": self.queue.current_item.name,
                    "artist": "Kaláka", "album": "Az én szívem", "imageUrl": "http://ma/cover.jpg"}
            self.pushed.append(body)
            self.replies.append(self.push(body))
        self.later(EventType.QUEUE_UPDATED, QUEUE)

    def later(self, event_type, object_id):
        """MA's event, a moment later: on the test's clock, as the client's copy gets it."""
        clock.after(0.1, self.fire, event_type, object_id)

    def fire(self, event_type, object_id):
        """MA's event_type for object_id, to the skill's subscriptions, after MA's copy changed."""
        event = SimpleNamespace(event=event_type, object_id=object_id, data=None)
        for callback, kind in list(ma_client._subscriptions):
            if kind == event_type:
                callback(self, event)

    def attach(self):
        """Run the MA client's calls on this MA, from now until detach()."""
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        ma_client.attach(self, self._loop)

    def detach(self):
        ma_client.attach(None, None)
        ma_client._host = None   # the kept host would leak into the next test
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join()
        close(self._loop)
