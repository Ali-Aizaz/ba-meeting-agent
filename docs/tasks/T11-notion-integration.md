# T11 — Notion: 4 linked databases and write tools

**Priority:** Must · **Milestone:** M3 Brain & Notion (client + schema work can start during M2) · **Depends on:** T01, T10 · **Estimate:** 3 days

## Why

- "Notion" row: **fills 4 linked databases: Meeting Notes, Tasks, Decisions, Requirements; tasks have owner, due date, status; no duplicates.**
- Required tools: **`create_task(task, owner, due, said_by, time)`** (skips duplicates), **`update_task(task, status)`**, **`log_decision(decision, by, time, reason)`**, **`add_requirement(story, criteria, moscow)`**, **`save_meeting()`** (writes summary + notes to Meeting Notes and returns the link).
- Answer rule: **confirms every Notion write in chat** ("✓ Task added: Ali — cost sheet — Fri") (C7).
- Live test: **log the battery decision, create tasks, write user stories, fill Notion and post the link.**
- Submission includes a **Notion export** (T17), so the data must be clean and correctly linked.

## What

**In scope**
- Notion API client (pinned API version), rate limiting, retries.
- Schema mapping (`config/notion_schema.yaml`) for the 4 **provided** databases, with startup validation.
- A sandbox setup script that creates matching databases for development.
- Meeting page lifecycle (created at join, finalised at `save_meeting`).
- The 5 write tools above, with dedup and idempotency.
- Chat confirmations for every write (success, duplicate, failure).
- Meeting Notes content: summary plus every decision, task, question, risk and requirement line with speaker and timestamp, plus the full transcript.

**Out of scope**
- Risks database (bonus, T18). Notion export procedure (T17).

## Details

### API notes
- Integration token in `BA_NOTION__TOKEN`. In Notion, add the integration to the **teamspace/page containing all 4 databases** (Share → Connections), or every call returns 404.
- **Pin `Notion-Version`** (`notion.version`). Notion API version `2025-09-03` introduced **data sources**: a database contains one or more data sources, queries go to `/v1/data_sources/{id}/query`, pages are created with a `data_source_id` parent, and relation properties are written with `data_source_id`. Pick one version, follow its upgrade guide, and at startup resolve each configured database ID to its data source ID (`GET /v1/databases/{id}` lists them). Check that the Python SDK you use supports the chosen version, or call the REST API with `httpx`.
- **Rate limits:** each connection gets a per-minute budget (180 requests/min ≈ 3/s on most plans, 600/min on Business/Enterprise), plus a per-workspace limit shared by all connections. Send all Notion calls through **one async queue** paced at ≤ 3 req/s by default (`notion.max_rps`). On `429` or `529`, wait for `Retry-After`, then back off exponentially with jitter (max 5 attempts).
- **Retrying writes safely:** Notion advises retrying `5xx` blindly only for idempotent requests (GET/DELETE). A write that returns `503` may already be saved, so check `additional_data.retry_guidance` / `committed_resource_id`. Before retrying a create, look up our `Key` (below), and retry only if the row doesn't exist.
- **Size limits:** a rich-text object holds up to 2,000 characters, arrays (including the children of one append request) hold up to 100 elements, and a payload can have at most 1,000 block elements and 500 KB. Chunk long transcripts accordingly.

### Schema mapping (`config/notion_schema.yaml`)
TechSol provides the workspace and 4 databases, but their exact property names and types are unknown (open questions Q21–Q24). Map logical fields to the actual properties:

