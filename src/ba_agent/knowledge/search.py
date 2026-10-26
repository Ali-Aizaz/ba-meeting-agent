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
            score = _score(query_tokens, set(_tokens(chunk.text)))
            if score < self.min_score:
                continue
            name_hit = 1 if any(token in chunk.file for token in query_tokens) else 0
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


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    useful = [word for word in words if word not in _STOP and len(word) > 1]
    return useful or words


def _score(query: set[str], chunk: set[str]) -> float:
    if not query:
        return 0.0
    return len(query & chunk) / len(query)
