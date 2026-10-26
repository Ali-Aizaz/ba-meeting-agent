# T09 — Tasks from the transcript

**Priority:** Must · **Depends on:** T03, T06 · **Estimate:** part of the one-day build

## Why

- The brief says to save tasks with an owner and a due date.
- Adnan’s earlier answer still holds for tasks: if someone agrees a task out loud, write it and confirm in chat. Do not wait for “Hey BA”.
- Decisions, requirements, and risks are not separate databases. If they matter, they belong in the summary (T11), which is diagrammed as a later set of databases (T18).

## What

**In scope**
- From committed caption lines, extract tasks: what, owner, due date, who said it, meeting time.
- Parse dates in `meeting.timezone` (“Friday”, “24 Oct”, “next Wednesday”).
- If owner or due date is missing on a task someone explicitly asked the bot to create, the agent asks one clarifying question (T12). For overheard tasks, skip the write until both fields are known, and do not nag the meeting.
- Hand complete tasks to `create_task` (T11) when `tasks.auto_create` is true.

**Out of scope**
- Six-way labels (Decision / Risk / Requirement). A separate decisions database.

## Details

Run this on a short rolling window of caption lines, in the background, so a “Hey BA” reply is not stuck behind it.

Prompt the model to return JSON only:

```json
{"tasks": [{"task": "update the cost sheet", "owner": "Ali", "due_text": "Friday", "said_by": "Sara"}]}
```

Drop a candidate when `task` or `owner` is empty. Resolve `due_text` to a date. If it does not resolve, drop an overheard task. Keep `due_text` on the record for the chat confirmation (`Fri`).

A line that is only a question to the bot (“Hey BA, what is a hybrid?”) is not a task.

## Acceptance criteria

- [ ] “Ali, can you update the cost sheet by Friday?” becomes one task: owner Ali, due the coming Friday, said_by the speaker.
- [ ] A car question to the bot does not become a task.
- [ ] An overheard task with no owner is not written and does not produce a chat message.
- [ ] “Hey BA, add a task to review the port” with no owner produces one clarifying question, not a Notion row.

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T09-U1 | unit | Date parser: “Friday”, “24 Oct”, “next Wednesday” in a fixed timezone | Expected dates |
| T09-U2 | unit | Model JSON fixture → `TaskRecord` | Owner, due, said_by set |
| T09-U3 | unit | Missing owner on an overheard line | No record, no chat |
| T09-U4 | unit | “Hey BA, what is a hybrid?” | No task |

## Dependencies

- T03, T06. Enables T10, T11.

## Related resources

- Timezone handling: the Python `zoneinfo` module. Default zone is `meeting.timezone` (UTC).
