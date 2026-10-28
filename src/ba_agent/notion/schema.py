"""Property names live in notion_schema.yaml so the attached database is a config change."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from ba_agent.config import StartupError
from ba_agent.models import TaskRecord


@dataclass
class Prop:
    name: str
    type: str


@dataclass
class NotionSchema:
    version: str = "2022-06-28"
    status_default: str = "Not started"
    summary_db: str = ""
    tasks_db: str = ""
    summary_props: dict[str, Prop] = field(default_factory=dict)
    task_props: dict[str, Prop] = field(default_factory=dict)


def load_notion_schema(path: Path) -> NotionSchema:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    databases = loaded.get("databases") or {}
    schema = NotionSchema(
        version=str(loaded.get("version") or "2022-06-28"),
        status_default=str(loaded.get("status_default") or "Not started"),
        summary_db=str(databases.get("summary") or ""),
        tasks_db=str(databases.get("tasks") or ""),
        summary_props=_props(loaded.get("summary") or {}),
        task_props=_props(loaded.get("tasks") or {}),
    )
    return schema


def _props(section: dict) -> dict[str, Prop]:
    props: dict[str, Prop] = {}
    for logical, raw in section.items():
        if isinstance(raw, dict) and raw.get("name") and raw.get("type"):
            props[str(logical)] = Prop(name=str(raw["name"]), type=str(raw["type"]))
    return props


def missing_properties(
    schema: NotionSchema,
    remote_summary: set[str],
    remote_tasks: set[str],
) -> list[str]:
    missing: list[str] = []
    if schema.summary_db:
        missing.extend(
            prop.name for prop in schema.summary_props.values() if prop.name not in remote_summary
        )
    if schema.tasks_db:
        missing.extend(
            prop.name for prop in schema.task_props.values() if prop.name not in remote_tasks
        )
    return missing


def require_properties(missing: list[str]) -> None:
    if missing:
        raise StartupError("Notion properties missing: " + ", ".join(missing))


def task_properties(
    record: TaskRecord,
    schema: NotionSchema,
    meeting_url: str = "",
) -> dict:
    props: dict = {}
    _put(props, schema.task_props.get("task"), record.task)
    _put(props, schema.task_props.get("owner"), record.owner)
    _put(props, schema.task_props.get("due"), record.due)
    _put(props, schema.task_props.get("status"), record.status or schema.status_default)
    _put(props, schema.task_props.get("said_by"), record.said_by)
    _put(props, schema.task_props.get("time"), record.time)
    if meeting_url:
        _put(props, schema.task_props.get("meeting"), meeting_url)
    return props


def summary_properties(
    schema: NotionSchema,
    *,
    title: str,
    day: date,
    summary: str,
    transcript_note: str,
) -> dict:
    props: dict = {}
    _put(props, schema.summary_props.get("title"), title)
    _put(props, schema.summary_props.get("date"), day)
    _put(props, schema.summary_props.get("summary"), summary)
    _put(props, schema.summary_props.get("transcript"), transcript_note)
    return props


def _put(props: dict, prop: Prop | None, value) -> None:
    if prop is None:
        return
    props[prop.name] = _value(prop.type, value)


def _value(kind: str, value):
    if kind == "title":
        return {"title": [_rich(str(value or ""))]}
    if kind == "rich_text":
        return {"rich_text": [_rich(str(value or ""))]}
    if kind == "date":
        if value is None or value == "":
            return {"date": None}
        start = value.isoformat() if hasattr(value, "isoformat") else str(value)
        return {"date": {"start": start}}
    if kind == "status":
        return {"status": {"name": str(value or "")}}
    if kind == "select":
        return {"select": {"name": str(value or "")}}
    if kind == "url":
        return {"url": str(value or "") or None}
    return {"rich_text": [_rich(str(value or ""))]}


def _rich(text: str) -> dict:
    return {"type": "text", "text": {"content": text[:2000]}}


def plain_property(prop: dict) -> str:
    if not isinstance(prop, dict):
        return ""
    kind = prop.get("type")
    if kind == "title" or "title" in prop:
        return "".join(
            part.get("plain_text") or (part.get("text") or {}).get("content") or ""
            for part in (prop.get("title") or [])
            if isinstance(part, dict)
        )
    if kind == "rich_text" or "rich_text" in prop:
        return "".join(
            part.get("plain_text") or (part.get("text") or {}).get("content") or ""
            for part in (prop.get("rich_text") or [])
            if isinstance(part, dict)
        )
    if kind == "date" or "date" in prop:
        date_obj = prop.get("date")
        if isinstance(date_obj, dict):
            return str(date_obj.get("start") or "")
        return ""
    if kind == "status" or "status" in prop:
        status_obj = prop.get("status")
        if isinstance(status_obj, dict):
            return str(status_obj.get("name") or "")
        return ""
    if kind == "select" or "select" in prop:
        select_obj = prop.get("select")
        if isinstance(select_obj, dict):
            return str(select_obj.get("name") or "")
        return ""
    if kind == "url" or "url" in prop:
        return str(prop.get("url") or "")
    if kind == "number" or "number" in prop:
        val = prop.get("number")
        return "" if val is None else str(val)
    if kind == "people" or "people" in prop:
        return ", ".join(
            str(p.get("name") or "")
            for p in (prop.get("people") or [])
            if isinstance(p, dict) and p.get("name")
        )
    return ""
