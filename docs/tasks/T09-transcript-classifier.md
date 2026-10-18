# T09 — Transcript classifier (Decision / Task / Question / Risk / Requirement / Note)

**Priority:** Must · **Milestone:** M3 Brain & Notion · **Depends on:** T03, T04, T06 · **Estimate:** 2 days

## Why

- "Classifier" part: **tags each transcript chunk as Decision / Task / Question / Risk / Requirement / Note.**
- "Notes" row: **decisions, tasks, questions and risks**, each tagged with speaker and timestamp. These lists come from this classifier.
- Live test: **create tasks from speech** (not only on request) and **log the battery decision**. Detecting them in normal conversation is this task's job.
- Short-term memory (T10) and `save_meeting()` (T11) are built from classified items.

## What

**In scope**
- Classify every finalized utterance (speech and chat) into exactly one of the 6 labels, with confidence.
- Extract structured fields per label (task action/owner/due, decision/reason/by, etc.).
- Owner resolution and due-date parsing.
- Auto-actions: hand qualifying Tasks and Decisions to the Notion tools (T11), controlled by config.
- Run in the background LLM lane so it never delays "Hey BA" answers (T06).

**Out of scope**
- Notion writes and dedup (T11), memory storage (T10).

## Details

### Labels (exactly these six, from the PDF)
| Label | Meaning | Example |
|---|---|---|
| Decision | The group commits to a choice | "OK, we're going with the 60 kWh pack." |
| Task | Someone commits / is asked to do something | "Ali, can you send the cost sheet by Friday?" |
| Question | An open question raised | "Do we know the NCAP impact of the lighter chassis?" |
| Risk | Possible problem, uncertainty, dependency | "If the cell supplier slips, we miss SOP." |
| Requirement | A need the product must meet | "The base trim must do at least … km range." |
| Note | Anything else worth recording (context, info, chit-chat) | "Last week's supplier visit went fine." |

Utterances that are trigger questions to BA ("Hey BA, …") are labelled `Question` with `addressed_to_ba=true`. They aren't auto-actioned; the agent handles them (T12).

### Pipeline
- Subscribe to `utterance_finalized` (T03/T04) and `ChatMessage` events (non-self, non-trigger).
- **Batching:** every `classifier.batch_seconds` (15 s) or 5 new utterances, whichever comes first, send the new utterances **plus the previous ~6 as context** (labelled "context, don't classify"). Context resolves "yes, let's do that" → Decision, and "I'll take it" → Task owner = speaker.
- LLM call in the `background` lane with a JSON schema:
```json
[{"utterance_id": "u_123", "label": "Task", "confidence": 0.86,
  "summary": "Send cost sheet", "owner": "Ali", "due_text": "by Friday",
  "reason": null, "addressed_to_ba": false}]
```
- Validate with pydantic. On invalid output, retry once. If that fails, label the batch `Note` with confidence 0 and log it (never block the pipeline).
- Prompt (`config/prompts/classifier.md`): label definitions plus 10–15 short few-shot examples from automotive meetings. **The examples must not use Falcon pack numbers** (C4: no hard-coded answers).

