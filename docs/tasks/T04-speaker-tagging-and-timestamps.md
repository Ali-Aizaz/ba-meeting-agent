# T04 — Speaker and time from caption lines

**Priority:** Must · **Depends on:** T02, T03 · **Estimate:** part of the one-day build

## Why

- The brief says every written line has who said it and when.
- Meet captions already include a speaker label. We normalise that label and stamp the meeting clock. We do not diarize audio.

## What

**In scope**
- Map a caption speaker string to a stable display name.
- Optional aliases in `config/people.yaml` (for example `Ali K.` → `Ali`).
- Meeting-time format `[mm:ss]`, from T03’s observation time.
- If the caption has no speaker, store `Unknown speaker`.

**Out of scope**
- Active-speaker tile sampling, audio-clock alignment, and pyannote. Those belonged to the mixed-audio Whisper design.

## Details

- Trim the speaker label. Drop a trailing “‘s presentation” style suffix if Meet adds one.
- The bot’s own labels (`TechSol BA`, `You`) are ignored by T03. If one arrives, do not attribute the line to a human.
- Owner names on tasks use this normalised speaker string (T09, T11). A person who is not in the meeting can still be named as owner if someone says so; store the name as given.
- Wall-clock UTC is stored next to the meeting clock so a later export can be matched to a calendar. Chat and Notion show meeting time.

## Acceptance criteria

- [ ] A caption line “Sara” / “Let’s use the hybrid” becomes speaker `Sara` and a `[mm:ss]` stamp.
- [ ] An alias in `people.yaml` rewrites the stored name.
- [ ] A line with a blank speaker is `Unknown speaker`, not a guess.

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T04-U1 | unit | Alias table | `Ali K.` → `Ali` |
| T04-U2 | unit | Blank speaker | `Unknown speaker` |
| T04-U3 | unit | Format at 75 s and at 3700 s | `[01:15]`, `[1:01:40]` |

## Dependencies

- T03 caption lines, T02 admission time.
- Enables T05, T09, T11.

## Related resources

- Caption speaker labels are part of the Meet captions region described in T03.
