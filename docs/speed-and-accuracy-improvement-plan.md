# TechSol BA — Speed, Accuracy & Reliability Improvement Plan (v2)

> **Baseline Reviewed**: Commit `ba3de19` (`v10`), `102 passed, 1 skipped` across the test suite.  
> **Scope**: Next-generation performance and accuracy roadmap building on top of the already-implemented v1 `docs/architecture-improvement-plan.md`.

---

## 1. Executive Summary & Baseline Assessment

### 1.1 What Is Already Working Well (v10 Baseline)
A full code and test audit confirms that **TechSol BA** has a clean, modular architecture and already incorporates several strong foundations:
- **Decoupled Async Event Loop** (`src/ba_agent/session.py`): `"Hey BA"` triggers run through an `asyncio.Queue` (`trigger_queue`), preventing LLM calls from blocking caption/chat polling.
- **WebRTC Media & CPU Discipline** (`src/ba_agent/bot/google_meet/scripts.py`, `video.py`): Outbound audio/video tracks are silenced and incoming video `m=video` SDP sections are rewritten to `a=inactive`, keeping Chromium viable on a 0.5-vCPU Cloudflare `standard-1` container.
- **Persistence & Crash Recovery** (`src/ba_agent/memory/store.py`): SQLite runs in `WAL` mode with composite indexes on captions/replies, canonical Meet URL matching, and cross-meeting history (`stored_context()`).
- **Safety & Privacy Guardrails** (`src/ba_agent/guardrails/`): Outbound web searches strip meeting text and participant names, untrusted prompt inputs use XML tags, and uncited numeric car claims trigger verification.
- **Zero-Friction Lifecycle** (`extension/`, `cloudflare/src/index.ts`): The Chrome extension auto-starts/switches meetings via `POST /join`, auto-admits **TechSol BA**, and renders live progress toasts.

### 1.2 High-Impact Opportunities Discovered
Despite those solid foundations, line-by-line profiling of the critical path (`speech/chat -> caption commit -> trigger -> context gather -> LLM/search -> Notion -> Meet chat`) revealed **6 major speed bottlenecks** and **7 high-impact response-accuracy gaps**:

| Dimension | Current Behavior (v10) | Target Behavior (After Plan) | Expected Gain |
|---|---|---|---|
| **Spoken `"Hey BA"` (Local KB)** | `3.3s – 6.6s` (up to `8.5s` on cold model fallback) | `1.2s – 2.0s` | **~65% faster** |
| **Spoken `"Hey BA"` (Web Search)** | `6.6s – 18.0s` (2–4 sequential LLM calls + sequential Notion queries) | `3.2s – 5.5s` (`<1.8s` on cached car follow-up) | **~55–70% faster** |
| **Meeting / Task Lookup (`"Hey BA"`)** | `3.3s – 6.5s` (`_save_live` wipes Notion summary cache every `1.5s`) | `1.3s – 2.2s` (hot cache + parallel refresh) | **~65% faster** |
| **Bare `"Hey BA"` Clarification** | `6.1s – 6.9s` (`800ms` stabilizer + fixed `5.0s` wait + `500ms` tick) | `2.6s – 3.0s` (caption-aware adaptive pause) | **~55% faster** |
| **Automotive Meeting Q&A Accuracy** | Meeting questions containing car terms (`AEB`, `brake`, `charging`) misroute to `car=True` and fail citation check -> false `"I'm not sure"` | Context-aware hybrid intent routing + grounded meeting citations | **Eliminates false `"not sure"`** |
| **Spoken Due-Date & Task Capture** | Ordinals (`"October 28th"`), `"tonight"`, `"EOD"`, `"end of week"`, `"in 3 days"` return `None` in `parse_due()` -> tasks silently dropped | Full spoken-English date parser + expanded task command grammar + alias normalization | **+35–50% recall on live spoken tasks** |
| **Local Knowledge Retrieval (RAG)** | Raw set-overlap ratio drops 1-term acronym queries (`"what does AEB stand for in modern cars?"` -> `0.25 < 0.5`) | BM25 + IDF weighting + acronym/synonym expansion + expanded automotive KB | **Higher precision & fewer slow web fallbacks** |
| **Cloudflare Container Idle Billing** | Extension `peek=1` calls `super.fetch` every `1–2.8s` even when `idle`, resetting `sleepAfter = "3h"` indefinitely | DO-cached status + zero `super.fetch` when idle + idle auto-stop | **Prevents runaway 4 GiB container hours** |

---

## 2. Diagnostic Deep Dive: Speed & Latency Bottlenecks

```mermaid
sequenceDiagram
    participant U as Participant (Meet)
    participant DOM as Meet DOM + Stabilizer
    participant Loop as Session / AgentLoop
    participant N as Notion API
    participant OAI as OpenAI Responses API

    U->>DOM: Speaks "Hey BA, what is the price of a RAV4 hybrid?"
    Note over DOM: 1. 300ms poll + 2-5 meeting_closed() IPCs + 800ms stable_ms (0.9s - 1.3s)
    DOM->>Loop: TriggerEvent queued
    Loop->>OAI: 2. search_cars() -> gpt-6-luna 404 -> retry gpt-4o-mini + web_search (3.5s - 7.5s)
    Note over Loop,N: 3. Sequential _full_digest() runs AFTER search_cars even on car=True!
    Loop->>N: POST /databases/{tasks}/query (350ms + 333ms rate limiter)
    Loop->>N: POST /databases/{summary}/query (cache wiped every 1.5s by _save_live!)
    Loop->>OAI: 4. Second LLM call with 4k-token digest + local_tools() (1.8s - 4.0s)
    opt Missing literal URL or tool call emitted
        Loop->>OAI: 5. Third / Fourth LLM call for tool or citation retry (2.0s - 4.0s)
    end
    Note over Loop,DOM: 6. post_chat() scans 8 overlay locators before fill() (200ms - 400ms)
    Loop->>DOM: Reply posted in Google Meet chat
```

### Root Causes Identified in Code

1. **Unconditional Notion & Cross-Meeting Fetch on Pure Car Questions (`src/ba_agent/agent/loop.py:252-254`)**
   - Line `252` checks `if not car and self.fetch_db_context is not None:` before emitting the `"Fetching stored data from database…"` toast, **but line `254` unconditionally awaits `self._full_digest()` anyway**—and **sequentially after** `await self.search_cars(question)` (`line 245`)!
   - Consequence: Every car question pays Notion HTTP latency AND bloats the synthesis prompt with up to ~4,000 tokens of unrelated tasks and past meeting transcripts, increasing OpenAI Time-To-First-Token (TTFT) and generation latency.

