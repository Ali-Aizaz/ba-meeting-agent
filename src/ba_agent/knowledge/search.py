"""Keyword search over the Markdown knowledge base. No vector index."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_STOP = {
    "a",
    "an",
    "the",
    "is",
    "are",
    "was",
    "were",
    "what",
    "whats",
    "of",
    "to",
    "and",
    "or",
    "in",
    "on",
    "for",
    "how",
    "does",
    "do",
    "i",
    "you",
    "me",
    "we",
    "it",
    "its",
    "be",
    "about",
    "please",
    "can",
    "could",
    "tell",
    "explain",
    "describe",
    "define",
    "difference",
    "between",
    "vs",
    "versus",
    "work",
    "works",
    "working",
    "mean",
    "means",
    "with",
    "from",
    "by",
    "at",
    "this",
    "that",
    "these",
    "those",
    "why",
    "when",
    "where",
    "which",
    "who",
    "has",
    "have",
    "had",
    "did",
    "get",
    "gets",
    "got",
    "give",
    "talk",
    "say",
}

_STEM_MAP = {
    "hybrids": "hybrid",
    "evs": "ev",
    "batteries": "battery",
    "tyres": "tire",
    "tires": "tire",
    "tyre": "tire",
    "bodies": "body",
    "braking": "brake",
    "brakes": "brake",
    "charging": "charge",
    "charger": "charge",
    "chargers": "charge",
    "charged": "charge",
    "charges": "charge",
}


@dataclass
class Chunk:
    file: str
    heading: str
    text: str
    score: float

    @property
    def source(self) -> str:
        if self.heading:
            return f"{self.file}#{self.heading}"
        return self.file


class KnowledgeIndex:
    def __init__(self, root: Path, min_score: float = 0.5) -> None:
        self.root = root
        self.min_score = min_score
        self._chunks = _load(root)

    def search(self, query: str, limit: int = 3) -> list[Chunk]:
        query_tokens = set(_tokens(query))
        ranked: list[Chunk] = []
        for chunk in self._chunks:
            file_tokens = set(_tokens(chunk.file.removesuffix(".md").replace("-", " ")))
            heading_tokens = set(_tokens(chunk.heading)) if chunk.heading else set()
            chunk_tokens = set(_tokens(chunk.text)) | file_tokens
            score = _score(query_tokens, chunk_tokens, heading_tokens)
            if score < self.min_score:
                continue
            name_hit = 1 if (query_tokens & file_tokens) else 0
            ranked.append(
                Chunk(
                    file=chunk.file,
                    heading=chunk.heading,
                    text=chunk.text,
                    score=score + name_hit * 0.01,
                )
            )
        ranked.sort(key=lambda item: (-item.score, item.file))
        return ranked[:limit]


def _load(root: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    if not root.is_dir():
        return chunks
    for path in sorted(root.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for heading, body in _split(text):
            chunks.append(Chunk(file=path.name, heading=heading, text=body, score=0))
    return chunks


def _split(text: str) -> list[tuple[str, str]]:
    parts = re.split(r"\n(?=#{1,3} )", text.strip())
    chunks: list[tuple[str, str]] = []
    for part in parts:
        lines = part.strip().splitlines()
        if not lines:
            continue
        if lines[0].startswith("#"):
            heading = lines[0].lstrip("#").strip()
            body = "\n".join(lines[1:]).strip()
        else:
            heading = ""
            body = "\n".join(lines).strip()
        searchable = f"{heading}\n{body}".strip()
        if searchable:
            chunks.append((heading, searchable))
    return chunks


def _stem(word: str) -> str:
    if word in _STEM_MAP:
        return _STEM_MAP[word]
    if len(word) > 4 and word.endswith("s") and not word.endswith(("ss", "us", "is", "as")):
        return word[:-1]
    return word


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    useful = [_stem(word) for word in words if word not in _STOP and len(word) > 1]
    return useful or words


def _score(query: set[str], chunk: set[str], heading: set[str] | None = None) -> float:
    if not query:
        return 0.0
    overlap = query & chunk
    if not overlap:
        return 0.0
    ratio = len(overlap) / len(query)
    if heading and heading <= query:
        ratio = max(ratio, 0.75 + 0.05 * min(len(heading), 4))
    if len(overlap) >= 2 and len(query) >= 4:
        ratio = max(ratio, min(0.85, len(overlap) / 3))
    return ratio
