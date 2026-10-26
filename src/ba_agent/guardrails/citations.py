"""A car fact in chat needs a source, or an explicit not-sure."""

from __future__ import annotations

import re

NOT_SURE = "I'm not sure — I don't have a source for that."

_CITATION = re.compile(r"\(src:\s*[^)]+\)", re.I)
_URL = re.compile(r"https?://", re.I)
_MONEY = re.compile(r"\$\s?\d")
_COMMAS = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")
_UNITS = re.compile(r"\b\d+(?:\.\d+)?\s*(?:kwh|km|kg|hp|mph|mpg|usd|dollars)\b", re.I)


def has_citation(text: str) -> bool:
    return _CITATION.search(text) is not None or _URL.search(text) is not None


def has_precise_number(text: str) -> bool:
    return any(pattern.search(text) for pattern in (_MONEY, _COMMAS, _UNITS))


def is_unsure(text: str) -> bool:
    return "not sure" in text.lower()


def reply_status(text: str) -> str:
    """Return allow or regenerate."""
    if is_unsure(text) and not has_precise_number(text):
        return "allow"
    if has_citation(text):
        return "allow"
    return "regenerate"


def finalize_reply(first: str, second: str | None = None) -> str:
    if reply_status(first) == "allow":
        return first
    if second is not None and reply_status(second) == "allow":
        return second
    return NOT_SURE
