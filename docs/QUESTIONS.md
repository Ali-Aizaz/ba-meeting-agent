# Open questions for TechSol — BA Meeting Agent

Source: [`TechSol AI Engineer Assignment — Build a BA Meeting Agent.pdf`](TechSol%20AI%20Engineer%20Assignment%20—%20Build%20a%20BA%20Meeting%20Agent.pdf) (Oct 13, 2026, @Raheel). Task plan: [`tasks/README.md`](tasks/README.md).

These are the things the PDF does not say, or says in a way that can be read two ways. For every question there is a **working assumption**: what we build if we get no answer. Nothing is blocked while we wait, but a few answers change the design, so those are marked **Blocking** and should be asked first.

Priority meaning:

- **Blocking** — the answer changes what we build or whether we can test at all. Ask this week.
- **Important** — the answer changes details we can adjust later, but late changes cost time.
- **Nice to know** — small wording or format choices.

---

## Summary table

| # | Question (short) | Priority | Working assumption | Affects |
|---|---|---|---|---|
| Q1 | What is the submission deadline? | Blocking | Unknown; plan is ~37 person-days, cut list in `tasks/README.md` | All |
| Q2 | When is the live test, how long, who joins, scripted or improvised? | Blocking | ~30–45 min, 3–4 people, follows the 9 items in the PDF but with their own words | T16 |
| Q3 | How is the assignment scored? | Important | Every Must row weighs the same; bonus +10 on top | All |
| Q4 | Where is the private repo and who is the point of contact? | Blocking | Unknown | T17 |
| Q5 | When do we get the meeting account, Notion workspace, VM and pack? | Blocking | We develop against our own sandbox until they arrive | T02, T07, T11, T15 |
| Q6 | Is Google Meet OK, and what kind of account is it? | Blocking | Google Meet; signed-in Google account named "TechSol BA"; guest join as fallback | T02 |
| Q7 | Can TechSol host rehearsal meetings with 3–4 people? | Important | We rehearse with our own people using the provided account | T16 |
| Q8 | Will the live-test host keep chat enabled and admit the bot? | Important | Yes; it is in our runbook request | T02, T16 |
| Q9 | Is "Hey BA" spoken, typed in chat, or both? | Important | Both | T05 |
| Q10 | "Hey BA" then silence: ask "Yes?" or stay quiet? | Nice to know | Ask one short question | T05, T12 |
| Q11 | Should the bot write tasks/decisions it hears (not asked) to Notion and confirm in chat on its own? | Blocking | Yes, with confidence thresholds; confirmations immediate (batched mode available) | T09, T11, T12 |
| Q12 | Timestamps as meeting time `[12:41]` or clock time `10:42`? | Nice to know | Meeting time in chat; both stored in Notion | T04, T11 |
| Q13 | When should `save_meeting()` run, and should the full transcript go into Notion? | Important | On request and at meeting end; transcript included in a collapsed toggle | T11 |
| Q14 | Where should the Notion link be posted? | Nice to know | In the consent notice at the start and again after `save_meeting()` | T02, T11 |
| Q15 | How big is a "small table", and what counts as a "line"? | Nice to know | Header + ≤ 8 rows, ≤ 5 columns; a line is ≤ 120 characters | T08, T12 |
| Q16 | Should the bot leave the meeting on its own? | Nice to know | Leaves 2 min after everyone else; never earlier unless told | T02 |
| Q17 | Language, timezone, date format, currency? | Important | English; timezone from config (default UTC); dates `24 Oct`; USD | T03, T09, T11 |
| Q18 | Do Meet's built-in captions count as a "hosted transcription service"? | Important | Yes, so we don't use them; we run Whisper locally | T03 |
| Q19 | Is there required wording for the consent notice? | Nice to know | Our default text (see T02) | T02 |
| Q20 | Can task owners be people who are not in the meeting? | Nice to know | Yes, the name is stored as given | T09, T11 |
| Q21 | What are the property names and types of the 4 Notion databases? | Blocking | Unknown; schema mapping file + startup check; sandbox copy for development | T11 |
| Q22 | May we add properties (e.g. `Key`, `Time`, `Said by`) and a 5th "Risks" database? | Blocking | Yes to properties; Risks DB only if allowed | T11, T18 |
| Q23 | Is "Owner" a Notion Person property or plain text? Are the staff Notion users? | Important | Text; if Person, we map names to users | T11 |
| Q24 | Who creates the Notion integration token, and is it shared with the right page? | Blocking | TechSol creates it and shares the page containing all 4 DBs | T11 |
| Q25 | Dedup within one meeting only, or across meetings too? Should rehearsal rows be removed before the live test? | Important | Within one meeting; we archive rehearsal rows before the live test | T11, T16, T17 |
| Q26 | Which task statuses exist, and should "due" be a date or date-time? | Nice to know | Not started / In progress / Done; date only | T11 |
| Q27 | What format is the automotive pack, and when do we get it? | Blocking | Support PDF, Markdown, DOCX, CSV, XLSX | T07, T08 |
| Q28 | Does the pack have per-part suppliers, lead times, unit costs and quantities for both 60 and 75 kWh? | Blocking | Assumed yes; missing values are shown as "not in pack" | T08 |
| Q29 | What counts as "long-lead" and "single-supplier"? | Important | ≥ 12 weeks; exactly one qualified supplier | T08 |
| Q30 | May the agent answer general car-engineering questions from its own knowledge (labelled), and may we add a small primer document? | Important | Yes to both, labelled "general knowledge", no Falcon numbers from memory | T07, T13 |
| Q31 | Is derived maths (e.g. pack cost = kWh × $/kWh) acceptable when the formula is stated? | Important | Yes, formula shown in the reply | T08, T13 |
| Q32 | VM details: OS, GPU model, driver, Docker, NVIDIA toolkit, disk, internet access? | Blocking | Ubuntu 22.04, NVIDIA driver present, we install Docker + toolkit, ≥ 100 GB disk, outbound internet | T15 |
| Q33 | How do we access the VM, and is anyone else using the GPU? | Blocking | SSH with key; GPU is ours alone | T15 |
| Q34 | Is Qwen2.5 7B (or 14B) via Ollama acceptable as the default LLM? | Nice to know | Yes, picked by our benchmark | T06 |
| Q35 | Should the bot stay running on the VM after submission so TechSol can test it alone? | Important | Yes; we leave it deployed with a how-to-join note | T15, T17 |
| Q36 | Which "two diagrams" do you expect? | Important | System architecture + "Hey BA" sequence with latency budget | T17 |
| Q37 | What format for the Notion export, and where should it go? | Nice to know | Markdown & CSV zip in `submission/notion-export/` | T17 |
| Q38 | Is a README section enough for the AI-assistant declaration? | Nice to know | Yes, plus `AI_USAGE.md` in the repo | T17 |
| Q39 | Is there a demo or review call after submission? | Nice to know | No; everything is in the README | T17 |

