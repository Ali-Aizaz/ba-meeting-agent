# TechSol BA — Speed, Accuracy & Reliability Improvement Plan (v2, Code & Telemetry Verified)

> **Baseline Reviewed**: Commit `ba3de19` (`v10`), verified against source code, `@cloudflare/containers@0.3.7` SDK internals, live Cloudflare production telemetry (`2026-10-26` – `2026-11-13`), and **`132 passed, 1 skipped`** across the expanded test suite (`tests/test_unit_components.py` + `tests/test_integration_components.py`).  
> **Scope**: Evidence-backed performance, accuracy, observability, and container-cost roadmap building on top of the v1 `docs/architecture-improvement-plan.md`.  
> **Implementation Status**: ✅ Complete — All Phases (`1.1`–`6.1`), Section 4 items, Section 6.2 v2 feature tests, and lint/type checks implemented and verified (`149 passed, 1 skipped`, `ruff` 0 errors, `tsc` 0 errors).

---

## 1. Executive Summary & Production Audit

### 1.1 What Is Already Working Well (v10 Baseline)
A line-by-line code, SDK, and test audit confirms that **TechSol BA** has strong architectural foundations:
- **Decoupled Async Event Loop** (`src/ba_agent/session.py`): `"Hey BA"` triggers dispatch through an `asyncio.Queue` (`trigger_queue`), preventing OpenAI/Notion calls from blocking caption/chat polling.
- **WebRTC Media & Audio Discipline** (`src/ba_agent/bot/google_meet/scripts.py`, `video.py`): Outbound audio/video tracks are silenced (`--mute-audio`, `silenceTrack`, `speechSynthesis` no-op). Note on video handling (`scripts.py:532`): `RTCPeerConnection.prototype` SDP `a=inactive` rewriting is gated by `window.location.hostname !== "meet.google.com"` (active on local/test fixtures), while on live `meet.google.com`, incoming video decoding is mitigated by stripping video tracks inside `HTMLMediaElement.prototype.srcObject` (`scripts.py:559-597`), rejecting video-only `getUserMedia` (`scripts.py:88-95`), and `BA_BROWSER__RECEIVE_VIDEO=false`.
- **Persistence & Crash Recovery** (`src/ba_agent/memory/store.py`): SQLite runs in `WAL` mode with composite indexes on captions/replies, canonical Meet URL matching (`/_meet/abc-defg-hij` -> `/abc-defg-hij`), crash recovery for open calls, and cross-meeting history (`stored_context()`).
- **Safety & Privacy Guardrails** (`src/ba_agent/guardrails/`): Outbound web searches block internal `notion.so` / `meet.google.com` URLs and strip meeting text + participant names, untrusted prompt inputs use XML tags (`<meeting_digest>`, `<knowledge>`, `<web_results>`, `<question>`), and uncited numeric car claims trigger verification.
- **Zero-Friction Lifecycle** (`extension/`, `cloudflare/src/index.ts`): The Chrome extension auto-starts/switches meetings via `POST /join`, auto-admits **TechSol BA**, stops polling immediately when the user leaves a call (`extension/content.js:368`: `if (!isInActiveCall() || statusPollInFlight) return;`), and renders live progress toasts.

### 1.2 Live Cloudflare Production Telemetry & Log Audit (`ba-meeting-agent`)
Pulling live `wrangler tail`, container health/instances, and historical Cloudflare GraphQL Analytics (`workersInvocationsAdaptive`, `durableObjectsInvocationsAdaptiveGroups`, `containersUsageAdaptiveGroups`, `containersMetricsAdaptiveGroups` for `2026-10-26` – `2026-11-13`) revealed several critical production facts—and disproved one major assumption in the initial draft:

| Production Metric (`2026-10-26` – `2026-11-13`) | Measured Value | Architectural Implication |
|---|---|---|
| **Worker Requests & Disconnects** | `841` total (`758` `ok`, **`83` `clientDisconnected` = 9.9%**) | Warm `peek=1` has `wallP50 = 125.5ms` (`488 reqs/hr` during active calls), but cold `/join` `wallP99` hit **`33.4s – 46.5s`** (Oct 27) and **`59.995s` (`clientDisconnected`)** (`2026-10-28T13:00Z`) while waiting synchronously on container boot. |
| **Durable Object Exceptions** | **`7` `scriptThrewException` errors** (Oct 27: `5` at `14:00Z`, `1` at `16:00Z`, `1` at `22:00Z`) | Total exception wall time reached `703.1s`; caused by unhandled container startup / port-ready failures in `BaContainer`. |
| **Container CPU & RAM (`standard-1`: `0.5 vCPU`, `4 GiB`)** | **`60.1% – 63.0%` avg CPU**, **`170.0% – 191.5%` peak CPU**, **`2,150 MiB` peak RAM** | Active calls saturate `0.5 vCPU`! Reducing poll intervals blindly to `150ms` without consolidating Playwright IPCs would worsen CPU throttling. |
| **Container Idle Memory Burn** | **`103.00 GiB-hours` in 48 hours** (`26.61h` runtime, `1.62 vCPU-h`) — **>4x Cloudflare's 25 GiB-h monthly included quota** | Burn occurred across **zero-Worker-request hours** (`17:00–19:00Z`, `23:00–01:00Z`, `09:00–11:00Z`, `14:00–18:00Z`) at `0.4–0.9%` CPU and `1,066–1,297 MiB` RAM for **3.5 – 4.75 hours** after 5-minute test calls. |
| **Worker Structured Logs (`wrangler tail`)** | `logs: []` on live `/status?peek=1` (`wallTime: 261ms`, `cpuTime: 2ms`) | `cloudflare/src/index.ts` emits **zero `console.log`/`console.error` events**, and `wrangler.jsonc` omits `head_sampling_rate: 1`, leaving Worker/DO state transitions blind in Workers Logs. |
| **Durable Object Namespace Hygiene** | `7` DO objects with stored data (`meeting` + orphaned `meeting-v2`..`meeting-v7`); stale `active: 1` control-plane counter | All instances are currently `inactive` (`0.00 GiB-h` Nov 2–13), with 6 legacy `meeting-v2..v7` DOs holding orphaned storage. |

> **Why the Container Burned 3.5 – 4.75 Hours with Zero Worker Traffic (Verified SDK Root Cause)**:
> 1. **Not post-call extension polling**: `extension/content.js:368` stops polling as soon as the Meet call ends (`!isInActiveCall()`).
> 2. **Unbounded Python keep-alive + `sleepAfter = "3h"`**: Because `BA_BROWSER__KEEP_ALIVE = "true"`, `src/ba_agent/session.py:154` blocks forever on `await next_url_event.wait()`, keeping Xvfb + Chromium (`~1.2 GiB` RAM) alive for at least `sleepAfter = "3h"` after every short call.
> 3. **DO Eviction Timer Reset (`@cloudflare/containers@0.3.7` `container.js:360`)**: The base `Container` class runs 20 internal `alarm()` heartbeats/hr (`every 3m`). If the idle DO is evicted from memory and later reconstructed by an alarm, `Container`'s `constructor` calls `this.renewActivityTimeout()`, resetting `sleepAfterMs` for **another 3 hours**—explaining the observed `4.25h – 4.75h` idle tails!
> 4. **In-call `peek=1` activity renewal (`container.js:887-891`)**: Whenever a Meet tab or popup *is* open and calls `GET /status?peek=1`, `BaContainer` calls `super.fetch()`, which invokes `containerFetch()` -> `this.renewActivityTimeout()`, resetting the 3-hour sleep timer even if the bot already left the call (`stage: "idle"`).

### 1.3 Summary of Target Gains