2. **`_save_live()` Destroys Notion Summary Cache Every `1.5s` (`src/ba_agent/notion/client.py:442`, `src/ba_agent/session.py:448, 563`)**
   - Every committed caption sets `notes_dirty.set()` (`session.py:448`). `live_notes()` wakes up `1.5s` later (`session.py:563`), calls `store.last(100000)` (`session.py:630`), and calls `notion.save_meeting(..., append_children=False)`.
   - Inside `save_meeting()`, line `442` executes `self._summaries_loaded_at = 0.0` unconditionally! During active speech, `_summaries_cache` is invalidated every ~2 seconds, causing a **near-100% cache miss rate** on `list_summaries()` AND contending on `NotionService._limiter` (`1/3`s interval) against user-facing `"Hey BA"` queries.

3. **Cold-Start `gpt-6-luna` 400/404 Retry & Unbounded Reasoning/Output Tokens (`src/ba_agent/llm/openai_client.py:79-109, 124`)**
   - When `llm.model` defaults to `"gpt-6-luna"` (`config/settings.yaml:23`, `cloudflare/src/index.ts:56`), the first live request sends `"gpt-6-luna"` to OpenAI, waits for a `400`/`404` error, and retries with `"gpt-4o-mini"`, adding `800ms–2,500ms` directly onto the user's first `"Hey BA"` question.
   - Furthermore, `search_cars()` (`line 124`) passes `self.model` instead of `self._fallback_model or self.model` to `build_search_request()`, has no TTL cache for repeated car model queries, sets no `max_output_tokens`, and sets no `reasoning` effort cap when reasoning-capable models (`gpt-5*` / `o*`) are configured.
   - `_public_web_search()` (`line 157`) instantiates and closes a fresh `httpx.AsyncClient` per call rather than pooling TLS connections.

4. **Sequential Notion `database_context()` & Short 30s TTL (`src/ba_agent/notion/client.py:315, 352, 392-401`)**
   - `database_context()` awaits `self.list_tasks()` and only afterward awaits `self.list_summaries()`. On cold/expired cache (every 30s), two HTTPS round-trips + `_Limiter(1/3)` add `700ms–1,500ms` serially.

5. **Caption Commit, Polling & Playwright IPC Overhead (`src/ba_agent/captions/stabilize.py:42, 107`, `src/ba_agent/session.py:483-536`, `src/ba_agent/bot/google_meet/adapter.py:488, 656`)**
   - When a speaker says `"Hey BA, what is a hybrid?"` and stops talking to wait for the answer, `split_completed_wake_sentence()` returns `None` (no trailing speech), forcing a full `800ms` wait in `Stabilizer` plus up to `300ms` poll delay.
   - `captions()` (`session.py:483`) runs `await adapter.meeting_closed()` every `300ms`, executing 2–5 Playwright locator `.count()` IPC round-trips before draining captions.
   - `post_chat()` (`adapter.py:488`) runs `await self._dismiss_incall_overlays()` (up to 8 Playwright locator `.count()` IPC calls) on **every** chat message, even when the chat textbox is already open and focused.
   - Bare `"Hey BA"` waits a fixed `5.0s` (`pause_wait_s`) + `500ms` (`ticks()`) even when `Stabilizer.open` shows the speaker stopped speaking `2s` ago.
   - `log_timing()` (`src/ba_agent/agent/loop.py:142`) still logs `caption_commit_ms=0`, hiding pre-dispatch latency.

6. **Unbounded `store.last(100000)` Deserialization (`src/ba_agent/memory/store.py:259`, `src/ba_agent/session.py:630`)**
   - `MeetingStore.digest()` deserializes all rows in the meeting (up to `100,000` Pydantic `CaptionLine` objects) only to keep the last ~60–120 rows fitting inside `max_tokens=3000`.

---

## 3. Diagnostic Deep Dive: Response Accuracy & Grounding Gaps

### 3.1 Car-Term Collision in Automotive Meeting Questions (`policy.py` + `loop.py`)
- **Where**: `src/ba_agent/knowledge/policy.py:232-323` & `src/ba_agent/agent/loop.py:239-304`.
- **Bug**: Because TechSol BA assists **automotive engineering meetings**, nearly every meeting decision and task contains words in `_CAR_WORDS` (`brake`, `AEB`, `LFP`, `NMC`, `hybrid`, `charging`, `inverter`, `NCAP`, `RAV4`, `suspension`).
- Any meeting/task question whose phrasing falls outside the fixed regex list in `_MEETING_PATTERNS`—for example:
  - `"Hey BA, has Omar finished the brake pad shortlist?"` (`_MEETING_PATTERNS` has `did <name> finish`, not `has <name> finished`)
  - `"Hey BA, what target did we set for DC fast charging?"` (`what target did we` misses `what did we`)
  - `"Hey BA, what was the budget for AEB radar calibration?"`
  - `"Hey BA, update me on Nadia's Euro NCAP sensor work"`
  is classified as `car = True`!
- **Impact**: Once `car = True`, `_answer()` enforces `reply_status(first.text)` (`guardrails/citations.py:28`), which **rejects any reply lacking `(src: *.md)` or `https://...`**. Because meeting facts come from `<meeting_digest>` (not a `.md` file or URL), the bot either regenerates and **returns `"I'm not sure — I don't have a source for that."`** for a fact sitting right in its own meeting notes, or hallucinates a bogus `(src: safety.md)` tag!

### 3.2 Spoken Due-Date Failures Dropping Tasks (`src/ba_agent/tasks_extract/dates.py:71-98`)
- **Where**: `parse_due()` in `src/ba_agent/tasks_extract/dates.py`.
- **Bug**:
  1. **Ordinal dates fail `\b` regex matching**: `rf"\b({_MONTH_PATTERN})\s+(\d{{1,2}})\b"` fails on `"October 28th"`, `"Nov 2nd"`, and `"24th of October"` because `8` and `t` in `28th` are both word characters (`\w`), so there is no word boundary after `28`! Google Meet captions almost always transcribe spoken dates with ordinals (`"October 28th"`).
  2. **Cue/Parser mismatch**: `might_contain_task()` (`extractor.py:21`) triggers LLM calls on `"tonight"` and `"next week"`, yet `parse_due()` returns `None` for `"tonight"`, `"next week"`, `"EOD"` / `"end of day"`, `"EOW"` / `"end of week"`, `"this Friday"`, and `"in 3 days"` / `"in two days"`.
- **Impact**: Overheard tasks with `due is None` are **silently discarded** (`extractor.py:169`: `if overheard and due is None: continue`), and direct `"Hey BA"` task requests needlessly ask `"When is that due?"` right after the user already stated `"by October 28th"` or `"by EOD"`.

