# T02 — Meeting bot: join Google Meet, chat I/O, consent notice

**Priority:** Must · **Milestone:** M2 Meeting I/O · **Depends on:** T01 · **Estimate:** 3–4 days (highest-risk task; start early)

## Why

- "Meeting" row: the agent **joins Google Meet, Zoom or Teams (our choice) as "TechSol BA"** and replies in the meeting chat.
- `post_chat(text)` is a required tool, and every answer and Notion confirmation goes through it.
- Live test: the agent must **post the consent notice**.
- Hosted bot services are not allowed (C2), so we have to build the bot ourselves.
- The README must answer **"platform choice"** (T17). This task produces that answer.

## What

**In scope**
- Platform decision (below) and a `MeetingPlatform` adapter interface, so a second platform (T18 bonus) can be added later.
- Google Meet adapter using Playwright + Chromium running headed under Xvfb inside the container.
- Join flow (signed-in account or guest), mic and camera off, lobby wait, admitted detection.
- Consent notice posted as the first message after admission.
- Chat: read incoming messages (sender, text, time) as events; `post_chat(text)`.
- Participant list (names) for speaker tagging (T04) and owner resolution (T09/T11).
- Active-speaker signal from the UI, exposed for T04.
- Route the browser's audio output to a PulseAudio sink (consumed by T03).
- Meeting-end detection, which triggers `save_meeting()` (T11).
- Output gate: the bot can post only for an allowed reason (see Details).

**Out of scope**
- Audio transcription (T03), speaker assignment logic (T04), voice output (T18).

## Details

### Platform decision (README answer draft)

| | Google Meet | Zoom | Microsoft Teams |
|---|---|---|---|
| How a self-hosted bot joins | Browser automation (Playwright + Chromium) | Meeting SDK for Linux (native) or web client automation | Real-time media bots need the Graph Communications Calling SDK (Windows/.NET, Azure registration, tenant admin consent) or web client automation |
| Audio access | Capture the browser's audio output (mixed) | SDK raw data gives mixed and per-participant audio, which helps speaker tagging. Subscribing needs host/co-host/local-recording rights or a recording token | Per-participant via the media SDK (heavy setup) |
| Chat | DOM read/write | SDK chat API | Graph / DOM |
| Setup friction | Low: a Google account, no app review | Medium–high: Marketplace app + SDK credentials; raw-data access needs host permission; check Zoom's current rules for SDK apps joining meetings outside the developer's account | High |
| Main risks | UI/DOM changes, Google sign-in bot detection, guest admission | App approval and authorization requirements | Windows media stack, tenant permissions |

**Recommendation: Google Meet.** It has the fewest external approvals, runs fully self-hosted on the Linux GPU VM, and the chat and participant UI are reachable from the DOM. The cost is DOM fragility and mixed audio, which we handle with central selectors, a fake-DOM contract test, and UI-based speaker tagging (T04). Zoom is the natural second platform for the bonus (T18).

### Adapter interface

```python
class MeetingPlatform(Protocol):
    async def join(self, url: str) -> None
    async def leave(self) -> None
    def chat_messages(self) -> AsyncIterator[ChatMessage]
    async def post_chat(self, text: str) -> bool
    async def participants(self) -> list[str]
    def speaking_events(self) -> AsyncIterator[SpeakingEvent]   # (wall_time, name, speaking: bool)
    def ended(self) -> asyncio.Event
```

### Browser setup
- Launch with `launch_persistent_context(user_data_dir=…)` so the signed-in Google session persists between runs (volume-mounted).
- Run **headed under Xvfb**, not `--headless`. Meet's behaviour and audio playback are less reliable in headless mode.
- Useful flags: `--use-fake-ui-for-media-stream` (auto-accept permission prompts), `--autoplay-policy=no-user-gesture-required`. Set `PULSE_SERVER` so Chromium plays into the virtual sink.
- `meeting.join_mode`:
  - `signed_in` (default): the provided meeting account, named "TechSol BA", signs in once interactively (through the debug noVNC service in T15, or locally and then copy the profile). Use a dedicated test account (C5: no production accounts).
  - `guest`: not signed in; type `meeting.bot_name` into the name field and click "Ask to join". The host must admit the bot. This fallback works if Google blocks automated sign-in.
- Before joining: turn the microphone and camera off on the pre-join screen.

