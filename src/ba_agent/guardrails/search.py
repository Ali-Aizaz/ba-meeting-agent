"""Refuse a web search that contains this meeting."""

from __future__ import annotations

import re

_NOTION = re.compile(r"https?://(?:www\.)?notion\.(?:so|site)/\S+", re.I)


def query_allowed(query: str, caption_lines: list[str], participants: list[str]) -> bool:
    lowered = query.lower()
    if "notion.so" in lowered or "notion.site" in lowered:
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
    return True


def strip_meeting_text(query: str, caption_lines: list[str], participants: list[str]) -> str:
    cleaned = _NOTION.sub(" ", query)
    for line in caption_lines:
        snippet = " ".join(line.split())
        if len(snippet) >= 12:
            cleaned = re.sub(re.escape(snippet), " ", cleaned, flags=re.I)
    names = [name.strip() for name in participants if name.strip()]
    if len(names) >= 2:
        blob = ", ".join(names)
        cleaned = re.sub(re.escape(blob), " ", cleaned, flags=re.I)
    return " ".join(cleaned.split())
