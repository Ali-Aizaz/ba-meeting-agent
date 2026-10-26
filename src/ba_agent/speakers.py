"""Normalise a Meet caption speaker label."""

from __future__ import annotations

from pathlib import Path

import yaml

_PRESENTATION_SUFFIXES = ("'s presentation", "’s presentation")


def normalize_speaker(raw: str | None, aliases: dict[str, str] | None = None) -> str:
    aliases = aliases or {}
    text = (raw or "").strip()
    lowered = text.lower()
    for suffix in _PRESENTATION_SUFFIXES:
        if lowered.endswith(suffix):
            text = text[: -len(suffix)].strip()
            break
    if not text:
        return "Unknown speaker"
    return aliases.get(text, text)


def is_bot_speaker(name: str, bot_name: str = "TechSol BA") -> bool:
    return name.strip().lower() in {bot_name.strip().lower(), "you"}


def load_aliases(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = loaded.get("aliases") or {}
    return {str(key): str(value) for key, value in raw.items()}
