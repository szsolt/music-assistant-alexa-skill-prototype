# How it works

On an Echo with a screen, the music never leaves your network. Amazon only carries the requests: what you say, the skill's answer, and the player page's button presses. The Echo fetches the audio, the cover art and the page's updates from your LAN.

Only the skill endpoint has to be public. Music Assistant stays local.

## Who talks to whom

```mermaid
flowchart LR
    subgraph Internet
        AMZ[Amazon Alexa cloud]
    end
    subgraph LAN[Your network]
        ECHO[Echo Show<br/>player page]
        SKILL[Skill]
        MA[Music Assistant]
        STREAM[Stream host<br/>HTTPS, LAN only]
    end

    ECHO -- "voice, button presses" --> AMZ
    AMZ -- "requests (public HTTPS)" --> SKILL
    SKILL -- "answers: page, APL page updates" --> AMZ
    AMZ --> ECHO

    ECHO -- "audio, cover art" --> STREAM
    ECHO -- "HTML page, its reports and updates" --> STREAM
    STREAM -- "/bell/" --> SKILL
    STREAM -- "streams, images" --> MA

    MA -- "new stream, pause, resume" --> SKILL
    SKILL -- "next, previous, pause, play, events" --> MA
```

- **Public:** only the skill endpoint (`SKILL_HOSTNAME`). Amazon calls it for every request.
- **LAN only:** the stream host (`MA_HOSTNAME`), MA's API (`MA_API_URL`), and the HTML page (`HTML_PAGE_URL`).
- The stream host needs a name with a trusted HTTPS certificate. Its DNS record can exist only on your LAN.

## Starting the music

An Echo plays nothing until the skill is open on it. Alexa only accepts audio as the answer to a request. So every start goes the same way: something makes the Echo open the skill, and the skill answers with the player page. The page then fetches MA's stream itself, and only then does the audio flow.

There are two ways in. MA can start it, when you play something on the Echo's player in MA. Or you can, when you say "Alexa, open Music Assistant". In the second case the skill asks MA to resume, and MA starts it the first way.

```mermaid
sequenceDiagram
    participant U as You
    participant E as Echo Show
    participant A as Amazon
    participant S as Skill
    participant M as Music Assistant

    alt you start it in MA
        U->>M: Play an album on the Echo's player
    else you start it by voice
        U->>E: "Alexa, open Music Assistant"
        E->>A: Launch request
        A->>S: Launch request
        S->>M: Resume this Echo's player (LAN)
    end
    M->>S: New stream URL (LAN)
    M->>A: "Alexa, ask Music Assistant to play"
    A->>E: Voice command
    E->>A: Play request
    A->>S: Play request
    S->>A: Player page, with the stream URL
    A->>E: Player page
    E->>M: Fetch the stream and cover art (LAN)
    Note over E,M: The audio flows only now
    loop every second (HTML page)
        E->>S: Position report (LAN)
    end
```

A new Echo pairs itself here. The first unpaired Echo that opens the skill within 10 seconds of MA's stream becomes that MA player's Echo.

## Two player pages

With `HTML_PAGE_URL` set, an Echo that supports it gets the HTML page. The Echo loads it from the skill over the LAN. The page reports to the skill every second, and the skill sends it news over an open event stream.

Older Echos, and setups without `HTML_PAGE_URL`, get the basic APL page. It talks to the skill only through Amazon. It asks for news every couple of seconds.

The HTML page has more: the queue, browsing and lyrics.

## One stream, many tracks

MA sends the page one long stream: the whole queue, track after track. So a track change needs no new page and no new request. The video simply plays on.

The video doesn't know where one track ends. The skill works that out. When a track starts, it asks MA once for the track's length and sends the page the offset where the track begins in the stream. The page then counts the time on its own. MA gives lengths in whole seconds, so the count can drift. When the page is more than 2 seconds off MA's clock, it resyncs.

The page also knows the next track: its title and cover. At the end of a track, it switches to the next one by itself, without asking anyone. When the next track changes in MA ("play next", shuffle, a rearranged queue), the skill hears it from MA's events. It sends the page its new next track.

## When the page is open

While a page is open, MA doesn't need to make the Echo speak again. For next, previous, a jump in the queue, seek, pause and resume, MA tells the skill and leaves the rest to it. The skill hands the change to the page, and the page stays open.

The HTML page gets the change at once, over the LAN:

```mermaid
sequenceDiagram
    participant U as You
    participant E as Echo Show
    participant S as Skill
    participant M as Music Assistant

    U->>M: Next track (MA app, or a button on the page)
    M->>S: New stream URL, "the page may take it" (LAN)
    S->>E: The new stream, title, cover (LAN)
    E->>M: Fetch the new stream (LAN)
    E->>S: Position report: "I have it" (LAN)
```

If the page doesn't confirm within 3 seconds, the skill sends the change once more. After 6 seconds, the skill asks MA to send it the spoken way, and a new page opens.

The basic APL page takes the change with its next request for news:

