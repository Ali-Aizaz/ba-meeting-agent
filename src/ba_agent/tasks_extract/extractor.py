"""Turn model JSON, or a Hey BA task request, into task records."""

from __future__ import annotations

import json
import re
from datetime import date

from ba_agent.models import TaskRecord
from ba_agent.tasks_extract.dates import parse_due
from ba_agent.trigger.match import match_wake

_TASK_COMMAND = re.compile(
    r"^(?:please\s+)?(?:add|create)(?:\s+a)?\s+task(?:\s+for\s+(?P<owner>.+?))?\s+to\s+(?P<body>.+)$",
    re.I,
)
_TASK_CUE = re.compile(
    r"\b(?:"
    r"will|i'll|we'll|you'll|he'll|she'll|they'll|"
    r"please|need\s+to|needs\s+to|should|let's|have\s+to|has\s+to|must|"
    r"by|before|due|tomorrow|today|tonight|next\s+week|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun|"
    r"send|update|prepare|book|review|share|draft|create|check|follow\s+up|"
    r"schedule|finish|complete|assign|deliver|submit|write|fix|build|test"
    r")\b",
    re.I,
)
_QUESTION_COMMITMENT = re.compile(
    r"\b(?:can\s+you|could\s+you|will\s+you|would\s+you|please)\b",
    re.I,
)


def dedup_key(task: str, owner: str) -> str:
    def norm(value: str) -> str:
        return re.sub(r"\s+", " ", value.strip().lower())

    return f"{norm(task)}|{norm(owner)}"


def is_task_request(question: str) -> bool:
    return _TASK_COMMAND.match(question.strip()) is not None


def is_addressed_line(text: str, *, fuzzy: bool = True, threshold: int = 85) -> bool:
    return match_wake(text, fuzzy=fuzzy, threshold=threshold) is not None


def might_contain_task(text: str, *, today: date) -> bool:
    cleaned = text.strip()
    if not cleaned or is_addressed_line(cleaned):
        return False
    if cleaned.endswith("?") and _QUESTION_COMMITMENT.search(cleaned) is None:
        return False
    if parse_due(cleaned, today=today) is not None:
        return True
    return _TASK_CUE.search(cleaned) is not None


def _first_task_clause(text: str) -> str:
    first = re.split(r"[.?!;]+(?:\s+|$)", text.strip(), maxsplit=1)[0].strip()
    first = re.split(
        r"\s+(?:and\s+now|so\s+now|okay\s+now|ok\s+now|now\s+it(?:'s|s|\s+is)?|"
        r"now\s+i(?:'m|m|\s+am|\s+have)?|and\s+it(?:'s|s|\s+is|\s+basically)?|"
        r"it\s+basically|it(?:'s|s|\s+is)\s+going\s+to|as\s+you\s+can\s+see)\b",
        first,
        maxsplit=1,
        flags=re.I,
    )[0].strip()
    return first.rstrip(" .,!?;:")


def _trim_due_text(due_text: str, *, today: date) -> str:
    cleaned = _first_task_clause(due_text)
    target = parse_due(cleaned, today=today)
    if target is None:
        return cleaned
    words = cleaned.split()
    for end in range(1, len(words) + 1):
        prefix = " ".join(words[:end]).strip(" .,!?;:")
        if parse_due(prefix, today=today) == target:
            return prefix
    return cleaned


def parse_task_command(question: str, *, said_by: str, time: str, today: date) -> TaskRecord | None:
    clause = _first_task_clause(question)
    match = _TASK_COMMAND.match(clause)
    if match is None:
        return None
    owner = (match.group("owner") or "").strip(" .")
    body = _first_task_clause(match.group("body"))
    due_text = ""
    due_match = re.search(r"\s+(?:by|on|before|due)\s+(.+)$", body, re.I)
    if due_match:
        due_raw = due_match.group(1).strip()
        due_text = _trim_due_text(due_raw, today=today)
        body = body[: due_match.start()].strip(" .,!?;:")
    due = parse_due(due_text, today=today) if due_text else None
    return TaskRecord(
        task=body,
        owner=owner,
        due=due,
        due_text=due_text,
        said_by=said_by,
        time=time,
        key=dedup_key(body, owner) if body and owner else "",
    )


