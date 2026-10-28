"""Refuse a web search that contains this meeting."""

from __future__ import annotations

import re

from ba_agent.knowledge.policy import _CAR_WORDS

_INTERNAL_URL = re.compile(
    r"https?://(?:www\.)?(?:notion\.(?:so|site)|meet\.google\.com)/\S+",
    re.I,
)
_TIMESTAMP = re.compile(r"\[\d{1,2}:\d{2}(?::\d{2})?\]")
_DIGEST_MARKER = re.compile(r"\b(?:meeting digest|participants)\s*:", re.I)


_GENERIC_SPEAKER_TOKENS = {
    "unknown",
    "speaker",
    "you",
    "guest",
    "host",
    "user",
    "participant",
    "participants",
    "presentation",
    "meeting",
    "techsol",
    "assistant",
    "the",
    "and",
    "for",
    "with",
}


def _participant_tokens(participants: list[str]) -> list[str]:
    tokens: list[str] = []
    for raw in participants:
        cleaned = raw.strip()
        if not cleaned or cleaned.lower() in {
            "unknown speaker",
            "you",
            "techsol ba",
            "ba assistant",
        }:
            continue
        for candidate in (cleaned, *re.findall(r"[A-Za-z]+", cleaned)):
            low = candidate.lower()
            if (
                len(low) >= 3
                and low not in _CAR_WORDS
                and low not in _GENERIC_SPEAKER_TOKENS
                and candidate not in tokens
            ):
                tokens.append(candidate)
    return sorted(tokens, key=len, reverse=True)


def query_allowed(query: str, caption_lines: list[str], participants: list[str]) -> bool:
    lowered = query.lower()
    if "notion.so" in lowered or "notion.site" in lowered or "meet.google.com" in lowered:
        return False
    if _TIMESTAMP.search(query) is not None or _DIGEST_MARKER.search(query) is not None:
        return False
    for line in caption_lines:
        snippet = " ".join(line.split())
        if len(snippet) >= 12 and snippet.lower() in lowered:
            return False
    names = [name.strip() for name in participants if name.strip()]
    if len(names) >= 2:
        blob = ", ".join(names).lower()
        if blob in lowered:
            return False
    for token in _participant_tokens(names):
        low = token.lower()
        if re.search(rf"\b{re.escape(low)}\b", lowered):
            return False
    return True


def strip_meeting_text(query: str, caption_lines: list[str], participants: list[str]) -> str:
    cleaned = _INTERNAL_URL.sub(" ", query)
    cleaned = _TIMESTAMP.sub(" ", cleaned)
    cleaned = _DIGEST_MARKER.sub(" ", cleaned)
    for line in caption_lines:
        snippet = " ".join(line.split())
        if len(snippet) >= 12:
            cleaned = re.sub(re.escape(snippet), " ", cleaned, flags=re.I)
    names = [name.strip() for name in participants if name.strip()]
    if len(names) >= 2:
        blob = ", ".join(names)
        cleaned = re.sub(re.escape(blob), " ", cleaned, flags=re.I)
    for token in _participant_tokens(names):
        cleaned = re.sub(rf"\b{re.escape(token)}\b", " ", cleaned, flags=re.I)
    return " ".join(cleaned.split())
