"""Stateful Hey BA detector: pause, duplicate, and follow-up window."""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from rapidfuzz import fuzz

from ba_agent.models import TriggerEvent
from ba_agent.trigger.match import (
    _POST_WAKE_COMMENTARY_PREFIXES,
    WakeMatch,
    _clean_question,
    _starts_with_any,
    _truncate_question_len,
    match_wake,
)

_CHAT_DIRECT_PREFIX = re.compile(r"^@?ba\s*[,:\-—]\s*(.+)$", re.I)

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

_INCOMPLETE_SPEECH_PREAMBLES: set[tuple[str, ...]] = {
    ("can", "you"),
    ("could", "you"),
    ("would", "you"),
    ("will", "you"),
    ("can", "you", "please"),
    ("could", "you", "please"),
    ("can", "you", "also"),
    ("could", "you", "also"),
    ("can", "you", "tell"),
    ("can", "you", "tell", "me"),
    ("could", "you", "tell", "me"),
    ("tell", "me"),
    ("tell", "me", "about"),
    ("can", "you", "tell", "me", "about"),
    ("could", "you", "tell", "me", "about"),
    ("can", "you", "check"),
    ("can", "you", "show"),
    ("can", "you", "show", "me"),
    ("can", "you", "give"),
    ("can", "you", "give", "me"),
    ("can", "you", "add"),
    ("can", "you", "also", "add"),
    ("can", "you", "create"),
    ("add", "a"),
    ("add", "a", "task"),
    ("create", "a"),
    ("create", "a", "task"),
    ("what", "is"),
    ("what", "are"),
    ("what", "about"),
    ("how", "about"),
    ("which", "one"),
}

_DANGLING_END_WORDS = {
    "about",
    "the",
    "a",
    "an",
    "for",
    "to",
    "of",
    "with",
    "from",
    "into",
    "between",
    "versus",
    "vs",
    "and",
    "or",
}


def _is_incomplete_speech_fragment(question: str) -> bool:
    words = [
        w.lower().strip(".,!?;:\"'")
        for w in question.split()
        if w.strip(".,!?;:\"'")
    ]
    if not words:
        return False
    if len(words) == 1:
        return words[0] not in _IMMEDIATE_ONE_WORD
    if tuple(words) in _INCOMPLETE_SPEECH_PREAMBLES:
        return True
    if len(words) <= 6 and words[-1] in _DANGLING_END_WORDS:
        return True
    return False