| Dimension | Current Behavior (v10) | Target Behavior (After Plan) | Expected Gain |
|---|---|---|---|
| **Spoken `"Hey BA"` (Local KB)** | `3.3s – 6.6s` (up to `8.5s` on cold `gpt-6-luna` 404 fallback) | `1.2s – 2.0s` | **~65% faster** |
| **Spoken `"Hey BA"` (Web Search)** | `6.6s – 18.0s` (2–4 sequential LLM calls + sequential Notion queries) | `3.2s – 5.5s` (`<1.8s` on cached car follow-up) | **~55–70% faster** |
| **Meeting / Task Lookup (`"Hey BA"`)** | `3.3s – 6.5s` (`_save_live` wipes Notion summary cache every `1.5s`) | `1.3s – 2.2s` (hot cache + parallel burst-2 refresh) | **~65% faster** |
| **Bare `"Hey BA"` Clarification** | `6.1s – 6.9s` (`800ms` stabilizer + fixed `5.0s` wait + `500ms` tick) | `2.6s – 3.0s` (caption-aware adaptive pause) | **~55% faster** |
| **Automotive Meeting Q&A Accuracy** | Meeting questions containing car terms (`AEB`, `brake`, `charging`) misroute to `car=True` and fail citation check -> false `"I'm not sure"` | Context-aware hybrid intent routing + grounded meeting citations | **Eliminates false `"not sure"`** |
| **Spoken Due-Date & Task Capture** | Ordinals (`"October 28th"`), `"tonight"`, `"EOD"`, `"EOW"`, `"in 3 days"`, and model JSON `"due"` key return `None` -> tasks silently dropped | Full spoken-English date parser + `"due"`/`"due_text"` support + expanded task grammar + alias normalization | **+35–50% recall on live tasks** |
| **Local Knowledge Retrieval (RAG)** | 4-letter plurals (`"cars"`) unstemmed + unweighted overlap drops 1-acronym queries (`"what does AEB stand for in modern cars?"` -> `0.25 < 0.5`) + `"wltp"`/`"epa"` force web search | BM25 + IDF weighting + `>=4`-char stemming + scoped `_CURRENT` policy + expanded automotive KB | **Higher precision & fewer slow web fallbacks** |
| **Cloudflare Container Idle Billing** | `103 GiB-h` burned in 2 days (`3.5–4.75h` idle tails after 5m calls; `super.fetch` on `peek=1` renews `sleepAfter`) | `15m` Python idle auto-exit + SDK `this.schedule()` stop + non-renewing `getTcpPort(8080).fetch()` on `peek=1` | **~85–90% reduction in container GiB-hours** |

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
    Loop->>OAI: 2. First call: gpt-6-luna 404 -> retry gpt-4o-mini + web_search (3.5s - 7.5s)
    Note over Loop,N: 3. Sequential _full_digest() runs AFTER search_cars even on car=True!
    Loop->>N: POST /databases/{tasks}/query (350ms)
    Loop->>N: 333ms _Limiter sleep + POST /databases/{summary}/query (cache wiped every 1.5s!)
    Loop->>OAI: 4. Second LLM call with 4k-token digest + local_tools() (1.8s - 4.0s)
    opt Missing literal URL or tool call emitted
        Loop->>OAI: 5. Third / Fourth LLM call for tool or citation retry (2.0s - 4.0s)
    end
    Note over Loop,DOM: 6. post_chat() scans 8 overlay locators before fill() (200ms - 400ms)
    Loop->>DOM: Reply posted in Google Meet chat
```

### Verified Root Causes in Code

1. **Unconditional Notion & Cross-Meeting Fetch on Pure Car Questions (`src/ba_agent/agent/loop.py:252-254`)**
   - Line `252` checks `if not car and self.fetch_db_context is not None:` before emitting the `"Fetching stored data from database…"` toast, **but line `254` unconditionally awaits `self._full_digest()` anyway**—and **sequentially after** `await self.search_cars(question)` (`line 245`)!
   - Every car question pays Notion HTTP latency AND bloats the synthesis prompt with up to ~4,000 tokens of unrelated tasks and past meeting transcripts.

2. **`_save_live()` Destroys Notion Summary Cache Every `1.5s` & Leaks Current Meeting into Stored Notes (`src/ba_agent/notion/client.py:363-368, 442`, `src/ba_agent/session.py:448, 563`)**
   - Every committed caption sets `notes_dirty.set()` (`session.py:448`). `live_notes()` wakes up `1.5s` later (`session.py:563`), calls `store.last(100000)` (`session.py:630`), and calls `notion.save_meeting(..., append_children=False)`.
   - Inside `save_meeting()`, line `442` executes `self._summaries_loaded_at = 0.0` unconditionally—causing a near-100% cache miss rate on `list_summaries()` during active speech AND contending on `NotionService._limiter`.
   - Worse: `list_summaries()` (`client.py:363-368`) only skips `self.summary_page_id` while its text equals the initial placeholders (`"Meeting in progress…"` / `"Waiting for captions…"`). Once the first live sync writes real captions at `t=1.5s`, `list_summaries()` includes the **current in-progress meeting** inside `"Stored Notion Meeting Notes:"`, duplicating the live transcript in the prompt!

3. **Cold-Start `gpt-6-luna` 400/404 Retry, Uncached Web Search & Unpooled Fallback HTTP (`src/ba_agent/llm/openai_client.py:79-109, 120-165`)**
   - When `llm.model` defaults to `"gpt-6-luna"` (`config/settings.yaml:23`, `cloudflare/src/index.ts:56`), the first live `respond()` call sends `"gpt-6-luna"` to OpenAI, waits for a `400`/`404` error, sets `self._fallback_model = "gpt-4o-mini"` (`line 100`), and retries—adding `800ms–2,500ms` directly onto the user's first `"Hey BA"` question. *(Note: Once `_fallback_model` is set, subsequent `search_cars()` calls already use `_fallback_model` via `self.respond()` at `line 79`; `build_search_request()`'s `"model"` field at `line 124` is unused dead metadata.)*
   - `search_cars()` has no TTL cache for repeated queries about the same vehicle, `respond()` sets no reasoning-effort cap when reasoning models (`gpt-5*` / `o*`) are configured, and `_public_web_search()` (`line 157`) opens and tears down a fresh `httpx.AsyncClient` on every fallback search.

4. **Sequential Notion `database_context()` & Strict Single-Slot `_Limiter(1/3)` (`src/ba_agent/notion/client.py:38-52, 315, 352, 392-401`)**
   - `database_context()` awaits `self.list_tasks()` and only afterward awaits `self.list_summaries()`. Furthermore, `_Limiter(1/3)` enforces a strict `333ms` sleep before a 2nd request even when no prior traffic occurred, turning a cold/expired 30s refresh into `700ms–1,500ms`.

5. **Caption Commit, Polling & Playwright IPC Overhead on `0.5 vCPU` (`src/ba_agent/captions/stabilize.py:42, 107`, `src/ba_agent/session.py:483-536`, `src/ba_agent/bot/google_meet/adapter.py:488, 656`)**
   - When a speaker asks `"Hey BA, what is a hybrid?"` and pauses for the answer, `split_completed_wake_sentence()` returns `None` (no trailing speech), forcing the full `800ms` `stable_ms` wait plus up to `300ms` poll delay.
   - `captions()` (`session.py:483`) runs `await adapter.meeting_closed()` every `300ms` (2–5 Playwright `.count()` IPC round-trips) alongside a separate `300ms` `chats()` loop—contributing to the **`63%` avg / `191.5%` peak CPU** measured on `standard-1`.
   - `post_chat()` (`adapter.py:488`) runs `await self._dismiss_incall_overlays()` (up to 8 Playwright locator `.count()` IPC calls) before **every** chat message, even when the chat input is already open and unobscured.
   - Bare `"Hey BA"` waits a fixed `5.0s` (`pause_wait_s`) + `500ms` (`ticks()`) even when `Stabilizer.open` has no active caption row for that speaker.
   - `log_timing()` (`src/ba_agent/agent/loop.py:142`) hardcodes `caption_commit_ms=0`.

6. **Unbounded `store.last(100000)` Deserialization (`src/ba_agent/memory/store.py:259`, `src/ba_agent/session.py:630`)**
   - `MeetingStore.digest()` and `_save_live()` deserialize all rows in the meeting (up to `100,000` Pydantic `CaptionLine` objects) only to keep a small tail window.

---

## 3. Diagnostic Deep Dive: Response Accuracy & Grounding Gaps

### 3.1 Car-Term Collision in Automotive Meeting Questions (`policy.py` + `loop.py`)
- **Where**: `src/ba_agent/knowledge/policy.py:232-323` & `src/ba_agent/agent/loop.py:239-304`.
- **Bug**: Because TechSol BA assists **automotive engineering meetings**, nearly every meeting decision and task contains words in `_CAR_WORDS` (`brake`, `AEB`, `LFP`, `NMC`, `hybrid`, `charging`, `inverter`, `NCAP`, `RAV4`, `suspension`).
- Any meeting/task question outside the fixed regex list in `_MEETING_PATTERNS`—such as:
  - `"Hey BA, has Omar finished the brake pad shortlist?"` (`_MEETING_PATTERNS` matches `did <name> finish`, not `has <name> finished`)
  - `"Hey BA, what target did we set for DC fast charging?"` (`what target did we` misses `what did we`)
  - `"Hey BA, what was the budget for AEB radar calibration?"`
  - `"Hey BA, update me on Nadia's Euro NCAP sensor work"`
  is classified as `car = True`.
- **Impact**: Once `car = True`, `_answer()` enforces `reply_status(first.text)` (`guardrails/citations.py:28`), which **rejects any reply lacking `(src: *.md)` or `https://...`**. Because meeting facts come from `<meeting_digest>`, the bot regenerates and **returns `"I'm not sure — I don't have a source for that."`** for a fact sitting in its own meeting transcript!

