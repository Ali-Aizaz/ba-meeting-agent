# T14 — Latency budget, instrumentation and tuning (≤ 10 s)

**Priority:** Must · **Milestone:** M4 Hardening · **Depends on:** T05, T12 · **Estimate:** 2 days

## Why

- "Meeting" row: **replies in chat within 10 s to "Hey BA …"** (C8).
- The README must answer **"latency budget"** (T17). This task produces the budget and the measured numbers.
- Everything runs on one 24 GB GPU (STT, embeddings, LLM, classifier). Without priorities and measurement, background work can push answers past 10 s in the live test.

## What

**In scope**
- End-to-end tracing of every trigger, from the end of the speaker's speech to the message appearing in chat.
- The latency budget per stage, with targets.
- Benchmark and report script, run on the target VM.
- Optimisations and a deadline fallback so a reply always appears within 10 s.

**Out of scope**
- Changing model choice by itself (T06 decides using these numbers).

## Details

### Clock definition
- **Start (t0):** end of the speaker's speech for the trigger utterance (`TriggerEvent.t_speech_end`, from the audio sample clock) **or** the chat message's timestamp.
- **End (t1):** the reply is visible in the Meet chat DOM (T02's `post_chat` confirmation).
- `latency = t1 − t0`. Each stage records a span (`observability/spans.py`) with `meeting_id`, `trigger_id`, `stage` and `duration_ms`. Spans go to the structured log and a SQLite table.

### Budget (targets on the VM; replace with measured values)

| # | Stage | Target p95 | Notes |
|---|---|---|---|
| 1 | End-of-utterance detection (VAD silence window) | 0.7 s | `vad.min_silence_ms` = 700 |
| 2 | Capture buffering + segment hand-off | 0.2 s | PulseAudio + frame queue |
| 3 | STT of the trigger utterance | 0.8 s | faster-whisper turbo/distil, fp16, beam 1 |
| 4 | Speaker tag + trigger detect + extraction | 0.05 s | T04/T05 |
| 4b | *Conditional:* split-question continuation wait | ≤ 1.5 s | Only for short fragments without end punctuation (T05). The ≤ 5 s pause wait after a bare "Hey BA" isn't counted, because t0 is the end of the question (T05) |
| 5 | Input guardrail | 0.05 s (rules) / 0.5 s (if LLM) | Prefer rules or fold into the main call |
| 6 | Context assembly (persona, digest, rolling summary, recent transcript) | 0.1 s | In-memory (T10) |
| 7 | LLM round 1: plan + tool choice | 1.5 s | ~5–9k prompt tokens (T10 context budget), of which ~2.5k is the cached persona/tools prefix; short output. **Measure on the VM.** This is the biggest uncertainty |
| 8 | Tool execution | 0.3 s (`search_knowledge`) · 0.1 s (BOM/compare/recall) · 1.0 s (Notion write) | T07 ≤ 300 ms, T10 ≤ 200 ms; Notion queue ≤ 3 req/s |
| 9 | LLM round 2: final prose (skipped for template fast paths) | 2.0 s | ≤ 150 output tokens |
| 10 | Output validator | 0.1 s | T13 |
| 11 | `post_chat` until visible | 0.5 s | T02 |
| | **Template fast path, read-only tool** (e.g. `build_bom`, `recall`; stage 9 skipped) | **≈ 4.1 s** | |
| | **Template fast path, Notion write** (e.g. `create_task`) | **≈ 5.0 s** | |
| | **Free-text answer** (e.g. RAG question: `search_knowledge` + round 2) | **≈ 6.3 s** | |
| | Any path + split-question wait (4b) | + up to 1.5 s (free-text ≈ 7.8 s) | Still inside the 8.5 s deadline |

The totals are the sums of stages 1–11 for each path, without 4b, and with stage 5 using the rules variant (0.05 s). Shared stages 1–7 + 10 + 11 = 4.0 s, plus the path's tool time (stage 8) and stage 9 if used.

**Regeneration rule:** a validator regeneration (T13) costs about 2 s, so it runs only if elapsed time is ≤ 6.0 s at that point. Otherwise go straight to the deadline fallback (template output or "not sure" wording). This keeps the worst planned case (free-text + Notion write + regeneration ≈ 9 s) from overrunning.

Targets: **p95 ≤ 8 s, max ≤ 10 s**, with the classifier running at the same time.

### Optimisations (in order of expected payoff)
1. **Keep models warm:** Ollama `keep_alive=-1` plus a warm-up call at start. Whisper and embedding models load once.
2. **Interactive lane pre-empts background** (T06): no classifier or summary call starts while an answer is in flight.
3. **Template fast paths** (T12): skip LLM round 2 for structured tools.
4. **Prompt-prefix caching:** keep persona + tool schemas byte-identical and first, so Ollama reuses the KV cache for the shared prefix. Put changing content (digest, question) last.
5. **Bound output:** `llm.max_tokens` 300 (interactive answers usually need ≤ 150).
6. **Bound rounds:** `llm.max_tool_rounds` = 3. Run independent tool calls in parallel.
7. **STT settings:** beam 1, fixed language, VAD silence 700 ms. Measure 500 ms and 900 ms trade-offs on trigger recall.
8. **Notion writes:** the confirmation waits for the write, but bulk work (transcript upload) happens only at `save_meeting`.
9. **Model size/quant:** if the 7B 4-bit p95 has headroom, try the higher-quality 8-bit or 14B (T06). If not, stay.

### Deadline fallback (guarantees a message ≤ 10 s)
- A timer starts at t0. At **`reply.deadline_s` (8.5 s)**, if the reply isn't posted:
  - If tool results exist: render them with the template and post (validator still runs; it's fast).
  - Otherwise: post one holding line, `Checking that — one moment.`, then post the answer when ready, as the single permitted follow-up. This is the only case where one trigger produces two messages (documented in T12).
- **Hard cap:** if the follow-up answer still isn't ready at `reply.hard_timeout_s` (20 s), or any stage raised, the orchestrator posts the honest failure line from T12 (`Sorry — I couldn't process that. Please try again.`) as the follow-up. A holding line is never left hanging.
- Every fallback and failure is logged and counted in the report. The goal is that fallbacks are rare (< 5% of triggers) and hard failures are 0 in rehearsal 2.

### Report (`scripts/latency_report.py`)
Reads spans and prints per-stage p50/p95/max, end-to-end p50/p95/max, the fallback count, and the slowest 5 triggers with their stage breakdown. Its output goes into the README latency answer.

## Acceptance criteria

- [ ] Every trigger has a complete span set from t0 to t1.
- [ ] On the VM, using the T16 replay (≥ 50 triggers of mixed types) with the classifier running: **p95 ≤ 8 s, max ≤ 10 s**.
- [ ] The deadline fallback is tested and fires at 8.5 s.
- [ ] The report output (table) is committed and quoted in the README.

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T14-U1 | unit | Span recorder: nesting, missing-end detection, SQLite write | Correct durations; incomplete spans flagged |
| T14-U2 | unit | Deadline fallback with a fake slow LLM (12 s) | Template output or holding line at 8.5 s; answer follows |
| T14-U4 | unit | Fake LLM that never returns | Holding line at 8.5 s; failure line at 20 s; nothing after |
| T14-U3 | unit | Report maths (percentiles) on fixture spans | Exact |
| T14-P1 | performance (VM) | 50+ trigger replay (BOM, compare, create_task, recall, user stories, general Q) | p95 ≤ 8 s; max ≤ 10 s |
| T14-P2 | performance (VM) | Same replay with the classifier forced to run continuously | Same thresholds |
| T14-P3 | performance (VM) | Cold start: first trigger right after `docker compose up` + warm-up | ≤ 10 s (warm-up finished before the bot joins) |
| T14-L1 | live rehearsal | Real Meet, spoken triggers, stopwatch spot-checks vs spans | Spans within ±0.5 s of observed |

## Dependencies

- T05 (t0), T02 (t1), T06 (lanes), T12 (orchestrator, fast paths, deadline hook), T13 (validator cost).
- Feeds T06 model choice, T16, T17.

## Related resources

- Ollama FAQ (keep-alive, parallel requests): https://docs.ollama.com/faq
- faster-whisper benchmarks and settings: https://github.com/SYSTRAN/faster-whisper#benchmark
- Python `time.perf_counter` / `time.monotonic`: https://docs.python.org/3/library/time.html
- OpenTelemetry Python (optional richer tracing): https://opentelemetry.io/docs/languages/python/
