# T16 — Live-test rehearsal

**Priority:** Must · **Depends on:** the Must tasks · **Estimate:** one short meeting before 30 Oct 2026

## Why

- They will test the agent live in a short meeting. Adnan and the interviewer join (Q2).
- The old nine-step Falcon script (60 vs 75 kWh, BOM, user stories, four databases) is not the test anymore.

## What

**In scope**
- One rehearsal that covers the five brief items, plus a typed “Hey BA” and a duplicate task.
- A few notes on what failed, fixed before submission.
- A runbook: host admits the bot, chat stays on, captions are turned on by the bot itself, English.

**Out of scope**
- An audio-replay harness, actor browsers, and a 10-second score.

## Rehearsal script

Two to four people is enough. Paraphrase the lines.

| # | What we check | Example | Pass |
|---|---|---|---|
| 1 | Join and consent | Bot admitted | First message is the consent text. Captions are on in the bot’s window. |
| 2 | Transcript | Someone speaks a normal sentence | That speaker and `[mm:ss]` show up in the stored transcript |
| 3 | Spoken “Hey BA” | “Hey BA, what is a hybrid?” | Chat answer cites the knowledge base |
| 4 | Web search | “Hey BA, find the battery size of the current Toyota RAV4 hybrid.” | Chat answer includes a URL. The search query is the car question |
| 5 | Typed “Hey BA” | Someone types `Hey BA what is an EV?` | One chat answer |
| 6 | Overheard task | “Ali, please send the cost sheet by Friday.” | Notion row with owner and due date, and `✓ Task added: …` in chat |
| 7 | Duplicate | The same task again | No second row. Chat says it is already tracked |
| 8 | Missing due date | “Hey BA, add a task for Sara to book the test drive.” | One question asking for the date. No row yet |
| 9 | Quiet | A minute of talk with no “Hey BA” and no task | No extra chat messages |
| 10 | Save | “Hey BA, save the meeting.” or everyone leaves | Summary page has the summary and the transcript. URL posted if chat is still open |

Distractors that must not fire a reply: “the BA team will review this”, “hey Bob”.

## Acceptance criteria

- [ ] One rehearsal completed and the failures either fixed or written down in the README as limits.
- [ ] Items 1, 3, 4, 6, and 10 pass. Those are the brief.

## Tests

The rehearsal is the test. Unit tests live on the other tasks.

## Dependencies

- T02, T03, T05, T07, T11, T12, T15.

## Related resources

- Run the bot with T15’s join command. The host uses a normal Google Meet link.
