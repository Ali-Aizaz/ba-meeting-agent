# T05 — “Hey BA” trigger and question extraction

**Priority:** Must · **Depends on:** T03, T04 · **Estimate:** part of the one-day build

## Why

- The bot replies in chat when someone says “Hey BA”, and stays quiet otherwise.
- The phrase can be spoken (a caption line) or typed in chat.

## What

**In scope**
- Detect the wake phrase on committed caption lines and on chat messages.
- Extract the question and who asked.
- Handle a pause after “Hey BA”, duplicates, and a clarification follow-up.
- Emit `TriggerEvent`.

**Out of scope**
- Answering (T12). An acoustic wake-word model.

## Details

### Design

Detection is on the caption text and the chat text. We already have the words from Meet, so there is no second listener.

### Matching

Normalise: lowercase, strip punctuation, collapse `b.a.`, `b a`, `bee a`, and `bea` to `ba`.

1. Reject lookalikes first: `hey bay`, `hey bob`, `hey bae`, `bay area`, `the BA team`, `BAE`.
2. Accept variants: `hey ba`, `hey bea`, `hi ba`, `okay ba`, `ok ba`.
3. Optional fuzzy match (`rapidfuzz` ratio ≥ `trigger.fuzzy_threshold`) for caption noise such as `hey baah`. If it causes false triggers in rehearsal, turn it off and keep the variant list.
4. Do not trigger on “BA” alone.

The wake phrase is expected at the start of the line (token 0, 1, or 2).

### Question

- Text after the phrase is the question.
- If fewer than 2 words follow, wait up to `trigger.pause_wait_s` (5 s) for the same speaker’s next line.
- If nothing arrives, emit `question=""` so the agent asks one short clarifying question in chat. Staying silent after being called looks like a failure.
- Chat uses the same variants. Chat matching can stay strict (`hey ba`, `@ba`).
- The same speaker and a near-duplicate question inside `trigger.dedup_window_s` (3 s) produce one event. That covers saying it and typing it.

### Follow-up

After the agent asks a clarifying question, the asking speaker’s next line within `trigger.followup_window_s` (20 s) is the answer, without another “Hey BA”. Other speakers are ignored.

### Output

`TriggerEvent(question, speaker, source, caption_id, t_detected)`.

## Acceptance criteria

- [ ] Spoken and typed “Hey BA, …” each produce one trigger with the question text.
- [ ] A transcript of ordinary meeting talk that never says “Hey BA” produces zero triggers.
- [ ] “Hey BA” alone, then the same speaker, becomes one question.
- [ ] Lookalikes in the negative list do not fire.
- [ ] The follow-up window accepts only that speaker, only inside the window.

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T05-U1 | unit | Positives: case, punctuation, `hey bea`, question extracted | All match |
| T05-U2 | unit | Negatives: “the BA team”, “hey Bob”, “bay area”, “BAE” | None match |
| T05-U3 | unit | Wake phrase, then same speaker 2 s later | One merged trigger |
| T05-U4 | unit | Wake phrase, then a different speaker | Not merged; empty question after the wait |
| T05-U5 | unit | Spoken and chat duplicate within 3 s | One event |
| T05-U6 | unit | Follow-up window | Asker inside / other speaker / expired |

## Dependencies

- T03, T04, T02 chat. Enables T12.

## Related resources

- RapidFuzz: https://github.com/rapidfuzz/RapidFuzz