---

## A. Timeline, logistics and scoring

### Q1 — Submission deadline (Blocking)

**Question:** What date (and time zone) is the submission due? Is there a separate date for the live test, and is a code freeze expected before it?

**Why it matters:** The PDF has no date. Our full plan is about 37 person-days of work. If the deadline is 2 weeks away, we must cut scope now (for example, skip the pyannote fallback, the reranker, and the second rehearsal) instead of finding out at the end.

**Example:** "Code due Friday 7 Nov 18:00 PKT, live test Tuesday 11 Nov 14:00" lets us plan backwards from the live test.

**If we don't hear back:** We follow the milestone order in `tasks/README.md` and keep the cut list there up to date.

### Q2 — Live test details (Blocking)

**Question:** When is the live test, how long is it, how many people join, and will they follow the 9 items from the PDF in that order or improvise freely? Will the live test use the provided Notion workspace?

**Why it matters:** We rehearse with a script. If the real test is improvised ("ask anything about Falcon"), we need more time on general knowledge and guardrails. If the test writes into the provided workspace, rehearsal rows must be cleaned out first (Q25).

**Example:** "45 minutes, 4 staff, we cover the 9 items but in our own words, plus a few surprise questions."

**If we don't hear back:** We assume 30–45 minutes, 3–4 people, the 9 items in their own words, plus surprise questions.

### Q3 — Scoring (Important)

**Question:** How is the assignment scored? Does every ✓ row count the same? Is partial credit given (for example, the bot answers in 12 s instead of 10 s)? "Bonus +10" is out of what total?

**Why it matters:** If latency is worth as much as Notion correctness, we tune for both equally. If the live test is the only thing that counts, we spend less on unit-test coverage and more on rehearsals.

**Example:** "100 points: 60 live test, 20 code quality, 20 README; bonus up to +10."

**If we don't hear back:** We treat every Must row as equal and the live test as the main judge.

### Q4 — Private repo and point of contact (Blocking)