### 3.3 Substring Collisions in Off-Topic & Digest Ranking (`src/ba_agent/agent/loop.py:889, 914`)
- **Where**: `_is_off_topic()` (`line 889`) and `_rank_lines()` (`line 914`).
- **Bug**: Both use raw substring `kw in digest_low` / `kw in low` for keywords of length `>= 3` without word boundaries.
  - Example: `"What should I eat tonight?"` -> keyword `"eat"` matches inside `"cold-weather"`, `"seatbelts"`, or `"create"` in the digest, bypassing `_is_off_topic()`!
  - Example: Short words like `"use"`, `"end"`, `"out"`, `"car"`, `"ten"` match inside unrelated words (`"send"`, `"start"`, `"latency"`), corrupting fallback line ranking.

### 3.4 Task Status Vocabulary Mismatch & False-Empty Override (`src/ba_agent/agent/loop.py:692-708, 840, 916-919`)
- **Bug 1 (`[Not started]` ignored)**: `config/notion_schema.yaml:4` and `TaskRecord.status` default to `"Not started"`, rendering `[Not started]` in `database_context()`. Yet `_rank_lines()` (`loop.py:916`) checks `if want_open and ("[todo]" in low or "[in progress]" in low):`—omitting `"[not started]"` and failing to filter out `[done]` items when the user specifically asks for **open/pending** tasks!
- **Bug 2 (`store.digest()` drops status & ISO date)**: `MeetingStore.digest()` (`store.py:266`) formats current-meeting tasks as `- Ali: cost sheet (Fri)` without `[status]` or ISO date `2026-10-30`.
- **Bug 3 (False-empty override dumps wrong tasks)**: In `_resolve_meeting_reply()` (`loop.py:700-707`), if a user asks `"What tasks are assigned to Bob?"` (who has none) and the LLM accurately says `"There are no tasks assigned to Bob"`, `"no tasks"` triggers `_answer_from_digest()`, whose line `840` (`chosen = matched_tasks[:8] if matched_tasks else task_lines[:8]`) dumps Ali's and Sara's tasks when `matched_tasks` is empty!

### 3.5 Lexical RAG Blind Spots on Acronyms & Multi-Word Queries (`src/ba_agent/knowledge/search.py:183-194`)
- **Bug**: `_score()` computes unweighted `len(overlap) / len(query)`. A natural 4-token query with one high-signal automotive acronym—e.g. `"what does AEB stand for in modern cars?"` (`query = {"aeb", "stand", "modern", "car"}`) against `safety.md`'s `## ADAS` chunk (`overlap = {"aeb"}`)—scores `1 / 4 = 0.25 < 0.5` (`min_score`) and returns **zero hits**, even though `safety.md` explicitly defines `Automatic Emergency Braking (AEB)`!
- Additionally, `data/knowledge/` has only 59 lines across 5 files, forcing slow web searches for common automotive engineering concepts (WLTP vs EPA, 400V vs 800V, heat pumps, NVH, BOM/Tier-1 sourcing).

### 3.6 Unverified Citations & Unchecked Numeric Claims (`src/ba_agent/guardrails/citations.py`)
- `has_citation()` accepts any regex match `(src: anything)` or `https://...`, even if the cited file was not in `hits` or the number in the reply does not appear anywhere in `hits` or `web.text`. `_UNITS` also misses `kw`, `mi`, `miles`, `nm`, `lb-ft`, `%`, `v`, `volts`, `min`, `minutes`.

### 3.7 Interim ASR First-Word Revision Splitting Captions (`src/ba_agent/captions/stabilize.py:165-176`)
- `_same_utterance()` only returns `True` if one string starts with the other or `prev_words[0] == new_words[0]`. When Google Meet ASR revises the first word of a live caption (`"Think we should use LFP"` -> `"I think we should use LFP"`), `_same_utterance()` returns `False` and prematurely commits the partial fragment, creating duplicate transcript lines and noisy task checks.

---

## 4. File-by-File Change Matrix

| Priority | File | Category | Key Symbols Modified / Added | Expected Impact |
|---|---|---|---|---|
| **P0** | `src/ba_agent/agent/loop.py` | Speed & Accuracy | `AgentLoop._answer`, `_dispatch`, `_full_digest`, `_prompt`, `_is_off_topic`, `_rank_lines`, `_answer_from_digest`, `_resolve_meeting_reply` | Skip Notion fetch on pure car queries; parallelize `search_cars` + `_full_digest` when both needed; hybrid car+meeting citation support; word-boundary matching; fix `[Not started]`/`[Done]` filtering; fix person-specific empty task lookup; log real `caption_commit_ms` + stage timings. |
| **P0** | `src/ba_agent/knowledge/policy.py` | Accuracy | `is_meeting_question`, `classify_query_intent` (new), `should_search_web` | Prevent automotive meeting/task questions (`has Omar finished...`, `what target did we set...`, participant-named queries) from misrouting into `car`-only mode. |
| **P0** | `src/ba_agent/knowledge/search.py` & `data/knowledge/*.md` | Accuracy & Speed | `KnowledgeIndex`, `_bm25_score` (new), `_SYNONYMS` | Upgrade unweighted overlap to BM25 + IDF rare-term boost + heading/acronym boost so 1-acronym queries (`AEB`, `ESC`, `LFP`, `PHEV`) hit `>= 0.5`; enrich car engineering notes to avoid unnecessary web round-trips. |
| **P0** | `src/ba_agent/tasks_extract/dates.py` & `extractor.py` | Accuracy | `parse_due`, `is_task_request`, `parse_task_command`, `tasks_from_model_json` | Parse ordinal dates (`Oct 28th`, `24th of Oct`), `tonight`, `EOD`, `EOW`, `this <day>`, `next week`, `in N days`; recognize `assign / log / track / action item`; map `"I"/"me"` to `said_by` + speaker aliases. |
| **P0** | `src/ba_agent/notion/client.py` | Speed & Accuracy | `NotionService.database_context`, `list_tasks`, `list_summaries`, `save_meeting`, `_transcript_toggle` | Stop resetting `_summaries_loaded_at = 0.0` on live `append_children=False` syncs; run `list_tasks` & `list_summaries` via `asyncio.gather`; add stale-while-revalidate (180s TTL); sort summaries by `Date` desc; expand transcript toggle capacity beyond 80 lines. |
| **P1** | `src/ba_agent/llm/openai_client.py` & `config.py` | Speed & Cost | `OpenAIResponsesClient.respond`, `search_cars`, `warm_up` (new), `_web_cache` | Pre-warm HTTP/2 TLS + resolve fallback model during `join()` off the critical path; add `max_output_tokens` & low-reasoning controls; add 10-min TTL LRU cache on `search_cars`; reuse pooled `httpx.AsyncClient` for web fallback. |
| **P1** | `src/ba_agent/captions/stabilize.py` & `trigger/detector.py` | Speed & Accuracy | `Stabilizer.push_frame`, `has_open_speaker` (new), `_same_utterance`, `TriggerDetector.tick` | Fast-commit punctuated wake questions at `450ms` instead of `800ms`; adaptive `2.2s` bare `"Hey BA"` response when speaker has no open live caption; token-overlap check in `_same_utterance` to prevent mid-sentence split duplicates. |
| **P1** | `src/ba_agent/session.py` & `bot/google_meet/adapter.py` | Speed & Reliability | `_loops`, `live_notes`, `drain_poll_state` (new), `post_chat` | Debounce `live_notes` from `1.5s` to `15s`; poll captions/chat at `150ms` via combined JS drain; move `meeting_closed()` off the 150ms hot path; skip 8-locator `_dismiss_incall_overlays()` in `post_chat()` when chat input is already visible; keep `extractor.recent` populated after wake turns. |
| **P1** | `src/ba_agent/guardrails/citations.py` | Accuracy | `has_precise_number`, `verify_grounded_citation` (new), `attach_verified_citation` (new) | Expand automotive unit detection; verify cited `(src: ...)` / URLs belong to retrieved context; deterministically attach single unambiguous source when all numbers are grounded in retrieved text (saving a 2–4s regeneration LLM call). |
| **P2** | `src/ba_agent/memory/store.py` | Speed & Accuracy | `MeetingStore._init`, `digest`, `stored_context` | Bound `digest()` query to `last(400)` instead of `last(100000)`; include `[status]` and ISO due date in current-meeting `digest()` task lines; add index for cross-meeting captions. |
| **P2** | `cloudflare/src/index.ts` & `wrangler.jsonc` | Cost & Edge Speed | `BaContainer.fetch`, `handleJoin`, `wrangler.jsonc` | Cache `peek=1` status for `1.5s` in DO memory; never call `super.fetch` on `peek=1` when stage is `idle` so `sleepAfter = "3h"` can actually fire; auto-stop idle containers after `25m` outside active calls; add `nodejs_compat`. |