```yaml
meeting_notes:
  title: {name: "Name", type: title}
  date: {name: "Date", type: date}
  attendees: {name: "Attendees", type: multi_select}
  summary: {name: "Summary", type: rich_text}
  status: {name: "Status", type: select}          # In progress / Final
  meeting_id: {name: "Meeting ID", type: rich_text}
tasks:
  task: {name: "Task", type: title}
  owner: {name: "Owner", type: rich_text}          # or people / select, see below
  due: {name: "Due", type: date}
  status: {name: "Status", type: status}           # Not started / In progress / Done
  said_by: {name: "Said by", type: rich_text}
  time: {name: "Time", type: rich_text}            # "mm:ss"
  meeting: {name: "Meeting", type: relation, target: meeting_notes}
  key: {name: "Key", type: rich_text}              # dedup / idempotency key
decisions:
  decision: {name: "Decision", type: title}
  by: {name: "Decided by", type: rich_text}
  time: {name: "Time", type: rich_text}
  reason: {name: "Reason", type: rich_text}
  meeting: {name: "Meeting", type: relation, target: meeting_notes}
  key: {name: "Key", type: rich_text}
requirements:
  story: {name: "User story", type: title}
  criteria: {name: "Acceptance criteria", type: rich_text}
  moscow: {name: "MoSCoW", type: select}           # Must / Should / Could / Won't
  meeting: {name: "Meeting", type: relation, target: meeting_notes}
  key: {name: "Key", type: rich_text}
status_map: {todo: "Not started", doing: "In progress", done: "Done"}
```
- **Startup validation:** fetch each database/data source schema. Check that every mapped property exists with the expected type, that relations target the Meeting Notes database, and that select/status options include the values we write. Fail fast with a readable diff ("Tasks: property 'Due' missing; found 'Due date' (date)").
- If a `Key` property can't be added to the provided databases, dedup falls back to the local SQLite key index plus title search (below).
- **Owner type:** if Owner is a Notion `people` property, map canonical names (T04 `people.yaml`) to Notion user IDs. Names without a Notion user go in the title suffix or a text property. `rich_text`/`select` owners need no mapping.
- **Status:** Notion `status` properties only accept existing options. Map our statuses through `status_map`.
- `scripts/notion_setup.py` creates the 4 linked databases with this schema under a sandbox page, for development before or alongside the provided workspace.

### Linking
- Tasks, Decisions and Requirements each have a **relation to Meeting Notes**. If the relation is two-way, Notion fills the reverse side automatically.
- **Meeting page lifecycle:** create the Meeting Notes page **on admission** (status "In progress", title `"<meeting title> — <date>"` where the title comes from `ba join --title` or `meeting.title_default`, attendees so far). Every later write can then link to it immediately, and the page URL is known from the start (T02 appends it to the consent notice when `meeting.notes_link_in_consent` is on). `save_meeting()` fills in the summary and body and sets status "Final".
- **`save_meeting()` is re-runnable.** Each run replaces the generated body (delete our previously written blocks, then append) and refreshes the summary, so it can be called mid-meeting ("Hey BA, save the meeting so far"), again at the end, and after a restart, always giving one consistent page. With `notion.autosave_minutes > 0` it also runs silently in the background every N minutes as a crash safety net (no chat line, since the page is the same one already announced; only explicit/final saves post `✓ Notes saved: <url>`). Default is off; see open question Q13.
- **If chat is gone when the meeting ends** (everyone left, bot removed), the link cannot be posted. It is then logged, shown in `GET /status` and printed by `ba leave`. The consent-notice link covers the participants' side.

### Tool behaviour

