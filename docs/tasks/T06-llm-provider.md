# T06 — OpenAI client, swappable by config

**Priority:** Must · **Depends on:** T01 · **Estimate:** part of the one-day build

## Why

- The new brief gives an LLM API key (OpenAI, Claude, or Gemini).
- Adnan asked to use the OpenAI API, including web search, to find cars.
- Car answers and the meeting summary both go through this client.

## What

**In scope**
- `LLMClient` on the OpenAI Responses API.
- Tool `web_search` for car lookups (called from T07 as `search_cars`).
- Function tools the agent runs locally (`search_knowledge`, `create_task`, `save_meeting`). `post_chat` is called by our code after the reply is ready, so the model does not post to Meet itself.
- Config swap of model and, if needed, provider.
- A startup check that the key is present.

**Out of scope**
- Ollama, vLLM, GPU memory budgets, and a ban on remote APIs.
- Prompts (T12) and the knowledge files (T07).

## Details

### Default

| Setting | Value |
|---|---|
| `llm.provider` | `openai` |
| `llm.base_url` | `https://api.openai.com/v1` |
| `llm.model` | Set from the key. Prefer a current model that supports Responses `web_search`. The API guide’s recommended new-search path is `gpt-5.5`. Override with `BA_LLM__MODEL`. |
| `llm.api_key` | `BA_LLM__API_KEY` (or `OPENAI_API_KEY`). Never logged. |

Claude or Gemini is a second implementation of the same client interface, selected with `llm.provider`. Do not build it unless the provided key is not OpenAI. The interface stays small so the swap is real:

```python
class LLMClient:
    async def respond(self, input, tools=None, *, enable_web_search: bool = False,
                      timeout_s: float | None = None) -> LLMResponse
```

`LLMResponse` carries text, local function calls (name + JSON args), web citations (`url`, `title`), and the web search queries the API reports.

### Web search

Enable with `tools: [{ "type": "web_search" }]` on `POST /v1/responses`. Use `web_search`, not the older `web_search_preview`.

`search_cars(query)` is a thin wrapper:

- `query` is one car question, written by the agent from what was asked (“2024 Toyota RAV4 hybrid battery warranty”, “difference between mild hybrid and full hybrid”).
- The request input is that question plus a one-line instruction to answer with public facts and cite URLs.
- Do not attach the transcript, the participant list, Notion pages, or earlier chat. This is the “internet only to fetch data in” rule (C4).
- Return the answer text and `url_citation` annotations. The chat reply must show at least one URL when the fact came from search.

Web search is for car facts. Meeting notes and task extraction use a normal response with no web search.

### Local tools

Pass function tools as Responses function tools. Validate arguments with pydantic. One repair round if the JSON does not match the schema, then fail that tool and tell the chat we could not complete it.

### Timeouts

`llm.timeout_s` default 25 for a reply that may search, 20 for a summary. There is no 10-second gate. Log duration so a slow search is visible in rehearsal.

### What we send to OpenAI

Sending the “Hey BA” question, a short digest of this meeting (T10), and tool results to the provided LLM is required. That is the key they give us.

Sending the meeting out anywhere else is not. Web search queries stay car questions. No Deepgram, no hosted meeting bot, no paste of the transcript into a separate chatbot.

## Acceptance criteria

- [ ] A missing API key stops startup with a clear error.
- [ ] `search_cars("What is a hybrid car?")` returns text and at least one URL.
- [ ] A unit test asserts the search request body contains the car question and does not contain a sample transcript fixture.
- [ ] Changing `BA_LLM__MODEL` changes the model with no code edit.
- [ ] Invalid tool arguments get one repair attempt.

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T06-U1 | unit | Mock Responses payload with a function call | Name and args parsed |
| T06-U2 | unit | Mock `web_search_call` + `url_citation` | Query and URL captured |
| T06-U3 | unit | Search body vs a transcript fixture | Transcript string absent |
| T06-I1 | live key | `search_cars` for a well-known car fact | Non-empty answer, at least one http URL |

## Dependencies

- T01. Used by T07, T09, T12.

## Related resources

- Web search guide: https://developers.openai.com/api/docs/guides/tools-web-search
- Responses API: https://developers.openai.com/api/docs/api-reference/responses
- OpenAI Python SDK: https://github.com/openai/openai-python