---

## 5. Detailed Phased Implementation Plan

### Phase 1: Critical-Path LLM & Context Speed Optimizations

#### 1.1 Eliminate Redundant Notion Calls & Parallelize Context Gathering (`src/ba_agent/agent/loop.py`)
- **Current issue**: In `AgentLoop._answer()` (`lines 235-265`), `await self.search_cars(question)` runs first, and `await self._full_digest()` runs afterward on **every** question—even pure car questions like `"what is a hybrid?"` or `"what is the current price of a Toyota RAV4 hybrid?"`.
- **Implementation**:
  1. Introduce `classify_query_intent(question, hits, participants)` returning `"car"`, `"meeting"`, or `"hybrid"` (e.g., `"compare the RAV4 hybrid price with the budget we discussed"` is `"hybrid"`).
  2. When intent is pure `"car"`:
     - Do **not** call `fetch_db_context()` (zero Notion HTTP calls) unless both `hits` and `web` come back empty (`line 255` fallback check).
     - Pass a compact `store.digest(max_tokens=600)` (or omit unrelated task lists) so synthesis prompt size stays under ~600 tokens instead of ~4,500 tokens.
  3. When intent is `"hybrid"` (needs both `search_cars` and `_full_digest()`):
     - Execute both concurrently via `web, full_digest = await asyncio.gather(self._safe_search_cars(question), self._full_digest())`.
  4. When `web` was already fetched (`web is not None and web.useful`) and the query is not a task/save command, pass `tools=None` into `self.llm.respond()` so the model synthesizes immediately in one pass without parsing 4 function schemas or re-invoking `search_cars`.

#### 1.2 Connection Pre-Warming, Token Caps & Search Caching (`src/ba_agent/llm/openai_client.py`)
- **Current issue**:
  - Cold `gpt-6-luna` 400/404 fallback happens on the user's first `"Hey BA"` turn (`lines 79-109`).
  - Note: `tests/test_t01_config.py:37` explicitly asserts `settings.llm.model == "gpt-6-luna"`, so YAML defaults must remain intact!
- **Implementation**:
  1. Add `async def warm_up(self) -> None` on `OpenAIResponsesClient`: launched as a non-blocking background task in `_run_single_meeting()` **while Playwright is navigating/joining Meet**. It establishes the HTTP/2 keep-alive TLS socket to `api.openai.com` and probes/resolves `_fallback_model` before any participant speaks—making first-turn fallback overhead **0ms** while keeping `test_t01_u1_yaml_defaults` 100% green.
  2. Fix `search_cars()` (`line 124`) to pass `chosen_model = self._fallback_model or self.model` to `build_search_request()`.
  3. Add optional `max_output_tokens: int | None = None` to `OpenAIResponsesClient.respond()` (using `inspect.signature` compatibility or `**kwargs` guard in `AgentLoop` so lightweight `FakeLLM` test doubles in `tests/` never break!):
     - `"Hey BA"` chat replies: `max_output_tokens = 260`
     - Overheard task JSON extraction: `max_output_tokens = 180`
     - Meeting summary: `max_output_tokens = 320`
  4. Add an in-memory TTL cache `_web_cache: dict[str, tuple[float, WebResult]]` (TTL = `600s`, max 64 entries, keyed on normalized lowercase car query) in `OpenAIResponsesClient.search_cars()`. Follow-up questions on the same vehicle return in **<1ms**.
  5. Maintain a shared lazy `self._web_http: httpx.AsyncClient` on `OpenAIResponsesClient` (closed via `aclose()`) instead of creating a new `httpx.AsyncClient` on every `_public_web_search()` fallback (`line 157`).

---

### Phase 2: Caption, Wake-Trigger & Playwright UI Latency Reduction

#### 2.1 Early Commit for Punctuated / Complete Wake Questions (`src/ba_agent/captions/stabilize.py`)
- **Current issue**: `Stabilizer` (`lines 72-109`) commits immediately via `split_completed_wake_sentence()` *only* when trailing speech follows the wake sentence. When a user asks `"Hey BA, what is a hybrid?"` and pauses for the bot's reply, `Stabilizer` waits the full `800ms`.
- **Implementation**:
  - Add `wake_stable_ms: int | None = None` to `Stabilizer.__init__` (defaulting to `min(stable_ms, 450)` when `stable_ms >= 600`, while respecting `stable_ms=0` in tests).
  - In `push_frame()` (`line 106`), if an open row `current.text` matches `match_wake(current.text)` with `>= 2` question words and ends with `?` or `.` (or has `>= 3` question words), commit after `wake_stable_s` (`450ms`) instead of `800ms`. All existing `test_t03_*` unit tests use non-wake sentences (`"Let's use the hybrid"`) or explicit timestamps (`0.0 -> 0.3 -> 1.5`) and remain 100% unchanged.

