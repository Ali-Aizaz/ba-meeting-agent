# T18 — Bonus: Risks DB, cross-meeting memory, voice replies, second platform

**Priority:** Bonus (+10 total) · **Milestone:** M5 Bonus · **Depends on:** all Must tasks passing (T16 rehearsal 2 green) · **Estimate:** 0.5–3 days each

## Why

- "Bonus" row: **voice replies, second platform, cross-meeting memory, Risks database** (+10).
- "Long-term memory" part (Bonus): **past meetings from Notion / vector DB**.
- Bonus points count only if the Must items are solid. **Don't start this until T16 rehearsal 2 passes.** Do the items in order of value per effort (below).

## What

| Order | Feature | Effort | Builds on |
|---|---|---|---|
| B1 | Risks database | 0.5–1 day | T09 (Risk label exists), T11 |
| B2 | Cross-meeting memory (long-term) | 1.5–2 days | T07 (Qdrant), T10, T11 |
| B3 | Voice replies | 1.5–2 days | T02 (browser mic), T12 |
| B4 | Second platform | 3+ days | T02 adapter interface |

Each bonus is behind a config flag and **off by default** unless it's stable, so it can't break the Must behaviour.

---

## B1 — Risks database

### Details
- Fifth Notion database **Risks**, linked to Meeting Notes (relation). Properties: Risk (title), Severity (select: High/Medium/Low), Likelihood (select, optional), Owner, Mitigation (rich_text), Raised by, Time, Status (Open/Mitigated/Closed), Meeting (relation), Key.
- Add it to `notion_schema.yaml` and `notion.databases.risks`. `scripts/notion_setup.py` creates it in the sandbox. Ask TechSol to add it to the provided workspace, or create it under the same page with the integration.
- Tool `log_risk(risk, severity, owner, mitigation, said_by, time)`: dedup like T11, confirmation `✓ Risk logged: cell supply delay (High) — Omar`.
- Auto-log from the classifier's `Risk` label above a confidence threshold (`classifier.auto_log_risks`, default `false`; enable after measuring precision). Severity is set only if stated or asked. The agent may propose a severity marked `(proposed)`.
- Requests: "Hey BA, log a risk: …", "Hey BA, what are the open risks?"
- **Supplier-risk link:** `build_bom` flags (single-supplier, long-lead) can be logged as risks on request ("Hey BA, log the single-supplier parts as risks").

### Tests
| ID | Test | Pass |
|---|---|---|
| T18-B1-U1 | Payload + dedup + confirmation format | As T11 |
| T18-B1-I1 | Sandbox: log risk, relation to meeting visible, Meeting Notes "Risks" section links rows | Pass |
| T18-B1-I2 | Classifier auto-log precision on the eval set | ≥ 0.85 before enabling by default |

---

## B2 — Cross-meeting memory (long-term)

### Details
- On `save_meeting()` and at startup, index past **Meeting Notes, Decisions, Tasks, Requirements (and Risks)** from Notion into a separate Qdrant collection `meetings` (chunks carry meeting date, title, Notion URL).
- Tool `recall_past(query)` returns hits with meeting date and link. The agent decides between `recall` (this meeting) and `recall_past` (earlier meetings). The persona says: "for earlier meetings, use recall_past and say which meeting."
- Use cases: "Hey BA, what did we decide about the battery last week?", "Hey BA, any open tasks from previous Falcon meetings?" (query Notion Tasks with status ≠ Done for open items).
- Answer format: `Decision (Falcon review, 11 Oct): 75 kWh pack shelved — reason: cost. <link>`
- Conflicts: if a past decision conflicts with today's, report both with dates. Never merge them silently.
- Privacy: only meetings in the provided workspace. Same self-hosted embedding model.

### Tests
| ID | Test | Pass |
|---|---|---|
| T18-B2-U1 | Indexer: Notion pages → chunks with date/title/URL; idempotent re-index | Pass |
| T18-B2-I1 | Two sandbox meetings; in meeting 2 ask about a meeting-1 decision | Correct answer with date + link |
| T18-B2-I2 | Open tasks from previous meetings | Lists only non-Done tasks |
| T18-B2-P1 | `recall_past` latency | p95 ≤ 400 ms |

