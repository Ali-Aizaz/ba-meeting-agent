# T02 — Meeting bot: join Google Meet, chat, captions on

**Priority:** Must · **Depends on:** T01 · **Estimate:** part of the one-day build (highest risk; do this early)

## Why

- The brief says to join a Google Meet, Zoom, or Teams call. We pick Google Meet.
- Replies go to the meeting chat when someone says “Hey BA”.
- Captions have to be turned on in this same browser, because that is our transcript (T03).

We join with Playwright. A hosted meeting bot would not give us the caption DOM.

## What

**In scope**
- `MeetingPlatform` with a Google Meet adapter: Playwright + Chromium, headed under Xvfb on the server.
- Join as a signed-in account, or as a guest named “TechSol BA”.
- Mic and camera off. Lobby wait. Consent message first.
- Chat read and `post_chat`.
- Turn captions on (the read loop is T03).
- Meeting-end detection. Leave 1 minute after the bot is alone, or when told.
- Output gate so the bot only posts for an allowed reason.

**Out of scope**
- Caption parsing (T03). Voice playback (diagram only, T18). PulseAudio capture.

## Details

### Platform

Google Meet via the browser. Chat and the captions button are both in the page. Zoom and Teams stay on the diagram as a second platform (T18). Guest join is the fallback when Google blocks sign-in from the server: the bot types “TechSol BA” and the host admits it.

### Adapter

```python
class MeetingPlatform(Protocol):
    async def join(self, url: str) -> None
    async def leave(self) -> None
    async def enable_captions(self) -> bool
    def chat_messages(self) -> AsyncIterator[ChatMessage]
    async def post_chat(self, text: str, reason: PostReason) -> bool
    def page(self) -> Page   # T03 installs the caption observer on this page
    def ended(self) -> asyncio.Event
```

### Browser

- `launch_persistent_context` so a signed-in Google session survives restarts.
- Headed under Xvfb. Flags: `--use-fake-ui-for-media-stream`, `--autoplay-policy=no-user-gesture-required`.
- `meeting.join_mode`: `signed_in` or `guest`.
- Mic and camera off on the pre-join screen. The bot does not speak.

### Lifecycle

1. Open the URL, mic/cam off, join or ask to join.
2. Wait up to `meeting.lobby_timeout_s`. On timeout, screenshot and exit.
3. On admission, meeting clock starts at 0. Open chat. Post the consent message. Call `enable_captions()`.
4. End when Meet says the meeting ended, the bot is removed, or it has been alone for `meeting.alone_timeout_s` (60 s). Then `save_meeting()` and leave.
5. “Hey BA, you can go” also leaves, after saving.
6. On disconnect, rejoin up to 3 times. Do not post the consent message again.

### Captions button

`enable_captions()` clicks the control whose accessible name is “Turn on captions”. If that click misses, press `c` with the Meet page focused. Success is a captions region in the page, or the button flipping to “Turn off captions”. T03 owns reading that region. Failure is logged as `captions_enabled: false` and does not stop chat.

### Consent message

> Hi, I'm TechSol BA, an AI assistant. I'm using this meeting's captions to keep notes (who said what, when) and to record tasks in Notion. Say "Hey BA …" to ask me something. Tell the host if you do not consent.

Posted once, first. When the summary page already exists, append `Notes will be here: <url>`.

### Chat

- Read with a `MutationObserver`. The bot’s own messages are labelled “You” in its own view; mark `is_self` and ignore them.
- Consecutive messages from one sender often omit a repeated name. Carry the last sender forward.
- Write by typing into the chat box. Shift+Enter for a new line, then Enter. Meet chat is plain text.
- If the host has disabled participant chat, log it and show `chat_enabled: false`. The bot cannot reply in that case.

### Output gate

`post_chat` requires a reason: `CONSENT`, `REPLY`, `CONFIRMATION`, `MEETING_SAVED`. Anything else is rejected. One `REPLY` per trigger.

### Selectors

All selectors live in `bot/google_meet/selectors.py`, each with a fallback. Prefer `aria-label` and `role`. On failure, screenshot to `data/debug/`.

## Acceptance criteria

- [ ] The bot shows as “TechSol BA”, mic and camera off.
- [ ] The consent message is the first chat message.
- [ ] A human chat message arrives as `ChatMessage` with the right sender.
- [ ] `post_chat` appears in the meeting.
- [ ] Captions turn on in the bot’s window (confirmed with T03-E1).
- [ ] The bot ignores its own messages.
- [ ] Alone for 60 s, or “Hey BA, you can go”, ends the meeting and starts save.
- [ ] Guest join works when sign-in is blocked.
- [ ] The output gate rejects a post with no reason.

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T02-U1 | unit | Saved chat HTML | Sender, text, `is_self` for “You”, sender carried forward |
| T02-U2 | unit | Output gate | Missing reason rejected; one REPLY per trigger |
| T02-I1 | integration | Local fake Meet page | Join, chat read/write, captions button |
| T02-E1 | manual | Real Meet with a teammate | Name, consent, chat, captions visible in the bot’s UI |

## Dependencies

- T01. Enables T03, T05, T12.

## Related resources

- Playwright Python: https://playwright.dev/python/
- Persistent context: https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context
- Meet live captions are turned on per participant from the meeting controls (the “c” shortcut and the CC button).
