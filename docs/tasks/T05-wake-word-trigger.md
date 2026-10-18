# T05 — "Hey BA" trigger and question extraction

**Priority:** Must · **Milestone:** M2 Meeting I/O · **Depends on:** T03, T04 · **Estimate:** 1.5 days

## Why

- "Meeting" row: replies to **"Hey BA …"**, **silent otherwise**.
- "Trigger" part: **detects "Hey BA" and extracts the question; ignores everything else.**
- Answer rule: the agent **never interrupts the meeting on its own** (C6). A false trigger counts as an interruption, so precision matters more than recall, though both must be high.
- The README must answer **"wake-word design"** (T17). This task produces that answer.

## What

**In scope**
- Detect the wake phrase in finalized speech utterances (T03/T04) and in chat messages (T02).
- Extract the question text and who asked.
- Handle "Hey BA" followed by a pause, questions split across segments, duplicates, and the clarification follow-up window.
- Emit `TriggerEvent` to the agent orchestrator (T12).

**Out of scope**
- Answering (T12). The output gate that blocks unsolicited posts lives in T02.

## Details

### Design choice: transcript-based detection (README answer draft)
We detect the wake phrase **in the Whisper transcript**, not with an acoustic wake-word model:
- We transcribe everything anyway (notes), so text detection adds no extra model or GPU cost.
- Acoustic wake-word engines need a custom model for "Hey BA". openWakeWord can be trained for it, but that takes training data and time. Picovoice Porcupine needs an access key and licence, which conflicts with fully self-hosted operation.
- Text detection lets us extract the question from the same utterance.
- The cost is latency: we wait for end-of-utterance plus STT, about 1.7 s p95 (T03, T14 stages 1–3). That fits the 10 s budget (T14). An acoustic detector remains a possible later optimisation.

### Normalisation
Lowercase, strip punctuation, collapse spelled-out letters: `"b.a."`, `"b a"`, `"bee a"`, `"bea"` → `"ba"`. Keep a mapping of the original character offsets so the question can be cut from the original text.

### Matching
Matching works on the **two-token window** (greeting + name) after normalisation, which has already collapsed `b a` / `bee a` / `b.a.` to `ba`. Windows start at token 0, 1 or 2 only, because the wake phrase normally opens a sentence.
1. **Negative list first:** windows like `hey bay`, `hey bob`, `hey bae` and any phrase from the negatives test set are rejected before matching.
2. **Exact/variant list** (`trigger.phrases` plus a curated variants list built from real ASR outputs during testing): `hey ba`, `hey bea`, `hey baa`, `hi ba`, `okay ba`, `ok ba`. Add variants only after seeing them in ASR logs, and run every variant against the negatives list (below).
3. **Fuzzy fallback:** `rapidfuzz.fuzz.ratio(window, variant)` ≥ `trigger.fuzzy_threshold` (default 85) against each variant. This catches spelling noise like `hey baah`. Fuzzy scores on very short strings are unstable, so the negative list and the T05-U2 negatives decide the final threshold. If tuning can't separate the two lists, disable fuzzy matching and rely on the variant list.
4. **Never** trigger on "BA" alone ("the BA team", "BA's report"), on "hey" + another name ("hey Bob"), or on "BAE", "bay area" and similar.
5. STT bias: T03 passes "Hey BA" as a hotword / initial prompt so Whisper writes it consistently.

### Question extraction
- `question` = text after the wake phrase, trimmed (`"Hey BA, what did we decide on the battery?"` → `"what did we decide on the battery?"`).
- **Pause after the wake phrase**: if the remainder has < 2 words, wait up to `trigger.pause_wait_s` (5 s) for the **same speaker's** next utterance and use it as the question.
- **Wake phrase with no question at all**: if the wait expires with nothing from the asker, emit the `TriggerEvent` anyway with `question=""`. The agent then asks its one clarifying question (`Yes — what would you like me to do?`, T12). Someone did address the bot, so this is a reply, not an interruption, and staying silent after being called would look like a failure in the live test. The negatives list (below) keeps this from firing on look-alike words.
- **Split question**: if the remainder doesn't end with `?`/`.` and has < 5 words, wait up to `trigger.split_wait_s` (1.5 s) for a continuation from the same speaker and merge. This adds latency, so it applies only to short fragments.
- Speaker = utterance speaker (T04), or chat sender.