---

## B3 — Voice replies

### Details
- Local TTS: **Piper** (fast on CPU, many voices). Self-hosted, consistent with C2.
- Route TTS audio into Chromium as its microphone: a PulseAudio null sink `bot_voice` + `module-remap-source` (or virtual source) from its monitor, set as Chromium's default input. Unmute the mic in Meet only while speaking, then mute again.
- **When to speak:** only when asked ("speaks only when asked"), as a mode: `voice.enabled: false` by default. When on, the agent speaks a short version (≤ 2 sentences) of the reply addressed to the asker. The **chat reply is still posted** (≤ 10 s rule, written record).
- Never speak tables. Speak a one-line summary instead ("Pack BOM posted in chat; total is about … dollars per the parts list").
- Echo safety: our capture reads only Chromium's output (remote audio), so the bot's own voice isn't transcribed. Still pause trigger detection while speaking.
- Latency: TTS for 2 sentences on CPU ~0.3–1 s (measure). Voice starts after the chat post, so C8 is unaffected.

### Tests
| ID | Test | Pass |
|---|---|---|
| T18-B3-U1 | TTS renders; text shortening for speech; no tables spoken | Pass |
| T18-B3-I1 | Real Meet: participants hear the reply; bot mic muted before/after | Pass |
| T18-B3-I2 | Bot's own speech never appears in the transcript or triggers | 0 occurrences |

---

## B4 — Second platform

### Details
- Implement the `MeetingPlatform` interface (T02) for a second platform. Options:
  - **Zoom** via the Meeting SDK for Linux: raw audio, mixed and per participant (`onOneWayAudioRawDataReceived`, which improves speaker tagging), plus chat. Needs a Zoom app with SDK credentials and PulseAudio in the container. Subscribing to raw audio needs host/co-host/local-recording rights or a recording token, so the meeting host must grant it. Check Zoom's current authorisation rules for SDK apps joining meetings.
  - **Microsoft Teams** via its web client with Playwright (guest join). Reuses most of the Meet approach: DOM chat, active speaker, PulseAudio capture. Selectors and the lobby flow differ.
- Choose based on which account TechSol can provide, and confirm with the point of contact.
- Everything above the adapter (STT, trigger, agent, Notion) must work unchanged. If it doesn't, that exposes a leaky abstraction to fix.
- `meeting.platform: zoom | teams` selects the adapter.

### Tests
| ID | Test | Pass |
|---|---|---|
| T18-B4-I1 | Adapter contract tests (same suite as T02 fake page, platform-specific fixtures) | Pass |
| T18-B4-E1 | T16 text + live scenario on the second platform | 9/9 items pass |

## Acceptance criteria

- [ ] Each delivered bonus is behind a flag, documented in the README, and covered by the tests above.
- [ ] With all bonus flags off, the T16 suite still passes unchanged (no regressions).
- [ ] With the delivered bonus flags on, the T16 suite still passes, plus the bonus tests.

## Dependencies

- Must tasks complete. B1 → T09/T11. B2 → T07/T10/T11. B3 → T02/T12. B4 → T02 interface.

## Related resources

- Notion relations: https://developers.notion.com/reference/page-property-values#relation
- Qdrant collections: https://qdrant.tech/documentation/concepts/collections/
- Piper TTS: https://github.com/rhasspy/piper
- PulseAudio modules (`module-null-sink`, `module-remap-source`): https://www.freedesktop.org/wiki/Software/PulseAudio/Documentation/User/Modules/
- Zoom Meeting SDK for Linux: https://developers.zoom.us/docs/meeting-sdk/linux/
- Zoom raw data (audio) in Meeting SDK for Linux: https://developers.zoom.us/docs/meeting-sdk/linux/default-ui/advanced-features/raw-data/
- Zoom Linux raw-recording sample (Docker + PulseAudio setup): https://github.com/zoom/meetingsdk-linux-raw-recording-sample
- Microsoft Teams web client join as guest: https://support.microsoft.com/en-us/office/join-a-meeting-without-a-teams-account-c6efc38f-4e03-4e79-b28f-e65a4c039508
