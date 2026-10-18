# T17 — README, two diagrams, Notion export, submission

**Priority:** Must · **Milestone:** M5 Submission · **Depends on:** all Must tasks · **Estimate:** 1.5 days

## Why

The PDF's "Submit" section requires:
- **Code + Docker + config** (built in T01–T15; checked here).
- **Your two diagrams.**
- **Notion export.**
- **README with setup, tech choices and answers to: platform choice, latency budget, wake-word design, speaker tagging, stopping invented specs.**

Plus: **AI coding assistants allowed if declared** (C9), and no real company data or production accounts (C5).

## What

**In scope**
- README.md at the repo root.
- Two diagrams (Mermaid source + rendered images).
- Notion export after the final rehearsal / live test.
- Submission checklist, secrets scan, final clean-clone verification.

**Out of scope**
- New features.

## Details

### README structure
1. **What it is:** 3–4 sentences + a screenshot of the bot in Meet chat and of the Notion Meeting Notes page.
2. **Architecture:** embed both diagrams. One paragraph per main component.
3. **Setup**
   - Prerequisites (VM with 24 GB GPU, NVIDIA driver, Docker, NVIDIA Container Toolkit).
   - Notion: integration token, share databases with the integration, put DB IDs in `.env`, adjust `config/notion_schema.yaml` if property names differ (T11).
   - Meeting account: one-time sign-in via noVNC (T02/T15), or guest mode.
   - Knowledge pack: copy to `data/pack/`. Ingest runs automatically (T07).
   - `docker compose up -d`, smoke test, `ba join <url>`.
4. **Configuration:** key settings table (link to T01's full list). **How to swap the LLM** with examples (Llama 3.1 8B, vLLM, the remote guard).
5. **Using it in a meeting:** "Hey BA …" examples, what it writes to Notion, how to end and save.
6. **Tech choices:** table of component → choice → why → alternatives considered (Meet/Playwright, faster-whisper model, Silero VAD, Qwen2.5 7B via Ollama + benchmark numbers from T06, bge + Qdrant hybrid, SQLite state, Notion API version).
7. **Required answers** (each 1–3 short paragraphs, using the drafts in the source tasks):
   | Question | Source draft |
   |---|---|
   | Platform choice | T02 "Platform decision" |
   | Latency budget | T14 budget table + measured p50/p95/max from the report |
   | Wake-word design | T05 "Design choice" + follow-up window |
   | Speaker tagging | T04 "README answer draft" |
   | Stopping invented specs | T13 "README answer draft" |
8. **Design decisions and limitations:** auto-writes from speech and their chat confirmations (T09), notes link in the consent notice (T02), "Hey BA" + silence gets a clarifying question (T05), honest failure line (T12/T14), DOM fragility, mixed audio, English only, one meeting at a time, known failure modes. Include the answers TechSol gave to `docs/QUESTIONS.md` and which working assumptions were left in place.
9. **Testing:** how to run unit/integration/e2e. Latest results (eval metrics, rehearsal outcome).
10. **Privacy and data:** consent notice, self-hosted by default, no hosted bot/STT, fictional data only, where data is stored and how to delete it.
11. **AI coding assistant declaration:** which tools, for what, and that a human reviewed the output (from `AI_USAGE.md`).

### The two diagrams (open question Q36)
The PDF says "your two diagrams" without defining them. Working assumption, to be confirmed with the point of contact:
1. **System architecture / deployment diagram:** the VM boundary; containers (`agent` with bot/audio/STT/agent/Notion modules, `ollama`, `qdrant`); the GPU; external Google Meet and Notion; data flows (audio, chat, API calls); what is self-hosted.
2. **"Hey BA" request sequence diagram:** participant speaks → PulseAudio → VAD → Whisper → speaker tagging → trigger → orchestrator → guardrail → LLM → tool (e.g. `build_bom` / `create_task` → Notion) → validator → `post_chat`, **annotated with the latency budget** per stage (T14).

If the point of contact means something else (e.g. agent flow + data model), adapt. The Mermaid sources make that cheap.

- Source: `docs/diagrams/architecture.mmd`, `docs/diagrams/hey-ba-sequence.mmd`.
- Rendered: `docs/diagrams/*.svg` and `*.png` via `mermaid-cli` (`mmdc`). Embedded in the README.

### Notion export
After the final rehearsal (and again after the live test if access remains):
1. In Notion, open the page that contains the 4 databases → `•••` → **Export** → format **Markdown & CSV**, include subpages.
2. Save the zip to `submission/notion-export/` (or attach it to the submission as instructed).
3. Check: the CSVs for Tasks/Decisions/Requirements have owner, due, status and relation columns; the Meeting Notes page has every section with speaker + time.
4. Optionally also export as PDF for easy reading.

### Submission checklist
- [ ] Code builds; `uv run pytest` green; lint clean.
- [ ] `Dockerfile`, `docker-compose.yml`, `.env.example`, `config/*.yaml`, prompts present.
- [ ] Two diagrams (source + rendered) in `docs/diagrams/` and embedded in the README.
- [ ] Notion export included.
- [ ] README includes setup, tech choices, all 5 answers, and the AI-assistant declaration.
- [ ] Latency report and eval metrics included (T14, T16).
- [ ] **Secrets scan** (`gitleaks detect`) clean. `.env` not committed. Browser profile not committed.
- [ ] Only fictional data. No real company data or production account details.
- [ ] No hard-coded answers (grep for scenario phrases in `src/`; there must be none).
- [ ] Clean-clone test: a teammate follows the README on the VM and runs the smoke test successfully.
- [ ] Pushed to the provided private repo; point of contact notified.

## Acceptance criteria

- [ ] Every checklist item ticked.
- [ ] Each of the 5 required README answers is present, specific to this implementation, and uses measured numbers where relevant.
- [ ] Diagrams render on the repo host and match the deployed architecture.
- [ ] Notion export opens and shows linked, duplicate-free data from a full scenario run.

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T17-D1 | doc | Fresh-clone walkthrough by someone who didn't build it | Completes without asking questions |
| T17-D2 | doc | Markdown link check (`lychee` or similar) on README and docs | No broken links |
| T17-D3 | doc | Diagrams render (`mmdc` exit 0); images referenced exist | Pass |
| T17-S1 | security | `gitleaks detect` on full history | No findings |
| T17-S2 | compliance | `rg` for scenario phrases / pack numbers inside `src/` | No matches (no hard-coding) |
| T17-N1 | data | Inspect Notion export CSVs | Required columns present; no duplicate rows; relations populated |

## Dependencies

- All Must tasks. Uses outputs of T02, T04, T05, T13, T14 (answers), T06 (benchmarks), T16 (results).

## Related resources

- Mermaid: https://mermaid.js.org/ · mermaid-cli: https://github.com/mermaid-js/mermaid-cli
- GitHub Mermaid rendering in Markdown: https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/creating-diagrams
- Notion export: https://www.notion.com/help/export-your-content
- gitleaks: https://github.com/gitleaks/gitleaks
- lychee link checker: https://github.com/lycheeverse/lychee