**Question:** Where is the private repo (GitHub, GitLab, other)? Who creates it, and who gets access? Who is the point of contact for questions, and which channel (email, Slack, WhatsApp) do they prefer?

**Why it matters:** We need the repo early to run CI there, and we need a channel where answers to this list arrive quickly.

**If we don't hear back:** We develop in our own private repo and push to theirs when it exists.

### Q5 — When do the provided resources arrive? (Blocking)

**Question:** When do we get (a) the meeting account, (b) the Notion workspace with the 4 databases, (c) the GPU VM, and (d) the automotive pack? Can we start on our own sandbox before then?

**Why it matters:** The riskiest parts (joining Meet from a bot account, the Notion schema, the pack format) can't be finished without the real resources. A one-week delay on the pack means the BOM tool is tested on fake data until then.

**If we don't hear back:** We build against our own Google account, a sandbox Notion workspace and a synthetic pack, then switch when the real ones arrive.

---

## B. Meeting platform and account

### Q6 — Platform and account type (Blocking)

**Question:** Is Google Meet acceptable as our platform choice? For the meeting account: is it a Google Workspace account or a personal Gmail? Is its display name already "TechSol BA" (or may we change it)? Does it have 2-step verification or a passkey? Is it OK that we sign it in once from a browser on the VM?

**Why it matters:** The bot is a real Chromium browser signed into that account. Google sometimes blocks sign-in from a cloud VM ("This browser may not be secure"). If that happens, we fall back to **guest join** (the bot types "TechSol BA" and the host clicks "Admit"), which is less convenient for you but always works.

**Example:** "Workspace account `techsol.ba@techsol.example`, name 'TechSol BA', no 2FA, Meet links will be from our domain so the bot needs to 'Ask to join'."

**If we don't hear back:** Google Meet, signed-in mode, with guest mode as a tested fallback.

### Q7 — Rehearsal meetings (Important)

**Question:** Can 3–4 TechSol people join one or two 30-minute rehearsal meetings before the live test? If not, may we rehearse with our own people using the provided account?

**Why it matters:** Speaker tagging depends on real people with real microphones. Rehearsing with TechSol staff also tells us how your names appear in Meet (for owner matching, see Q23).

**If we don't hear back:** We rehearse with our own team and the provided account.

### Q8 — Host settings during the live test (Important)

**Question:** Will the host (a) admit "TechSol BA" from the waiting room, (b) keep "Let everyone send chat messages" turned on in Meet's host controls, and (c) say "Hey BA" clearly before each request to the bot?

**Why it matters:** If chat is turned off for participants, the bot **cannot answer at all**; there is no workaround from our side. The bot only replies in chat.

**If we don't hear back:** We include these three points in the request we send before the live test.

---

## C. How the agent should behave

### Q9 — Spoken or typed "Hey BA" (Important)

**Question:** Do people say "Hey BA" out loud, type it in the chat, or both?

**Why it matters:** Spoken triggers go through speech-to-text and take ~2 s longer. Typed triggers are exact and faster. Supporting both is cheap, so we plan to, but it affects how we measure the 10-second limit.

**Example:** Spoken: *Sara says "Hey BA, compare the packs."* Typed: *Sara writes `Hey BA compare the packs` in the chat.* Both should get a reply.

**If we don't hear back:** Both work; speech is the main path.

### Q10 — "Hey BA" followed by silence (Nice to know)

**Question:** If someone says "Hey BA" and then nothing for 5 seconds, should the bot ask "Yes — what would you like me to do?" or stay silent?

**Why it matters:** The PDF says the bot never interrupts on its own, but here it *was* called. Staying silent after being called may look like a failure in the live test.

**If we don't hear back:** It asks one short question.

### Q11 — Writing things it overhears (Blocking)

**Question:** The live test says "create tasks from speech". When the bot hears a task in normal conversation (nobody said "Hey BA"), should it (a) write it to Notion straight away and confirm in chat, (b) write it but stay quiet in chat, or (c) only collect it and write at the end in `save_meeting()`? Same question for decisions.

**Why it matters:** Two PDF rules pull in different directions: "silent otherwise" and "confirms every Notion write in chat". Option (a) means the bot posts chat lines it was not asked for.

**Example (option a):** Sara says *"Ali, can you update the cost sheet by Friday?"*. Ten seconds later the chat shows `✓ Task added: Ali — cost sheet — Fri`. Nobody spoke to the bot.

**Example (option c):** Nothing appears in chat during the meeting; the task shows up in Notion when the notes are saved.

