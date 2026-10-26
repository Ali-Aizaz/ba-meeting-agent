# T10 — This meeting’s transcript and tasks

**Priority:** Must · **Depends on:** T03, T09 · **Estimate:** part of the one-day build

## Why

- The notes are the caption transcript plus the tasks extracted from it.
- A “Hey BA” answer about the meeting (“what did we just agree?”) needs that context.
- `save_meeting()` reads it. Memory across past meetings is diagram-only (T18).

## What

**In scope**
- `MeetingState` for this meeting: caption lines, tasks, earlier bot replies.
- SQLite so a restart keeps the transcript.
- `digest()` : a short text block (recent lines, open tasks) added to the next “Hey BA” prompt. Cap it (about 3,000 tokens) so a long meeting does not blow the request. Older lines stay in SQLite and in the summary at the end.

**Out of scope**
- A vector index of past meetings. Embeddings.

## Details

- `digest()` lists participants seen so far, tasks already created, and the last few minutes of captions with `[mm:ss]` and speaker.
- The digest may be sent to the provided LLM. It must not be copied into a `search_cars` query (T06).
- Dedup keys for tasks are computed here or in T11 from normalised task text + owner. T11 decides whether a past Notion row already exists.

## Acceptance criteria

- [ ] After 10 caption lines, `digest()` contains those speakers and lines.
- [ ] Restart reloads the lines and tasks from SQLite.
- [ ] Digest length stays under the cap on a long fixture.

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T10-U1 | unit | Append lines, digest | Speakers and text present |
| T10-U2 | unit | Reload SQLite | Same lines and tasks |
| T10-U3 | unit | 400-line fixture | Digest truncated, full log still stored |

## Dependencies

- T03, T09. Enables T11, T12.

## Related resources

- SQLite via the Python standard library, or `aiosqlite`.
