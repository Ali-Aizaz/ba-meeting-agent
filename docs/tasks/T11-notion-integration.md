# T11 — Notion summary and tasks

**Priority:** Must · **Depends on:** T01, T10 · **Estimate:** part of the one-day build

## Why

- The brief says to save a summary and the tasks (owner + due date) to Notion.
- Each task write is confirmed in chat.
- The real database is not attached yet (N1). Build against a sandbox, and map property names when Adnan attaches it.

Decisions, Requirements, and Risks are not created. They appear on the diagram (T18).

## What

**In scope**
- Notion client, token from the environment, version pinned.
- `config/notion_schema.yaml` mapping our fields to property names.
- `create_task(task, owner, due, said_by, time)`.
- `save_meeting()` writes a summary of the transcript and links or lists the tasks. Returns the URL.
- Chat confirmations.
- Dedup against tasks already in the Tasks database, including earlier meetings (Adnan’s Q25 answer).

**Out of scope**
- Four linked databases. A Risks database. User stories and MoSCoW as their own rows.

## Details

### Until the database is attached

Sandbox, created by `scripts/notion_setup.py` if we have a token:

| Place | Fields |
|---|---|
| Summary | Title, Date, Summary (rich text), Transcript (toggle or child page) |
| Tasks | Task (title), Owner (text), Due (date), Status (Not started / In progress / Done), Said by (text), Time (text), Meeting (url or relation) |

When the real database arrives, change the YAML only. Startup reads the database, checks that the mapped properties exist, and refuses to start with a list of what is missing.

### API

- `BA_NOTION__TOKEN`. The integration must be connected to the page that holds the databases.
- Pin `Notion-Version`. If the account is on a version that uses data sources (`2025-09-03` and later), resolve the data source id at startup and create pages against that id.
- One async queue, about 3 requests/second. On 429, honour `Retry-After`.
- Before retrying a create, look up the dedup key so a timeout does not double-write.

### `create_task`

- Key: normalised task text + owner, lowercased, whitespace collapsed.
- If a row with that key exists in this meeting or an earlier one, do not create another. Chat: `Already tracked: Ali — cost sheet`.
- On success: `✓ Task added: Ali — cost sheet — Fri`.
- On failure: `✗ Couldn't save the task: cost sheet`.
- Status defaults to Not started when that property exists.

### `save_meeting()`

- Runs when someone says “Hey BA, save the meeting”, and when the meeting ends (T02).
- Summary: a short paragraph from the transcript (T06, no web search) plus the task list.
- Transcript: the caption lines (`[mm:ss] Speaker: text`) in a collapsed toggle or a child page, so the page stays readable.
- Chat, if people are still there: `✓ Notes saved: <url>`.
- Safe to run twice. The second run updates the same summary page.

### Consent link

T02 appends the summary URL to the first message once the page exists. Create the empty summary page at join so the link is real.

## Acceptance criteria

- [ ] A task with owner and due date appears in Notion and the chat shows the confirmation line.
- [ ] The same task a second time does not add a row, and the chat says it is already tracked.
- [ ] `save_meeting()` writes a summary and the transcript, and returns a URL.
- [ ] Running save twice updates one page.
- [ ] Startup fails clearly if a mapped property is missing.
- [ ] Swapping `notion_schema.yaml` is enough to point at the attached database.

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T11-U1 | unit | Payload from `TaskRecord` via the YAML map | Property names match the sandbox |
| T11-U2 | unit | Dedup key | “Update the cost sheet” and “update the cost sheet” match |
| T11-I1 | integration | Sandbox Notion, or a recorded HTTP mock | Create, duplicate, save, second save updates |

## Dependencies

- T01, T10. T09 produces the tasks. Used by T02 (link) and T12.

## Related resources

- Notion API: https://developers.notion.com/reference/intro
- Pages: https://developers.notion.com/reference/post-page
- Data sources (newer API versions): https://developers.notion.com/reference/data-source