**If we don't hear back:** Option (a) with confidence thresholds, and a `batched` mode that groups confirmations into one message every 2 minutes.

### Q12 — Timestamp format (Nice to know)

**Question:** Should timestamps show **meeting time** (minutes since the bot joined, like `[12:41]`) or **clock time** (like `10:42`)?

**Why it matters:** Meeting time is what the PDF's examples look like, but clock time is easier to match with your calendar.

**Example:** `[12:41] Sara: We'll go with the 60 kWh pack.` versus `10:42 Sara: We'll go with the 60 kWh pack.`

**If we don't hear back:** Meeting time in chat and in notes; both values are stored in Notion.

### Q13 — When to save the meeting, and whether to store the transcript (Important)

**Question:** Should `save_meeting()` run (a) only when someone says "Hey BA, save the meeting", (b) automatically when the meeting ends, or (c) both? Should the Notion page include the **full transcript**, or only the summary and the tagged lines (decisions, tasks, questions, risks)?

**Why it matters:** (b) is safer (nothing is lost if people forget), but if everyone leaves first, the bot can't post the link in chat (Q14). The full transcript is useful but is also the most sensitive data in Notion.

**If we don't hear back:** Both triggers; the transcript goes in, inside a collapsed section at the bottom of the page.

### Q14 — Where to post the Notion link (Nice to know)

**Question:** Is it OK to include the Notion notes link in the **first** message (the consent notice), as well as at the end after saving?

**Why it matters:** Meet chat is only visible to people who are in the meeting at that moment. If everyone leaves before the notes are saved, a link posted afterwards reaches nobody. Posting it at the start means it is always in the chat.

**Example first message:** `Hi, I'm TechSol BA … Say "Hey BA …" to ask me something. Notes will be here: https://notion.so/…`

**If we don't hear back:** We post it at the start and at the end.

### Q15 — "Small table" and "line" (Nice to know)

**Question:** What is the largest table you consider "small"? And is a "line" a line of text (one paragraph, however long) or a visual line in the chat panel?

**Why it matters:** The limit "≤ 6 lines or one small table" decides how much of a BOM we show in chat and how much we move to Notion.

**Example:** Our plan is a header plus up to 8 rows and up to 5 columns, e.g. `Part | Qty | Est. cost | Supplier | Flags`. Everything beyond that goes into the Notion notes with a `+5 more` line in chat.

**If we don't hear back:** Header + 8 rows, ≤ 5 columns; a line is ≤ 120 characters.

### Q16 — Leaving on its own (Nice to know)

**Question:** Should the bot leave when everyone else has left? Should it leave when told ("Hey BA, you can go")?

**If we don't hear back:** It saves the notes and leaves 2 minutes after it is alone; it also leaves when told via "Hey BA" or via our control command.

### Q17 — Language, timezone, dates, currency (Important)

**Question:** Is the meeting in English only? Which timezone should "by Friday" be interpreted in? How should dates be shown (`24 Oct`, `Oct 24`, `24/10`)? Are all costs in USD?

**Why it matters:** "Next Wednesday" on a Monday evening in Karachi is a different date than in a US timezone. Whisper is set to English for speed; a second language would need a change.

**If we don't hear back:** English; timezone set in config (we'll set Asia/Karachi if the staff are in Pakistan, otherwise UTC); dates shown as `24 Oct`; USD.

### Q18 — Meet's built-in captions (Important)

**Question:** Do Google Meet's own live captions count as a "hosted transcription service" (not allowed)? We assume yes and run our own speech-to-text (faster-whisper) on the VM.

**Why it matters:** Using Meet captions would be simpler and more accurate, but they run on Google's servers. If they are allowed, we could use them as a backup source.

**If we don't hear back:** We do not use them.

### Q19 — Consent notice wording (Nice to know)

**Question:** Do you need specific wording for the consent notice, or is ours fine?

**Our default:** `Hi, I'm TechSol BA, an AI assistant. I'm transcribing this meeting to keep notes (who said what, when) and to record tasks and decisions in Notion. Say "Hey BA …" to ask me something. Tell the host if you do not consent.`

### Q20 — Task owners outside the meeting (Nice to know)

**Question:** Can a task owner be someone who is not in the meeting (e.g. "ask Fatima in purchasing to get a quote")?

**If we don't hear back:** Yes. The name is stored as spoken; it is not checked against the participant list.

---

## D. Notion

### Q21 — Property names and types of the 4 databases (Blocking)

**Question:** Can you send the exact property names and types of the Meeting Notes, Tasks, Decisions and Requirements databases (or give us access early, or a duplicate template)? Which relation links them?

