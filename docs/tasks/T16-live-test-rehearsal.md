# T16 — Live-test scenario, evaluation harness and rehearsals

**Priority:** Must · **Milestone:** M4 Hardening · **Depends on:** all Must tasks · **Estimate:** 3 days (spread across M3–M4)

## Why

- The assignment is judged in a **live test**: 3–4 TechSol staff discuss **Project Falcon (a 30,000 USD electric SUV)**. The agent must:
  1. post the consent notice
  2. compare 60 vs 75 kWh packs
  3. log the battery decision
  4. create tasks **from speech** and
  5. **on request**
  6. write user stories
  7. list the main parts and suppliers for the 60 kWh pack with a cost estimate
  8. recall the decision
  9. fill Notion and post the link
- Each item depends on several components working together. Only an end-to-end harness and real rehearsals catch integration failures: timing, speaker mix-ups, false triggers.
- Not allowed: hard-coded answers (C4). The scenario is test **input**. The agent must not contain special cases for these exact phrases.

## What

**In scope**
- A Falcon scenario script for 3–4 actors that covers all 9 items, plus distractors and edge cases.
- An evaluation harness with two replay modes (text and audio) and automatic pass/fail checks.
- Evaluation datasets, organised in one place (reused by T05, T06, T09, T12, T13, T14).
- At least two real rehearsals on Google Meet on the TechSol VM, with a fixed-issues log.
- A live-day runbook.

**Out of scope**
- Writing the components themselves.

## Details

### Scenario script (`tests/e2e/falcon_scenario.yaml`)
Actors: e.g. **Sara** (programme lead), **Ali** (cost engineer), **Omar** (chassis/safety), optionally **Nadia** (product). Approximate times; lines are paraphrasable by real actors.

