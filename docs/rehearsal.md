# Rehearsal

Run this once in a real Google Meet before 30 Oct 2026. It still has to be run. The bot needs a Meet link, a host who admits it, and `BA_LLM__API_KEY` plus `BA_NOTION__TOKEN`.

Two to four people is enough. English captions. Chat stays available. The bot turns captions on itself. Paraphrase the lines if you need to.

```bash
uv run python -m ba_agent join "https://meet.google.com/your-meeting"
```

| # | Check | Example | Pass |
|---|---|---|---|
| 1 | Join and consent | Host admits the bot | First chat line is the consent text. Captions are on in the bot window. |
| 2 | Transcript | Someone speaks a normal sentence | That speaker and `[mm:ss]` are in the stored transcript |
| 3 | Spoken Hey BA | “Hey BA, what is a hybrid?” | Chat answer cites the knowledge base |
| 4 | Web search | “Hey BA, find the battery size of the current Toyota RAV4 hybrid.” | Chat answer includes a URL. The search query is the car question |
| 5 | Typed Hey BA | Type `Hey BA what is an EV?` | One chat answer |
| 6 | Overheard task | “Ali, please send the cost sheet by Friday.” | Notion row with owner and due date, and `✓ Task added: …` in chat |
| 7 | Duplicate | The same task again | No second row. Chat says it is already tracked |
| 8 | Missing due date | “Hey BA, add a task for Sara to book the test drive.” | One question asking for the date. No row yet |
| 9 | Quiet | A minute of talk with no Hey BA and no task | No extra chat messages |
| 10 | Save | “Hey BA, save the meeting.” or everyone else leaves | Summary page has the summary and the transcript. URL posted if chat is still open |

These must not get a reply: “the BA team will review this”, “hey Bob”.

Write down anything that failed, then fix it before submission. Items 1, 3, 4, 6, and 10 are the brief.