### 3.2 Spoken Due-Date, Schema Key & Short-Date Bugs Dropping Tasks (`dates.py` & `extractor.py`)
- **Where**: `src/ba_agent/tasks_extract/dates.py:71-132` and `src/ba_agent/tasks_extract/extractor.py:161-170`.
- **Verified Bugs**:
  1. **Ordinal dates fail `\b` regex matching (`dates.py:94-97`)**: `rf"\b({_MONTH_PATTERN})\s+(\d{{1,2}})\b"` fails on `"October 28th"`, `"Nov 2nd"`, and `"24th of October"` because `8` and `t` in `28th` are both `\w` characters (no word boundary after `28`).
  2. **Cue/Parser mismatch (`dates.py:71-98`)**: `might_contain_task()` (`extractor.py:21`) triggers LLM calls on `"tonight"` and `"next week"`, yet `parse_due()` returns `None` for `"tonight"`, `"next week"`, `"EOD"` / `"end of day"`, `"EOW"` / `"end of week"`, `"this week"`, and `"in 3 days"` / `"in two days"`. *(Note: `"this Friday"` already works via `\b({_DAY_PATTERN})\b` at `dates.py:90`, and `"next Monday"` intentionally means Monday of the following week via `following_week=True`.)*
  3. **Model JSON `"due"` vs `"due_text"` drop (`extractor.py:164`)**: `tasks_from_model_json()` reads `item.get("due_text")` only and ignores `item.get("due")`, even though `local_tools()`'s `create_task` schema (`llm/tools.py:50`) names the field `"due"`. Whenever the LLM emits `"due": "tomorrow"` in overheard task JSON, `due_text` is `""`, `due` is `None`, and `if overheard and due is None: continue` **silently drops the task**!
  4. **Substring weekday collision in `short_due()` (`dates.py:126`)**: `if name in token:` matches `"mon"` inside `"next month"`, returning `"Mon"`! Also `today_in()` (`dates.py:65`) raises an uncaught `ZoneInfoNotFoundError` if `meeting.timezone` is misconfigured.

### 3.3 Substring Collisions in Off-Topic & Digest Ranking (`src/ba_agent/agent/loop.py:889, 914`)
- `_is_off_topic()` (`line 889`) and `_rank_lines()` (`line 914`) check `kw in digest_low` / `kw in low` for keywords of length `>= 3` without word boundaries:
  - `"What should I eat tonight?"` -> `"eat"` matches inside `"cold-weather"`, `"seatbelts"`, or `"create"`, bypassing `_is_off_topic()`.
  - Short words (`"use"`, `"end"`, `"out"`, `"car"`, `"ten"`) match inside unrelated words (`"send"`, `"start"`, `"latency"`).

### 3.4 Task Status Vocabulary Mismatch & False-Empty Override (`src/ba_agent/agent/loop.py:692-708, 840, 916-919`)
- **Bug 1 (`[Not started]` ignored)**: `config/notion_schema.yaml:4` and `TaskRecord.status` default to `"Not started"`, rendering `[Not started]` in `database_context()`. Yet `_rank_lines()` (`loop.py:916`) checks `if want_open and ("[todo]" in low or "[in progress]" in low):`—omitting `"[not started]"` and failing to exclude `[done]` items when asking for open tasks.
- **Bug 2 (`store.digest()` drops status & ISO date)**: `MeetingStore.digest()` (`store.py:266`) formats current-meeting tasks as `- Ali: cost sheet (Fri)` without `[status]` or ISO date `2026-10-30`.
- **Bug 3 (False-empty override dumps wrong tasks)**: In `_resolve_meeting_reply()` (`loop.py:700-707`), if a user asks `"What tasks are assigned to Bob?"` and the LLM accurately replies `"There are no tasks assigned to Bob"`, `"no tasks"` triggers `_answer_from_digest()`, whose line `840` (`chosen = matched_tasks[:8] if matched_tasks else task_lines[:8]`) dumps Ali's and Sara's tasks!

### 3.5 Lexical RAG Blind Spots, 4-Letter Plural Stemming & `_CURRENT` Policy Trap (`search.py` & `policy.py`)
- **4-letter plural stemming bug (`src/ba_agent/knowledge/search.py:172`)**: `_stem()` checks `if len(word) > 4:`. Four-letter plurals (`"cars"`, `"suvs"`, `"oems"`, `"ecus"`) are **not stemmed at all** (`len("cars") == 4`), so `"cars"` fails to match `"car"` in knowledge files!
- **Unweighted overlap ratio (`search.py:183-194`)**: `_score()` computes `len(overlap) / len(query)`. `"what does AEB stand for in modern cars?"` (`query = {"aeb", "stand", "modern", "cars"}`) against `safety.md`'s `## ADAS` chunk (`overlap = {"aeb"}`) scores `1 / 4 = 0.25 < 0.5` and returns `[]` even though `safety.md` defines `Automatic Emergency Braking (AEB)`.
- **`WLTP`/`EPA` policy trap (`src/ba_agent/knowledge/policy.py:37-38`)**: `_CURRENT` unconditionally includes `"epa"` and `"wltp"`, causing `needs_current_fact("what is the difference between WLTP and EPA?")` to return `True` and force a slow web search even if `ev.md` explains both test cycles!

### 3.6 Unverified Citations & Unchecked Numeric Claims (`src/ba_agent/guardrails/citations.py`)
- `has_citation()` accepts any `(src: anything)` or `https://...`, even if the cited file was not in `hits` or the number in the reply does not appear in `hits` or `web.text`. `_UNITS` also misses `kw`, `mi`, `miles`, `nm`, `lb-ft`, `%`, `v`, `volts`, `min`, `minutes`.

### 3.7 Interim ASR First-Word Revision Splitting Captions (`src/ba_agent/captions/stabilize.py:165-176`)
- `_same_utterance()` only returns `True` if one string starts with the other or `prev_words[0] == new_words[0]`. When Google Meet ASR revises the leading word (`"Think we should use LFP"` -> `"I think we should use LFP"`), `_same_utterance()` returns `False` and commits the partial fragment prematurely.

---

## 4. File-by-File Change Matrix

