# T01 — Project setup, configuration and domain models

**Priority:** Must · **Milestone:** M1 Foundations · **Depends on:** — · **Estimate:** 1 day

## Why

- The assignment requires the **LLM to be swappable by config** (C1) and the whole system to start with `docker compose up` on TechSol's VM. That requires one configuration system from day one, not settings scattered through the code.
- 17 other tasks share the same data (utterances, tasks, decisions…). Defining the domain models once avoids mismatched field names between the listener, the classifier, the agent and Notion.
- AI coding assistants are allowed only **if declared** (C9). Keep a declaration log from the first commit so the README section (T17) is accurate.

## What

**In scope**
- Repository layout, Python packaging, dependency management.
- Typed configuration: YAML defaults overridden by environment variables, validated at startup.
- Structured logging with meeting ID and timing spans (used by T14).
- Core domain models shared across tasks.
- Test, lint and type-check tooling; a CI workflow if the provided private repo supports it.
- `.gitignore` / `.dockerignore` for secrets, browser profiles, model caches.
- `AI_USAGE.md` log (feeds the README declaration).

**Out of scope**
- Docker images (T15), any feature logic.

## Details

### Stack
- Python 3.11+, `asyncio` throughout (bot, audio, LLM and Notion calls all run concurrently).
- Dependency management: `uv` with a `pyproject.toml` and a lockfile.
- Config: `pydantic-settings` (YAML file + env vars, nested delimiter `__`, prefix `BA_`). Example: `BA_LLM__MODEL=llama3.1:8b`.
- Logging: `structlog` (or stdlib `logging` with a JSON formatter). Every log line carries `meeting_id`, and timing spans carry `span`, `duration_ms`.
- Quality: `pytest`, `pytest-asyncio`, `ruff` (lint + format), `mypy` (non-strict to start), `pre-commit`.

### Repository layout

```
ba-meeting-agent/
  docker-compose.yml
  .env.example
  config/
    settings.yaml            # defaults (no secrets)
    notion_schema.yaml       # logical field → Notion property mapping (T11)
    people.yaml              # participant name aliases (T04)
    pack_mapping.yaml        # pack column headers → data model fields (T08)
    systems.yaml             # car-system synonyms (T08)
    prompts/                 # persona + classifier prompts (T09, T12)
  src/ba_agent/
    app.py                   # entrypoint, wires everything together
    config.py
    models.py                # domain models (below)
    bot/                     # meeting platform adapters (T02)
    audio/                   # capture, VAD, STT (T03)
    transcript/              # transcript store, speaker attribution (T04)
    trigger/                 # wake phrase detection (T05)
    llm/                     # LLM client, priority lanes (T06)
    knowledge/               # ingest + retrieval (T07)
    parts/                   # BOM + compare (T08)
    classifier/              # T09
    memory/                  # meeting state, recall (T10)
    notion/                  # client, schema, write tools (T11)
    agent/                   # persona, tool registry, orchestrator, formatter (T12)
    guardrails/              # T13
    observability/           # spans, latency report (T14)
  data/pack/                 # provided fictional pack (mounted into the container)
  data/primer/               # optional reviewed general-knowledge notes (T07); no Falcon numbers
  data/state/                # SQLite state, git-ignored (T10)
  data/debug/                # screenshots + DOM dumps on selector failures, git-ignored (T02)
  tests/{unit,integration,e2e,fixtures,eval}/   # eval = shared datasets (T16)
  scripts/                   # notion_setup.py, latency_report.py, replay.py, smoke_test.sh …
  docs/{tasks,diagrams}/
  docs/QUESTIONS.md          # open questions for the TechSol point of contact
  docs/runbook.md            # live-day runbook (T16)
  submission/                # Notion export, latency report, eval results (T17)
  AI_USAGE.md
```

### Configuration keys (canonical list; other tasks refer to these names)