**Why it matters:** Our code maps logical fields (`task`, `owner`, `due`, `status`, `said_by`, `time`) to real property names. If the Tasks DB has `Due date` (date) and we expect `Due` (date), the bot refuses to start with a clear error. Knowing the schema up front saves a round trip on live-test day.

**Example of what we need:** `Tasks: Task (title), Owner (person), Due (date), Status (status: Not started/In progress/Done), Meeting (relation → Meeting Notes)`.

**If we don't hear back:** We use a configurable mapping file plus a startup check that prints exactly what is missing.

### Q22 — May we add properties or a 5th database? (Blocking)

**Question:** May we add properties to your databases (for example `Key` for duplicate detection, `Time`, `Said by`, `Meeting ID`)? May we add a 5th database "Risks" for the bonus item?

**Why it matters:** "No duplicates" is much more reliable with a hidden `Key` property. Without it we fall back to fuzzy title matching. The Risks database is a bonus item that needs a new DB.

**If we don't hear back:** We add properties only in our sandbox and use fuzzy matching against your databases; Risks DB only in the sandbox.

### Q23 — Owner property type and Notion users (Important)

**Question:** Is "Owner" a Notion **Person** property (needs a Notion user per person) or plain text / select? Are the people who join the meeting (e.g. Ali, Sara) users in that Notion workspace, and do their Meet display names match their Notion names?

**Why it matters:** A Person property can only hold real Notion users. If "Ali Khan" in Meet is "akhan@techsol" in Notion, we need a mapping table.

**If we don't hear back:** We treat Owner as text. If it is a Person property, we add a name → user mapping file.

### Q24 — Integration token and sharing (Blocking)

**Question:** Will you create the Notion integration and send us its token, or should we create one? Please share the **page that contains all 4 databases** with that integration (Share → Connections), otherwise every API call returns "not found".

**If we don't hear back:** We ask for an integration token when the workspace is handed over.

### Q25 — Duplicate scope and rehearsal rows (Important)

**Question:** "No duplicates" — within one meeting only, or also against earlier meetings? Should we delete/archive rehearsal rows from your workspace before the live test? Do you want rehearsal meetings in the final export, or only the live meeting?

**Why it matters:** If an earlier rehearsal already has "Ali — cost sheet", cross-meeting dedup would block the same task in the live test, which would look like a bug.

**If we don't hear back:** Dedup within the current meeting; we archive rehearsal rows before the live test; the export contains the live meeting only.

### Q26 — Task statuses and due format (Nice to know)

**Question:** Which status options exist in the Tasks database (we assume `Not started / In progress / Done`)? Should "due" be a date only, or date and time?

**If we don't hear back:** Those three statuses; date only.

---

## E. Knowledge pack (Falcon specs, parts, suppliers)

### Q27 — Format and delivery date (Blocking)

**Question:** In which format is the pack (PDF, Word, Markdown, Excel, CSV)? How many files, roughly? When do we get it?

**Why it matters:** Tables in PDFs are the hardest to read reliably. Excel/CSV is best for the BOM. The retrieval quality tests can only be written against the real pack.

**If we don't hear back:** We support all five formats and test on a synthetic pack until the real one arrives.

### Q28 — Does the pack contain what the BOM needs? (Blocking)

**Question:** For each part, does the pack list (a) quantity per vehicle, (b) unit cost, (c) one or more suppliers, (d) lead time in weeks? Does it cover both the 60 kWh and the 75 kWh pack, with range, mass, cost and charge time for each?