| Priority | Status | File | Category | Key Symbols Modified / Added | Expected Impact |
|---|---|---|---|---|---|
| **P0** | ✅ Done | `src/ba_agent/agent/loop.py` | Speed & Accuracy | `AgentLoop._answer`, `_dispatch`, `_full_digest`, `_prompt`, `_is_off_topic`, `_rank_lines`, `_answer_from_digest`, `_resolve_meeting_reply` | Skip Notion fetch on pure car queries (with empty-hit fallback preserved); parallelize `search_cars` + `_full_digest` on hybrid queries; hybrid car+meeting citation rules; word-boundary matching; fix `[Not started]`/`[Done]` filtering & empty-owner lookup; record real `caption_commit_ms`. |
| **P0** | ✅ Done | `src/ba_agent/knowledge/policy.py` | Accuracy & Speed | `is_meeting_question`, `classify_query_intent` (new), `needs_current_fact`, `should_search_web` | Prevent automotive meeting/task questions from misrouting into `car`-only mode; scope `"epa"`/`"wltp"` in `needs_current_fact` to model/year queries so conceptual EV cycle questions stay fast & local. |
| **P0** | ✅ Done | `src/ba_agent/knowledge/search.py` & `data/knowledge/*.md` | Accuracy & Speed | `KnowledgeIndex`, `_stem`, `_bm25_score` (new), `_SYNONYMS` | Fix `_stem` for 4-letter plurals (`cars` -> `car`); upgrade unweighted overlap to BM25 + IDF rare-term boost + heading/acronym boost so 1-acronym queries (`AEB`, `ESC`, `LFP`) hit `>= 0.5`; expand car engineering notes. |
| **P0** | ✅ Done | `src/ba_agent/tasks_extract/dates.py` & `extractor.py` | Accuracy | `parse_due`, `short_due`, `today_in`, `is_task_request`, `parse_task_command`, `tasks_from_model_json` | Parse ordinals (`Oct 28th`, `24th of Oct`), `tonight`, `EOD`, `EOW`, `this week`, `next week`, `in N days`; accept `"due"` or `"due_text"` in model JSON; fix `short_due` word boundaries & `today_in` fallback; expand task verbs & pronoun/alias normalization. |
| **P0** | ✅ Done | `src/ba_agent/notion/client.py` | Speed & Accuracy | `_Limiter`, `NotionService.database_context`, `list_tasks`, `list_summaries`, `save_meeting`, `_transcript_toggle` | Stop resetting `_summaries_loaded_at = 0.0` on live syncs; exclude `self.summary_page_id` in `database_context()` while keeping `list_summaries()` unit-test compatible; burst-2 `_Limiter` + `asyncio.gather`; 120s stale-while-revalidate; multi-line paragraph packing in `_transcript_toggle`. |
| **P1** | ✅ Done | `src/ba_agent/llm/openai_client.py` & `parse.py` | Speed & Cost | `OpenAIResponsesClient.respond`, `search_cars`, `warm_up` (new), `_web_cache`, `_web_http` | Zero-token `models.retrieve()` warm-up + instant `gpt-6-luna` fallback detection during `join()`; safe `max_output_tokens >= 400` + `reasoning={"effort": "low"}` for reasoning models; 10-min TTL LRU cache on `search_cars`; pooled `httpx.AsyncClient` for fallback search; clean dead `"model"` key in `build_search_request`. |
| **P1** | ✅ Done | `src/ba_agent/captions/stabilize.py` & `trigger/detector.py` | Speed & Accuracy | `Stabilizer.push_frame`, `has_open_row_for` (new), `_same_utterance`, `TriggerDetector.tick` | Fast-commit `?`-terminated wake questions (`>= 3` words) at `450ms`; adaptive `2.2s` bare `"Hey BA"` response when speaker has no open caption; length-guarded sequence alignment in `_same_utterance` without overwriting long captions. |
| **P1** | ✅ Done | `src/ba_agent/session.py` & `bot/google_meet/adapter.py` | Speed, CPU & Idle | `run_join`, `_loops`, `live_notes`, `drain_poll_state` (new), `post_chat` | Add `15m` `idle_timeout_s` to `next_url_event.wait()` so idle containers exit cleanly; debounce `live_notes` to `15s`; single `window.__baPollAll()` IPC every `200–250ms` (protecting `0.5 vCPU`); fast-path `post_chat()` when chat input is open; preserve `extractor.recent`. |
| **P1** | ✅ Done | `src/ba_agent/guardrails/citations.py` | Accuracy | `has_precise_number`, `verify_reply_sources` (new), `numbers_grounded_in_context` (new) | Expand automotive unit detection; verify cited `(src: ...)` / URLs belong to retrieved context; deterministically attach unambiguous grounded source without an extra LLM call. |
| **P2** | ✅ Done | `src/ba_agent/memory/store.py` | Speed & Accuracy | `MeetingStore._init`, `digest` | Bound `digest()` query to `last(400)` instead of `last(100000)`; include `[status]` and ISO due date in current-meeting `digest()` task lines; add index for cross-meeting captions. |
| **P2** | ✅ Done | `cloudflare/src/index.ts` & `wrangler.jsonc` | Cost & Observability | `BaContainer.fetch`, `stopIfIdle` (new), `handleJoin`, `wrangler.jsonc` | Use `this.ctx.container.getTcpPort(8080).fetch()` + `1.5s` cache on `peek=1` so `peek=1` never calls `renewActivityTimeout()`; schedule idle stop via SDK `this.schedule(900, "stopIfIdle")` (never overriding `alarm()`); emit structured JSON Worker logs + `head_sampling_rate: 1` + `nodejs_compat`. |

---

## 5. Detailed Phased Implementation Plan

### Phase 1: Critical-Path LLM & Context Speed Optimizations

#### 1.1 ✅ Done — Eliminate Redundant Notion Calls & Parallelize Context Gathering (`src/ba_agent/agent/loop.py`)
- **Current issue**: In `AgentLoop._answer()` (`lines 235-265`), `await self.search_cars(question)` runs first, and `await self._full_digest()` runs afterward on **every** question—even pure car questions like `"what is a hybrid?"` or `"what is the current price of a Toyota RAV4 hybrid?"`.
- **Implementation**:
  1. Introduce `classify_query_intent(question, hits, participants)` returning `"car"`, `"meeting"`, or `"hybrid"` (e.g., `"compare the RAV4 hybrid price with the budget we discussed"` is `"hybrid"`).
  2. When intent is pure `"car"`:
     - Do **not** call `fetch_db_context()` (zero Notion HTTP calls) **unless** both `hits` and `web` come back empty (`not hits and (web is None or not web.useful)`—preserving `test_t12_stored_database_context_and_web_fallback_priority` in `tests/test_t12_t14_agent.py:344-362`!).
     - Pass a compact `store.digest(max_tokens=600)` so synthesis prompt size stays under ~600 tokens instead of ~4,500 tokens.
  3. When intent is `"hybrid"` (needs both `search_cars` and `_full_digest()`):
     - Execute both concurrently via `web, full_digest = await asyncio.gather(self._safe_search_cars(question), self._full_digest())`.
  4. **Test-safe `tools=None` fast path**: Pass `tools=None` on the first `self.llm.respond()` call **only** when `web is not None and web.useful` and the query is not a task/save command. Keep passing `local_tools()` when `web is None` so `test_t12_function_call_create_task_and_search_knowledge` (`tests/test_t12_t14_agent.py:228-235`) remains 100% green.

#### 1.2 ✅ Done — Zero-Token Connection Pre-Warming, Safe Token Caps & Search Caching (`src/ba_agent/llm/openai_client.py`)
- **Current issue**:
  - Cold `gpt-6-luna` 400/404 fallback happens on the user's first `"Hey BA"` turn (`lines 79-109`), while `tests/test_t01_config.py:37` explicitly requires `settings.llm.model == "gpt-6-luna"` in YAML defaults.
- **Implementation**:
  1. Add `async def warm_up(self) -> None` on `OpenAIResponsesClient`: launched as a non-blocking background task in `_run_single_meeting()` while Playwright joins Meet. It calls `await self._sdk().models.retrieve(self.model)` (**zero billable tokens!**), establishing the HTTP/2 TLS socket to `api.openai.com` and immediately setting `self._fallback_model = "gpt-4o-mini"` if `gpt-6-luna` returns `404`—making first-turn fallback overhead **0ms** while keeping `test_t01_u1_yaml_defaults` green.
  2. Add optional `max_output_tokens: int | None = None` to `OpenAIResponsesClient.respond()` (guarded via `inspect.signature` in `AgentLoop` so 4-arg `FakeLLM.respond` test doubles in `tests/` never break):
     - **Important Responses API safety rule**: On OpenAI's Responses API, `max_output_tokens` includes hidden reasoning tokens and tool-call tokens; tight caps (`180`–`260`) cause reasoning models to return `status="incomplete"` with empty `output_text`.
     - Use safe caps (`max_output_tokens = 450` for `"Hey BA"` chat replies and overheard task JSON, `600` for meeting summaries), **omit** `max_output_tokens` when `enable_web_search=True`, and pass `reasoning={"effort": "low"}` **only** when `chosen_model.startswith(("gpt-5", "o1", "o3", "o4"))`.
  3. Add an in-memory TTL cache `_web_cache: dict[str, tuple[float, WebResult]]` (TTL = `600s`, max 64 entries, keyed on normalized lowercase car query) in `OpenAIResponsesClient.search_cars()`.
  4. Maintain a shared lazy `self._web_http: httpx.AsyncClient` on `OpenAIResponsesClient` (closed via `aclose()`) instead of creating a new `httpx.AsyncClient` per `_public_web_search()` call (`line 157`).

---

### Phase 2: Caption, Wake-Trigger & Playwright UI Latency (CPU-Safe on `0.5 vCPU`)

