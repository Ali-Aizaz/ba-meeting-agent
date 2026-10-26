# T01 — Project setup, configuration and domain models

**Priority:** Must · **Depends on:** — · **Estimate:** part of the one-day build

## Why

- One config object so the OpenAI model, Notion ids, and Meet join mode are not scattered through the code.
- Shared models so captions, tasks, and Notion use the same field names.
- AI coding assistants are declared in `AI_USAGE.md` (C10).

## What

**In scope**
- Python project layout, `uv`, `pyproject.toml`.
- Settings: YAML defaults, `BA_` env overrides, secrets only from the environment.
- Domain models below.
- `pytest`, `ruff`. `AI_USAGE.md`.

**Out of scope**
- Feature logic (later tasks). A GPU image.

## Details

### Stack

- Python 3.11+, asyncio.
- `uv`, pydantic-settings (`BA_` prefix, nested `__`).
- Logging with `meeting_id`.

### Layout

```
ba-meeting-agent/
  .env.example
  config/
    settings.yaml
    notion_schema.yaml       # filled in when the Notion DB is attached (T11)
    people.yaml               # speaker aliases (T04)
    prompts/persona.md        # T12
  src/ba_agent/
    app.py
    config.py
    models.py
    bot/                      # T02
    captions/                 # T03, T04
    trigger/                  # T05
    llm/                      # T06
    knowledge/                # T07
    tasks_extract/            # T09
    memory/                   # T10
    notion/                   # T11
    agent/                    # T12
    guardrails/               # T13
  data/knowledge/             # car notes we write (T07)
  data/state/                 # SQLite, git-ignored
  data/debug/                 # screenshots, git-ignored
  tests/
  docs/
  AI_USAGE.md
```

### Configuration keys

| Key | Default | Used by |
|---|---|---|
| `meeting.platform` | `google_meet` | T02 |
| `meeting.join_mode` | `signed_in` (`guest` allowed) | T02 |
| `meeting.bot_name` | `TechSol BA` | T02 |
| `meeting.consent_message` | see T02 | T02 |
| `meeting.timezone` | `UTC` | T09, T11 |
| `meeting.lobby_timeout_s` / `meeting.alone_timeout_s` | `300` / `60` | T02 |
| `captions.stable_ms` / `captions.language` | `800` / `en` | T03 |
| `speakers.aliases_file` | `config/people.yaml` | T04 |
| `trigger.phrases` / `trigger.fuzzy_threshold` / `trigger.followup_window_s` | `["hey ba"]` / `85` / `20` | T05 |
| `trigger.pause_wait_s` / `trigger.dedup_window_s` | `5` / `3` | T05 |
| `llm.provider` / `llm.model` / `llm.api_key` / `llm.timeout_s` | `openai` / from env / secret / `25` | T06 |
| `llm.base_url` | `https://api.openai.com/v1` | T06 |
| `knowledge.dir` / `knowledge.min_score` | `data/knowledge` / tuned in T07 | T07 |
| `tasks.auto_create` | `true` | T09 |
| `notion.enabled` / `notion.token` / `notion.schema_file` | `true` / secret / `config/notion_schema.yaml` | T11 |
| `notion.databases.summary` / `notion.databases.tasks` | empty until the DB is attached | T11 |
| `storage.sqlite_path` | `data/state/meeting.db` | T10 |

Secrets (`BA_LLM__API_KEY`, `BA_NOTION__TOKEN`) are `SecretStr` and never logged.

### Domain models

```python
class CaptionLine:    id, meeting_id, speaker, speaker_raw, start_s, end_s, wall_time, text
class ChatMessage:    sender, text, wall_time, is_self
class TaskRecord:     task, owner, due (date | None), due_text, status, said_by, time,
                      notion_page_id?, key
class TriggerEvent:   trigger_id, question, speaker, source: "speech" | "chat",
                      caption_id, t_detected
class Citation:       kind: "knowledge" | "web", source: str   # file heading or URL
```

## Acceptance criteria

- [ ] `uv sync && uv run pytest` passes on a clean clone.
- [ ] `BA_LLM__MODEL` overrides the YAML model.
- [ ] A missing API key or a missing Notion token (when Notion is enabled) fails startup with the key name.
- [ ] Secrets do not appear in `repr(settings)`.
- [ ] `AI_USAGE.md` exists.

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T01-U1 | unit | YAML defaults load | Keys in the table exist |
| T01-U2 | unit | `BA_LLM__MODEL` override | Model matches the env |
| T01-U3 | unit | Missing secret | Validation error names the key |
| T01-U4 | unit | `repr(settings)` | Token value absent |

## Dependencies

None. Every other task depends on this.

## Related resources

- uv: https://docs.astral.sh/uv/
- pydantic-settings: https://docs.pydantic.dev/latest/concepts/pydantic_settings/