**Why it matters:** The bot must "flag single-supplier and long-lead parts" and "compare 60 vs 75 kWh". It cannot flag what the pack does not say, and it is not allowed to invent it. If lead time is per supplier rather than per part, we need to know the rule (we would use the shortest qualified supplier's lead time and say so).

**Example of a row we can use fully:** `BMS | qty 1 | $420 | Suppliers: Acme, Volta | Lead time: 14 weeks`.

**If we don't hear back:** Missing values appear as "not in pack" in replies and the totals are marked partial.

### Q29 — Definitions of "long-lead" and "single-supplier" (Important)

**Question:** Does the pack (or TechSol) define "long-lead" (e.g. ≥ 12 weeks? ≥ 16?) and "single-supplier" (exactly one qualified supplier, or one *approved* supplier)?

**If we don't hear back:** Long-lead = lead time ≥ 12 weeks; single-supplier = exactly one supplier listed for the part. Both are configurable.

### Q30 — General knowledge and a primer document (Important)

**Question:** For general questions ("What does Euro NCAP test?", "What is MoSCoW?") may the agent answer from the model's own knowledge, clearly labelled as general knowledge? May we add a small primer document of our own (no Falcon numbers) so even those answers can cite a source?

**Why it matters:** The "Knowledge" row expects car-engineering and BA knowledge, but the pack is about Falcon. We never let the model recall Falcon prices or suppliers from memory; this question is only about generic concepts.

**If we don't hear back:** Yes to both, labelled; any number in such an answer is marked as an assumption or typical value.

### Q31 — Derived numbers (Important)

**Question:** Is it acceptable for the bot to compute values from pack inputs (for example *pack cost = 60 kWh × $/kWh from the spec sheet*, or *cost per km of range*), as long as the formula and the source are shown?

**Why it matters:** "Numbers always come with assumptions or a source doc" allows this reading, but a strict reading of "never invent prices" might not.

**Example reply line:** `Pack cost ≈ $7,200 — assumption: 60 kWh × $120/kWh (falcon_specs.pdf p.4)`.

**If we don't hear back:** Yes, with the formula shown every time.

---

## F. VM and deployment

### Q32 — VM details (Blocking)

**Question:** For the GPU VM: operating system and version? GPU model (which 24 GB card: L4, A10G, RTX 4090, A5000…)? Is the NVIDIA driver installed, and which version? Are Docker and the NVIDIA Container Toolkit installed, or may we install them (sudo)? Disk size? Does the VM have outbound internet (to pull Docker images and models, ~20–30 GB once)?

**Why it matters:** faster-whisper and Ollama need a CUDA 12-compatible driver. Model downloads need internet and disk. If the VM is offline, we must pre-load everything.

**If we don't hear back:** We assume Ubuntu 22.04, a working NVIDIA driver, sudo, ≥ 100 GB disk and outbound internet; we document the exact versions we used.

### Q33 — Access and sharing (Blocking)

**Question:** How do we access the VM (SSH key, username, IP)? Is it OK to open an SSH tunnel for a web-based debug screen (noVNC) that stays bound to localhost? Is anyone else using the VM or its GPU at the same time?

**Why it matters:** One-time Google sign-in needs a visible browser; we do that through a tunnel, not an open port. Another process on the GPU would break our latency numbers.

**If we don't hear back:** SSH with a key; GPU is exclusively ours during the test.

### Q34 — Default model (Nice to know)

**Question:** Is Qwen2.5 7B via Ollama acceptable as the default self-hosted LLM? If a 14B model fits in the GPU and is faster/more accurate for tool calling, may we use it as the default instead?

**If we don't hear back:** We choose by our benchmark among Qwen2.5 7B, Llama 3.1 8B and Qwen2.5 14B, and document it. The LLM remains swappable by config.

### Q35 — Keep it running after submission? (Important)

**Question:** Should the bot stay deployed and running on the VM after submission so TechSol can invite it to meetings without us? If yes, how do you want to trigger a join (a command we document, or a fixed meeting link it joins automatically)?

**If we don't hear back:** We leave it deployed, with a one-line join command in the README.

---

## G. Submission

### Q36 — The "two diagrams" (Important)

**Question:** Which two diagrams do you expect? The PDF says "your two diagrams" without naming them.

**Our plan:** (1) **System architecture / deployment**: VM, containers, GPU, Google Meet, Notion, data flows. (2) **"Hey BA" request sequence**: speech → speech-to-text → trigger → LLM → tool → Notion → chat, with the time budget per step.

**Alternatives you might mean:** agent flow + data model; or architecture + Notion database relations. All are cheap to produce, we just need to know.

### Q37 — Notion export format and location (Nice to know)

**Question:** Which export do you want: Notion's "Markdown & CSV", PDF, or both? Should it be committed to the repo or sent separately?

**If we don't hear back:** Markdown & CSV zip, committed under `submission/notion-export/`, plus a PDF of the Meeting Notes page.

### Q38 — AI-assistant declaration (Nice to know)

**Question:** Is a section in the README that lists which AI coding assistants were used, for what, and that a human reviewed the output, enough? Or do you want a per-file or per-commit log?

**If we don't hear back:** README section plus a running `AI_USAGE.md` log in the repo.

### Q39 — Demo or review call (Nice to know)

**Question:** Is there a demo, walkthrough or review call after submission, or is the live test plus the repo the whole evaluation?

**If we don't hear back:** We assume no call and make the README self-sufficient.