#### 2.1 ✅ Done — Safe Early Commit for Explicit `?`-Terminated Wake Questions (`src/ba_agent/captions/stabilize.py`)
- **Current issue**: When a user asks `"Hey BA, what is a hybrid?"` and pauses for the reply, `Stabilizer` waits the full `800ms`.
- **Trap to avoid**: Fast-committing unpunctuated 3-word wake prefixes (`"Hey BA, what is the ... [460ms breath] ... price of a RAV4?"`) or provisional mid-pause periods (`.`) splits the user's spoken question in half and drops the second half!
- **Implementation**:
  - Add `wake_stable_ms: int | None = None` to `Stabilizer.__init__` (defaulting to `min(stable_ms, 450)` when `stable_ms >= 600`, preserving `stable_ms=0` in tests).
  - In `push_frame()` (`line 106`), commit at `wake_stable_s` (`450ms`) **only** when `current.text.rstrip().endswith("?")` **AND** `match_wake(current.text)` has `>= 3` question words. Unpunctuated or `.`-terminated wake utterances continue waiting the standard `800ms` (unless completed and followed by a new sentence via `split_completed_wake_sentence()`).

#### 2.2 ✅ Done — Live-Speech-Aware Pause Window for Bare `"Hey BA"` (`src/ba_agent/trigger/detector.py` & `stabilize.py`)
- **Implementation**:
  - Expose `Stabilizer.has_open_row_for(speaker: str) -> bool` (checking normalized speaker names).
  - Add optional `is_speaker_active: Callable[[str], bool] | None = None` to `TriggerDetector`:
    - When `is_speaker_active` is provided (live session) and `is_speaker_active(pending.speaker)` is `False` (no uncommitted caption words on screen for that speaker), expire a bare `"Hey BA"` after `fast_pause_wait_s = 2.2s` instead of `5.0s`.
    - When `is_speaker_active(pending.speaker)` is `True` (speaker is actively continuing their question), wait up to the full `self.pause_wait_s` (`5.0s`).
    - When `is_speaker_active` is `None` (unit tests `test_t05_u3` / `test_t05_u4`), preserve exact `5.0s` behavior.

#### 2.3 ✅ Done — Combined `200–250ms` Browser Poll & Fast-Path `post_chat()` (`adapter.py`, `scripts.py` & `session.py`)
- **Telemetry constraint**: Production `standard-1` (`0.5 vCPU`) already runs at **`60–63%` avg / `191.5%` peak CPU** during active calls. Polling at `150ms` would increase event-loop wakeups on a throttled half-core.
- **Implementation**:
  1. Add `window.__baPollAll()` in `PAGE_OBSERVER_JS` (`src/ba_agent/bot/google_meet/scripts.py`) returning `{ captions, chats }` in **one** `page.evaluate()` IPC round-trip every **`200ms–250ms`**, and move `await adapter.meeting_closed()` into the `1.0s` `alone()` health loop—cutting Playwright IPC round-trips by **>75%** while *lowering* container CPU usage.
  2. In `GoogleMeetAdapter.post_chat()`, check `box = self._chat_box()` **first**: if `await box.count() > 0`, click/fill/Enter immediately (`~60–100ms`); only run the 8-locator `_dismiss_incall_overlays()` + drawer-open fallback if `count() == 0` or an actionability error occurs.
  3. Add `t_committed_perf: float = 0.0` on `TriggerEvent` (`src/ba_agent/models.py`) and record real `caption_commit_ms` in `log_timing()`.

---

### Phase 3: Notion & SQLite Data-Layer Speed & Concurrency

#### 3.1 ✅ Done — Fix `_save_live()` Cache Thrashing & Current-Meeting Duplication (`notion/client.py` & `session.py`)
- **Important test invariant (`tests/test_t11_notion.py:350-366`)**: `test_t11_list_tasks_summaries_and_database_context` calls `await service.save_meeting(...)` (setting `service.summary_page_id`) and immediately asserts `len(await service.list_summaries(refresh=True)) == 1`! Therefore, `list_summaries()` **must continue returning saved pages**, while live-session exclusion belongs in `database_context()`.
- **Implementation**:
  1. In `NotionService.save_meeting()` (`client.py:442`), **do not reset `self._summaries_loaded_at = 0.0` when `not append_children`** (live background sync), and update `self._summaries_cache` in-place on final save (`append_children=True`) instead of invalidating the cache.
  2. Add `exclude_current_meeting: bool = False` to `NotionService.database_context()`: pass `exclude_current_meeting=True` from `session.py:251` (`fetch_db_context`) so the live call's own `self.summary_page_id` is never duplicated into `"Stored Notion Meeting Notes:"`, while default `database_context()` in `test_t11` stays 100% compatible.
  3. Increase `live_notes()` debounce in `src/ba_agent/session.py:563` from `1.5s` to `15.0s` (`notion.live_sync_interval_s: float = 15.0`), and replace `store.last(100000)` (`session.py:630`) with `store.last(1500)`.

#### 3.2 ✅ Done — Burst-2 `_Limiter`, Parallel `database_context()` & Multi-Paragraph Toggle (`src/ba_agent/notion/client.py`)
- **Implementation**:
  1. Upgrade `_Limiter` (`client.py:38-52`) from a strict `1/3`s single-slot spacer to a lightweight **token-bucket limiter (`rate = 3.0/s`, `burst = 2`)** (preserving `min_interval=0.0` in tests). Without `burst = 2`, `asyncio.gather(list_tasks(), list_summaries())` still sleeps `333ms` between the two read requests!
  2. In `NotionService.database_context()` (`lines 384-415`), run `list_tasks` and `list_summaries` concurrently via `asyncio.gather(...)`.
  3. Pre-warm `asyncio.create_task(notion.database_context(refresh=True, exclude_current_meeting=True))` right after `notion.verify()` in `_run_single_meeting()`, raise fresh cache TTL from `30s` to `120s`, and serve stale-while-revalidate up to `600s`.
  4. In `list_summaries()` (`line 357`), pass `"sorts": [{"timestamp": "created_time", "direction": "descending"}]` and raise the per-summary transcript excerpt limit in `database_context()` (`line 408`) from `600` to `1,200` chars.
  5. In `_transcript_toggle()` (`lines 509-523`), pack multiple caption lines (`<= 1,800` chars per paragraph block, up to `95` children per toggle) when `len(lines) > 80` so full 800–1,500-line transcripts fit cleanly inside Notion's 100-block child limit without truncating at line 80.

#### 3.3 ✅ Done — Bounded SQLite `digest()` & Enhanced Task Formatting (`src/ba_agent/memory/store.py`)
- **Implementation**:
  1. In `MeetingStore.digest(max_tokens=3000)` (`line 259`), replace `self.last(100000)` with `self.last(min(1000, max(120, max_tokens // 4)))`.
  2. Format current-meeting tasks in `digest()` (`line 266`) consistently with `stored_context()` and `_STORED_TASK_LINE`:
     ```python
     due_str = task.due.isoformat() if task.due else (task.due_text or "unscheduled")
     status_str = f" [{task.status}]" if task.status else ""
     f"- {task.owner}: {task.task} (due: {due_str}){status_str}"
     ```
  3. Add `CREATE INDEX IF NOT EXISTS idx_captions_wall_time ON captions (wall_time DESC, start_s DESC)` in `MeetingStore._init()`.

---

### Phase 4: Response Accuracy, Intent Classification, BM25 RAG & Grounding

#### 4.1 ✅ Done — Context-Aware Intent Routing & Scoped `_CURRENT` Policy (`policy.py` & `loop.py`)
- **Implementation**:
  1. Expand `_MEETING_PATTERNS` in `src/ba_agent/knowledge/policy.py` to cover:
     - Perfect/progressive speaker queries: `\b(?:did|has|have|is|was|were)\s+[a-z]+\s+(?:finish|finished|complete|completed|send|sent|prepare|prepared|review|reviewed|update|updated|schedule|scheduled|request|requested|validate|validated|finalize|finalized|working|assigned)\b`
     - Intervening nouns in first-person plural meeting queries: `\bwhat\s+(?:[\w-]+\s+){0,3}(?:did|have|do)\s+we\s+(?:set|decide|agree|say|discuss|pick|choose|target|lock|plan|budget)\b`
     - Project/BA artifacts: `\b(?:budget|shortlist|procurement|tier-1|supplier|deliverable|milestone|blocker|timeline|kickoff|benchmark\s+sync|action\s+item|lab\s+slot|slide\s+deck)\b`
  2. Pass known `participants: list[str] | None = None` into `classify_query_intent(question, hits, participants)`: if a non-brand participant name (`Omar`, `Nadia`, `Sara`, `Ali`) appears in the question, route to `"meeting"` (or `"hybrid"` if explicitly comparing against public vehicle prices/specs).
  3. **Fix `_CURRENT` policy trap (`policy.py:37-38`)**: Remove bare `"epa"` and `"wltp"` from unconditional `_CURRENT`; treat `"epa"` / `"wltp"` as current-fact triggers **only** when accompanied by a vehicle brand (`_BRANDS`), model pattern (`_MODEL_PATTERNS`), or 4-digit model year (`2024`–`2029`), so conceptual questions like `"what is WLTP vs EPA?"` answer in `<1.5s` from `ev.md` instead of making a 6–12s web search!