```mermaid
sequenceDiagram
    participant E as Echo Show
    participant A as Amazon
    participant S as Skill
    participant M as Music Assistant

    M->>S: New stream URL, "the page may take it" (LAN)
    E->>A: Request for news
    A->>S: Request for news
    S->>A: The new stream, title, cover
    A->>E: The answer
    E->>M: Fetch the new stream (LAN)
```

## Buttons and voice

A button on the HTML page goes to the skill over the LAN. The skill asks MA, and the page shows the result when MA reports the change. A button on the basic APL page, and anything you say, goes through Amazon. For "Alexa, next", the skill waits for MA's new stream and answers with it.

```mermaid
sequenceDiagram
    participant U as You
    participant E as Echo Show
    participant A as Amazon
    participant S as Skill
    participant M as Music Assistant

    alt a button on the HTML page
        U->>E: Tap next
        E->>S: Next (LAN)
        S->>M: Next track (LAN)
        M->>S: New stream URL, "the page may take it" (LAN)
        S->>E: The new stream, title, cover (LAN)
    else voice
        U->>E: "Alexa, next"
        E->>A: Next request
        A->>S: Next request
        S->>M: Next track (LAN)
        M->>S: New stream URL (LAN)
        S->>A: The new stream, the page stays open
        A->>E: The answer
    end
```

"Alexa, pause" works like the pause button: the skill remembers where the page was, so play resumes from there.

The page stays open until you say "stop" or "cancel", the queue ends, or you move the music to another player. When MA fails, the skill says so, or the HTML page shows a short notice, and the page stays. While the HTML page waits for an answer it dims. It stops waiting after 10 seconds.

## The player page

The page shows the cover, title, artist, album, time, and the file's quality. Its buttons are play/pause, previous, next, shuffle, repeat, favourite, and up next. Tapping the cover opens the album.

The HTML page has more. A moon chip counts down MA's sleep timer. The page shows the next song and, in the playlist sheet, what happens after the queue ends. Audiobooks and podcasts get skip back, skip forward and a speed chip.

The menu can show MA's visualiser in place of the cover. MA's MilkDrop visualizer plugin sends the player's waveform. The skill relays it to the page over the event stream, and the page draws it with MilkDrop. Presets too slow for the Echo are skipped, and the page reports them to the skill log.

Every button goes to MA, so MA's queue stays the source of truth.

## Page updates

The HTML page reports its position to the skill every second, over the LAN. The skill sends it news over an open event stream on the LAN, as soon as MA reports a change. When the stream drops, the page reconnects, and the skill sends again what the page hasn't confirmed. When a hand-off goes unconfirmed, the skill's answer to the page's next report asks it to reopen the stream.

A restart of the skill keeps open pages. The skill saves them in its data directory. After the restart, the HTML page reconnects by itself.

The basic APL page asks the skill for news through Amazon every couple of seconds. Each answer schedules the next question.

## Staying in sync

The Echo and MA can disagree: a page closes, a stream stalls, Alexa breaks off. The skill watches for that and repairs it. Its corrections never start music that MA isn't playing.

| What goes wrong | How the skill notices | What it does |
|---|---|---|
| A new page shows but never plays | No event from it within 10 s. An HTML page: no report that says it plays within 20 s | Asks MA to resend the stream |
| Alexa closes the page on its own ("set volume to 2") | Alexa ends the session with its own error | Asks MA to resend the stream, so the page comes back |
| The page and MA disagree on playing | The page's report differs from MA for 2 checks, 5 s apart | Pauses the one that plays |
| The stream stalls | The page says it plays, but its position stays put for 20 s | Pauses MA |
| The page closed | No report for 30 s | Pauses MA, without speech |
| MA ended its stream early (it does at a sample-rate change) | The page plays 20 s past the track's end | Asks MA for the next track |

For a player with more than one Echo, a stalled or closed page doesn't pause MA, and the skill doesn't skip or reopen: another Echo may still play it.

## Voice commands

"Alexa, ask Music Assistant to play the album …", "shuffle artist …", "queue song … next", "move the music to the kitchen", "stop in 30 minutes". See [Voice commands](README.md#voice-commands).

The skill reads your artist, album, playlist and player names from MA once a day and uploads them to the skill's voice model at Amazon. Alexa needs them to understand the names.

## What Amazon sees

- What you say to the skill, and the skill's answers.
- The basic APL page's button presses, and its requests for news.
- Your library and player names, in the voice model.
- The stream and image URLs, but not the audio. It can't fetch them, because they resolve only on your LAN.

## Echos without a screen

An Echo without a screen plays through Alexa's AudioPlayer. Amazon checks that stream URL from the cloud, so it must be reachable from the internet over HTTPS. The LAN-only setup above doesn't work for these Echos. See [LIMITATIONS.md](LIMITATIONS.md).

## Requirements for the LAN-only setup

- An Echo with a screen, and `ENABLE_APL=true`.
- Each MA player's output codec set to MP3 or WAV. The Echo drops FLAC without a sound.
- A reverse proxy for the stream host. It sends `/bell/` to the skill and everything else to MA's stream port (8097).
- The Echo paired with its MA player. A new Echo pairs itself the first time MA plays on it. The `/devices` page pairs one by hand.