### Join and lifecycle
1. Open the meeting URL, wait for the pre-join screen, set mic/cam off, click "Join now" or "Ask to join".
2. Wait for admission up to `meeting.lobby_timeout_s`. On timeout or denial, log it, screenshot, exit with a clear status.
3. On admission: set **meeting clock t=0** (shared with T03/T04), open the chat panel, start observers, **post the consent notice**.
4. Meeting end: "You've been removed" / "Meeting ended" screen, or the bot is alone for `meeting.alone_timeout_s`. Then emit `ended`, and the orchestrator calls `save_meeting()` and posts the link if chat is still available.
5. The orchestrator also exposes `leave` for a clean shutdown (CLI/API, T15). `leave` runs `save_meeting()` and posts the link **before** leaving, while chat is still visible to the participants.
6. **Disconnects:** if Meet shows "You've been disconnected" / "Trying to reconnect" or the page reloads, re-run the join flow up to `meeting.reconnect_attempts` times (same URL, same browser profile). The meeting clock, transcript and state continue (T10 persistence). Do **not** post the consent notice again after a reconnect within the same meeting; log the gap so it shows in the notes.
7. **Unexpected dialogs:** Meet shows modals that can cover the chat UI ("Are you still there?", "This meeting is being recorded", layout tips, "Others may see your video differently"). Poll for known dialogs and dismiss them (click the confirm/dismiss button). Unknown modals: screenshot + log.

### Meet chat must be enabled for everyone
Meet hosts can turn off "Send chat messages" for participants (Host controls). If that is off, `post_chat` silently fails and the bot cannot reply at all. On admission, check that the chat input is enabled; if it isn't, log a loud warning and expose it in `/status` (T15). The live-day runbook (T16) asks the host to leave chat enabled, and to check it again if the meeting's settings are changed.

### Consent notice (`meeting.consent_message`, default)
> Hi, I'm TechSol BA, an AI assistant. I'm transcribing this meeting to keep notes (who said what, when) and to record tasks and decisions in Notion. Say "Hey BA …" to ask me something. Tell the host if you do not consent.

Posted once, within 5 s of admission, before anything else.

When `meeting.notes_link_in_consent` is `true` (default), a last line is appended: `Notes will be here: <Meeting Notes URL>`. The page exists from admission (T11 creates it on join), so the link is in the chat from the start. This covers the case where everyone leaves before `save_meeting()` runs: Meet chat is only visible to people present at that moment, so a link posted after they've left reaches nobody. The final `✓ Notes saved: <url>` after `save_meeting()` is still posted whenever chat is available (open question Q14).

### Chat I/O
- **Read:** inject a `MutationObserver` on the chat message list and send new messages to Python through `page.expose_binding`. Parse sender, text and time, then push `ChatMessage` events to an `asyncio.Queue`. Mark the bot's own messages `is_self=True` and ignore them downstream so the bot never answers itself. Meet labels the bot's own messages **"You"** (not "TechSol BA") in its own view, and groups consecutive messages from one sender under a single name header, so the parser must carry the last-seen sender forward and treat "You" as self.
- **Write (`post_chat`)**: focus the chat textarea, type the text, using Shift+Enter for line breaks, then press Enter. Confirm success by waiting for the message to appear in the DOM, and return `True`/`False`. Retry once on failure.
- Meet chat is **plain text** with no Markdown rendering or monospace. Tables must be compact pipe-separated rows (T12 formatter).
- **Message length:** Meet chat allows up to 32,000 characters per message (`reply.max_chars`). Replies are capped far below that by the 6-line / one-small-table rule (T12), so no message splitting is needed. `post_chat` still rejects anything longer than `reply.max_chars` as a bug guard.
- If the chat panel is closed (e.g. the UI re-renders), reopen it before posting.

### Output gate (enforces "silent otherwise" / C6)
`post_chat` takes a required `reason` enum internally: `CONSENT`, `REPLY` (answer to a trigger, including clarifying questions, refusals and the honest failure line from T12/T14), `CONFIRMATION` (Notion write result: success `✓ …`, duplicate `Already tracked …`, or failure `✗ Couldn't save …`), `MEETING_SAVED`. Anything else is rejected and logged. The LLM-facing `post_chat(text)` tool (T12) maps to `REPLY`. The gate allows **one logical reply per trigger** (it takes a `trigger_id`). The only exception is the T14 deadline holding line plus its follow-up.