#### 4.2 ✅ Done — Fix 4-Letter Stemming & Upgrade `KnowledgeIndex` to BM25 + IDF (`search.py` & `data/knowledge/*.md`)
- **Implementation**:
  1. **Fix `_stem()` (`src/ba_agent/knowledge/search.py:172`)**: Change `if len(word) > 4:` on trailing `"s"` to `if len(word) >= 4 and word not in {"this", "does", "bias", "plus", "less", "loss", "mass", "pass", "gas", "bus"}:` so `"cars" -> "car"`, `"suvs" -> "suv"`, `"oems" -> "oem"`, and `"ecus" -> "ecu"`.
  2. Precompute per-chunk token frequencies (`tf`), chunk lengths, average chunk length (`avgdl`), and corpus Inverse Document Frequency (`idf(t) = log(1 + (N - df + 0.5) / (df + 0.5))`) at `KnowledgeIndex.__init__` time (pure Python `math.log`, `<1ms`).
  3. Normalize BM25 + IDF-weighted coverage onto `[0.0, 1.0]` so `min_score = 0.5` semantics are preserved and high-IDF domain acronyms (`aeb`, `lka`, `acc`, `bsm`, `esc`, `abs`, `ncap`, `adas`, `lfp`, `nmc`, `bms`, `phev`, `mhev`, `hev`, `bev`) score `>= 0.65` even inside conversational multi-word questions.
  4. Add domain synonym/acronym expansion (`"ev" <-> "electric vehicle"`, `"bev" <-> "battery electric"`, `"phev" <-> "plug in hybrid"`, `"mhev" <-> "mild hybrid"`, `"aeb" <-> "emergency braking"`, `"lka" <-> "lane keeping"`) and a `+0.10` adjacent-bigram bonus.
  5. Expand `data/knowledge/*.md` (keeping `"hey ba"` absent per `test_knowledge_files_have_no_wake_table`) with concise sections on 400V vs 800V architectures, heat pumps, WLTP vs EPA cycles (`ev.md`); series vs parallel vs e-CVT hybrids (`hybrid.md`); IIHS/NHTSA vs Euro NCAP & sensor fusion (`safety.md`); and NVH, skateboard platforms, and brake-by-wire (`body-and-chassis.md`).

#### 4.3 ✅ Done — Source-Verified Citations & Deterministic Citation Attachment (`citations.py` & `loop.py`)
- **Implementation**:
  1. Expand `_UNITS` in `src/ba_agent/guardrails/citations.py` to include `kw`, `mi`, `miles`, `nm`, `lb-ft`, `lbs`, `v`, `volts`, `min`, `minutes`, `sec`, `seconds`, `%` (keeping `reply_status` and `finalize_reply` 100% compatible with `tests/test_t13_guardrails.py`).
  2. Add `verify_reply_sources(text, allowed_files, allowed_urls) -> bool` to reject fabricated `(src: fake.md)` citations not present in `hits` or `web.urls`.
  3. Add `numbers_grounded_in_context(reply_text, context_text) -> bool`: if `reply_status(first.text) == "regenerate"` solely because the LLM omitted the `(src: ...)` tag, **and** all numbers in `first.text` appear in the single retrieved source (`len(hits) == 1` or `web.urls`), deterministically append `(src: {hits[0].file})` or `({web.urls[0]})`—saving a 2–4s regeneration LLM call! Conversely, if any precise number is ungrounded, force regeneration or return `NOT_SURE`.

#### 4.4 ✅ Done — Word-Boundary Matching & Accurate Digest Answers (`src/ba_agent/agent/loop.py`)
- **Implementation**:
  1. Replace raw substring checks (`kw in digest_low` / `kw in low`) in `_is_off_topic()` (`line 889`) and `_rank_lines()` (`line 914`) with word-boundary regex (`re.search(rf"\b{re.escape(kw)}", text)`).
  2. In `_rank_lines()` (`lines 896-922`), include `"[not started]"` alongside `"[todo]"` and `"[in progress]"` when `want_open` is `True`, and exclude `"[done]"` / `"[completed]"` lines when `want_open and not want_done`.
  3. In `_answer_from_digest()` (`line 840`) and `_resolve_meeting_reply()` (`lines 692-708`), when `keywords` targets a specific owner/topic and `matched_tasks` is empty, return `"No recorded tasks matched that query."` instead of dumping `task_lines[:8]`, and do not override an LLM reply that accurately states no tasks exist for that person.

---

### Phase 5: Task Extraction, Spoken Date Parsing & Safe Transcript Deduplication

#### 5.1 ✅ Done — Full Spoken-English Due-Date Parser & Timezone Safety (`src/ba_agent/tasks_extract/dates.py`)
- **Implementation**: Upgrade `parse_due(text, *, today)`, `short_due()`, and `today_in()` while preserving every assertion in `test_t09_u1_dates` and `test_dates_timezones_rollover_and_short_due`:
  1. **Ordinal day suffixes & `"of"` connector**: Support `"October 28th"`, `"Nov 2nd"`, `"1st May"`, `"24th of October"`, `"the 30th of Oct"` via `r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_PATTERN})\b"` and `r"\b({_MONTH_PATTERN})\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b"`.
  2. **Same-day / EOD cues**: `"tonight"`, `"this evening"`, `"end of day"`, `"by eod"`, `"cob"` -> `today`.
  3. **Relative offsets**: `"in (\d+|one|two|three|four|five|six|seven) days?"` -> `today + timedelta(days=n)`; `"in (\d+|a|one|two) weeks?"` -> `today + timedelta(weeks=n)`.
  4. **Week cues (strictly ordered AFTER named weekday matching!)**:
     - Keep `next <weekday>` (`following_week=True`) and `<weekday>` / `this <weekday>` (`following_week=False`) **first** so `"Wednesday this week"` resolves to Wednesday!
     - Evaluate `"end of (the )?week"`, `"by eow"`, and standalone `"this week"` **after** named weekdays -> upcoming Friday (`_weekday(today, 4, following_week=False)`).
     - Evaluate standalone `"next week"` -> next Monday (`_weekday(today, 0, following_week=True)`).
  5. **Word-boundary fix in `short_due()` (`dates.py:126`) & safe `today_in()`**: Use `re.search(rf"\b{re.escape(name)}\b", token)` in `short_due()` so `"next month"` never matches `"mon"`, and catch `(ZoneInfoNotFoundError, KeyError)` in `today_in()` with a logged fallback to `UTC` if a user misconfigures `BA_MEETING__TIMEZONE`.

#### 5.2 ✅ Done — Model JSON `"due"` Fallback, Broader Task Grammar & Alias Normalization (`extractor.py` & `session.py`)
- **Implementation**:
  1. **Fix `tasks_from_model_json()` (`extractor.py:164`)**: Read `due_text = str(item.get("due_text") or item.get("due") or "").strip()` so tasks are never silently dropped when the LLM outputs `"due"` instead of `"due_text"`.
  2. Expand `_TASK_COMMAND` (`extractor.py:13-16`) to match `add/create/log/track/record/note (a )?(new )?(task|action item|to-do|todo)` and `assign <owner> to <task>` / `assign a task to/for <owner> to <task>`.
  3. Map first-person pronouns (`"i"`, `"me"`, `"myself"`) to `said_by` (when `said_by != "Unknown speaker"`) and apply `normalize_speaker(owner, aliases)` so `"Ali K."` and `"Ali"` produce identical `owner` and `dedup_key` values.
  4. In `session.py:474-479` (`on_caption`), keep appending non-addressed speech to `extractor.recent` even within `6s` of a `"Hey BA"` turn so multi-line commitments (`"Ali, can you send the spec?"` -> `"Sure, by Friday"`) retain context.