def apply_followup(record: TaskRecord, text: str, *, today: date) -> TaskRecord:
    clause = _first_task_clause(text)
    due = parse_due(clause, today=today)
    owner = record.owner
    if not owner:
        has_explicit_prefix = (
            re.match(r"^(?:for|owner(?:\s+is)?)\s+", clause, flags=re.I) is not None
        )
        candidate = re.sub(r"^(?:for|owner(?:\s+is)?)\s+", "", clause, flags=re.I)
        candidate = re.split(r"\s+(?:by|on|before|due)\s+|[,;]", candidate, maxsplit=1)[0].strip()
        if has_explicit_prefix or due is not None or len(candidate.split()) <= 4:
            tokens = [
                tok
                for tok in candidate.split()
                if parse_due(tok, today=today) is None
                and tok.lower()
                not in {"next", "by", "on", "for", "and", "the", "now", "it", "it's", "i", "we"}
            ]
            if tokens:
                owner_str = " ".join(tokens[:2]).strip(" .")
                if owner_str and not parse_due(owner_str, today=today):
                    owner = owner_str
    due_text = record.due_text
    if due is not None:
        due_text = _trim_due_text(clause, today=today)
    task = record.task
    return TaskRecord(
        task=task,
        owner=owner,
        due=due or record.due,
        due_text=due_text,
        status=record.status,
        said_by=record.said_by,
        time=record.time,
        notion_page_id=record.notion_page_id,
        key=dedup_key(task, owner) if task and owner else "",
    )


def tasks_from_model_json(
    payload,
    *,
    said_by: str,
    time: str,
    today: date,
    overheard: bool,
) -> list[TaskRecord]:
    data = payload if isinstance(payload, dict) else json.loads(extract_json(str(payload)))
    records: list[TaskRecord] = []
    for item in data.get("tasks") or []:
        task = str(item.get("task") or "").strip()
        owner = str(item.get("owner") or "").strip()
        due_text = str(item.get("due_text") or "").strip()
        speaker = str(item.get("said_by") or said_by or "").strip()
        if not task or not owner:
            continue
        due = parse_due(due_text, today=today) if due_text else None
        if overheard and due is None:
            continue
        records.append(
            TaskRecord(
                task=task,
                owner=owner,
                due=due,
                due_text=due_text,
                said_by=speaker,
                time=time,
                key=dedup_key(task, owner),
            )
        )
    return records


def apply_overheard(
    line_text: str,
    payload,
    *,
    said_by: str,
    time: str,
    today: date,
) -> tuple[list[TaskRecord], list[str]]:
    """Overheard lines never nag the room. A Hey BA line is not a task."""
    if is_addressed_line(line_text):
        return [], []
    records = tasks_from_model_json(
        payload,
        said_by=said_by,
        time=time,
        today=today,
        overheard=True,
    )
    return records, []


_OVERHEARD_PATTERNS = (
    re.compile(
        r"^(?P<owner>[A-Z][a-z]+)(?:,\s*|\s+)(?:please\s+|can\s+you\s+|could\s+you\s+|will\s+|should\s+|needs?\s+to\s+)(?P<body>.+)$",
    ),
    re.compile(
        r"^(?:i'll|i\s+will|i\s+need\s+to|i\s+can)\s+(?P<body>.+)$",
        re.I,
    ),
)


def fallback_overheard_tasks(
    line_text: str,
    *,
    said_by: str,
    time: str,
    today: date,
) -> list[TaskRecord]:
    """Rule-based fallback when the LLM call is unavailable."""
    cleaned = _first_task_clause(line_text)
    if not cleaned or is_addressed_line(cleaned):
        return []
    cmd = parse_task_command(cleaned, said_by=said_by, time=time, today=today)
    if cmd is not None and cmd.task and cmd.owner and cmd.due is not None:
        return [cmd]
    for pattern in _OVERHEARD_PATTERNS:
        match = pattern.match(cleaned)
        if match is None:
            continue
        groups = match.groupdict()
        owner = (groups.get("owner") or said_by or "").strip(" .,")
        body = _first_task_clause(groups.get("body") or "")
        due_match = re.search(r"\s+(?:by|on|before|due)\s+(.+)$", body, re.I)
        if due_match:
            due_text = _trim_due_text(due_match.group(1).strip(), today=today)
            body = body[: due_match.start()].strip()
        else:
            due_text = _trim_due_text(body, today=today)
        due = parse_due(due_text, today=today)
        if due is None or not body or not owner or owner.lower() == "unknown speaker":
            continue
        return [
            TaskRecord(
                task=body,
                owner=owner,
                due=due,
                due_text=due_text,
                said_by=said_by,
                time=time,
                key=dedup_key(body, owner),
            )
        ]
    return []


def extract_json(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return '{"tasks": []}'
    return text[start : end + 1]

