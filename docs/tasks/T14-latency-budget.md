# T14 — Timing logs

**Priority:** Optional · **Depends on:** T05, T12

## Why

The new brief does not set a 10-second reply limit, and it does not ask for a latency write-up. Logging how long a reply took is still useful in rehearsal.

## What

Log one line per trigger: caption commit → model start → tool time → chat posted, in milliseconds. No deadline fallback and no GPU budget.

## Acceptance criteria

- [ ] A reply logs those durations.
- [ ] Nothing in the agent waits on this task.

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T14-U1 | unit | A finished trigger | Log line contains the four timestamps or durations |

## Dependencies

- T05, T12.