#### 5.3 ✅ Done — Length-Guarded ASR Revision Merging (`src/ba_agent/captions/stabilize.py:165-176`)
- **Critical trap avoided**: Using `len(overlap) / min(len(prev), len(new)) >= 0.6` is unsafe because `Stabilizer.push_frame()` (`lines 91-99`) **overwrites** `current.text = text` without committing when `_same_utterance()` returns `True`! If a 10-word unpunctuated sentence were followed on the same `row_id` by a new 3-word phrase sharing 2 words (`2/3 = 0.67`), the 10-word caption would be silently erased!
- **Safe implementation**:
  - In `_same_utterance(previous: str, new: str) -> bool`, in addition to existing prefix and `prev_words[0] == new_words[0]` checks, treat `new` as a leading-word ASR revision of `previous` **only** when:
    1. `len(prev_words) >= 3` and **`len(new_words) >= len(prev_words) - 1`** (never treat a sharp length drop as an in-place revision!),
    2. `previous` did not end with `.?!` unless `new` also contains the rest of `previous`, and
    3. `prev_words[1:] == new_words[1:]` or `prev_words[1:] == new_words[2:]` or `difflib.SequenceMatcher(None, prev_words, new_words).ratio() >= 0.75`.
  - That cleanly merges `"Think we should use LFP"` -> `"I think we should use LFP"` while guaranteeing zero transcript loss on short subsequent utterances.

---

### Phase 6: Cloudflare Container Idle Sleep, SDK-Safe `peek=1` & Worker Observability

#### 6.1 ✅ Done — SDK-Safe `peek=1` Port Read, 15-Minute Idle Exit & Structured Worker Logs (`cloudflare/src/index.ts`, `session.py` & `wrangler.jsonc`)
- **Two critical `@cloudflare/containers@0.3.7` traps to avoid**:
  1. **Never override `alarm()` or call `ctx.storage.setAlarm()` on `BaContainer`**: In `node_modules/@cloudflare/containers/dist/lib/container.js:1502-1590`, `Container.prototype.alarm()` is the internal heartbeat managing `sleepAfter`, `onActivityExpired()`, `syncPendingStoppedEvents()`, and `container_schedules`. Overriding `alarm()` breaks `@cloudflare/containers` lifecycle management! Instead, use the SDK's built-in `await this.schedule(delaySeconds, "stopIfIdle")` + `this.deleteSchedules("stopIfIdle")`.
  2. **Never permanently latch `idle = true` in `BaContainer`**: `POST /join` returns HTTP 200 while Python's `loop.call_soon_threadsafe(_schedule)` (`session.py:87-107`) has not yet flipped `status.stage` from `"idle"` to `"joining"` (`1–50ms` race). Permanently latching `idle = true` on `peek=1` would freeze `peek=1` as `idle: true`, causing `extension/content.js:382` (`needsSummon`) to fire `POST /join` with `force: true` every 12s (`adapter.ended_event.set()`) and abort lobby admission in an infinite loop!
- **Verified Implementation**:
  1. **Non-renewing `peek=1` reads via `getTcpPort(8080)` (`cloudflare/src/index.ts:164-204`)**:
     - Cache `/status` responses in `BaContainer` memory for `1,500ms` (`lastStatusCache = { at, payload }`) to coalesce concurrent tab + popup polls.
     - On cache miss for `GET /status?peek=1` when `this.ctx.container?.running` is `true`, query Python directly via **`this.ctx.container.getTcpPort(this.defaultPort).fetch("http://localhost/status")`** instead of `super.fetch(...)`! Unlike `super.fetch()` (`container.js:887-891`), `getTcpPort(8080).fetch()` **never calls `this.renewActivityTimeout()`**, never resets `sleepAfter`, and always returns live, unlatched container state!
  2. **Dual-layer 15-minute idle shutdown (stops the `103 GiB-h` zero-traffic burn while keeping `sleepAfter = "3h"` for `test_t15_deploy.py`)**:
     - **Python layer (`src/ba_agent/session.py:154` & `config.py`)**: Add `browser.idle_timeout_s: int = 900` (`15 minutes`). Replace bare `await next_url_event.wait()` with `await asyncio.wait_for(next_url_event.wait(), timeout=settings.browser.idle_timeout_s)`; on `TimeoutError`, break out of `run_join` cleanly so Xvfb + Chromium exit and the container stops immediately after 15 idle minutes (eliminating both the 3h `sleepAfter` tail and DO-eviction constructor timer resets!).
     - **DO safety net (`cloudflare/src/index.ts`)**: When `peek=1` or `/status` observes `payload.idle === true` and no `stopIfIdle` schedule is queued, call `await this.schedule(900, "stopIfIdle")` (and cancel via `this.deleteSchedules("stopIfIdle")` on `POST /join`), where `async stopIfIdle()` re-checks `getTcpPort(8080)` `idle === true` before calling `await this.stop("SIGTERM")`.
  3. **Structured Worker Observability & Config (`cloudflare/src/index.ts` & `wrangler.jsonc`)**:
     - Emit structured JSON `console.log(JSON.stringify({ event, meetUrl, coldStart, durationMs, ... }))` and `console.error(...)` on `/join`, `stopForNextMeeting`, `onError`, and `stopIfIdle` (never logging secrets) so `wrangler tail` and Workers Logs capture every lifecycle transition instead of `logs: []`.
     - Update `wrangler.jsonc` with `"compatibility_flags": ["nodejs_compat"]` and `"observability": { "enabled": true, "head_sampling_rate": 1 }` (preserving all assertions in `tests/test_t15_deploy.py`).

---

## 6. Verification, Benchmarking & Regression Test Plan

### 6.1 Current Verified Test Baseline (`132 passed, 1 skipped`)
As part of this audit, two comprehensive component test suites (`tests/test_unit_components.py` and `tests/test_integration_components.py`, **29 new tests**) were added and verified against the current codebase:
- `uv run pytest -v` -> **`132 passed, 1 skipped`** (`test_t06_i1_live_web_search` gated by `BA_RUN_LIVE_TESTS=1`).
- `npx tsc --noEmit` -> **passes (`0` errors)**.
- `uv run ruff check tests/test_unit_components.py tests/test_integration_components.py` -> **passes (`0` errors)**. *(Note: `uv run ruff check .` currently reports **46 pre-existing `E501`/`I001` lint errors** across 6 untouched files: `scripts/notion_seed.py`, `src/ba_agent/session.py`, `src/ba_agent/trigger/match.py`, `tests/test_t02_t04_meet.py`, `tests/test_t11_notion.py`, `tests/test_t12_t14_agent.py`; clean these up during implementation so `uv run ruff check .` can serve as a strict CI gate.)*

Pay special attention to preserved test contracts during v2 implementation:
- `tests/test_t01_config.py`: `settings.llm.model == "gpt-6-luna"`, `stable_ms == 800`, `pause_wait_s == 5`, `min_score == 0.5`.
- `tests/test_t06_t07_llm.py`: `build_search_request()` excludes transcript; knowledge files never contain `"hey ba"`.
- `tests/test_t11_notion.py`: Exact `POST /pages` and `PATCH /blocks/{id}/children` call counts on mock transport, AND `len(await service.list_summaries(refresh=True)) == 1` after `save_meeting()`.
- `tests/test_t12_t14_agent.py`: `FakeLLM.respond(self, input, tools=None, *, enable_web_search=False, timeout_s=None)` 4-arg signature compatibility; `ToolLLM` receives `tools` when `web is None`; `fetch_db_context` fallback runs when `hits` and `web` are both empty.
- `tests/test_t15_deploy.py`: Required literals in `wrangler.jsonc` and `cloudflare/src/index.ts` (`sleepAfter = "3h"`, `idFromName("meeting")`, `portReadyTimeoutMS`, `enableInternet = true`, `timingSafeEqual`, zero secret prefixes).
- `tests/test_unit_components.py` & `tests/test_integration_components.py`: Full CLI/config/redaction, stabilizer/pump/chat/event-bus, wake/trigger edge cases, OpenAI 404 model fallback + DDG/Wikipedia fallback, Notion 429/503 retries + cursor pagination + local dedup, Status HTTP/CORS server, full `_run_single_meeting` pipeline, and Worker/Extension/Python Meet URL parity.

### 6.2 Additional v2 Feature Tests to Add During Implementation

