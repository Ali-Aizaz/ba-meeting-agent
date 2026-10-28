"""Commit a caption once the on-screen text stops changing."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser

from ba_agent.trigger.match import match_wake, split_completed_wake_sentence


@dataclass
class CaptionRow:
    row_id: str
    speaker: str
    text: str


@dataclass
class RawCaption:
    speaker: str
    text: str
    start_s: float
    end_s: float
    row_id: str


@dataclass
class _Open:
    row_id: str
    speaker: str
    text: str
    start_s: float
    last_change: float
    full_text: str = ""


class Stabilizer:
    """Merge interim redraws of one caption row into a single line."""

    def __init__(
        self,
        stable_ms: int = 800,
        bot_names: tuple[str, ...] = ("techsol ba", "ba assistant", "you"),
    ) -> None:
        self.stable_s = stable_ms / 1000
        self.bot_names = bot_names
        self.open: dict[str, _Open] = {}
        self.committed: dict[str, tuple[str, str]] = {}

    def push_frame(self, rows: list[CaptionRow], now: float) -> list[RawCaption]:
        committed: list[RawCaption] = []
        seen: set[str] = set()
        for row in rows:
            full_text = " ".join(row.text.split())
            speaker = row.speaker.strip()
            seen.add(row.row_id)
            if not full_text or speaker.lower() in self.bot_names:
                self.open.pop(row.row_id, None)
                continue
            prev_committed = self.committed.get(row.row_id)
            if prev_committed == (speaker, full_text):
                continue
            text = full_text
            if prev_committed is not None and prev_committed[0] == speaker:
                prev_full = prev_committed[1]
                text = _strip_committed_prefix(prev_full, full_text)
                if not text:
                    self.committed[row.row_id] = (speaker, full_text)
                    self.open.pop(row.row_id, None)
                    continue
            current = self.open.get(row.row_id)
            wake_split = split_completed_wake_sentence(text)
            if wake_split is not None:
                wake_head, wake_tail = wake_split
                full_words = full_text.split()
                tail_words = wake_tail.split()
                prefix_len = max(1, len(full_words) - len(tail_words))
                committed_prefix_full = " ".join(full_words[:prefix_len])
                start_s = current.start_s if current is not None else now
                if current is None:
                    committed.extend(self._commit_others(now))
                self.open.pop(row.row_id, None)
                self.committed[row.row_id] = (speaker, committed_prefix_full)
                committed.append(RawCaption(speaker, wake_head, start_s, now, row.row_id))
                self.open[row.row_id] = _Open(row.row_id, speaker, wake_tail, now, now, full_text)
                continue
            if current is None:
                committed.extend(self._commit_others(now))
                self.open[row.row_id] = _Open(row.row_id, speaker, text, now, now, full_text)
                continue
            if speaker != current.speaker or not _same_utterance(current.text, text):
                committed.append(self._commit(current, now))
                self.open[row.row_id] = _Open(row.row_id, speaker, text, now, now, full_text)
                continue
            if text != current.text and not current.text.startswith(text):
                prev_tokens = [_norm_word(w) for w in current.text.split()]
                new_tokens = [_norm_word(w) for w in text.split()]
                current.text = text
                current.full_text = full_text
                if new_tokens != prev_tokens:
                    current.last_change = now
        if rows:
            for row_id in list(self.open):
                if row_id not in seen:
                    committed.append(self._commit(self.open[row_id], now))
        for row_id, current in list(self.open.items()):
            if now - current.last_change + 1e-9 >= self.stable_s:
                committed.append(self._commit(current, now))
        return committed

    def _commit_others(self, now: float) -> list[RawCaption]:
        committed = [self._commit(current, now) for current in list(self.open.values())]
        return committed

    def _commit(self, current: _Open, now: float) -> RawCaption:
        self.open.pop(current.row_id, None)
        self.committed[current.row_id] = (
            current.speaker,
            current.full_text or current.text,
        )
        return RawCaption(current.speaker, current.text, current.start_s, now, current.row_id)


def _norm_word(word: str) -> str:
    return "".join(ch for ch in word.lower() if ch.isalnum())


def _strip_committed_prefix(prev_full: str, full_text: str) -> str:
    prev_words = prev_full.split()
    full_words = full_text.split()
    prev_norm = [_norm_word(w) for w in prev_words if _norm_word(w)]
    full_norm = [_norm_word(w) for w in full_words]
    if prev_norm and len(full_words) >= len(prev_words):
        prefix_norm = [_norm_word(w) for w in full_words[: len(prev_words)]]
        if prefix_norm == [_norm_word(w) for w in prev_words]:
            return " ".join(full_words[len(prev_words) :]).lstrip(" .,!?;:-").strip()
    if full_text.startswith(prev_full):
        return full_text[len(prev_full) :].lstrip(" .,!?;:-").strip()
    for offset in range(min(3, len(prev_words))):
        end_pos = len(prev_words) - offset
        for k in range(min(8, end_pos), 1, -1):
            tail = [_norm_word(w) for w in prev_words[end_pos - k : end_pos]]
            if not all(tail):
                continue
            for idx in range(len(full_norm) - k, -1, -1):
                if full_norm[idx : idx + k] == tail:
                    cut = min(len(full_words), idx + k + offset)
                    return " ".join(full_words[cut:]).lstrip(" .,!?;:-").strip()
    if len(prev_words) >= 2 and len(full_words) >= len(prev_words):
        p_norm = [_norm_word(w) for w in prev_words]
        matches = sum(
            1
            for a, b in zip(p_norm, full_norm[: len(prev_words)])
            if a and b and (a == b or a.startswith(b) or b.startswith(a))
        )
        if matches / len(prev_words) >= 0.7:
            return " ".join(full_words[len(prev_words) :]).lstrip(" .,!?;:-").strip()
    if match_wake(prev_full) is not None:
        wake_split = split_completed_wake_sentence(full_text)
        if wake_split is not None and match_wake(wake_split[1]) is None:
            return wake_split[1].lstrip(" .,!?;:-").strip()
    return full_text


def _same_utterance(previous: str, new: str) -> bool:
    if new.startswith(previous) or previous.startswith(new):
        return True
    prev_words = [_norm_word(w) for w in previous.split() if _norm_word(w)]
    new_words = [_norm_word(w) for w in new.split() if _norm_word(w)]
    if prev_words and new_words:
        if prev_words[0] == new_words[0]:
            return True
        if len(prev_words[0]) >= 3 and len(new_words[0]) >= 3:
            if new_words[0].startswith(prev_words[0]) or prev_words[0].startswith(new_words[0]):
                return True
    return False


class _CaptionHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[CaptionRow] = []
        self._active: dict | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        if self._active is not None:
            self._active["depth"] += 1
            return
        if "data-row" in attr:
            self._active = {
                "row_id": attr.get("data-row", ""),
                "speaker": attr.get("data-speaker", ""),
                "text": [],
                "depth": 1,
            }

    def handle_endtag(self, tag: str) -> None:
        if self._active is None:
            return
        self._active["depth"] -= 1
        if self._active["depth"] <= 0:
            self.rows.append(
                CaptionRow(
                    row_id=self._active["row_id"],
                    speaker=self._active["speaker"],
                    text="".join(self._active["text"]),
                )
            )
            self._active = None

    def handle_data(self, data: str) -> None:
        if self._active is not None:
            self._active["text"].append(data)


def parse_caption_html(html: str) -> list[CaptionRow]:
    parser = _CaptionHTMLParser()
    parser.feed(html)
    return parser.rows
