# T17 — Short README and one diagram

**Priority:** Must · **Depends on:** the Must tasks · **Estimate:** the end of the one-day build

## Why

The brief says to send code, one diagram (today’s build + extensions), and a short README.

## What

**In scope**
- `README.md` at the repo root.
- One diagram. Mermaid source in `docs/diagrams/architecture.mmd`, plus a rendered image if the README needs it.
- `AI_USAGE.md` summarised in a short paragraph.
- A check that `.env` and browser profiles are not committed.

**Out of scope**
- A second diagram. A latency report. A Notion export zip. Those were in the old brief.

## README

Keep it short:

1. What TechSol BA does (the five brief items).
2. The diagram.
3. Setup: Python or Docker, Playwright, `.env` keys (`BA_LLM__API_KEY`, `BA_NOTION__TOKEN`), `notion_schema.yaml`, `python -m ba_agent join <url>`.
4. How to talk to it: say or type “Hey BA”. What lands in Notion.
5. Tech choices, a few lines each: Meet + Playwright, Meet captions, OpenAI Responses API with `web_search`, the Markdown knowledge base, Notion.
6. Limits: captions are English-biased and the DOM can change; guest join needs the host to admit the bot; the Notion map depends on the attached database.
7. AI coding assistants used, from `AI_USAGE.md`.

## The diagram

One picture, two layers.

**Built (solid):**

- Google Meet. The bot joins, turns on captions, reads speaker + text, posts chat.
- “Hey BA” on a caption line or a chat message.
- Local car knowledge base.
- OpenAI for the reply. Web search fetches public car pages in. The search arrow is labelled with the car question.
- Notion: summary page and tasks (owner, due date).

**Extensions (dashed), from T08 and T18:**

- Decisions, Requirements, Risks databases.
- BOM table and option comparison.
- Second meeting platform and voice replies (text-to-speech API).
- Memory across past meetings.
- A note on data flow: the knowledge base is ours; the internet is used to fetch car data in; the meeting transcript is not sent out as a search query. The provided LLM API is the model path.

## Acceptance criteria

- [ ] README setup steps are enough for a teammate to join a Meet.
- [ ] The diagram shows the built path and every extension row in T18, and the extension rows are visibly “not built”.
- [ ] No secrets in the repo.

## Tests

Read the README against a clean checkout. There is no automated test.

## Dependencies

- The Must tasks, so the README matches what actually runs.

## Related resources

- Mermaid: https://mermaid.js.org/
