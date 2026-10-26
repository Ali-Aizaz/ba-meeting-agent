# Answers

Earlier answers (Q1–Q39) were written against the 13 Oct brief. Where the 25 Oct revision below disagrees with an earlier answer, the revision wins.

## Revision — 25 Oct 2026

Source: [`TechSol_AI_Engineer_Assignment_new.pdf`](TechSol_AI_Engineer_Assignment_new.pdf). Adnan confirmed the same day:

- Text-to-speech APIs are allowed.
- The OpenAI API is allowed, and it should use web search to find cars.
- Google Meet’s own speech-to-text should be used so we do not add another speech-to-text API.
- The Notion database will be attached separately. Schema mapping waits for that file.

### What the new brief requires

1. Join a Google Meet, Zoom, or Teams call. We stay on Google Meet.
2. Write down what is said, with who said it and when.
3. Reply in the meeting chat when someone says “Hey BA”.
4. Answer car questions from a car knowledge base we build.
5. Save a summary and the tasks (owner + due date) to Notion.

Extensions are drawn on **one** diagram and are not built: more Notion databases (Decisions, Requirements, Risks), BOM tables and option comparisons, a second meeting platform and voice replies, memory across past meetings, and the data-flow rule (own knowledge base; internet only to fetch data in; meeting data is not sent out).

They give a cloud server, an LLM API key (OpenAI, Claude, or Gemini), and a Notion workspace. We send code, one diagram, and a short README.

### Decisions for the build

| Topic | Decision |
|---|---|
| LLM | OpenAI by default, via the Responses API. Model id is config (`llm.model`) so it matches the key they provide. Claude and Gemini remain a config swap. |
| Web search | OpenAI `web_search` tool, for public car facts. The search string is the car question. The transcript, names, and Notion contents are not placed in that query. |
| Knowledge base | We write it (`data/knowledge/`). Web search fills gaps and current facts (a specific model, a current price). Cite the file or the URL. |
| Speech-to-text | Google Meet live captions, scraped from the bot’s Meet page. No Whisper, Deepgram, AssemblyAI, or other STT API. |
| Text-to-speech | Allowed. Not built. Replies stay in chat (this matches the earlier “chat, not speech” answer). Voice is an extension on the diagram, and the allowed implementation is a TTS API such as OpenAI speech, not a local Piper install. |
| Notion | Summary + tasks with owner and due date. Decisions, Requirements, and Risks are diagram-only. Property names come from the database Adnan attaches. Until then, a sandbox uses Task, Owner (text), Due (date), and a summary page. |
| BOM, compare, second platform, cross-meeting memory | Diagram only. |
| 10-second reply limit, self-hosted LLM, GPU VM, Falcon parts pack, four databases, two diagrams | Not in the new brief. Do not build them. |
| Deadline | Still 30 Oct 2026 (Q1). |

### Earlier answers that still hold

Q1 deadline 30 Oct 2026. Q2 live test is with the interviewer and Adnan. Q4 the repo is Adnan’s private GitHub. Q6 Google Meet. Q8 host keeps chat on. Q9 “Hey BA” spoken and typed. Q10 reply in chat. Q11 write overheard tasks and confirm in chat. Q12 meeting-time timestamps. Q13 save on request and at meeting end. Q14 link in the first message and after save. Q16 leave 1 minute after everyone else. Q17 English, UTC default, `24 Oct`, USD. Q19 default consent text, updated so it mentions captions and tasks. Q20 owner name stored as given. Q23 owner as text. Q25 if a task already exists from an earlier meeting, refuse the duplicate and say so in chat. Q26 statuses Not started / In progress / Done, date only, if the attached database has a status; due date is the required field. Q38 `AI_USAGE.md`.

### Earlier answers that no longer apply

Q18 (do not use Meet captions) — reversed. We use Meet captions.

Q27–Q31 (Falcon pack, BOM, long-lead, “not in pack”) — the new brief has no parts pack. We build a car knowledge base and search the web.

Q32–Q34 (GPU VM, Ollama, Qwen) — replaced by the cloud server and the OpenAI API.

Q36 (two diagrams) — one diagram.

Q21–Q22 (four provided databases, extra properties, Risks database) — must-have is summary + tasks. Extra databases are diagram-only. The real database is still to be attached.

Q3 and Q39 referred to bonus points and a scoring sheet that the new brief does not include.

---

## Original answers (13 Oct brief)

Here are the answers for each question in the questions document

Q1: The deadline is 30th october 2026
Q2. the live test will be improved and the interviewer and I will join
Q3. Every Must row weighs the same; bonus +10 on top
Q4. I have created the private repo for the project in my personal github
Q5. We develop against our own sandbox until they arrive
Q6. I will be using google meet so that is okay
Q7. The TechSol reharsal meeting can be between 2 to 4 people
Q8. The host will enable chat but I am not sure about admin being the bot
Q9. Both Spoken and typed
Q10. The bot should only type in the chat not speach
Q11. Yes the bot should write tasks/decisions to notion and confirm in the chat on its own
Q12. timestamps as meeting time
Q13. the save_meeting() should run on request and at the meeting end; transcript included in a collapsed toggle
Q14. In the consent notice at the start and again after save_meeting()
Q15. Header + ≤ 8 rows, ≤ 5 columns; a line is ≤ 120 characters
Q16. Leaves 1 min after everyone else; never earlier unless told
Q17. English; timezone from config (default UTC); dates `24 Oct`; USD
Q18. We use the Google Meet hosted transcription service
Q19. Our default text (see T02)
Q20. Yes, the name is stored as given
Q21. There are no set property names for the 4 databases (Meeting Notes, Tasks,
Decisions, Requirements). decide what the property names are and use them accordingly
Q22. Yes to properties; Risks DB only if allowed
Q23. Text; if Person, we map names to users
Q24. TechSol creates it and shares the page containing all 4 DBs
Q25. Every past meeting. The bot would also refuse that task if a rehearsal already created it. and notify the use in chat that the task is already created.
Q26. Not started / In progress / Done; date only
Q27. Support PDF, Markdown, DOCX, CSV, XLSX
Q28. yes; missing values are shown as "not in pack"
Q29. ≥ 12 weeks; exactly one qualified supplier
Q30. Yes to both, labelled "general knowledge", no Falcon numbers from memory
Q31. Yes, formula shown in the reply
Q32. Ubuntu 22.04, NVIDIA driver present, we install Docker + toolkit, ≥ 100 GB disk, outbound internet
Q33. SSH with key; GPU is ours alone
Q34. Yes, picked by our benchmark
Q35. Yes; we leave it deployed with a how-to-join note
Q36. System architecture + "Hey BA" sequence with latency budget
Q37. Markdown & CSV zip in submission/notion-export/
Q38. Yes, plus AI_USAGE.md in the repo
Q39. No; everything is in the README

Note on Q11 and Q18 in this original list: Q11’s “decisions” database is now diagram-only (tasks are still written and confirmed). Q18 already said to use Meet’s transcription; the questions file had assumed the opposite. The revision above is the one to follow: Meet captions, tasks confirmed in chat, decisions only inside the summary.
