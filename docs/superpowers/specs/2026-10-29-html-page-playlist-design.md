# Playlist on the HTML player page

## Why

The HTML player page talks to the skill over the LAN, and it is fast. It is a
real web page, so it can show more than the APL page could. The user wants to
see MA's queue on the Echo, jump to a song and reorder songs by dragging, as
in the MA app.

## Goal

A full-screen playlist on the HTML player page. It shows MA's queue, jumps to
a tapped song, and moves a dragged song in MA's queue.

## Decisions (the user's)

- **Layout:** a full-screen list over the player (mockup option A). It reads
  better on the Echo Show 5 and 8 than a side panel or a short inline list.
- **The player screen stays as it is, except for one new button:** a
  playlist button (≡) in the control row, beside the other buttons. It
  always opens the list, also on the last song, on radio and after the queue
  ends. Its place in the row is my call: at the right end, after the song heart.
- **The Echo Show 5 shows durations too.**
- **Drag feel:** the dragged row follows the finger, and the rows in between
  slide aside to show where it lands (spike v2). The user found it "very
  smooth, also visually instantly understood".

## Behaviour (played back to the user and accepted)

- The playlist button opens the list. Tapping the "Up next" card opens it too.
- The list opens scrolled to the playing song, with one played song above it.
- Played songs are greyed out (about 40% opacity, as in the mockup) and have
  no ☰ handle, so they read as "already played" at a glance. Tapping one
  still jumps back to it. The playing song is highlighted and shows its
  progress (`1:12 / 3:09`).
- The header shows the position and the time left: `3 of 42 · 2 h 31 min left`.
- Each row shows the cover, title, `artist · album`, duration and a ☰ handle.
- Tapping a row makes MA play that song. The list closes.
- Dragging the ☰ handle moves the song in MA's queue. The list stays open.
- ✕ closes the list. It also closes by itself after ~30 s without a touch.
- The list follows MA's queue live: track changes, and edits from the MA app.

## What the drag spike showed (Office, Echo Show 8, 2026-10-29)

- The Echo sends pointer events (`pointerType` touch), plus touchstart and
  mousedown. A drag gives 22–134 pointermoves. No pointercancel was seen, and
  every drop ended in pointerup.
- `touch-action: none` on the handle and `setPointerCapture` on the row work.
- Not tried: auto-scroll while dragging past the top or bottom edge, and long
  lists.

## How it works

### MA's queue is the only truth (my call)

The page keeps no order of its own. It draws what the skill sends. After a
drop the page shows the new order at once. MA's answer then replaces it. If
MA refuses the move, the list goes back to MA's order.

### Queue data (skill → page)

- The skill reads the queue with `player_queues.get_queue_items(queue_id,
  limit, offset)` and the current index from the player queue.
- Per row: `queue_item_id`, title, artist, album, duration, a small cover URL.
- It goes out over the page's SSE stream as a new message kind, separate from
  `set`. It is sent when the list opens and when MA reports
  `QUEUE_ITEMS_UPDATED` or a track change while the list is open.
- **Long queues (my call):** MA queues can hold hundreds of songs. The skill
  sends a window around the playing song (e.g. 10 back, 100 ahead). The list
  asks for more when it is scrolled near either end. The header counts come
  from the queue (`items`, current index), not from the window.
- "Time left" is the sum of the remaining durations plus what is left of the
  playing song. With a window, the skill sums the whole queue (my call), so
  the header stays right.

### Actions (page → skill)

New entries in `html_page._ACTIONS`, sent on the existing
`POST /bell/html/<page>/action`:

- `{"action": "queue", "offset": n}`: send the window starting at `n`. Opening
  the list asks for the window around the playing song.
- `{"action": "jump", "item": queue_item_id}`: play that song. This is the
  same path as the page renew between tracks (`ma_control._resume_at` with
  `item_id`, which calls `play_index`). The Echo plays MA's flow stream, so a
  jump starts a new stream and the page gets a new `src`, as at a renew.
- `{"action": "move", "item": queue_item_id, "shift": k}`: move the song `k`
  places (`player_queues.move_item(queue_id, item_id, pos_shift=k)`, positive
  is down). One drop is one call.

These are queue operations, not APL presses. They may call `ma_control`
directly instead of going through `_invoke` as a fake APL UserEvent (builder's
choice).

### MA limits

- MA's `move_item` fails for the playing song and for a song already loaded
  in the stream buffer. The handle of the playing row is shown but does
  nothing (my call). Any refused move puts the list back to MA's order.
- Moving a song to just after the playing one may be too late if MA already
  picked the next song for the flow stream. Then MA's order wins and the list
  shows it.

### Smaller calls (mine)

- **The playing row** follows the page's own position, which matches the
  player screen. The skill adds the current queue item id to the page state.
- **Played songs:** a drop stops at the row
  below the playing song. The page never sends a shift of 0, because MA reads
  0 as "play next".
- **Jump** uses `lambda_function._resume_for(device_id, item_id=...)`: the
  renew's MA call without a new page.
- **Time left** is left out of the header with repeat on or a radio queue.
- Not checked in MA's server code: whether `move_item` refuses a drop among
  played songs, and how far ahead its buffer runs.

### Up next

A move or a jump can change the next song. The "Up next" card and the page's
own track-change timing (`nextDuration` etc.) must follow, through the
existing update path that runs on queue changes.

### Page swap

The page renews itself between tracks (every ~28 min). The new page starts
with the list closed (my call). Swaps fall between songs, about every 7th
song, so this is rare.

### Echo Show 5

Same list, larger text, fewer rows. Durations stay. The album may drop from
the second line if it does not fit (my call).

## Non-goals

- A playlist on the APL page. APL stays the fallback without it.
- Removing songs, adding songs, clearing the queue, saving as a playlist.
- Search or browse.

## Left to the builder

- The SSE message shape for the queue window.
- How the window is fetched when scrolling (exact sizes).
- Cover image size and source (MA image proxy or the URLs already used for
  "Up next").
- Auto-scroll speed when dragging near the edges.
- Tests: the skill side (queue window, jump, move, refused move, events while
  open). The page itself is checked on a real Echo.