### Selectors
- Keep every selector in `bot/google_meet/selectors.py`, each with a primary and a fallback. Prefer `aria-label`, `role` and visible text over generated class names (Meet's class names are obfuscated and change).
- On any selector failure: screenshot plus DOM dump to `data/debug/` to make fixes fast.

### Participants and active speaker
- Scrape names from the People panel, or from tile name labels, on join and every 10 s.
- Speaking indicator: sample participant tiles at 5–10 Hz in an injected script and detect the "speaking" visual indicator (exact selector or attribute found in the spike). Emit `SpeakingEvent`s with `Date.now()` timestamps. T04 aligns these to the audio clock.

## Acceptance criteria

- [ ] The bot appears in the participant list as **"TechSol BA"**, with mic and camera off.
- [ ] The consent notice is the bot's first chat message, posted ≤ 5 s after admission.
- [ ] A human chat message shows up as a `ChatMessage` with the correct sender and text in ≤ 1 s.
- [ ] `post_chat` messages appear in Meet chat in ≤ 1 s. Multi-line messages keep their line breaks.
- [ ] The bot never processes its own messages.
- [ ] Meeting end is detected, and `ended` fires within `meeting.alone_timeout_s`.
- [ ] Lobby timeout and denial are handled without crashing, with a screenshot saved.
- [ ] A network drop / page reload mid-meeting leads to a rejoin without a second consent notice; the transcript continues.
- [ ] Known Meet dialogs are dismissed automatically; the chat stays usable afterwards.
- [ ] If participant chat is disabled by the host, the bot logs a warning and `/status` shows it.
- [ ] The output gate blocks any post without an allowed reason (unit-tested).
- [ ] Both `signed_in` and `guest` join modes are tested at least once.

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T02-U1 | unit | Chat parser on saved Meet chat HTML fixtures | Correct sender/text/time; own messages flagged |
| T02-U2 | unit | Output gate | Posts without an allowed reason are rejected; one REPLY per `trigger_id`, except holding line + follow-up |
| T02-U3 | unit | Length guard: message of 32,000 vs 32,001 characters | First accepted; second rejected and logged |
| T02-I1 | integration (CI) | **Fake Meet page**: a local static HTML page that mimics pre-join, lobby, chat and tiles; run the adapter against it with Playwright | Join flow, chat read/write and speaking events all work |
| T02-I2 | integration (manual, real Meet) | Join a test meeting hosted by a teammate | Name, consent and chat round trip pass; screenshots captured |
| T02-I3 | integration (manual) | Host denies, or lets the lobby time out | Clean exit with status + screenshot |
| T02-I4 | integration (manual) | Everyone leaves | `ended` fires; `save_meeting` invoked (with T11 mocked) |
| T02-I5 | integration (manual) | Kill the container's network for 20 s mid-meeting | Rejoins; one consent notice in total; `/status` shows the reconnect |
| T02-I6 | integration (manual) | Host disables participant chat | Warning logged; `/status` reports `chat_enabled: false`; no crash |
| T02-U4 | unit | Chat parser: own messages labelled "You"; grouped consecutive messages without repeated sender header | `is_self` set; sender carried forward |
| T02-S1 | soak | 60-min meeting | No crash; chat still readable/writable at minute 60; memory stable |

## Dependencies

- T01 (config, models, logging).
- Enables T03 (audio sink), T04 (speaking events, participants), T05 (chat triggers), T12 (`post_chat`).

## Related resources

- Playwright for Python: https://playwright.dev/python/
- Playwright persistent context: https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context
- Playwright `expose_binding`: https://playwright.dev/python/docs/api/class-page#page-expose-binding
- MDN MutationObserver: https://developer.mozilla.org/en-US/docs/Web/API/MutationObserver
- Chromium command-line switches (reference list): https://peter.sh/experiments/chromium-command-line-switches/
- PulseAudio: https://www.freedesktop.org/wiki/Software/PulseAudio/
- Zoom Meeting SDK for Linux (comparison / T18): https://developers.zoom.us/docs/meeting-sdk/linux/ · raw audio: https://developers.zoom.us/docs/meeting-sdk/linux/default-ui/advanced-features/raw-data/
- Teams real-time media bots (comparison): https://learn.microsoft.com/en-us/microsoftteams/platform/bots/calls-and-meetings/real-time-media-concepts
- Open-source self-hostable meeting bots, as reference implementations only (don't use their hosted services): Vexa https://github.com/Vexa-ai/vexa · Attendee https://github.com/attendee-labs/attendee
