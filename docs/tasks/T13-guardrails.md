# T13 — Citations, empty results, search privacy

**Priority:** Must · **Depends on:** T07, T12 · **Estimate:** part of the one-day build

## Why

- A car fact in chat should point at the knowledge base or a URL.
- If both tools are empty, the reply says we are not sure.
- Web search must not receive the meeting (C4).

## What

**In scope**
- Check `search_cars` inputs before the HTTP call.
- Check a car reply for a citation, or for an explicit “not sure”, before `post_chat`.
- One regenerate if the draft states a precise number and cites nothing. If the second draft still has an uncited number, replace that reply with “I’m not sure — I don’t have a source for that.”

**Out of scope**
- A local safety model. Falcon-pack grounding. BOM checks.

## Details

- `search_cars` rejects a query that contains a caption line from this meeting, a participant name list, or a Notion URL. The caller sends the car question again with those removed, or skips search.
- A reply that used `search_knowledge` or `search_cars` must include that source string.
- Task confirmations and the consent message skip this check.

## Acceptance criteria

- [ ] A draft “The RAV4 hybrid is $31,000” with no citation is not posted. The fallback “not sure” line is posted instead when regenerate also fails.
- [ ] A draft with `(src: hybrid.md)` or a URL is posted.
- [ ] A search query that embeds a fixture caption line is rejected.

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T13-U1 | unit | Uncited price | Blocked |
| T13-U2 | unit | Cited sentence | Allowed |
| T13-U3 | unit | Query containing a transcript fixture | Rejected |
| T13-U4 | unit | “I’m not sure” with no number | Allowed |

## Dependencies

- T07, T12.

## Related resources

- Citation fields on web search results: https://developers.openai.com/api/docs/guides/tools-web-search
