"""Turn model JSON, or a Hey BA task request, into task records."""

from __future__ import annotations

import json
import re
from datetime import date

from ba_agent.models import TaskRecord
from ba_agent.speakers import normalize_speaker
from ba_agent.tasks_extract.dates import parse_due
from ba_agent.trigger.match import match_wake

_TASK_COMMAND = re.compile(
    r"^(?:please\s+)?(?:add|create|log|track|record|note)"
    r"(?:\s+a)?(?:\s+new)?\s+(?:task|action\s+item|to-?do)"
    r"(?:\s+(?:for|to)\s+(?P<owner>.+?))?\s+to\s+(?P<body>.+)$",
    re.I,
)
_ASSIGN_COMMAND = re.compile(
    r"^(?:please\s+)?assign"
    r"(?:\s+(?:a(?:\s+new)?\s+)?(?:task|action\s+item|to-?do)\s+(?:to|for))?"
    r"\s+(?P<owner>[A-Za-z][A-Za-z .'-]{0,30}?)\s+to\s+(?P<body>.+)$",
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


_FIRST_PERSON_OWNERS = {"i", "me", "myself"}


def _match_task_command(text: str) -> re.Match[str] | None:
    cleaned = text.strip()
    return _TASK_COMMAND.match(cleaned) or _ASSIGN_COMMAND.match(cleaned)


def _lookup_alias(name: str, aliases: dict[str, str] | None) -> str | None:
    if not aliases:
        return None
    mapped = normalize_speaker(name, aliases)
    if mapped != name:
        return mapped
    low = name.lower()
    for k, v in aliases.items():
        if k.strip().lower() == low:
            return v
    return None


def normalize_owner(
    raw_owner: str,
    *,
    said_by: str = "",
    aliases: dict[str, str] | None = None,
) -> str:
    collapsed = " ".join(raw_owner.strip().split())
    owner = collapsed.rstrip(" .")
    if owner.lower() in _FIRST_PERSON_OWNERS:
        sb = " ".join(said_by.strip().split())
        owner = sb if sb and sb.lower() != "unknown speaker" else ""
        collapsed = owner
    if not owner:
        return ""
    for candidate in (collapsed, owner):
        alias = _lookup_alias(candidate, aliases)
        if alias:
            return alias
    return owner


def dedup_key(task: str, owner: str) -> str:
    def norm(value: str) -> str:
        return re.sub(r"\s+", " ", value.strip().lower())

    return f"{norm(task)}|{norm(owner)}"


def is_task_request(question: str) -> bool:
    return _match_task_command(question) is not None


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


_DUE_PREP_SPLIT = re.compile(r"\s+(?:by|on|before|due)\s+", re.I)
_TRAILING_BARE_DUE = re.compile(
    r"\s+((?:tomorrow|today|tonight|this\s+evening|"
    r"end\s+of\s+(?:the\s+)?(?:day|week)|eod|cob|eow|"
    r"this\s+week|next\s+week|(?:this|next)\s+[a-z]+|"
    r"in\s+(?:\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:days?|weeks?)))$",
    re.I,
)


def _split_body_and_due(body: str, *, today: date) -> tuple[str, str, date | None]:
    matches = list(_DUE_PREP_SPLIT.finditer(body))
    for match in reversed(matches):
        due_raw = body[match.end() :].strip()
        due_text = _trim_due_text(due_raw, today=today)
        due = parse_due(due_text, today=today) if due_text else None
        cleaned_body = body[: match.start()].strip(" .,!?;:")
        if due is not None and cleaned_body:
            return cleaned_body, due_text, due
    trailing = _TRAILING_BARE_DUE.search(body)
    if trailing is not None:
        due_raw = trailing.group(1).strip()
        due = parse_due(due_raw, today=today)
        cleaned_body = body[: trailing.start()].strip(" .,!?;:")
        if due is not None and cleaned_body:
            return cleaned_body, due_raw, due
    return body.strip(" .,!?;:"), "", None


def parse_task_command(
    question: str,
    *,
    said_by: str,
    time: str,
    today: date,
    aliases: dict[str, str] | None = None,
) -> TaskRecord | None:
    clause = _first_task_clause(question)
    match = _match_task_command(clause)
    if match is None:
        return None
    owner = normalize_owner(match.group("owner") or "", said_by=said_by, aliases=aliases)
    raw_body = _first_task_clause(match.group("body"))
    body, due_text, due = _split_body_and_due(raw_body, today=today)
    return TaskRecord(
        task=body,
        owner=owner,
        due=due,
        due_text=due_text,
        said_by=said_by,
        time=time,
        key=dedup_key(body, owner) if body and owner else "",
    )


def apply_followup(
    record: TaskRecord,
    text: str,
    *,
    today: date,
    aliases: dict[str, str] | None = None,
) -> TaskRecord:
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
                    owner = normalize_owner(owner_str, said_by=record.said_by, aliases=aliases)
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
    aliases: dict[str, str] | None = None,
) -> list[TaskRecord]:
    data = payload if isinstance(payload, dict) else json.loads(extract_json(str(payload)))
    records: list[TaskRecord] = []
    for item in data.get("tasks") or []:
        task = str(item.get("task") or "").strip()
        speaker = str(item.get("said_by") or said_by or "").strip()
        owner = normalize_owner(
            str(item.get("owner") or ""),
            said_by=speaker or said_by,
            aliases=aliases,
        )
        due_text = str(item.get("due_text") or item.get("due") or "").strip()
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
    aliases: dict[str, str] | None = None,
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
        aliases=aliases,
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
    aliases: dict[str, str] | None = None,
) -> list[TaskRecord]:
    """Rule-based fallback when the LLM call is unavailable."""
    cleaned = _first_task_clause(line_text)
    if not cleaned or is_addressed_line(cleaned):
        return []
    cmd = parse_task_command(cleaned, said_by=said_by, time=time, today=today, aliases=aliases)
    if cmd is not None and cmd.task and cmd.owner and cmd.due is not None:
        return [cmd]
    for pattern in _OVERHEARD_PATTERNS:
        match = pattern.match(cleaned)
        if match is None:
            continue
        groups = match.groupdict()
        raw_owner = (groups.get("owner") or said_by or "").strip(" .,")
        owner = normalize_owner(raw_owner, said_by=said_by, aliases=aliases)
        raw_body = _first_task_clause(groups.get("body") or "")
        body, due_text, due = _split_body_and_due(raw_body, today=today)
        if due is None:
            due_text = _trim_due_text(raw_body, today=today)
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

