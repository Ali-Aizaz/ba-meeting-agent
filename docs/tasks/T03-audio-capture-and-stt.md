# T03 — Audio capture and speech-to-text (Listener)

**Priority:** Must · **Milestone:** M2 Meeting I/O · **Depends on:** T01, T02 · **Estimate:** 2–3 days

## Why

- "Listener" part: **streams meeting audio to speech-to-text and keeps a rolling transcript with speaker + time.**
- "Notes" row: every line needs speaker and timestamp, which starts with accurate utterance timing here.
- Spoken "Hey BA" detection (T05) and the 10 s reply budget (C8) depend on fast, accurate transcription.
- Hosted transcription is not allowed (C2), so STT runs locally on the GPU with **faster-whisper** (the stack the PDF suggests).

## What

**In scope**
- Capture the browser's audio output from the PulseAudio sink as 16 kHz mono PCM.
- Voice activity detection (VAD) to cut the stream into utterances.
- Transcribe each utterance with faster-whisper on the GPU.
- Filter Whisper hallucinations.
- Emit `Utterance` events (without speaker; T04 adds it) on the shared meeting clock.
- `TranscriptStore`: append-only rolling transcript, persisted, with window queries.

**Out of scope**
- Speaker attribution (T04), wake phrase logic (T05), classification (T09).

## Details

### Pipeline

```
Chromium → PulseAudio null sink "meet_out" → monitor source
  → parec/ffmpeg (16 kHz, mono, s16le) → 32 ms frames (512 samples)
  → Silero VAD → utterance segmenter → faster-whisper (GPU) → filters
  → Utterance{start_s, end_s, text} → T04 speaker tagging → TranscriptStore + event bus
```

- Create the sink at container start: `pactl load-module module-null-sink sink_name=meet_out` and make it the default sink so Chromium plays into it.
- Read `meet_out.monitor` with `parec --format=s16le --rate=16000 --channels=1` (or ffmpeg) in an asyncio subprocess.
- We capture only what the browser plays (remote participants), so the bot's own voice (T18 voice replies) never enters the transcript.

### Segmentation (Silero VAD)
- Speech probability per 512-sample (32 ms) frame at 16 kHz, the chunk size current Silero VAD versions expect. Threshold `vad.threshold`.
- An utterance ends after `vad.min_silence_ms` (default 700 ms) of silence. This is the main latency/accuracy trade-off: shorter values reply faster but split sentences more often.
- Force a split at `vad.max_utterance_s` (default 20 s) so long monologues still produce timely transcripts.
- Pad 200 ms before and after each segment so first and last words aren't cut.

### Transcription (faster-whisper)
- Model `stt.model`: benchmark **`large-v3-turbo`** and **`distil-large-v3`** on the VM; pick the better accuracy that fits the latency budget (T14). `device=cuda`, `compute_type=float16`.
- `language="en"` (fixed; skips language detection latency), `beam_size=stt.beam_size` (1 for speed; try 5 if accuracy suffers).
- `condition_on_previous_text=False` to avoid repetition loops across segments.
- Domain vocabulary bias with `hotwords` and/or `initial_prompt`: "Hey BA, TechSol, Falcon, kWh, NCAP, ADAS, BOM, MoSCoW, inverter, chassis, LFP, NMC". This improves recognition of the wake phrase and jargon.
- `word_timestamps=True`, so T05 can locate "Hey BA" inside an utterance and T04 can split speaker changes if needed.
- Run inference in a worker thread (`asyncio.to_thread`) so the event loop is never blocked. Keep one model instance loaded (warm) for the meeting's lifetime.

### Hallucination filters
Whisper can produce text on silence or noise. Drop a segment if:
- `no_speech_prob > 0.6` and `avg_logprob < -1.0`, or
- the text matches a small blocklist of known silence hallucinations ("Thank you for watching", "Subtitles by…") **and** the segment is short or low-confidence, or
- it is a verbatim repeat of the previous segment within 2 s.
Log every drop for later tuning.