| Tool | Behaviour | Confirmation |
|---|---|---|
| `create_task(task, owner, due, said_by, time)` | Normalise, dedup (below), create page linked to the meeting, store `notion_page_id` in memory (T10) | `✓ Task added: Ali — cost sheet — Fri` · duplicate: `Already tracked: Ali — cost sheet — Fri` |
| `update_task(task, status)` | Find the task by fuzzy match among this meeting's tasks, then the open tasks in the DB. Several close matches → one clarifying question (T12). Map status through `status_map` | `✓ Task updated: cost sheet → Done` |
| `log_decision(decision, by, time, reason)` | Dedup, create, link | `✓ Decision logged: 60 kWh pack (Sara, 12:41)` |
| `add_requirement(story, criteria, moscow)` | Validate the story format ("As a …, I want …, so that …"), criteria (Given/When/Then lines preferred), and `moscow` ∈ {Must, Should, Could, Won't}. Dedup, create, link | `✓ Requirement added [Must]: As a buyer, I want … (short)` |
| `save_meeting()` | Generate the summary, write properties and body (below), set Final, return the page URL | `✓ Notes saved: <url>` |

- **Confirm only after Notion returns success.** On failure after retries: `✗ Couldn't save task "cost sheet" to Notion — will retry` (queued for retry; the state is kept in SQLite). Never claim a write that didn't happen.
- Due display in confirmations: weekday short name (`Fri`) if within 7 days, otherwise `24 Oct`. No due → `no date`. Owner missing → `TBD` (T09 explains the follow-up question).
- `update_task` may take optional `owner`/`due` keyword arguments (an extension; the required signature stays `(task, status)`) so "Hey BA, Ali owns the cooling task" can fill a TBD owner.
- **Status synonyms:** the `status` argument arrives as natural language. Map before `status_map`: `done / complete / completed / finished / closed` → `done`; `started / in progress / working on it / ongoing` → `doing`; `not started / reopen / to do / open` → `todo`. Anything else (e.g. "blocked") → if the Notion status property has such an option, use it; otherwise ask one clarifying question listing the available statuses.
- **Owners** are stored as given (after alias normalisation). They don't have to be meeting participants (T09). If Owner is a `people` property and the name has no Notion user, fall back to the text property / title suffix described above and say so in the confirmation (`Ali (no Notion user)` only in logs, not in chat, to keep the line short).

### Dedup and idempotency ("no duplicates")
Dedup is **scoped to the current meeting**. Matching against other meetings' rows would make leftover rehearsal tasks block live-test tasks. Cross-meeting awareness is the bonus long-term memory (T18 B2).
1. **Key:** `sha1(meeting_id + normalise(task) + normalise(owner))` for tasks; `sha1(meeting_id + normalise(decision))` for decisions; `sha1(meeting_id + normalise(story))` for requirements. `normalise` lowercases, strips punctuation and stop-words, and lemmatises simple plurals. `meeting_id` is stable for a meeting, including across agent restarts and when the T16 replay is re-run with the same ID.
2. **Exact:** the key already exists in the local index (SQLite) or in Notion (`Key` property) → duplicate.
3. **Fuzzy:** compare against this meeting's records of the same type. `rapidfuzz.token_set_ratio ≥ 90` (or embedding cosine ≥ 0.9) **and the same owner** (tasks) → duplicate. A different named owner means a different task.
4. **TBD merge:** if the matching task in this meeting has owner `TBD` (T09) and the new call names an owner, **update that row** (owner, and due if given) instead of creating a new one. Confirm with `✓ Task updated: Ali — cost sheet — Fri`.
5. Retries reuse the same key, so a network retry never creates a second page.
6. Duplicate → no write, and an `Already tracked …` line instead (counts as the confirmation).

### `save_meeting()` content
Properties: title, date, attendees, summary (≤ 2,000 chars), status Final.
Body blocks:
1. **Summary:** ≤ 10 bullets, generated (LLM, interactive lane) **only from classified items and BA answers**, with timestamps.
2. **Decisions:** `[12:41] Sara: 60 kWh pack — reason: price target` (linked to the Decisions page).
3. **Tasks:** `[08:15] Ali (said by Sara): cost sheet — due Fri — Not started`.
4. **Requirements:** story + MoSCoW.
5. **Open questions** and **Risks:** `[09:12] Omar: …`.
6. **BOM / comparison tables** produced during the meeting (full versions from T08).
7. **Full transcript** in a toggle: every line `[mm:ss] Speaker: text`, chunked into ≤ 100 blocks per append call.

Every line in sections 2–5 and 7 carries a speaker and a timestamp ("Notes" row).

## Acceptance criteria

- [ ] Startup validation passes against the provided databases (or prints an actionable diff).
- [ ] Tasks rows have owner, due date (when given), status, said by, time, and a meeting relation. Decisions and Requirements rows are linked to the meeting.
- [ ] Within a meeting, repeating the same task (same or reworded, same owner) produces **no duplicate row**. Naming the owner of a TBD task updates that row. The same task with a different named owner creates a new row.
- [ ] Every successful write produces exactly one confirmation line in the PDF's format. Failures are reported honestly.
- [ ] `save_meeting()` returns a working URL, and the page contains all sections with speaker + time on every line.
- [ ] Running the same meeting replay twice with the same `meeting_id` creates no duplicate rows (idempotency).
- [ ] No 429 errors surface to users during a 60-minute meeting replay.

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T11-U1 | unit | Property payload builders for title, rich_text, date, select, status, multi_select, people, relation | Match Notion API JSON shapes |
| T11-U2 | unit | Schema validation against fixture schema JSON (good / missing property / wrong type / missing option) | Pass / readable diffs |
| T11-U3 | unit | Dedup: exact task; reworded task (≥ 90); same task with a different owner; existing TBD task then the same task with a named owner; same task or decision in a different meeting | dup / dup / new / TBD row updated (no new row) / new |
| T11-U4 | unit | Rate limiter + 429/529 `Retry-After` + backoff; a create that returns 503 is retried only after a `Key` lookup finds no row (respx) | ≤ 3 req/s; retries honoured; no double create |
| T11-U5 | unit | Transcript chunking (2,000-char rich text, 100-block appends) | No text lost; limits respected |
| T11-U6 | unit | Confirmation formatting (due today/≤ 7 days/> 7 days/none; owner TBD) | Exact strings |
| T11-U7 | unit | `update_task` fuzzy match: unique / ambiguous / none | update / clarification / "not found" |
| T11-U8 | unit | Status synonyms ("finished", "working on it", "reopen", "blocked" with/without option) | Mapped / mapped / mapped / option or clarification |
| T11-U9 | unit | `save_meeting()` twice on the same page (fake client) | Second run replaces the body; no duplicated sections |
| T11-I1 | integration (sandbox Notion) | Create meeting → create task → duplicate → update status → log decision → add requirement → save_meeting | Rows and relations visible both ways; one row per item; URL opens |
| T11-I2 | integration (sandbox) | Replay T16 scenario twice with the same `meeting_id` | Identical row counts after the second run |
| T11-I3 | integration (provided workspace) | Startup validation + one test write, then delete | Passes; cleanup done |

## Dependencies

- T01 (config), T10 (state, IDs, content for save_meeting), T04 (names), T09 (auto-writes).
- Used by T12 (tools), T16, T17 (export).

## Related resources

- Notion API reference: https://developers.notion.com/reference/intro
- Create a page: https://developers.notion.com/reference/post-page
- Update page properties: https://developers.notion.com/reference/patch-page
- Append block children: https://developers.notion.com/reference/patch-block-children
- Property values: https://developers.notion.com/reference/page-property-values
- Request limits (rate limits, retrying writes, size limits): https://developers.notion.com/reference/request-limits
- Upgrade guide for API version 2025-09-03 (data sources): https://developers.notion.com/docs/upgrade-guide-2025-09-03
- Authorization / sharing pages with an integration: https://developers.notion.com/docs/authorization
- notion-sdk-py: https://github.com/ramnes/notion-sdk-py
- RapidFuzz: https://github.com/rapidfuzz/RapidFuzz
- User stories: https://www.atlassian.com/agile/project-management/user-stories · MoSCoW: https://en.wikipedia.org/wiki/MoSCoW_method · Gherkin (Given/When/Then): https://cucumber.io/docs/gherkin/reference/