| Key | Default | Used by |
|---|---|---|
| `meeting.platform` | `google_meet` | T02 |
| `meeting.url` | — (passed at join time) | T02 |
| `meeting.join_mode` | `signed_in` (`guest` also allowed) | T02 |
| `meeting.bot_name` | `TechSol BA` | T02 |
| `meeting.title_default` | `Project Falcon meeting` (used when `ba join` has no `--title`) | T02, T11 |
| `meeting.consent_message` | see T02 | T02 |
| `meeting.notes_link_in_consent` | `true` (append the Meeting Notes URL to the consent notice, see T02/T11) | T02, T11 |
| `meeting.timezone` | `UTC` | T09, T11 |
| `meeting.lobby_timeout_s` / `meeting.alone_timeout_s` | `300` / `120` | T02 |
| `meeting.reconnect_attempts` | `3` | T02 |
| `audio.sample_rate` | `16000` | T03 |
| `vad.threshold` / `vad.min_silence_ms` / `vad.max_utterance_s` | `0.5` / `700` / `20` | T03 |
| `stt.model` / `stt.device` / `stt.compute_type` / `stt.language` / `stt.beam_size` / `stt.hotwords` | `large-v3-turbo` / `cuda` / `float16` / `en` / `1` / list | T03 |
| `speakers.source` / `speakers.aliases_file` / `speakers.audio_offset_ms` | `ui` / `config/people.yaml` / calibrated (T04) | T04 |
| `trigger.phrases` / `trigger.fuzzy_threshold` / `trigger.followup_window_s` | `["hey ba"]` / `85` / `20` | T05 |
| `trigger.pause_wait_s` / `trigger.split_wait_s` / `trigger.dedup_window_s` | `5` / `1.5` / `3` | T05 |
| `llm.base_url` / `llm.model` / `llm.api_key` | `http://ollama:11434/v1` / `qwen2.5:7b-instruct` / `ollama` | T06 |
| `llm.temperature` / `llm.max_tokens` / `llm.timeout_s` / `llm.max_tool_rounds` / `llm.allow_remote` | `0.1` / `300` / `6` / `3` / `false` | T06, T12, T14 |
| `embeddings.model` | `BAAI/bge-small-en-v1.5` | T07, T10 |
| `vectordb.url` | `http://qdrant:6333` | T07 |
| `knowledge.pack_dir` / `knowledge.top_k` / `knowledge.min_score` | `/data/pack` / `5` / tuned in T07 | T07, T13 |
| `bom.long_lead_weeks` | `12` | T08 |
| `classifier.batch_seconds` / `classifier.auto_create_tasks` / `classifier.auto_log_decisions` / `classifier.task_confidence` / `classifier.decision_confidence` | `15` / `true` / `true` / `0.75` / `0.8` | T09 |
| `notion.enabled` | `true` (`false` only for local tests without a workspace; writes are then logged, not sent) | T11 |
| `notion.token` / `notion.version` / `notion.schema_file` / `notion.max_rps` | secret / pinned API version / `config/notion_schema.yaml` / `3` | T11 |
| `notion.databases.{meeting_notes,tasks,decisions,requirements,risks}` | IDs (risks only for T18) | T11, T18 |
| `notion.autosave_minutes` | `0` (off; `>0` re-runs `save_meeting()` silently every N minutes as a crash safety net, see T11) | T11 |
| `reply.max_lines` / `reply.max_table_rows` / `reply.max_chars` / `reply.confirmation_mode` / `reply.batch_interval_s` / `reply.deadline_s` / `reply.hard_timeout_s` | `6` / `8` / `32000` (Meet's per-message limit) / `immediate` (`batched` also allowed) / `120` / `8.5` / `20` | T02, T09, T11, T12, T14 |
| `control.host` / `control.port` | `127.0.0.1` / `8080` | T15 |
| `storage.sqlite_path` | `/data/state/meeting.db` | T10, T11 |
| Bonus only: `classifier.auto_log_risks` / `voice.enabled` | `false` / `false` | T18 |

Secrets (`BA_NOTION__TOKEN`, meeting account details) come only from env or `.env`, never YAML. Mark them as `SecretStr` so they never appear in logs.

### Domain models (`models.py`, pydantic)

```python
class Word:           text, start_s, end_s                      # Whisper word timestamps (T03)
class Utterance:      id, meeting_id, speaker, speaker_confidence, co_speaker?, start_s, end_s,
                      wall_time (UTC), text, words: list[Word], source: "speech" | "chat"
class ChatMessage:    sender, text, wall_time, is_self
class SpeakingEvent:  wall_time, name, speaking: bool           # UI active-speaker samples (T02 → T04)
class Label(Enum):    DECISION, TASK, QUESTION, RISK, REQUIREMENT, NOTE
class ClassifiedItem: utterance_id, label, confidence, summary, owner?, due?, due_text?,
                      reason?, addressed_to_ba: bool
class TaskRecord:     task, owner, due (date | None), due_text?, status, said_by, time,
                      source: "speech" | "request", notion_page_id?, key
class DecisionRecord: decision, by, time, reason?, superseded_by?, notion_page_id?, key
class RequirementRecord: story, criteria, moscow: "Must"|"Should"|"Could"|"Won't", notion_page_id?, key
class TriggerEvent:   trigger_id, question, speaker, source, utterance_id, t_speech_end, t_detected
```

`time` is always the meeting clock (`mm:ss`) plus wall time. These names line up with the tool signatures in the PDF (`said_by`, `time`, `by`, `reason`, `story`, `criteria`, `moscow`).

### AI usage declaration
Create `AI_USAGE.md` with a running log: tool used (e.g. Cursor), what for (scaffolding, tests, docs), and what a human reviewed. T17 summarises it in the README.

## Acceptance criteria

- [ ] `uv sync && uv run pytest` passes on a clean clone.
- [ ] `uv run ruff check . && uv run ruff format --check .` pass.
- [ ] Config loads from `config/settings.yaml`. Any key can be overridden by a `BA_…` env var. Invalid config (e.g. unknown `meeting.platform`, missing Notion token when Notion is enabled) stops startup with a readable error.
- [ ] Secrets never appear in logs or in `repr()` of the settings object.
- [ ] Domain models exist and other tasks import them instead of redefining them.
- [ ] `.gitignore` excludes `.env`, browser profile dirs, model caches, `data/state/`.
- [ ] `AI_USAGE.md` exists with the first entry.

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T01-U1 | unit | Default settings load from YAML | All keys in the table have values |
| T01-U2 | unit | Env override `BA_LLM__MODEL=llama3.1:8b` | `settings.llm.model == "llama3.1:8b"` |
| T01-U3 | unit | Invalid enum / missing required secret | Raises a validation error naming the key |
| T01-U4 | unit | `repr(settings)` and a log line containing settings | No token value present |
| T01-U5 | unit | Domain model round-trip (JSON serialise / parse) | Equal objects; `Label` covers exactly the 6 labels from the PDF |
| T01-C1 | CI | Lint + tests on push | Green |

## Dependencies

None. Every other task depends on this one.

## Related resources

- uv: https://docs.astral.sh/uv/
- pydantic-settings: https://docs.pydantic.dev/latest/concepts/pydantic_settings/
- structlog: https://www.structlog.org/
- pytest-asyncio: https://pytest-asyncio.readthedocs.io/
- Ruff: https://docs.astral.sh/ruff/
- pre-commit: https://pre-commit.com/
