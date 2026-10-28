"""Stateful Hey BA detector: pause, duplicate, and follow-up window."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from rapidfuzz import fuzz

from ba_agent.models import TriggerEvent
from ba_agent.trigger.match import (
    _POST_WAKE_COMMENTARY_PREFIXES,
    _starts_with_any,
    _truncate_question_len,
    match_wake,
)

_IMMEDIATE_ONE_WORD = {
    "leave",
    "exit",
    "bye",
    "quit",
    "save",
    "sync",
    "summarize",
    "summary",
    "recap",
    "notes",
    "tasks",
    "actions",
    "status",
    "help",
    "hello",
    "hi",
    "hey",
}


@dataclass
class _Pending:
    speaker: str
    fragment: str
    source: str
    t: float
    caption_id: str | None


@dataclass
class _Followup:
    speaker: str
    until: float


@dataclass
class _Last:
    speaker: str
    question: str
    t: float


class TriggerDetector:
    def __init__(
        self,
        *,
        pause_wait_s: float = 5,
        dedup_window_s: float = 3,
        followup_window_s: float = 20,
        fuzzy_threshold: int = 85,
        fuzzy: bool = True,
        extra_phrases: list[str] | None = None,
    ) -> None:
        self.pause_wait_s = pause_wait_s
        self.dedup_window_s = dedup_window_s
        self.followup_window_s = followup_window_s
        self.fuzzy_threshold = fuzzy_threshold
        self.fuzzy = fuzzy
        self.extra_phrases = extra_phrases or []
        self.pending: _Pending | None = None
        self.followup: _Followup | None = None
        self.last: _Last | None = None

    def arm_followup(self, speaker: str, now: float) -> None:
        self.followup = _Followup(speaker=speaker, until=now + self.followup_window_s)
        self.pending = None

    def tick(self, t: float) -> list[TriggerEvent]:
        if self.pending and t - self.pending.t >= self.pause_wait_s:
            return self._expire(t)
        if self.followup and t > self.followup.until:
            self.followup = None
        return []

    def on_line(
        self,
        speaker: str,
        text: str,
        source: str,
        t: float,
        caption_id: str | None = None,
    ) -> list[TriggerEvent]:
        events: list[TriggerEvent] = []
        if (
            self.pending
            and speaker == self.pending.speaker
            and t - self.pending.t <= self.pause_wait_s
        ):
            pending = self.pending
            self.pending = None
            events.extend(self._merge(pending, speaker, text, source, t, caption_id))
            return events
        if self.pending and t - self.pending.t > self.pause_wait_s:
            events.extend(self._expire(t))
        if self.followup and t > self.followup.until:
            self.followup = None
        if self.followup and speaker == self.followup.speaker and t <= self.followup.until:
            self.followup = None
            explicit_match = match_wake(
                text,
                fuzzy=self.fuzzy,
                threshold=self.fuzzy_threshold,
                extra_phrases=self.extra_phrases,
            )
            if explicit_match is not None and len(explicit_match.question.split()) >= 2:
                if not self._duplicate(speaker, explicit_match.question, t):
                    events.append(
                        self._emit(
                            explicit_match.question, speaker, source, t, caption_id, followup=False
                        )
                    )
                return events
            raw_tokens = text.strip().split()
            kept = _truncate_question_len(raw_tokens)
            question = " ".join(raw_tokens[:kept]).strip(" .,!?;:")
            if not self._duplicate(speaker, question, t):
                events.append(self._emit(question, speaker, source, t, caption_id, followup=True))
            return events
        match = match_wake(
            text,
            fuzzy=self.fuzzy,
            threshold=self.fuzzy_threshold,
            extra_phrases=self.extra_phrases,
        )
        if match is None:
            return events
        words = match.question.split()
        single_token = words[0].lower().strip(".,!?;:") if len(words) == 1 else ""
        if (
            len(words) < 2
            and source != "chat"
            and single_token not in _IMMEDIATE_ONE_WORD
        ):
            self.pending = _Pending(speaker, match.question, source, t, caption_id)
            return events
        if self._duplicate(speaker, match.question, t):
            return events
        events.append(self._emit(match.question, speaker, source, t, caption_id, followup=False))
        return events

    def _merge(self, pending, speaker, text, source, t, caption_id) -> list[TriggerEvent]:
        match = match_wake(
            text,
            fuzzy=self.fuzzy,
            threshold=self.fuzzy_threshold,
            extra_phrases=self.extra_phrases,
        )
        if match is not None and len(match.question.split()) >= 2:
            question = match.question
        else:
            raw_tokens = text.strip().split()
            norms = [tok.lower().strip(".,!?;:\"'") for tok in raw_tokens]
            if not pending.fragment.strip() and _starts_with_any(
                norms, _POST_WAKE_COMMENTARY_PREFIXES
            ):
                question = ""
            else:
                kept = _truncate_question_len(raw_tokens)
                merged_tail = " ".join(raw_tokens[:kept]).strip(" .,!?;:")
                question = f"{pending.fragment} {merged_tail}".strip()
        if self._duplicate(speaker, question, t):
            return []
        return [self._emit(question, speaker, source, t, caption_id, followup=False)]

    def _expire(self, t: float) -> list[TriggerEvent]:
        pending = self.pending
        if pending is None:
            return []
        self.pending = None
        return [
            self._emit(
                pending.fragment,
                pending.speaker,
                pending.source,
                t,
                pending.caption_id,
                followup=False,
            )
        ]

    def _duplicate(self, speaker: str, question: str, t: float) -> bool:
        if self.last is None or speaker != self.last.speaker:
            return False
        dt = t - self.last.t
        if dt > max(self.dedup_window_s, 12.0):
            return False
        q_clean = question.strip().lower().rstrip(".?!")
        last_clean = self.last.question.strip().lower().rstrip(".?!")
        if not q_clean or not last_clean:
            return False
        if dt <= self.dedup_window_s and fuzz.ratio(q_clean, last_clean) >= 90:
            return True
        if len(last_clean.split()) >= 2 and (
            q_clean == last_clean or q_clean.startswith(last_clean + " ")
        ):
            return True
        return False

    def _emit(self, question, speaker, source, t, caption_id, *, followup: bool) -> TriggerEvent:
        event = TriggerEvent(
            trigger_id=uuid.uuid4().hex,
            question=question,
            speaker=speaker,
            source=source,  # type: ignore[arg-type]
            caption_id=caption_id,
            t_detected=t,
            followup=followup,
        )
        self.last = _Last(speaker=speaker, question=question, t=t)
        return event