| Test File | Status | New v2 Feature Test Cases |
|---|---|---|
| `tests/test_t02_t04_meet.py` | ✅ Done | 1. `test_stabilizer_fast_commits_question_mark_wake_only`: `"Hey BA, what is a hybrid?"` commits at `450ms`, whereas unpunctuated `"Hey BA, what is the"` waits `800ms` so breath pauses never truncate questions.<br>2. `test_stabilizer_leading_word_asr_revision_does_not_split_or_clobber`: `"Think we should use LFP"` -> `"I think we should use LFP"` commits once, while a subsequent short 3-word caption never overwrites an uncommitted 10-word caption. |
| `tests/test_t05_trigger.py` | ✅ Done | `test_bare_wake_adaptive_pause_expires_early_when_speaker_silent`: Bare `"Hey BA"` expires at `~2.2s` when `is_speaker_active` is `False`, but waits up to `5.0s` while `is_speaker_active` is `True`. |
| `tests/test_t06_t07_llm.py` | ✅ Done | 1. `test_bm25_acronym_and_four_letter_plural_queries_hit_knowledge`: `"what does AEB stand for in modern cars?"`, `"what is ESC in SUVs?"`, `"WLTP vs EPA range"` score `>= 0.5` locally with `needs_current_fact == False`.<br>2. `test_search_cars_ttl_cache_and_warm_up_model_fallback`: `warm_up()` resolves `gpt-6-luna` -> `gpt-4o-mini` via `models.retrieve()`, and repeated `search_cars()` queries hit `_web_cache`. |
| `tests/test_t09_tasks.py` | ✅ Done | 1. `test_parse_due_ordinals_eod_eow_and_weekday_precedence`: Tests `"October 28th"`, `"24th of October"`, `"tonight"`, `"by EOD"`, `"end of week"`, `"Wednesday this week"` (-> Wednesday, not Friday), `"in 3 days"`, and `short_due("next month", ...)` != `"Mon"`.<br>2. `test_model_json_due_key_and_pronoun_alias_normalization`: Verifies `tasks_from_model_json` accepts `"due"` as well as `"due_text"`, maps `"I"`/`"me"` to `said_by`, and normalizes `"Ali K."` -> `"Ali"`. |
| `tests/test_t11_notion.py` | ✅ Done | 1. `test_live_save_keeps_summaries_cache_and_excludes_current_in_db_context`: Verifies `_save_live(..., append_children=False)` keeps `_summaries_cache` warm and `database_context(exclude_current_meeting=True)` omits `self.summary_page_id`.<br>2. `test_burst_limiter_parallel_db_context_and_packed_transcript_toggle`: Verifies 250-line transcript packs into multi-line toggle paragraphs without truncating at line 80. |
| `tests/test_t12_t14_agent.py` | ✅ Done | 1. `test_pure_car_question_skips_notion_db_fetch`: Verifies `fetch_db_context` is NOT awaited on `"what is a hybrid?"` when local KB hits.<br>2. `test_automotive_meeting_questions_do_not_false_not_sure`: Verifies `"has Omar finished the brake pad shortlist?"` and `"what target did we set for DC fast charging?"` answer from `<meeting_digest>` instead of returning `NOT_SURE`.<br>3. `test_open_vs_done_task_filtering_and_missing_owner_precision` & `test_word_boundary_off_topic_blocks_substring_collisions`. |
| `tests/test_t13_guardrails.py` | ✅ Done | `test_citation_source_verification_and_grounded_auto_citation`: Rejects fabricated `(src: nonexistent.md)` and ungrounded prices while auto-attaching single verified source when all numbers are grounded. |
| `tests/test_t15_deploy.py` | ✅ Done | `test_worker_peek_uses_non_renewing_tcp_port_and_structured_logs` & `test_keep_alive_exits_cleanly_after_idle_timeout`. |

---

## 7. Ready-to-Run Implementation Prompt

Copy and paste the prompt below into an agent session when you are ready to implement this plan:

```markdown
Implement the speed, response-accuracy, RAG, task-extraction, observability, and container-idle improvements specified in `docs/speed-and-accuracy-improvement-plan.md`.

### Execution Rules
1. Read `docs/speed-and-accuracy-improvement-plan.md` completely first, and read each target file before editing.
2. Preserve 100% compatibility with all 133 tests in `tests/` (including `settings.llm.model == "gpt-6-luna"` in `test_t01_config.py`, `list_summaries()` behavior in `test_t11_notion.py`, `FakeLLM.respond` 4-arg signatures via `inspect.signature` guards, `ToolLLM` tool-passing when `web is None` in `test_t12_t14_agent.py`, and required Worker strings in `test_t15_deploy.py`).
3. Execute in order:
   - **Phase 1 (Critical-Path Speed)**: Conditional/parallel `_full_digest()` and safe `tools=None` (only when `web is not None and web.useful`) in `src/ba_agent/agent/loop.py`; zero-token `warm_up()` via `models.retrieve()`, safe `max_output_tokens >= 450` (omitted when `enable_web_search=True`), low-reasoning effort on reasoning models, `_web_cache`, and pooled `_web_http` in `src/ba_agent/llm/openai_client.py`.
   - **Phase 2 (Caption/Trigger/UI Speed on 0.5 vCPU)**: `450ms` fast-commit ONLY on `?`-terminated wake questions (`>= 3` words) and length-guarded sequence alignment in `_same_utterance()` (`src/ba_agent/captions/stabilize.py`); live-speech-aware adaptive pause in `src/ba_agent/trigger/detector.py`; single `window.__baPollAll()` IPC at `200-250ms` + fast-path `post_chat()` in `src/ba_agent/bot/google_meet/scripts.py`, `adapter.py`, and `session.py`; real `caption_commit_ms` in `log_timing()`.
   - **Phase 3 (Notion & SQLite Speed)**: Preserve `_summaries_loaded_at` on `append_children=False` live syncs, add `exclude_current_meeting=True` on `database_context()`, upgrade `_Limiter` to `burst=2` + `asyncio.gather`, add 120s stale-while-revalidate + startup warm-up, sort summaries descending, and pack multi-line paragraphs in `_transcript_toggle()` (`src/ba_agent/notion/client.py`); debounce `live_notes()` to `15s`; bound `store.digest()` to `last(400)` and include `[status]` + ISO due dates (`src/ba_agent/memory/store.py`).
   - **Phase 4 (Response Accuracy & BM25 RAG)**: Context-aware `classify_query_intent()` and scoped `"epa"`/`"wltp"` `_CURRENT` rules in `src/ba_agent/knowledge/policy.py`; `>=4`-char plural stemming + BM25/IDF + synonym/acronym scoring in `src/ba_agent/knowledge/search.py` + enriched `data/knowledge/*.md` (no `"hey ba"` string); verified source/number grounding in `src/ba_agent/guardrails/citations.py`; word-boundary matching, `[Not started]`/`[Done]` filtering, and exact empty-owner task replies in `src/ba_agent/agent/loop.py`.
   - **Phase 5 (Task & Spoken Date Accuracy)**: Ordinals (`Oct 28th`, `24th of Oct`), `tonight`, `EOD`, `EOW`, `this week` (ordered AFTER named weekdays), `next week`, `in N days`, word-boundary `short_due()`, and safe `today_in()` in `src/ba_agent/tasks_extract/dates.py`; `"due"`/`"due_text"` fallback in `tasks_from_model_json()`, expanded task commands, and pronoun/alias normalization in `src/ba_agent/tasks_extract/extractor.py` and `src/ba_agent/session.py`.
   - **Phase 6 (Cloudflare Idle Efficiency & Observability)**: `1.5s` DO status cache + non-renewing `this.ctx.container.getTcpPort(8080).fetch()` on `peek=1` (never overriding `alarm()` and never permanently latching `idle`), `15m` `idle_timeout_s` in `session.py:154` + SDK `this.schedule(900, "stopIfIdle")` in `cloudflare/src/index.ts`, structured JSON Worker `console.log`/`console.error` events, and `nodejs_compat` + `head_sampling_rate: 1` in `wrangler.jsonc`.
4. Add all tests listed in Section 6.2 of `docs/speed-and-accuracy-improvement-plan.md` and fix the 46 pre-existing `ruff` lint errors across existing files.
5. Run `uv run pytest -v`, `uv run ruff check .`, and `npx tsc --noEmit` until everything passes with zero warnings or errors.
```
