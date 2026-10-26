"""Chat lines for Notion writes."""

from __future__ import annotations

from datetime import date

from ba_agent.tasks_extract.dates import short_due


def task_added(owner: str, task: str, due_text: str, due: date | None) -> str:
    return f"✓ Task added: {owner} — {task} — {short_due(due_text, due)}"


def already_tracked(owner: str, task: str) -> str:
    return f"Already tracked: {owner} — {task}"


def task_failed(task: str) -> str:
    return f"✗ Couldn't save the task: {task}"


def notes_saved(url: str) -> str:
    return f"✓ Notes saved: {url}"