### Field extraction and resolution
- **Owner:** names resolved through T04's alias resolver against the current participants. "I'll do it" → the speaker. "Can you…" addressed by name → that person. A name that isn't a participant (e.g. "ask Fatima in purchasing to…") is **kept as given**, normalised through the aliases file; owners don't have to be in the meeting. Only a pronoun or no name at all → `owner=None`.
- **Due:** first a small rule table for business phrases that `dateparser` doesn't handle reliably ("end of day/EOD" → meeting date, "end of week/EOW" → Friday, "end of month" → last day of the month, "next week" → Monday of next week). Then `dateparser` relative to the meeting date in `meeting.timezone` (`RELATIVE_BASE` = meeting date, `PREFER_DATES_FROM: future`), so "Friday" → the coming Friday. Unparseable → `None`, and `due_text` is kept.
- **Decision:** `decision` (short), `by` (speaker, or "Team" if consensus), `reason` (from context if stated, else `None`. Don't invent one).
- `said_by` / `by` / `time` always come from the utterance (T04), never from the LLM.

### Auto-actions (design for open question Q11)
| Item | Condition | Action |
|---|---|---|
| Task | `classifier.auto_create_tasks` and confidence ≥ `classifier.task_confidence` (0.75) and a concrete action | `create_task(task, owner, due, said_by, time)` → T11 (dedup + confirmation in chat) |
| Task with missing owner/due | as above | Still created with owner "TBD" / no due date. The confirmation line asks for the missing field in the same message (e.g. `✓ Task added: TBD — cost sheet — no date. Who owns it, and by when?`). This meets the persona's "asks for owner/date when missing" without a separate unprompted message |
| Decision | `classifier.auto_log_decisions` and confidence ≥ `classifier.decision_confidence` (0.8) and explicit commitment language | `log_decision(decision, by, time, reason)` → T11 |
| Requirement | — | Stored as a candidate in memory (T10). Written only on request ("Hey BA, write user stories…") via `add_requirement` |
| Question / Risk / Note | — | Stored in memory and written into Meeting Notes by `save_meeting()` (Risks also to the Risks DB in bonus T18) |

The confirmation for an auto-detected write is a chat message the agent posts without being called by name. That's acceptable because the PDF requires confirming **every** Notion write, and a chat line does not interrupt the spoken conversation. To reduce chat noise, `reply.confirmation_mode: batched` combines auto-write confirmations into one message at most every `reply.batch_interval_s` (2 minutes). Record this as a design decision in the README (open question Q11 asks TechSol to confirm it).

**The owner/date question in an auto-write confirmation does not open a follow-up window.** Unlike a clarifying question after "Hey BA" (T05/T12), nobody addressed the bot here, so routing the speaker's next sentence to the agent would be a false trigger. The missing field is filled in one of two ways: (a) someone says "Hey BA, Ali owns the cost sheet" → `update_task` with the owner extension (T11), or (b) later speech ("I'll take the cost sheet") is classified as a Task with owner = speaker, and T11's **TBD merge** rule updates the existing row instead of creating a new one.

### Throughput
With ~10–20 utterances/min and 15 s batches, that's ~4 LLM calls/min at low priority. Measure that interactive answers stay within budget while the classifier runs (T14).

## Acceptance criteria

- [ ] Every finalized utterance gets exactly one of the 6 labels within 30 s (background latency).
- [ ] On the labelled eval set: macro-F1 ≥ 0.75. **Precision ≥ 0.85 for Task and Decision**, because false writes to Notion are costly.
- [ ] Owner and due are extracted correctly for ≥ 85% of true Tasks that state them.
- [ ] `said_by`, `by` and `time` always match the utterance's speaker and time.
- [ ] Auto-actions obey the config flags and thresholds. Turning the flags off stops all auto-writes.
- [ ] Malformed LLM output never crashes or stalls the pipeline.
- [ ] Interactive answer latency is unaffected (T14 measurement with the classifier running).

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T09-U1 | unit | Schema validation + retry on malformed JSON (fake LLM) | Retry once; fallback to Note(0) |
| T09-U2 | unit | Batching triggers (time and count); context window composition | As specified |
| T09-U3 | unit | Owner resolution ("I'll do it", "Ali, can you…", unknown name) | Speaker / Ali / None |
| T09-U4 | unit | Due parsing relative to a fixed meeting date and timezone ("Friday", "next Tuesday", "end of month", "EOW", "ASAP") | Correct dates; "ASAP" → None with due_text kept |
| T09-U5 | unit | Auto-action gating by flags and thresholds | Correct calls to mocked T11 tools |
| T09-U6 | unit | Trigger utterances labelled Question with `addressed_to_ba` and never auto-actioned | Holds |
| T09-U7 | unit | Auto-write with TBD owner, then the same speaker's next unrelated sentence | No follow-up window opened; sentence classified normally |
| T09-I1 | integration (Ollama) | Labelled eval set ≥ 150 utterances from our own scripted Falcon-style meetings (hand-labelled, in `tests/eval/classifier_eval.jsonl`, see T16) | Macro-F1 ≥ 0.75; Task/Decision precision ≥ 0.85 |
| T09-I2 | integration | Replay T16 scenario transcript | Expected tasks/decisions detected; no spurious writes |

## Dependencies

- T03/T04 (utterances with speaker and time), T06 (background lane, JSON output).
- Feeds T10 (memory), T11 (auto-writes), T18 (Risks DB).

## Related resources

- dateparser: https://dateparser.readthedocs.io/
- Ollama structured outputs: https://ollama.com/blog/structured-outputs
- Pydantic validation: https://docs.pydantic.dev/latest/
- scikit-learn classification metrics (F1, precision): https://scikit-learn.org/stable/modules/model_evaluation.html#classification-metrics