#### 2.2 Live-Speech-Aware Pause Window for Bare `"Hey BA"` (`src/ba_agent/trigger/detector.py` & `stabilize.py`)
- **Current issue**: Saying `"Hey BA"` alone waits `5.0s` (`pause_wait_s`) before asking `"Yes — what would you like me to do?"`.
- **Implementation**:
  - Expose `Stabilizer.has_open_row_for(speaker: str) -> bool`.
  - Add optional `active_speech_Wait` / `is_speaker_active: Callable[[str], bool] | None = None` to `TriggerDetector`:
    - When `is_speaker_active` is provided (live session) and `is_speaker_active(pending.speaker)` is `False` (no uncommitted caption words on screen for that speaker), allow expiring a bare `"Hey BA"` after `fast_pause_wait_s = 2.2s` instead of `5.0s`.
    - If `is_speaker_active(pending.speaker)` is `True` (the user is actively speaking the second half of their question!), keep waiting up to the full `self.pause_wait_s` (`5.0s`).
    - When `is_speaker_active` is `None` (unit tests `test_t05_u3` / `test_t05_u4`), `TriggerDetector` uses `self.pause_wait_s` (`5.0s`) identically to today.

#### 2.3 Combined Browser Poll & Fast-Path `post_chat()` (`src/ba_agent/bot/google_meet/adapter.py` & `session.py`)
- **Current issue**:
  1. `captions()` (`session.py:483`) runs `await adapter.meeting_closed()` (2–5 Playwright locator IPCs) every `300ms`, and `chats()` runs a separate `collect_chat()` loop every `300ms`.
  2. `post_chat()` (`adapter.py:488`) scans 8 overlay locators via `_dismiss_incall_overlays()` before every message.
  3. `log_timing()` (`agent/loop.py:142`) hardcodes `caption_commit_ms=0`.
- **Implementation**:
  1. Move `await adapter.meeting_closed()` into the `1.0s` `alone()` health loop (or check an in-page JS flag `window.__baPollAll()` returning `{captions, chats, ended}` in a **single** `page.evaluate()` IPC round-trip every `150ms`), cutting browser IPC traffic by **>75%**.
  2. In `GoogleMeetAdapter.post_chat()`, check `box = self._chat_box()` **first**: if `await box.count() > 0`, click/fill/Enter immediately (`~60–100ms`); only call `_dismiss_incall_overlays()` and chat-drawer opener if the fast path finds `count() == 0` or raises an overlay intercept error.
  3. Add `t_committed_perf: float` (default `0.0`) on `TriggerEvent` (`src/ba_agent/models.py`) and record actual `(started - event.t_committed_perf) * 1000` + `caption_commit_ms` in `log_timing()`.

---

### Phase 3: Notion & SQLite Data-Layer Speed & Concurrency

#### 3.1 Fix `_save_live()` Cache Thrashing & Debounce Live Sync (`src/ba_agent/notion/client.py` & `session.py`)
- **Current issue**:
  - `NotionService.save_meeting()` (`client.py:442`) resets `self._summaries_loaded_at = 0.0` even when `append_children=False` (live background sync).
  - `live_notes()` (`session.py:563`) fires `1.5s` after every caption.
