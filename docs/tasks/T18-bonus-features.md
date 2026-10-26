# T18 — Extensions to draw

**Priority:** Diagram only · **Do not build**

## Why

The new brief asks for one diagram of today’s build and how it would extend. These items are the extension. Text-to-speech is allowed, and voice replies are still on the “do not build” list.

## What the diagram shows (dashed, not built)

| Extension | Later shape |
|---|---|
| More Notion databases | Decisions, Requirements, and Risks, linked to the meeting summary. |
| BOM and comparisons | See T08. |
| Second meeting platform | The same `MeetingPlatform` interface (T02) with a Zoom or Teams adapter. Captions are Meet-specific, so another platform would need its own transcript source. |
| Voice replies | Chat reply stays. A short spoken version uses a text-to-speech API (OpenAI speech or the provider on the key). The bot’s mic turns on only while it speaks. |
| Memory across past meetings | Read earlier summaries and open tasks from Notion when someone asks about a previous meeting. |
| Data flow | Own knowledge base. Internet is used to fetch public car data in. The meeting transcript is not sent out as a web search query. The provided LLM API is the allowed model path. |

## Acceptance criteria

- [ ] All six rows appear on the T17 diagram, marked as extensions.
- [ ] No voice output, second platform, Risks database, or cross-meeting index is in the running agent.
