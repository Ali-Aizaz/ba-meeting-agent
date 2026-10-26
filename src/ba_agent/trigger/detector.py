"""Stateful Hey BA detector: pause, duplicate, and follow-up window."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from rapidfuzz import fuzz

from ba_agent.models import TriggerEvent
from ba_agent.trigger.match import match_wake


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
            question = text.strip()
            if not self._duplicate(speaker, question, t):
                events.append(self._emit(question, speaker, source, t, caption_id, followup=True))
            return events
        match = match_wake(
            text,
            fuzzy=self.fuzzy and source != "chat",
            threshold=self.fuzzy_threshold,
            extra_phrases=self.extra_phrases,
        )
        if match is None:
            return events
        if len(match.question.split()) < 2:
            self.pending = _Pending(speaker, match.question, source, t, caption_id)
            return events
        if self._duplicate(speaker, match.question, t):
            return events
        events.append(self._emit(match.question, speaker, source, t, caption_id, followup=False))
        return events

    def _merge(self, pending, speaker, text, source, t, caption_id) -> list[TriggerEvent]:
        match = match_wake(
            text,
            fuzzy=self.fuzzy and source != "chat",
            threshold=self.fuzzy_threshold,
            extra_phrases=self.extra_phrases,
        )
        if match is not None and len(match.question.split()) >= 2:
            question = match.question
        else:
            question = f"{pending.fragment} {text}".strip()
        if self._duplicate(speaker, question, t):
            return []
        return [self._emit(question, speaker, source, t, caption_id, followup=False)]

    def _expire(self, t: float) -> list[TriggerEvent]:
        pending = self.pending
        if pending is None:
            return []
        self.pending = None
        return [
            self._emit("", pending.speaker, pending.source, t, pending.caption_id, followup=False)
        ]

    def _duplicate(self, speaker: str, question: str, t: float) -> bool:
        if self.last is None or speaker != self.last.speaker:
            return False
        if t - self.last.t > self.dedup_window_s:
            return False
        if not question.strip() or not self.last.question.strip():
            return False
        return fuzz.ratio(question.lower(), self.last.question.lower()) >= 90

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
