# T07 — Car knowledge base and web search

**Priority:** Must · **Depends on:** T01, T06 · **Estimate:** part of the one-day build

## Why

- The brief says to answer car questions from a knowledge base we build.
- Adnan asked that the OpenAI API web-search cars when we need a fact that is not in that base.
- Answers should cite the file or the page. If both come back empty, the agent says it is not sure (T13).

The old Falcon parts pack, Qdrant, and local embeddings are not in this brief.

## What

**In scope**
- A small knowledge base we write, under `data/knowledge/`, in Markdown.
- `search_knowledge(query)` over those files.
- `search_cars(query)` which calls T06 web search when the local hit is weak or the question needs a current or specific fact (a model year, a price, a named car).
- Citations on every car fact in the reply.

**Out of scope**
- BOM tables, supplier flags, and 60 vs 75 kWh comparisons (T08, diagram only).
- PDF/DOCX/XLSX ingestion of a TechSol pack. They are not providing one.
- A vector database. The base is small enough to search as text.

## Details

### What we put in the knowledge base

A handful of short Markdown files, written by us, for example:

- `hybrid.md`, `ev.md`, `body-and-chassis.md`, `safety.md` — plain explanations of terms a meeting might use.
- `examples.md` — a few named cars with facts we have checked, each fact next to its source URL, so the base itself is sourced.

Keep it short. It exists so the agent has its own base, which the brief requires. It is not a dump of the model’s memory, and it is not a table of canned questions and answers.

### `search_knowledge(query)`

- Split files on headings. Score chunks by keyword overlap (case-insensitive tokens, light stemming is optional).
- Return the top chunks with `source` set to the file and heading.
- If the best score is under `knowledge.min_score`, return no hit.

### When to search the web

The agent (T12) calls tools in this order for a car question:

1. `search_knowledge`.
2. If that misses, or the question names a specific car, year, price, or “latest”, call `search_cars` (T06).
3. Reply from those tool results. Cite `(src: hybrid.md)` or the URL.
4. If both are empty, say we are not sure. Do not fill the gap from unstated model memory.

`search_cars` must receive the car question only (C4).

### Numbers

A number in a chat reply needs the knowledge-base file or a URL next to it. A rough comparison (“a hybrid usually uses less fuel in town”) does not need a fake precise figure.

## Acceptance criteria

- [ ] `data/knowledge/` ships with the repo and `search_knowledge` returns a chunk for “what is a hybrid”.
- [ ] A question about a specific current car that is not in the files calls web search and the reply includes a URL.
- [ ] A nonsense car question with search mocked to empty produces “not sure” and no invented spec.
- [ ] The knowledge files contain no hard-coded “Hey BA” answers.

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T07-U1 | unit | Query “hybrid” against the fixture files | Top chunk is `hybrid.md`; source set |
| T07-U2 | unit | Query with no token overlap | Empty result |
| T07-U3 | unit | Agent policy: weak local score → `search_cars` called with the question text only | Transcript fixture not in the query |

## Dependencies

- T01, T06. Used by T12 and T13.

## Related resources

- OpenAI web search: https://developers.openai.com/api/docs/guides/tools-web-search