### Chat triggers
The same matcher runs on chat messages (`is_self=False`). Chat text is exact, so matching is strict (prefix "hey ba" / "@ba" / "ba:" case-insensitive).

### Duplicates and cooldown
- If the same speaker triggers twice within `trigger.dedup_window_s` (3 s) with ≥ 80% similar questions (e.g. they said it and typed it, or Whisper repeated a segment), keep only the first.
- One in-flight answer per speaker. A new trigger from the same speaker while the previous answer is running is queued, not dropped. Triggers from **different** speakers run in parallel (T06 `OLLAMA_NUM_PARALLEL=2`; the background lane waits).

### Clarification follow-up window
When the agent asks a clarifying question (T12), it registers a `pending_clarification(speaker, expires=now + trigger.followup_window_s)`. During that window, the **asking speaker's** next utterance is routed to the agent as the answer **without** "Hey BA". No other speaker's speech and nothing after expiry is routed. This is the only exception to "wake phrase required", and it is documented in the README wake-word answer.

### Output
`TriggerEvent(question, speaker, source: "speech"|"chat", utterance_id, t_speech_end, t_detected)`. `t_speech_end` is the end of the speaker's audio for the **last utterance that forms the question** (after any pause/split merge above), or the chat message time. It starts the 10 s latency clock (T14). The split-question wait (≤ 1.5 s) happens after `t_speech_end`, so it counts against the budget. The pause wait (≤ 5 s) happens while the person is still asking, so it doesn't.

## Acceptance criteria

- [ ] Positive set: recall ≥ 95% on spoken "Hey BA" phrasings in the recorded rehearsal audio.
- [ ] Negative set: **0 false triggers** in a 30-min recording of meeting talk without the wake phrase, including the word "BA" used normally.
- [ ] Question extraction is exact on the unit set; pause and split cases are merged correctly.
- [ ] Chat triggers work, and a duplicate spoken + typed trigger produces one answer.
- [ ] The follow-up window accepts only the asking speaker, only within the window.
- [ ] Detection takes ≤ 50 ms per utterance.

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T05-U1 | unit | Table-driven positives (≥ 25: punctuation, case, ASR variants, wake phrase mid-sentence at token ≤ 2) | All detected; question extracted exactly |
| T05-U2 | unit | Table-driven negatives (≥ 25: "the BA team", "hey Bob", "BAE", "bay area", "hey everyone", "baseline", "a BA should…") | None detected |
| T05-U3 | unit | Wake phrase alone, then same-speaker utterance 2 s later | Merged into one trigger |
| T05-U4 | unit | Wake phrase alone, then a *different* speaker | Not merged; waits for the asker; after `pause_wait_s` emits a trigger with `question=""` (→ one clarifying question, T12) |
| T05-U5 | unit | Duplicate spoken + chat within 3 s | One event |
| T05-U6 | unit | Follow-up window: asker inside window / other speaker / after expiry | Routed / ignored / ignored |
| T05-I1 | integration | Rehearsal audio (T16) through T03→T04→T05 | Recall ≥ 95%, 0 false triggers |
| T05-I2 | integration | 30-min "no wake phrase" meeting recording | 0 triggers, 0 bot messages after the consent notice |

## Dependencies

- T03 (finalized utterances, word timestamps, hotwords), T04 (speaker), T02 (chat messages).
- Enables T12 (orchestrator), T14 (latency clock start).

## Related resources

- RapidFuzz: https://github.com/rapidfuzz/RapidFuzz
- openWakeWord (acoustic alternative considered): https://github.com/dscripka/openWakeWord
- Picovoice Porcupine (considered, rejected: needs access key / licence): https://picovoice.ai/platform/porcupine/
- faster-whisper `hotwords` / `initial_prompt` parameters: https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py