@dataclass
class _Pending:
    speaker: str
    fragment: str
    source: str
    t: float
    caption_id: str | None
    committed_at: float | None = None


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
        is_speaker_active: Callable[[str], bool] | None = None,
        fast_pause_wait_s: float = 2.2,
    ) -> None:
        self.pause_wait_s = pause_wait_s
        self.dedup_window_s = dedup_window_s
        self.followup_window_s = followup_window_s
        self.fuzzy_threshold = fuzzy_threshold
        self.fuzzy = fuzzy
        self.extra_phrases = extra_phrases or []
        self.is_speaker_active = is_speaker_active
        self.fast_pause_wait_s = fast_pause_wait_s
        self.pending: _Pending | None = None
        self.followup: _Followup | None = None
        self.last: _Last | None = None
        self._recent: list[_Last] = []

    def arm_followup(self, speaker: str, now: float) -> None:
        self.followup = _Followup(speaker=speaker, until=now + self.followup_window_s)
        self.pending = None

    def _pending_deadline(self, pending: _Pending, *, fast: bool) -> float:
        wait_s = min(self.pause_wait_s, self.fast_pause_wait_s) if fast else self.pause_wait_s
        base_deadline = pending.t + wait_s
        if pending.committed_at is None or pending.committed_at <= pending.t:
            return base_deadline
        min_post_commit = min(wait_s, 1.8) if fast else self.pause_wait_s
        return max(base_deadline, pending.committed_at + min_post_commit)

    def tick(self, t: float) -> list[TriggerEvent]:
        if self.pending:
            fast = False
            if self.is_speaker_active is not None:
                try:
                    active = bool(self.is_speaker_active(self.pending.speaker))
                except Exception:
                    active = True
                fast = not active
            if t >= self._pending_deadline(self.pending, fast=fast):
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
        *,
        committed_at: float | None = None,
    ) -> list[TriggerEvent]:
        events: list[TriggerEvent] = []
        if self.pending:
            merge_deadline = self._pending_deadline(self.pending, fast=False)
            if speaker == self.pending.speaker and t <= merge_deadline:
                pending = self.pending
                self.pending = None
                events.extend(
                    self._merge(
                        pending,
                        speaker,
                        text,
                        source,
                        t,
                        caption_id,
                        committed_at=committed_at,
                    )
                )
                return events
            if t > merge_deadline:
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
                if not self._duplicate(speaker, explicit_match.question, t, source=source):
                    events.append(
                        self._emit(
                            explicit_match.question, speaker, source, t, caption_id, followup=False
                        )
                    )
                return events
            raw_tokens = text.strip().split()
            kept = _truncate_question_len(raw_tokens)
            question = " ".join(raw_tokens[:kept]).strip(" .,!?;:")
            if not self._duplicate(speaker, question, t, source=source):
                events.append(self._emit(question, speaker, source, t, caption_id, followup=True))
            return events
        match = match_wake(
            text,
            fuzzy=self.fuzzy,
            threshold=self.fuzzy_threshold,
            extra_phrases=self.extra_phrases,
        )
        if match is None and source == "chat":
            direct = _CHAT_DIRECT_PREFIX.match(text.strip())
            if direct:
                q_clean = _clean_question(direct.group(1))
                if q_clean:
                    match = WakeMatch(question=q_clean, phrase="ba:")
        if match is None:
            return events
        words = match.question.split()
        single_token = words[0].lower().strip(".,!?;:") if len(words) == 1 else ""
        if source != "chat" and (
            (len(words) < 2 and single_token not in _IMMEDIATE_ONE_WORD)
            or _is_incomplete_speech_fragment(match.question)
        ):
            commit_t = max(t, committed_at) if committed_at is not None else None
            self.pending = _Pending(
                speaker,
                match.question,
                source,
                t,
                caption_id,
                committed_at=commit_t,
            )
            return events
        if self._duplicate(speaker, match.question, t, source=source):
            return events
        events.append(self._emit(match.question, speaker, source, t, caption_id, followup=False))
        return events

    def _merge(
        self,
        pending,
        speaker,
        text,
        source,
        t,
        caption_id,
        *,
        committed_at: float | None = None,
    ) -> list[TriggerEvent]:
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
        if source != "chat" and question and _is_incomplete_speech_fragment(question):
            commit_t = max(t, committed_at) if committed_at is not None else t
            self.pending = _Pending(
                speaker,
                question,
                source,
                pending.t,
                caption_id,
                committed_at=commit_t,
            )
            return []
        if self._duplicate(speaker, question, t, source=source):
            return []
        return [self._emit(question, speaker, source, t, caption_id, followup=False)]

    def _expire(self, t: float) -> list[TriggerEvent]:
        pending = self.pending
        if pending is None:
            return []
        self.pending = None
        expired_q = (
            ""
            if (pending.source != "chat" and _is_incomplete_speech_fragment(pending.fragment))
            else pending.fragment
        )
        return [
            self._emit(
                expired_q,
                pending.speaker,
                pending.source,
                t,
                pending.caption_id,
                followup=False,
            )
        ]

    def _duplicate(
        self,
        speaker: str,
        question: str,
        t: float,
        *,
        source: str = "speech",
    ) -> bool:
        q_clean = question.strip().lower().rstrip(".?!")
        if not q_clean:
            return False
        if self.last is not None and speaker == self.last.speaker:
            dt = t - self.last.t
            last_clean = self.last.question.strip().lower().rstrip(".?!")
            if last_clean and 0.0 <= dt <= max(self.dedup_window_s, 12.0):
                if dt <= self.dedup_window_s and fuzz.ratio(q_clean, last_clean) >= 90:
                    return True
                if len(last_clean.split()) >= 2 and (
                    q_clean == last_clean or q_clean.startswith(last_clean + " ")
                ):
                    return True
        if source != "chat" and len(q_clean.split()) >= 4:
            replay_window = max(self.dedup_window_s, 180.0 if len(q_clean.split()) >= 5 else 60.0)
            for prev in reversed(self._recent):
                if prev.speaker != speaker:
                    continue
                dt = t - prev.t
                if dt < 0.0 or dt > replay_window:
                    continue
                prev_clean = prev.question.strip().lower().rstrip(".?!")
                if prev_clean and (
                    q_clean == prev_clean or q_clean.startswith(prev_clean + " ")
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
            t_committed_perf=time.perf_counter(),
        )
        entry = _Last(speaker=speaker, question=question, t=t)
        self.last = entry
        self._recent = [item for item in self._recent if 0.0 <= t - item.t <= 240.0][-29:]
        self._recent.append(entry)
        return event
