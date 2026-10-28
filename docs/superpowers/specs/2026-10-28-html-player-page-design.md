# HTML player page

## Why

The APL player page talks to the skill only through Amazon. Every update is a
round trip: the doorbell image says "something changed", the page sends an
event, Amazon calls the skill, the skill answers with commands. Next/previous
feel slow, and the Office Echo stutters and slows down over days. A suspect is
the doorbell's ~250k unique image URLs piling up on the Echo.

`Alexa.Presentation.HTML` (Web API for Games) runs a real web page on the Echo
Show. A spike on 2026-10-28 showed on Office (Echo Show 8, Chrome 118 WebView):

- The page loads from `https://stream.kucko.cc/...` in ~4 s after "open music assistant".
- `<audio>` plays without a tap.
- An `EventSource` straight to the skill gets pushes every second, no Amazon in between.
- `alexa.skill.sendMessage` round trip to the skill: ~2 s.
- `timeoutInSeconds: 300` closes the page at exactly 300 s, even with audio,
  pushes and a 60 s sendMessage ping running.
- `timeoutInSeconds: 1800` is accepted. The page closed at exactly 1800 s,
  again with audio, pushes and the ping running.

## Goal

A second player page, in HTML, that looks and behaves like the APL page, and
talks to the skill only over the local network.

## Decisions (the user's)

- **Look:** replicate the APL page (`app/skill/apl_document.json`): background,
  header with MA logo and clock, cover, title (three sizes), artist, album,
  "Up next" panel, slider with times and quality label, buttons (album heart,
  shuffle, previous, play/pause, next, repeat / repeat-one, song heart),
  busy overlay, indeterminate bar for radio. All viewport profiles the APL
  page handles.
- **Sound:** the page plays the MA stream itself in `<audio>`, as the APL page
  does in its Video component. Music stops when the page closes. That is
  accepted.
- **APL stays in the code, switched off.** The HTML page becomes what the skill
  sends. APL comes back by flipping one setting.
- **No doorbell for the HTML page. Everything on the local network:**
  - skill → page: Server-Sent Events from the skill.
  - page → skill: plain HTTP requests to the skill (button presses, position,
    playing state).
  - No Amazon round trips for updates or buttons.
- **The 30-minute limit is not settled.** The user wants to look at more ways
  to keep the page open before accepting a reload.

## How it works

### Start

Where the skill now adds the APL page (`apl.add_apl`), it adds
`Alexa.Presentation.HTML.Start` instead (SDK class `StartDirective`):

- `request.uri`: the page on the skill, through NPM host `stream.kucko.cc`
  (LAN only). The URL carries the page id.
- `configuration.timeoutInSeconds`: 1800 (until "the timer" is settled).
- `data`: optional; the page can fetch its start state from the skill instead.

A page id is a random token per page, as now (`secrets.token_hex`). It is the
page's only credential: the skill's auth lets `/bell/`-style GETs through
because the Echo has no credentials. The HTML page's routes get the same
treatment, and every request must name a page id the skill issued and still
holds.

### The API

The page is a plain web app. It never sees APL.

- `GET /bell/html/<page>`: the page, with its start state inline.
- `GET /bell/html/<page>/events`: SSE. Each message is JSON:
  `{"set": {field: value}, "src": new stream, "media": "play"|"pause", "ready": true}`.
- `POST /bell/html/<page>/state` `{seen, at, playing}`: every second.
- `POST /bell/html/<page>/action` `{action, ...}`: a button, e.g.
  `{"action": "seek", "position": 61000}`.

The skill's logic still speaks APL. `app/skill/html_page.py` is the one
adapter: reply commands become messages, actions and reports run as APL
UserEvents. Moving the skill's logic to neutral state is left for later.

### Updates (skill → page)

One SSE stream per page. The skill pushes what today goes out as
`ExecuteCommands`/`SetValue` (see `util.update_apl_metadata`): title, artist,
album, title size, cover/background, up next, quality, track offset and
duration, button states, stream hand-offs (`live_page`: new stream, pause,
resume). Full state on connect, changes after that.

Track changes inside the flow stream stay on the page, as now: when the play
position passes the track's end, the page shows "up next" itself.

### Presses and position (page → skill)

The page sends presses, seek position and playing state over HTTP. These
replace the APL `UserEvent`s and the doorbell's `press`/`at`/`playing` parts.
The skill handles them the way `APLUserEventHandler` and `bell.rang` do now.

### Page open / closed

Today a page is "open" while its doorbell or refreshes keep coming, and the
bell sweeper pauses MA when a page goes quiet. For the HTML page the SSE
connection is the signal: open while connected, closed when it drops and does
not come back within the same grace time.

### Voice while the page is open

Intents (pause, next, ...) still reach the skill in the open session. The
reply must not carry `AudioPlayer` or APL directives: per Amazon's docs any
directive from another interface closes the web app. The change goes to the
page over SSE.

## The timer

- Amazon closes the page after 1800 s without a touch or voice.
- A sendMessage ping with a HandleMessage reply does not reset it.
- A text command (MA's fake voice) ends the session first, so it closes the page.
- What works: the page asks the skill for a new page. The skill answers with
  a new Start in the same session. The swap takes about 1 s.
- The page asks 1.5 s before the last track change that comes before 28 min,
  naming the next track. The skill starts MA on that track, so the swap falls
  between songs.
- With no track change in time, the page asks at 28 min. MA's stream resumes
  at the same spot.
- Messages for a new page wait until it connects. The page reports at once,
  so the new stream could arrive before it listens.
- A paused page doesn't ask, so it closes after 30 min.

## Non-goals

- AudioPlayer playback, background audio, Alexa app now-playing.
- Changing the APL page.
- Pages reachable from outside the LAN.

## Left to the builder

- File layout: the page's HTML/JS/CSS and the new routes.
- How the shared state code (`update_apl_metadata`, `live_page`, `bell`
  presses/sweeper) is split so both pages use it without copying.
- The name of the setting that picks HTML or APL.
- Tests: the skill side (Start directive, SSE state, press handling, page
  open/closed). The page itself is checked on a real Echo.