| # | Live-test item | Example input (actor) | Expected behaviour |
|---|---|---|---|
| 1 | Consent notice | Bot admitted | Consent text is the first bot message, ≤ 5 s |
| 2 | Compare 60 vs 75 kWh | Sara: "Hey BA, compare the 60 and 75 kWh packs on cost, range and weight." | `compare` called; table ≤ 8 rows; values from pack or "not in pack"; source cited; neutral; ≤ 10 s |
| 3 | Log the battery decision | Sara: "OK, we're going with the 60 kWh pack to stay under thirty thousand." (speech) and/or "Hey BA, log that decision." | `log_decision(decision≈"60 kWh pack", by="Sara", time=…, reason≈"price target")`; `✓ Decision logged…`; one row only even if both happen |
| 4 | Task from speech | Sara: "Ali, can you update the cost sheet by Friday?" Ali: "Sure." | Task auto-created (owner Ali, due Friday's date, said_by Sara); confirmation in chat |
| 5 | Task on request | Omar: "Hey BA, add a task for me to check NCAP impact of the lighter chassis by next Wednesday." | `create_task` with owner Omar, due date, said_by Omar; confirmation |
| 5b | Missing owner | Nadia: "Hey BA, add a task to review the charging port location." | One clarifying question: owner + date; completes after the answer |
| 6 | User stories | Nadia: "Hey BA, write user stories for home charging." | ≤ 3 `add_requirement` calls; story format; criteria; MoSCoW; confirmations ≤ 6 lines |
| 7 | 60 kWh pack parts + suppliers + cost | Ali: "Hey BA, list the main parts and suppliers for the 60 kWh pack with a cost estimate." | `build_bom`; part/qty/cost/supplier; single-supplier and long-lead flags; total + assumptions; source |
| 8 | Recall the decision | Omar (later): "Hey BA, what did we decide about the battery?" | `recall`; decision with time + speaker; mentions it's logged |
| 9 | Fill Notion + post link | Sara: "Hey BA, save the meeting." (or meeting ends) | `save_meeting()`; `✓ Notes saved: <url>`; all 4 DBs filled and linked |

Distractors (must produce **no** bot message):
- 3+ minutes of normal discussion with jargon, including "our BA team", "BAE", "bay", "baseline".
- Two people talking over each other.
- An off-topic aside ("anyone watch the match?").

Edge prompts (expected guarded responses):
- "Hey BA, what's the weather like?" → one-line refusal.
- "Hey BA, what's the Falcon's towing capacity?" (if not in pack) → "Not sure — the pack doesn't cover…".
- "Hey BA, which pack is better?" → trade-off, no side taken, asks for the deciding criterion.
- Repeat of task 4 in different words → `Already tracked…`, no duplicate row.
- "Hey BA, mark the cost sheet task done." → `update_task` → `✓ Task updated: cost sheet → Done`.
- "Hey BA." followed by 5 s of silence → one line: `Yes — what would you like me to do?` (T05/T12).
- Two people say "Hey BA, …" within a second of each other → both get exactly one reply (T12).
- Speech-detected task with no owner ("Someone needs to check the charging port.") → `✓ Task added: TBD — … — no date. Who owns it, and by when?`; the next sentence by that speaker is **not** treated as an answer (T09). Later: "Hey BA, Nadia owns the charging port task" → `✓ Task updated: Nadia — …`.

Failure drill (run once in rehearsal, not in the live test): stop `ollama` during a trigger → holding line at 8.5 s, honest failure line at 20 s, no silence and no invented answer (T14).

### Harness (`scripts/replay.py`)
- **Text replay mode** (fast, CI-friendly): feed the scenario's utterances (speaker, time, text) directly into the pipeline after STT, with T02 replaced by a fake chat sink and Notion pointed at the sandbox (or a mock). This tests T05 → T13 and T11 logic in minutes.
- **Audio replay mode** (realistic): render each actor's lines to WAV with distinct Piper voices. Then either (a) pipe them into the PulseAudio sink with speaker timelines from the script, or (b) join a real Meet with 3–4 Playwright "actor" browsers, each playing its voice via Chromium's fake mic (`--use-file-for-fake-audio-capture`) and named after the actor. Option (b) exercises T02–T04 for real.
- **Checks** (automatic, after the run):
  - Chat log: consent first; one reply per trigger (plus permitted confirmations, and the T14 holding line only if the deadline fallback fired); zero messages during distractors; every reply ≤ 6 lines or one small table; every reply with numbers has a source or an assumption.
  - Latency per trigger (from T14 spans): max ≤ 10 s.
  - Notion: expected rows per DB, fields (owner, due, status, said by, time), relations to the meeting page, no duplicates, Meeting Notes sections with speaker + time per line.
  - Tool calls: expected tool and key arguments per scripted trigger.
- Output: a Markdown report with pass/fail per item, plus latency and the chat transcript.

### Evaluation datasets (`tests/eval/`)
| File | Used by | Size |
|---|---|---|
| `trigger_positive.jsonl` / `trigger_negative.jsonl` | T05 | ≥ 25 each (+ audio) |
| `tool_requests.jsonl` | T06, T12 | ≥ 60 |
| `classifier_eval.jsonl` | T09 | ≥ 150 |
| `retrieval_eval.jsonl` / `no_answer.jsonl` | T07 | ≥ 30 / ≥ 15 |
| `redteam.jsonl` / `in_scope.jsonl` | T13 | ≥ 40 / ≥ 30 |

Written by us from the pack and the scenario; expected outputs are **tool calls and sources**, not canned reply texts.

### Rehearsals
- **Rehearsal 1** (after M3): text replay green, then 3 humans on real Meet on the VM. Log every issue with a timestamp and a link to the spans/log.
- **Rehearsal 2** (after fixes): full scenario plus improvised questions not in the script (tests that nothing is hard-coded). Target: all 9 items pass, max latency ≤ 10 s, zero unsolicited messages.
- Run rehearsals against the **sandbox** Notion workspace. If a rehearsal has to use the provided workspace, archive its rows (all 4 DBs) before the live test, so the export (T17) contains only the live meeting, or clearly labelled rehearsal meetings if TechSol wants those too.

### Live-day runbook (`docs/runbook.md`)
1. T−60 min: VM up, `smoke_test.sh` green, models warm, Notion validation OK, provided Notion workspace clear of rehearsal rows, browser profile signed in (check via noVNC through the SSH tunnel), `config/people.yaml` has the expected attendees' display names.
2. T−15 min: ask the host (by message, before the meeting) to **keep participant chat enabled** in Meet's host controls, to **admit "TechSol BA"** from the lobby, and to say "Hey BA" clearly before each request.
3. T−10 min: `ba join <url> --title "Project Falcon …"`. Host admits "TechSol BA". Check the consent message (and the notes link in it), and `GET /status` shows `chat_enabled: true`.
4. During: watch `docker compose logs -f agent` and `GET /status` for errors (don't intervene in chat).
5. Failure playbook: bot dropped → it reconnects on its own; if not, `ba join` again (state resumes, no second consent notice). LLM stuck → `docker compose restart ollama` (warm-up runs). Notion errors → writes are queued and retried; confirm later. Chat disabled by the host → ask the host to re-enable it (the bot cannot work around it).
6. After: `ba leave` (or "Hey BA, save the meeting") → link posted → export Notion (T17). If the meeting ended before that, take the link from `GET /status` / logs and send it to the point of contact.

## Acceptance criteria

- [ ] Scenario file covers all 9 live-test items + distractors + edge prompts.
- [ ] Text replay passes all automatic checks in CI (with the LLM against Ollama on the VM or a GPU runner).
- [ ] Audio replay on the VM passes all checks.
- [ ] Two human rehearsals done. Rehearsal 2 passes all 9 items with max latency ≤ 10 s and zero unsolicited messages.
- [ ] Improvised questions in rehearsal 2 are answered correctly or with "not sure" (no hard-coding).
- [ ] Runbook written and tried once.

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T16-E1 | e2e (text replay) | Full scenario | All automatic checks pass |
| T16-E2 | e2e (audio replay, PulseAudio) | Full scenario with TTS voices | All checks pass; speaker accuracy ≥ 90% |
| T16-E3 | e2e (real Meet, actor browsers) | Full scenario | All checks pass |
| T16-E4 | e2e (idempotency) | Run E1 twice against sandbox Notion with the same meeting ID | No duplicate rows |
| T16-R1 | rehearsal (humans) | Rehearsal 1 | Issues logged and triaged |
| T16-R2 | rehearsal (humans) | Rehearsal 2 incl. improvised questions | 9/9 items; max latency ≤ 10 s; 0 unsolicited messages |

## Dependencies

- All Must tasks (T01–T15). Results feed T17 (README numbers, Notion export) and decide whether time allows T18.

## Related resources

- Piper TTS (multi-voice test audio): https://github.com/rhasspy/piper
- Chromium fake media capture flags: https://peter.sh/experiments/chromium-command-line-switches/
- pytest docs: https://docs.pytest.org/
- Playwright multiple browser contexts: https://playwright.dev/python/docs/browser-contexts