- **Implementation**:
  1. In `NotionService.save_meeting()` (`client.py:442`), **do not reset `self._summaries_loaded_at = 0.0` when `not append_children`**, because `list_summaries()` already skips the current in-progress `self.summary_page_id` (or we already hold the current meeting's live transcript in `MeetingStore`!). Even on final save (`append_children=True`), update `self._summaries_cache` in-place rather than forcing a network re-query.
  2. Increase `live_notes()` debounce in `src/ba_agent/session.py:563` from `1.5s` to `15.0s` (configurable via `notion.live_sync_interval_s: float = 15.0`), and fetch `store.last(1500)` instead of `store.last(100000)` since `save_meeting()` truncates the `Transcript` property note at `1,800` chars (`client.py:433`)!

#### 3.2 Parallel `database_context()`, Stale-While-Revalidate & Sorted Summaries (`src/ba_agent/notion/client.py`)
- **Implementation**:
  1. In `NotionService.database_context()` (`lines 384-415`), fetch tasks and summaries **in parallel**:
     ```python
     tasks, summaries = await asyncio.gather(
         self.list_tasks(refresh=refresh),
         self.list_summaries(refresh=refresh, limit=max_summaries),
     )
     ```
  2. Warm the Notion cache once at meeting start (`asyncio.create_task(notion.database_context(refresh=True))` right after `notion.verify()` in `_run_single_meeting()`), extend fresh TTL from `30s` to `120s`, and serve cached results in **0ms** while triggering a background refresh when `120s <= age < 600s` (stale-while-revalidate).
  3. In `list_summaries()` (`line 357`), pass `"sorts": [{"timestamp": "created_time", "direction": "descending"}]` so the most recent past meetings are always returned first, and raise per-summary transcript excerpt cap from `600` chars to `1,200` chars (`line 408`) so late-meeting decisions (`[02:30]+`) are never clipped.
  4. In `_transcript_toggle()` (`lines 509-523`), group lines into multi-line paragraph blocks (`<= 1,800` chars per paragraph, up to `95` blocks per toggle) instead of 1 caption per block capped at 80 lines! That stores **800–1,500+ caption lines** inside a single Notion toggle block instead of truncating after 80 lines!

#### 3.3 Bounded SQLite `digest()` & Enhanced Task Formatting (`src/ba_agent/memory/store.py`)
- **Implementation**:
  1. In `MeetingStore.digest(max_tokens=3000)` (`line 259`), replace `self.last(100000)` with `self.last(min(1000, max(120, max_tokens // 4)))`.
  2. Format current-meeting tasks in `digest()` (`line 266`) consistently with `stored_context()` and `_STORED_TASK_LINE`:
     ```python
     due_str = task.due.isoformat() if task.due else (task.due_text or "unscheduled")
     status_str = f" [{task.status}]" if task.status else ""
     f"- {task.owner}: {task.task} (due: {due_str}){status_str}"
     ```
  3. Add `CREATE INDEX IF NOT EXISTS idx_captions_wall_time ON captions (wall_time DESC, start_s DESC)` in `MeetingStore._init()` to accelerate `stored_context()`.

---

### Phase 4: Response Accuracy, Intent Classification, BM25 RAG & Grounding

#### 4.1 Context-Aware Intent Routing (`src/ba_agent/knowledge/policy.py` & `src/ba_agent/agent/loop.py`)
- **Problem**: Automotive terms inside meeting/task questions (`"has Omar finished the brake pad shortlist?"`, `"what target did we set for DC fast charging?"`, `"what was the budget for AEB radar calibration?"`) misclassify as `car=True` and get blocked by `reply_status()` when no `.md` or URL is cited.
- **Implementation**:
  1. Expand `_MEETING_PATTERNS` in `src/ba_agent/knowledge/policy.py` to cover:
     - Perfect/progressive auxiliaries with speakers: `\b(?:did|has|have|is|was|were)\s+[a-z]+\s+(?:finish|finished|complete|completed|send|sent|prepare|prepared|review|reviewed|update|updated|schedule|scheduled|request|requested|validate|validated|finalize|finalized|working|assigned)\b`
     - Intervening nouns in first-person plural meeting queries: `\bwhat\s+(?:[\w-]+\s+){0,3}(?:did|have|do)\s+we\s+(?:set|decide|agree|say|discuss|pick|choose|target|lock|plan|budget)\b`
     - Project/BA artifacts: `\b(?:budget|shortlist|procurement|tier-1|supplier|deliverable|milestone|blocker|timeline|kickoff|benchmark\s+sync|action\s+item|lab\s+slot|slide\s+deck)\b`
  2. Pass known `participants: list[str] | None = None` (including task owners from digest) into `classify_query_intent(question, hits, participants)`: if a non-brand participant name (`Omar`, `Nadia`, `Sara`, `Ali`) appears in the question, route to `"meeting"` (or `"hybrid"` if explicitly comparing with public market prices/specs).
  3. For `"hybrid"` questions (both car facts and meeting notes), allow grounded meeting facts while requiring `(src: <file>.md)` or `https://...` on external vehicle claims.

#### 4.2 Upgrade `KnowledgeIndex` to BM25 + Acronym/Synonym Boost (`src/ba_agent/knowledge/search.py` & `data/knowledge/*.md`)
- **Problem**: `len(overlap) / len(query)` scores `"what does AEB stand for in modern cars?"` as `0.25 < 0.5` (`[]` hits).
- **Implementation**:
  1. Precompute per-chunk token frequencies (`tf`), chunk lengths, average chunk length (`avgdl`), and corpus Inverse Document Frequency (`idf(t) = log(1 + (N - df + 0.5) / (df + 0.5))`) at `KnowledgeIndex.__init__` time (zero external dependencies; pure Python stdlib `math.log`, `<1ms` for hundreds of chunks).
  2. Normalize BM25 + IDF-weighted coverage onto `[0.0, 1.0]` so `min_score = 0.5` semantics are preserved:
     - High-IDF domain acronyms/terms (`aeb`, `lka`, `acc`, `bsm`, `esc`, `abs`, `ncap`, `adas`, `lfp`, `nmc`, `bms`, `phev`, `mhev`, `hev`, `bev`, `unibody`, `monocoque`, `macpherson`, `pretensioner`, `crumple`, `regenerative`, `inverter`) carry high IDF weight, so matching an exact defined technical acronym in a chunk scores `>= 0.65` regardless of conversational filler words in the question.
     - Add domain synonym/acronym expansion map (`"ev" <-> "electric vehicle"`, `"bev" <-> "battery electric"`, `"phev" <-> "plug in hybrid"`, `"mhev" <-> "mild hybrid"`, `"aeb" <-> "emergency braking"`, `"lka" <-> "lane keeping"`).
     - Add bigram phrase bonus (`+0.10`) when consecutive query tokens appear adjacent in the chunk or heading.
  3. Expand `data/knowledge/` Markdown notes (keeping `"hey ba"` absent per `test_knowledge_files_have_no_wake_table`) with concise, factual sections on:
     - `ev.md`: 400V vs 800V architectures, thermal management & heat pumps, WLTP vs EPA range testing.
     - `hybrid.md`: Series vs parallel vs power-split (e-CVT) hybrids.
     - `safety.md`: IIHS & NHTSA vs Euro NCAP, sensor fusion (camera + radar + ultrasonic).
     - `body-and-chassis.md`: NVH (noise, vibration, harshness), skateboard EV platforms, brake-by-wire.

#### 4.3 Source-Verified Citations & Deterministic Citation Attachment (`src/ba_agent/guardrails/citations.py` & `loop.py`)
- **Implementation**:
  1. Expand `_UNITS` in `src/ba_agent/guardrails/citations.py` to include `kw`, `mi`, `miles`, `nm`, `lb-ft`, `lbs`, `v`, `volts`, `min`, `minutes`, `sec`, `seconds`, `%` (keeping `reply_status` and `finalize_reply` 100% backward-compatible with `tests/test_t13_guardrails.py`).
  2. Add `verify_reply_sources(text, allowed_files, allowed_urls) -> bool` in `citations.py`: checks that every `(src: X)` in `text` references a file actually present in `hits` (or embedded source URLs in `hits`), preventing hallucinated `(src: fake.md)` tags.
  3. Add `numbers_grounded_in_context(reply_text, context_text) -> bool`: extracts numeric tokens from `reply_text` and checks whether they appear in `notes + web_text`.
     - **Fast path (saves 2–4s LLM retry)**: If `reply_status(first.text) == "regenerate"`, **and** all numbers in `first.text` are grounded in a single retrieved source (`len(hits) == 1` or `web.urls`), deterministically append `(src: {hits[0].file})` or `({web.urls[0]})` instead of making a second OpenAI API call!
     - **Safety path**: If `first.text` contains an **ungrounded number** (a price/spec not in `hits` or `web_text`), force regeneration with strict grounding instructions or return `NOT_SURE`.

#### 4.4 Word-Boundary Matching & Accurate Digest Answers (`src/ba_agent/agent/loop.py`)
- **Implementation**:
  1. Replace substring checks (`kw in digest_low` / `kw in low`) in `_is_off_topic()` (`line 889`) and `_rank_lines()` (`line 914`) with word-boundary regex / token-prefix matching (`re.search(rf"\b{re.escape(kw)}", text)`).
  2. Fix `_rank_lines()` (`lines 896-922`):
     - Include `"[not started]"` alongside `"[todo]"` and `"[in progress]"` when `want_open` is `True`.
     - When `want_open` is `True` and `not want_done`, **exclude** lines containing `"[done]"` or `"[completed]"`; when `want_done` is `True` and `not want_open`, **exclude** non-done lines.
  3. Fix `_answer_from_digest()` (`line 840`) and `_resolve_meeting_reply()` (`lines 692-708`):
     - If the question asks about a specific person/keyword (`keywords` non-empty) and `matched_tasks` is empty, return `"No recorded tasks matched that query."` rather than dumping unrelated people's tasks (`task_lines[:8]`), and do **not** override an LLM reply that specifically names the queried person (`"There are no tasks assigned to Bob"`).

---

### Phase 5: Task Extraction, Spoken Date Parsing & Transcript Deduplication

#### 5.1 Full Spoken-English Due-Date Parser (`src/ba_agent/tasks_extract/dates.py`)
- **Implementation**: Upgrade `parse_due(text, *, today)` while keeping all existing rules in `test_t09_u1_dates` intact:
  1. **Ordinal day suffixes & `"of"` connector**: Support `"October 28th"`, `"Nov 2nd"`, `"1st May"`, `"24th of October"`, `"the 30th of Oct"` via `r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_PATTERN})\b"` and `r"\b({_MONTH_PATTERN})\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b"`.
  2. **Same-day / EOD cues**: `"tonight"`, `"this evening"`, `"end of day"`, `"by eod"`, `"cob"` -> `today`.
  3. **End-of-week / Next-week cues**:
     - `"end of (the )?week"`, `"by eow"`, `"this week"` -> upcoming Friday (`_weekday(today, 4, following_week=False)`).
     - `"this <weekday>"` -> `_weekday(today, target, following_week=False)`.
     - `"next week"` (when no specific weekday follows) -> next Monday (`_weekday(today, 0, following_week=True)`).
  4. **Relative day/week offsets**: `"in (\d+|one|two|three|four|five|six|seven) days?"` -> `today + timedelta(days=n)`; `"in (\d+|a|one|two) weeks?"` -> `today + timedelta(weeks=n)`.

#### 5.2 Broader Direct Task Grammar, Pronoun Resolution & Alias Normalization (`extractor.py` & `session.py`)
- **Implementation**:
  1. Expand `_TASK_COMMAND` (`src/ba_agent/tasks_extract/extractor.py:13-16`) to match:
     - `add/create/log/track/record/note (a )?(new )?(task|action item|to-do|todo)`
     - `assign <owner> to <task>` / `assign a task to/for <owner> to <task>`
  2. Normalize task owners in `tasks_from_model_json()`, `parse_task_command()`, and `fallback_overheard_tasks()`:
     - Map first-person pronouns (`"i"`, `"me"`, `"myself"`) to `said_by`.
     - Apply `normalize_speaker(owner, aliases)` (`config/people.yaml`) so `"Ali K."` and `"Ali"` share the exact same `dedup_key` and task owner string.
  3. In `session.py:474-479` (`on_caption`), always append non-addressed speech to `extractor.recent` (even within `6s` of a `"Hey BA"` line, without triggering an LLM call unless `since_trig > 6.0` or `parse_due` matches) so 2-turn overheard commitments (`"Ali, can you send the spec?"` -> `"Sure, by Friday"`) never lose their referent.
  4. In `_Extractor.run()` (`session.py:795`), pass the recent multi-line speaker window to `fallback_overheard_tasks()` if single-line fallback finds a bare deadline (`"I'll do it by Friday"`) whose task description was on the immediately preceding line.

#### 5.3 Fix Mid-Sentence ASR Revision Splits (`src/ba_agent/captions/stabilize.py:165-176`)
- **Implementation**:
  - Update `_same_utterance(previous: str, new: str) -> bool`: in addition to prefix matching and first-word equality, return `True` if both strings have `>= 2` normalized words and their normalized word-set overlap `len(set(prev_words) & set(new_words)) / min(len(set(prev_words)), len(set(new_words))) >= 0.6` (unless `previous` ended with terminal sentence punctuation and `new` does not contain `previous`). This prevents Google Meet ASR's leading-word corrections (`"Think we..."` -> `"I think we..."`) from emitting duplicate partial captions.

---

### Phase 6: Cloudflare Container Idle Sleep & Extension Polling Efficiency

#### 6.1 Prevent `peek=1` from Blocking Container Sleep (`cloudflare/src/index.ts` & `wrangler.jsonc`)
- **Current issue**: In `BaContainer.fetch()` (`cloudflare/src/index.ts:164-204`), every `GET /status?peek=1` request from the Chrome extension (`every 1.0–2.8s`) calls `super.fetch(new Request("http://localhost/status"))` whenever `isRunning` is `true`—even after the call has ended and Python's status is `stage: "idle"`. Calling `super.fetch()` resets `@cloudflare/containers`'s `sleepAfter = "3h"` timer, keeping the 4 GiB container billed as long as any browser tab or popup remains open!
- **Implementation**:
  1. Cache the last `/status` payload inside `BaContainer` memory for `1,500ms` (`lastStatusCache = { at, payload }`) so multiple tabs + popup polling concurrently share a single container localhost read.
  2. Track `idleSince: number | null` on `BaContainer`:
     - Once the container transitions from `"in_call"` / `"leaving"` to `"idle"` (`payload.idle === true`), cache the idle state in `BaContainer` and return the cached idle JSON on subsequent `peek=1` requests **without calling `super.fetch()`** unless a new `POST /join` arrives!
     - If `idleSince` exceeds `25 minutes` (or via Durable Object `alarm()`), proactively call `this.stop("SIGTERM")` so a finished 15-minute meeting consumes ~0.7 hours instead of burning 3 full hours (12 GiB-hours) of the 25 GiB-hour monthly allowance!
  3. Add `"compatibility_flags": ["nodejs_compat"]` to `wrangler.jsonc` per Cloudflare Workers production best practices while preserving all assertions in `tests/test_t15_deploy.py`.

---

## 6. Verification, Benchmarking & Regression Test Plan

### 6.1 Existing Test Suite Invariants (Must Stay 100% Green)
Run full regression suite and linter after each phase:
```bash
uv run pytest -v
uv run ruff check .
npx tsc --noEmit
```
Pay special attention to preserved test contracts:
- `tests/test_t01_config.py`: `settings.llm.model == "gpt-6-luna"`, `stable_ms == 800`, `pause_wait_s == 5`, `min_score == 0.5`.
- `tests/test_t06_t07_llm.py`: `build_search_request()` excludes transcript; knowledge files never contain `"hey ba"`.
- `tests/test_t11_notion.py`: Exact `POST /pages` and `PATCH /blocks/{id}/children` call counts on mock transport.
- `tests/test_t12_t14_agent.py`: `FakeLLM.respond(self, input, tools=None, *, enable_web_search=False, timeout_s=None)` 4-arg signature compatibility (use `inspect.signature` before passing `max_output_tokens` or `model_override`).
- `tests/test_t15_deploy.py`: Required literals in `wrangler.jsonc` and `cloudflare/src/index.ts` (`sleepAfter = "3h"`, `idFromName("meeting")`, `portReadyTimeoutMS`, `enableInternet = true`, `timingSafeEqual`, zero secret prefixes).

### 6.2 New Unit & Integration Tests to Add

| Test File | New Test Cases |
|---|---|
| `tests/test_t02_t04_meet.py` | 1. `test_stabilizer_fast_commits_punctuated_wake_question`: `"Hey BA, what is a hybrid?"` commits at `450ms` instead of `800ms`.<br>2. `test_stabilizer_leading_word_asr_revision_does_not_split`: `"Think we should use LFP"` -> `"I think we should use LFP"` commits once, not twice. |
| `tests/test_t05_trigger.py` | `test_bare_wake_adaptive_pause_expires_early_when_speaker_silent`: Bare `"Hey BA"` expires at `~2.2s` when `is_speaker_active` is `False`, but waits up to `5.0s` while `is_speaker_active` is `True`. |
| `tests/test_t06_t07_llm.py` | 1. `test_bm25_acronym_and_multiword_queries_hit_knowledge`: `"what does AEB stand for in modern cars?"`, `"what is ESC in a car?"`, `"400V vs 800V EV charging"` score `>= 0.5` on the right file.<br>2. `test_search_cars_ttl_cache_avoids_duplicate_api_calls`: Repeated query within TTL hits memory cache in 0ms. |
| `tests/test_t09_tasks.py` | 1. `test_parse_due_ordinals_eod_eow_and_relative_days`: Tests `"October 28th"`, `"24th of October"`, `"Nov 2nd"`, `"tonight"`, `"by EOD"`, `"end of week"`, `"next week"`, `"in 3 days"`.<br>2. `test_assign_and_log_task_commands_and_pronoun_owner`: Tests `"assign Ali to send the deck by Friday"`, `"I'll update the cost sheet by tomorrow"` (`owner -> said_by`), and alias normalization (`"Ali K." -> "Ali"`). |
| `tests/test_t11_notion.py` | 1. `test_live_save_does_not_invalidate_summaries_cache`: Verifies `_save_live(..., append_children=False)` keeps `_summaries_cache` warm.<br>2. `test_parallel_database_context_and_long_transcript_toggle`: Verifies 250-line transcript fits inside toggle paragraphs without truncating at line 80. |
| `tests/test_t12_t14_agent.py` | 1. `test_pure_car_question_skips_notion_db_fetch`: Verifies `fetch_db_context` is NOT awaited on `"what is a hybrid?"` or `"current price of a Toyota RAV4 hybrid?"`.<br>2. `test_automotive_meeting_questions_do_not_false_not_sure`: Verifies `"has Omar finished the brake pad shortlist?"` and `"what target did we set for DC fast charging?"` answer from `<meeting_digest>` instead of returning `NOT_SURE`.<br>3. `test_open_vs_done_task_filtering_and_missing_owner_precision`: Verifies `[Not started]` is included in open tasks, `[Done]` is excluded when asking for open tasks, and asking for `"Bob's tasks"` does not dump Ali's tasks.<br>4. `test_word_boundary_off_topic_blocks_substring_collisions`: Verifies `"what should I eat tonight?"` returns `OFF_TOPIC` even when `"cold-weather"` / `"seatbelts"` is in the digest. |
| `tests/test_t13_guardrails.py` | `test_citation_source_verification_and_grounded_auto_citation`: Rejects fabricated `(src: nonexistent.md)` and ungrounded prices while allowing verified grounded numbers. |

---

## 7. Ready-to-Run Implementation Prompt

Copy and paste the prompt below into an agent session when you are ready to implement this plan:

```markdown
Implement the speed, response-accuracy, RAG, task-extraction, and container-efficiency improvements specified in `docs/speed-and-accuracy-improvement-plan.md`.

### Execution Rules
1. Read `docs/speed-and-accuracy-improvement-plan.md` completely first, and read each target file before editing.
2. Preserve 100% compatibility with all existing tests in `tests/` (including `settings.llm.model == "gpt-6-luna"` in `test_t01_config.py`, `FakeLLM.respond` 4-arg signatures via `inspect.signature` guards, and required Worker strings in `test_t15_deploy.py`).
3. Execute in order:
   - **Phase 1 (Critical-Path Speed)**: Conditional/parallel `_full_digest()` in `src/ba_agent/agent/loop.py`; `warm_up()`, `max_output_tokens`, `_web_cache`, and pooled `_web_http` in `src/ba_agent/llm/openai_client.py`.
   - **Phase 2 (Caption/Trigger/UI Speed)**: `450ms` wake-question fast commit and token-overlap `_same_utterance()` in `src/ba_agent/captions/stabilize.py`; live-speech-aware adaptive pause in `src/ba_agent/trigger/detector.py`; combined 150ms poll + fast-path `post_chat()` in `src/ba_agent/bot/google_meet/adapter.py` and `src/ba_agent/session.py`; real `caption_commit_ms` in `log_timing()`.
   - **Phase 3 (Notion & SQLite Speed)**: Preserve `_summaries_loaded_at` on `append_children=False` live syncs, parallelize `database_context()` with `asyncio.gather`, add 120s stale-while-revalidate + startup cache warm-up, sort summaries descending, and multi-line paragraph packing in `_transcript_toggle()` (`src/ba_agent/notion/client.py`); debounce `live_notes()` to `15s`; bound `store.digest()` to `last(400)` and include `[status]` + ISO due dates (`src/ba_agent/memory/store.py`).
   - **Phase 4 (Response Accuracy & BM25 RAG)**: Context-aware `classify_query_intent()` in `src/ba_agent/knowledge/policy.py`; BM25 + IDF + synonym/acronym scoring in `src/ba_agent/knowledge/search.py` + enriched `data/knowledge/*.md` (no `"hey ba"` string); verified source/number grounding in `src/ba_agent/guardrails/citations.py`; word-boundary matching, `[Not started]`/`[Done]` filtering, and exact empty-owner task replies in `src/ba_agent/agent/loop.py`.
   - **Phase 5 (Task & Spoken Date Accuracy)**: Ordinals (`Oct 28th`, `24th of Oct`), `tonight`, `EOD`, `EOW`, `next week`, `in N days` in `src/ba_agent/tasks_extract/dates.py`; expanded task commands + pronoun (`I`/`me` -> `said_by`) + speaker alias normalization in `src/ba_agent/tasks_extract/extractor.py` and `src/ba_agent/session.py`.
   - **Phase 6 (Cloudflare Idle Efficiency)**: `1.5s` DO status cache, idle `peek=1` bypass so `sleepAfter = "3h"` is not reset when idle, 25m idle stop alarm in `cloudflare/src/index.ts`, and `nodejs_compat` in `wrangler.jsonc`.
4. Add all regression and accuracy tests listed in Section 6.2 of `docs/speed-and-accuracy-improvement-plan.md`.
5. Run `uv run pytest -v`, `uv run ruff check .`, and `npx tsc --noEmit` and fix any issues until everything passes with zero warnings or errors.
```

