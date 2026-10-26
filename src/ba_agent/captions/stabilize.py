"""Commit a caption once the on-screen text stops changing."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser


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


class Stabilizer:
    """Merge interim redraws of one caption row into a single line."""

    def __init__(
        self,
        stable_ms: int = 800,
        bot_names: tuple[str, ...] = ("techsol ba", "you"),
    ) -> None:
        self.stable_s = stable_ms / 1000
        self.bot_names = bot_names
        self.open: dict[str, _Open] = {}
        self.committed: dict[str, tuple[str, str]] = {}

    def push_frame(self, rows: list[CaptionRow], now: float) -> list[RawCaption]:
        committed: list[RawCaption] = []
        seen: set[str] = set()
        for row in rows:
            text = " ".join(row.text.split())
            speaker = row.speaker.strip()
            seen.add(row.row_id)
            if not text or speaker.lower() in self.bot_names:
                self.open.pop(row.row_id, None)
                continue
            if self.committed.get(row.row_id) == (speaker, text):
                continue
            current = self.open.get(row.row_id)
            if current is None:
                committed.extend(self._commit_others(now))
                self.open[row.row_id] = _Open(row.row_id, speaker, text, now, now)
                continue
            if speaker != current.speaker or not _same_utterance(current.text, text):
                committed.append(self._commit(current, now))
                self.open[row.row_id] = _Open(row.row_id, speaker, text, now, now)
                continue
            grown = text if len(text) >= len(current.text) else current.text
            if grown != current.text:
                current.text = grown
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
        self.committed[current.row_id] = (current.speaker, current.text)
        return RawCaption(current.speaker, current.text, current.start_s, now, current.row_id)


def _same_utterance(previous: str, new: str) -> bool:
    return new.startswith(previous) or previous.startswith(new)


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
