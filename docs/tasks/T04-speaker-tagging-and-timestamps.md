# T04 — Speaker tagging and timestamps

**Priority:** Must · **Milestone:** M2 Meeting I/O · **Depends on:** T02, T03 · **Estimate:** 2–3 days

## Why

- "Notes" row: **every line tagged with speaker and timestamp**.
- "Listener" part: rolling transcript **with speaker + time**.
- Tools need it: `create_task(…, said_by, time)` and `log_decision(decision, by, time, reason)`.
- The README must answer **"speaker tagging"** (T17). This task produces that answer.
- Google Meet gives us **one mixed audio stream** (T02/T03), so the transcript alone can't tell who spoke.

## What

**In scope**
- Speaker attribution for each `Utterance`, using Meet's active-speaker UI signal aligned to the audio clock.
- Optional fallback: local speaker diarization (pyannote) mapped to names.
- Name normalisation and aliases (display name → canonical person name used as owner).
- Timestamp conventions shared by the transcript, notes and Notion.

**Out of scope**
- Collecting the raw speaking events from the DOM (T02 provides them).

## Details

### Primary approach: UI speaking timeline + overlap voting
1. T02 emits `SpeakingEvent(wall_time, name, speaking)` at 5–10 Hz from the participant tiles.
2. Build a per-participant **speaking interval timeline**: merge consecutive "speaking" samples, and close an interval after ~300 ms without the indicator.
3. Convert wall times to the meeting clock (t=0 at admission) and apply a calibrated **audio pipeline offset**, i.e. PulseAudio + capture latency (expected ~50–300 ms). Measure it once with a scripted test, store it as `speakers.audio_offset_ms`, and verify it in rehearsal.
4. For each utterance `[start_s, end_s]`, compute each participant's overlap duration:
   - Top participant overlap ≥ 50% of utterance duration → assign, `speaker_confidence = overlap ratio`.
   - Two participants both significant (overlapping speech) → assign the larger, mark `speaker_confidence` low, and add `co_speaker` in metadata.
   - No overlap (indicator missed) → if the previous utterance ended < 1.5 s ago, reuse that speaker with low confidence; else `"Unknown speaker"`.
5. Speaker change inside one long utterance: if the timeline shows a clean handover, split the utterance at the nearest word boundary using Whisper word timestamps (T03), giving one utterance per speaker.
6. Ignore the bot's own tile ("TechSol BA") and screen-share tiles.

With 3–4 participants, as in the live test, all tiles are visible in the default layout, which makes the UI signal reliable. If the spike finds otherwise (e.g. the speaking indicator is missing for some tiles), turn on the fallback.

### Fallback: local diarization (`speakers.source: diarization`)
- pyannote.audio speaker diarization on a rolling window (models downloaded once and run locally on the GPU, so C2 still holds; pyannote's models are gated on Hugging Face and need a one-time token to download).
- Map anonymous clusters (`SPEAKER_00`…) to names by voting with the UI timeline across the meeting; a cluster takes the name it overlaps most.
- Costs extra GPU time and latency. Use it only if the UI signal fails in testing.

### Chat messages
`ChatMessage.sender` comes directly from the DOM, so chat-sourced lines are attributed exactly.

### Names and aliases (`config/people.yaml`)

```yaml
people:
  - name: Ali            # canonical, used in Notion "Owner" and confirmations
    aliases: ["Ali Khan", "ali.khan@…"]
  - name: Sara
    aliases: ["Sara M."]
```
- Display names from Meet map to canonical names. Unknown display names are used as-is.
- T09 and T11 use the same resolver for spoken owners ("Ali, can you…", "Sara will do it").

### Timestamp conventions (used everywhere)
- Display: `[mm:ss]` meeting clock (`[h:mm:ss]` past one hour).
- Storage: `start_s`, `end_s` (float seconds) plus `wall_time` UTC ISO-8601.
- Notion `time` fields: `mm:ss` text plus the meeting date (T11).
- Note line format (T11 Meeting Notes): `[12:41] Sara: We'll go with the 60 kWh pack.`

### README answer draft (speaker tagging)
> Meet sends us one mixed audio stream, so we don't diarize from audio by default. The bot samples Meet's active-speaker indicator on each participant tile 5–10 times per second and builds a speaking timeline. Each Whisper segment is assigned to the participant with the most time-overlap, after correcting for audio pipeline latency. Overlapping speech is marked low-confidence, and handovers inside one segment are split on word timestamps. Chat lines carry the sender directly. Local pyannote diarization is available as a fallback and is mapped to names through the same timeline.

## Acceptance criteria

- [ ] Every utterance has a `speaker` (name or "Unknown speaker") and `speaker_confidence`.
- [ ] In a 3-person scripted meeting, ≥ 90% of speech duration is attributed to the correct person, and "Unknown speaker" covers ≤ 5% of utterances.
- [ ] Late joiners and renamed participants are handled (new tiles are picked up within 10 s).
- [ ] The bot's own tile never appears as a speaker.
- [ ] The audio offset calibration procedure is documented and its value is set in config.

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T04-U1 | unit | Overlap assignment: single speaker, clean handover, overlap, gap, no signal | Expected speaker + confidence in each case |
| T04-U2 | unit | Utterance split at handover using word timestamps | Two utterances, correct text split |
| T04-U3 | unit | Interval builder from noisy 10 Hz samples (dropouts < 300 ms) | Intervals merged correctly |
| T04-U4 | unit | Alias resolver (case, partial names, unknown names) | Canonical names returned |
| T04-U5 | unit | Offset correction applied before assignment | Shifted timeline assigns correctly |
| T04-I1 | integration (real Meet) | 3 people (or 3 Playwright browsers with distinct names, each playing a TTS voice through Chromium's fake mic, `--use-file-for-fake-audio-capture`) follow a turn-taking script | ≥ 90% duration accuracy |
| T04-I2 | integration | Participant joins late / renames | Attributed under the new name within 10 s |
| T04-I3 | integration (fallback) | Same script with `speakers.source: diarization` | ≥ 85% duration accuracy; latency recorded |

## Dependencies

- T02 (speaking events, participants), T03 (utterances with word timestamps, meeting clock).
- Enables T05 (who asked), T09 (said_by / by), T10, T11.

## Related resources

- pyannote.audio: https://github.com/pyannote/pyannote-audio
- faster-whisper word timestamps: https://github.com/SYSTRAN/faster-whisper#word-level-timestamps
- Chromium fake media flags for test participants (`--use-fake-device-for-media-stream`, `--use-file-for-fake-audio-capture`): https://peter.sh/experiments/chromium-command-line-switches/
- Diarization error rate concepts (pyannote.metrics): https://pyannote.github.io/pyannote-metrics/
