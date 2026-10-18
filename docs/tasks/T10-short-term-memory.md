# T10 — Short-term meeting memory and recall

**Priority:** Must · **Milestone:** M3 Brain & Notion · **Depends on:** T03, T09 · **Estimate:** 1.5 days

## Why

- "Memory" row: **recalls earlier points in the same meeting.**
- "Short-term memory" part: **full current-meeting context: decisions, tasks, earlier answers.**
- Live test: **recall the decision** (e.g. "Hey BA, what did we decide about the battery?").
- A 7B model with a 16k context can't take a whole meeting transcript on every call (latency and truncation), so memory has to be structured and searchable.
- `save_meeting()` (T11) and dedup (T11) read from this state.

## What

**In scope**
- `MeetingState`: participants, utterances (from T03's store), classified items (T09), tasks/decisions/requirements with Notion IDs and status, questions, risks, agent Q&A history, tool-call log.
- Persistence in SQLite so a restart mid-meeting resumes with full state.
- `digest()`: a compact, token-capped summary injected into every agent call.
- `recall(query)`: an agent tool that searches the meeting (items, utterances, earlier answers) and returns cited hits.
- Rolling summary of older transcript for long meetings.

**Out of scope**
- Cross-meeting memory (bonus, T18).

## Details

### State model
```python
MeetingState:
  meeting_id, title, started_at, participants: set[str]
  utterances  → TranscriptStore (T03)
  items: list[ClassifiedItem]            # T09
  tasks: dict[key, TaskRecord]           # incl. notion_page_id, status history
  decisions: list[DecisionRecord]        # ordered; later can supersede earlier
  requirements: list[RequirementRecord]
  questions, risks: list[ClassifiedItem]
  qa_history: list[{t, asker, question, answer, tools_used, sources}]
  rolling_summary: str                   # older transcript compressed
  pending_clarification: {speaker, question, expires} | None   # T05/T12
```
- All writes go through `MeetingState` methods that persist to SQLite (`storage.sqlite_path`) in the same call.
- Superseded decisions: if a new decision on the same topic contradicts an earlier one (detected by embedding similarity ≥ 0.8 plus the classifier's summary), keep both and mark the earlier one `superseded_by`. Recall reports the latest and mentions the change.

### `digest()` (always in context; cap ~1,500 tokens)
```
Meeting: Project Falcon review · started 10:02 · participants: Ali, Sara, Omar
Decisions: [12:41] Sara — 60 kWh pack (reason: price target) ✓Notion
Open tasks: [08:15] Ali — cost sheet — Fri · [15:02] Sara — cell supplier quote — Wed
Recent BA answers: [10:30] compare 60 vs 75 kWh (src: falcon_specs.pdf)
Open questions: [09:12] Omar — NCAP impact of lighter chassis?
```
The order is decisions, open tasks, last 5 answers, open questions, risks. If it's over the cap, trim the oldest answers first, then notes. Never trim decisions or open tasks.

### `recall(query)` tool (added to the PDF's required tool list; extra tools are allowed)
- Hybrid search over: classified items (weighted higher), utterances and `qa_history`. It uses an in-memory embedding index (same model as T07) plus keyword match, and indexes new items as they arrive.
- Returns the top 5 hits: `{type, time, speaker, text, notion_page_id?}`.
- The agent answers with time and speaker, e.g. `Decision at 12:41 (Sara): 60 kWh pack, reason: price target. Logged in Notion.`
- Nothing found → the agent says it doesn't find that in this meeting (consistent with "not sure", T13).

### Rolling summary
Every 10 minutes (background lane), summarise utterances older than 10 minutes that aren't already summarised into ≤ 10 bullets with timestamps. The agent context gets `digest + rolling_summary + last ~3 minutes of raw transcript + retrieved hits`.

### Context assembly budget (for T12)
| Part | Budget (tokens) |
|---|---|
| Persona + rules + tool schemas | ~2,500 |
| `digest()` | ≤ 1,500 |
| Rolling summary | ≤ 800 |
| Recent raw transcript (last ~3 min) | ≤ 1,500 |
| Retrieved knowledge / recall hits | ≤ 2,500 |
| Question + tool results + answer | remainder (≤ 16k total) |

## Acceptance criteria

- [ ] "What did we decide about X?" returns the correct, latest decision with time and speaker in the scripted scenario (T16).
- [ ] "What did you say about Y earlier?" returns the earlier BA answer with its source.
- [ ] Open tasks reflect status changes made with `update_task` (T11).
- [ ] Killing and restarting the agent mid-meeting restores the full state from SQLite.
- [ ] `digest()` stays ≤ 1,500 tokens in a 60-minute meeting and never drops decisions or open tasks.
- [ ] `recall` p95 ≤ 200 ms.

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T10-U1 | unit | State updates for each item type; persistence + reload | Identical state after reload |
| T10-U2 | unit | Superseded decision handling | Latest returned; earlier marked `superseded_by` |
| T10-U3 | unit | `digest()` token cap with a synthetic 60-min meeting | ≤ 1,500 tokens; decisions/tasks intact |
| T10-U4 | unit | `recall` ranking on fixture meeting ("battery decision", "cost sheet owner", "what did you say the 75 kWh range was") | Expected item ranked #1 |
| T10-U5 | unit | Task status update reflected in digest | Done tasks removed from "open tasks" |
| T10-I1 | integration | Replay T16 scenario; ask recall questions at the end | Correct answers with time + speaker |
| T10-I2 | integration | Restart agent container mid-replay | Recall still correct after restart |

## Dependencies

- T03 (TranscriptStore), T09 (classified items), T04 (speaker/time).
- Used by T11 (dedup lookups, save_meeting content), T12 (context and `recall` tool), T18 (cross-meeting memory builds on it).

## Related resources

- SQLite in Python: https://docs.python.org/3/library/sqlite3.html · aiosqlite: https://github.com/omnilib/aiosqlite
- sentence-transformers semantic search: https://sbert.net/examples/sentence_transformer/applications/semantic-search/README.html
- Hugging Face tokenizers, for counting tokens with the model's own tokenizer: https://huggingface.co/docs/transformers/main_classes/tokenizer