### Clock and timestamps
- Meeting clock t=0 = admission time (from T02). Compute timestamps from **sample counts** (`samples / 16000`), not wall-clock reads, so they don't drift. Record the wall time of sample 0 to convert to UTC.
- `Utterance.start_s/end_s` = segment boundaries on that clock. Display format is `[mm:ss]` (`[h:mm:ss]` past one hour).

### TranscriptStore
- Append-only. Persist to SQLite (`storage.sqlite_path`) as utterances arrive, so a restart mid-meeting keeps the record (T10/T15).
- Queries: `last(n)`, `window(seconds)`, `between(t1, t2)`, `by_id`.
- Publishes `utterance_finalized` on the internal event bus. T05, T09 and T10 subscribe.

### Backpressure
If STT falls behind (queue > 3 segments), log a warning and keep going. Never drop audio silently. The trigger path matters most, so T06's LLM priority lanes must keep the GPU free enough for STT. Measure this in T14.

## Acceptance criteria

- [ ] Continuous capture from the Meet sink for 60 min with no gaps or growing lag.
- [ ] Utterances are emitted ≤ 1.7 s after the speaker stops (VAD silence 0.7 s + buffering 0.2 s + STT 0.8 s; T14 stages 1–3), p95, on the VM GPU.
- [ ] WER ≤ 15% on the clean scripted test audio. Domain terms ("Hey BA", "kWh", "NCAP", "ADAS", "BOM") are recognised in ≥ 90% of occurrences.
- [ ] No hallucinated text during 5 min of silence.
- [ ] Timestamps are within ±0.3 s of ground truth on the scripted audio.
- [ ] The transcript survives an agent restart (reloaded from SQLite).

## Tests

| ID | Type | Test | Pass |
|---|---|---|---|
| T03-U1 | unit | Segmenter on synthetic audio (speech / silence / speech) | Boundaries within ±100 ms of ground truth; padding applied |
| T03-U2 | unit | Max-length forced split on a 45 s speech fixture | Splits ≤ 20 s each; no lost audio |
| T03-U3 | unit | Timestamp maths from sample counts; `[mm:ss]` and `[h:mm:ss]` formatting | Exact |
| T03-U4 | unit | Hallucination filter on a fixture list of segments with probabilities | Expected drops/keeps |
| T03-U5 | unit | TranscriptStore persistence + reload; window queries | Identical after reload |
| T03-I1 | integration (GPU) | Transcribe `tests/fixtures/audio/falcon_script.wav` (multi-voice TTS or recorded reading of a known script) | WER ≤ 15%; domain-term recall ≥ 90% |
| T03-I2 | integration (GPU) | 5 min silence + background noise fixture | 0 utterances emitted |
| T03-P1 | performance | STT latency on 2 s, 5 s, 15 s segments on the VM | p95 ≤ 1.0 s for 5 s segment (record real numbers for T14) |
| T03-S1 | soak | 60 min replay through PulseAudio into the pipeline | Queue never > 3; RSS / GPU memory stable |

Test audio can be generated locally with Piper TTS in several voices, or recorded by teammates reading the T16 script. Store it under `tests/fixtures/audio/` with a ground-truth transcript JSON.

## Dependencies

- T01 (config, models), T02 (sink routing, meeting clock t=0).
- Enables T04, T05, T09, T10.

## Related resources

- faster-whisper: https://github.com/SYSTRAN/faster-whisper
- Silero VAD: https://github.com/snakers4/silero-vad
- PulseAudio modules (`module-null-sink`): https://www.freedesktop.org/wiki/Software/PulseAudio/Documentation/User/Modules/
- `parec` / `pacat` manual: https://manpages.ubuntu.com/manpages/jammy/man1/pacat.1.html
- jiwer (WER computation for tests): https://github.com/jitsi/jiwer
- Piper TTS (local test-audio generation): https://github.com/rhasspy/piper
