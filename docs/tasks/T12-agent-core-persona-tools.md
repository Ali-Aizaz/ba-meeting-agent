# T12 — Agent core: persona, tools, chat replies

**Priority:** Must · **Depends on:** T05, T06, T07, T10, T11 · **Estimate:** part of the one-day build

## Why

- Someone says “Hey BA”, and the bot answers in chat.
- Car questions use the knowledge base and, when needed, web search.
- Task requests and “save the meeting” call Notion.
- The bot does not start conversations.

## What

**In scope**
- Persona prompt.
- Tool loop for `search_knowledge`, `search_cars`, `create_task`, `save_meeting`.
- `post_chat` after the reply is checked (T13).
- One clarifying question when a requested task has no owner or no due date.

**Out of scope**
- `build_bom`, `compare`, `log_decision`, `add_requirement`. Voice.

## Details

### Persona (draft)

```
You are TechSol BA, in a meeting about cars.
You answer only when someone says "Hey BA". You are neutral and brief.
Car facts come from search_knowledge and search_cars. Cite the file or the URL.
If those tools have nothing, say you are not sure. Do not invent specs or prices.
For a task, call create_task only when you have a task, an owner, and a due date.
If one is missing, ask one question.
search_cars receives the car question only, never the meeting transcript.
```

### Loop

1. `TriggerEvent` arrives.
2. Build the prompt from the persona, `digest()` (T10), and the question.
3. Call the model with the local tools. For a car question it may call `search_knowledge` then `search_cars`.
4. T13 checks the draft.
5. `post_chat` with reason `REPLY`.

Keep replies short enough to read in Meet chat: a few sentences, or a small plain-text table. Meet does not render Markdown.

### Clarifying question

“Hey BA, add a task to review the charging port” → one chat line asking who owns it and when it is due. The follow-up window (T05) accepts the answer without another “Hey BA”.

### Save and leave

“Hey BA, save the meeting” → `save_meeting()` → confirmation with the URL.
“Hey BA, you can go” → save, then T02 leaves.

Overheard tasks do not go through this loop. T09 calls `create_task` directly and T11 posts the confirmation.

## Acceptance criteria

- [ ] “Hey BA, what is a hybrid?” posts a short answer with a knowledge-base citation, and does not call web search if the local hit is strong.
- [ ] “Hey BA, what’s the current price of a Toyota RAV4 hybrid?” calls `search_cars` with that question and includes a URL.
- [ ] A task request missing a due date asks one question and writes nothing yet.
- [ ] Ordinary caption lines that are not “Hey BA” produce no reply from this loop.
- [ ] “Hey BA, save the meeting” posts the Notion URL.

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T12-U1 | unit | Mock LLM returns a knowledge citation | `post_chat` called once with `REPLY` |
| T12-U2 | unit | Mock local miss, then web citation | `search_cars` argument equals the question |
| T12-U3 | unit | Task missing due date | One question, `create_task` not called |
| T12-U4 | unit | Caption line without the wake phrase | No `post_chat` |

## Dependencies

- T05, T06, T07, T10, T11. T13 checks the draft before it is posted.

## Related resources

- Tool use on the Responses API: https://developers.openai.com/api/docs/guides/function-calling
