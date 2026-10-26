# T03 — Transcript from Google Meet captions

**Priority:** Must · **Depends on:** T01, T02 · **Estimate:** part of the one-day build

## Why

- The brief says to write down what is said, with who said it and when.
- Spoken “Hey BA” (T05) has to come from that transcript.
- Adnan asked to use Google Meet’s own speech-to-text so we do not add another STT API (C2).

Meet already runs speech-to-text for live captions and draws the result in the meeting page, with a speaker name. There is no public Google API that streams that transcript to a bot during the call. The workable path is to turn captions on in the bot’s own Meet window and read the caption region from the page.

## What

**In scope**
- After join (T02), turn captions on in the bot’s Meet client.
- Watch the captions region and emit stable caption lines: speaker, text, meeting time.
- Drop interim redraws of the same line.
- `TranscriptStore`: append-only transcript, persisted, with window queries.

**Out of scope**
- A second speech engine (faster-whisper, Whisper API, Deepgram, AssemblyAI, local VAD, PulseAudio capture).
- Name aliases (T04). Wake phrase (T05).

## Details

### Why this works

Live captions are rendered for the participant who turns them on. Turning them on in the bot’s browser does not require a separate API key, and it does not require the host to start Meet’s saved “transcript” (that product writes a file after the meeting and is not the live path).

Captions are lossy, strongest in English, and the DOM changes when Meet ships a new UI. That is acceptable for this brief. If captions never appear, typed “Hey BA” in chat still works. We do not add a backup STT API.

### Pipeline

```
T02 join
  → click "Turn on captions" (or press c) in the bot's Meet window
  → MutationObserver on the captions region
  → drain {speaker, text, observed_at}
  → stabilise (commit when the text stops changing, or the speaker changes)
  → CaptionLine → T04 name cleanup → TranscriptStore
```

### Turning captions on

- Prefer the control named “Turn on captions” (`aria-label`). Fallback: the keyboard shortcut `c` while the Meet page is focused.
- Captions are on when a region with role `region` and an accessible name containing “aption” exists, or when the button label flips to “Turn off captions”.
- Retry for about 15 s after admission. If it never appears, log `captions_enabled: false` on `/status` and keep chat working.

### Reading lines

Inject a script (Playwright `add_init_script` or `evaluate`) that:

- Finds `[role="region"][aria-label*="aption" i]`, with fallbacks for the current caption nodes if the ARIA name is missing. Keep every selector in `bot/google_meet/selectors.py`.
- Observes `childList`, `subtree`, and `characterData`.
- Reads each caption row’s speaker and text. Meet rewrites the current row in place, so the script queues snapshots rather than assuming each mutation is a new sentence.
- Exposes `window.__baDrainCaptions()` which returns and clears the queue. Python polls it a few times a second.

Commit a line when:

- the text has been unchanged for `captions.stable_ms` (default 800), or
- the speaker changes, or
- a new row replaces the current one.

Merge a growing line into the previous one when it is the same speaker and the new text starts with the previous text. Store the final text once.

Ignore empty text and the bot’s own name (“TechSol BA” / “You”) if Meet ever captions the bot. The bot’s mic stays off (T02), so this should be rare.

### Clock

- Meeting clock t=0 is admission (T02).
- `start_s` is when we first saw the line. `end_s` is when we committed it. Display `[mm:ss]`.
- These are observation times, not word-level audio times. That is what Meet captions give us, and it is enough for “when”.

### TranscriptStore

- Append-only SQLite (`storage.sqlite_path`).
- Queries: `last(n)`, `since(t)`, `by_speaker(name)`.
- Publish `caption_committed` for T05, T09, and T10.

## Acceptance criteria

- [ ] In a real Meet, a short English sentence from another participant shows up in the transcript with that person’s display name.
- [ ] The bot turns captions on itself. The host does not have to start a transcription product.
- [ ] A redraw of the same caption does not create a second transcript row.
- [ ] A meeting with captions failing still joins and can answer a typed “Hey BA”.
- [ ] No speech-to-text API key, model download, or audio device is required.
- [ ] The transcript survives an agent restart (reloaded from SQLite).

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T03-U1 | unit | HTML fixture that grows one caption row, then starts a new speaker | One committed line per final sentence; speaker set; no duplicates |
| T03-U2 | unit | `[mm:ss]` from admission time | Exact |
| T03-U3 | unit | TranscriptStore reload | Identical after reload |
| T03-I1 | integration | Fake Meet page (T02) emits caption DOM mutations | Lines arrive on the event bus |
| T03-E1 | manual | Two people in a Meet; one says “Hey BA, what is a hybrid?” | Line stored with speaker and a plausible time |

## Dependencies

- T01, T02 (page, admission time, captions button).
- Enables T04, T05, T09, T10.

## Related resources

- Meet captions are per client: the bot turns them on in its own window. There is no real-time transcript REST API for a bot in the call (Meet’s saved transcript is a host feature and lands after the meeting).
- Caption DOM pattern used by other Meet bots: a region `[role="region"][aria-label*="aption"]` plus a `MutationObserver`. Selectors will need a check against a live Meet during T02.
- Playwright `add_init_script` and `expose_function`: https://playwright.dev/python/docs/api/class-page
