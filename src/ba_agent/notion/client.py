"""Notion HTTP client: summary page, tasks, dedup, and a small rate limit."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx

from ba_agent.config import StartupError
from ba_agent.models import TaskRecord
from ba_agent.notion.messages import already_tracked, notes_saved, task_added, task_failed
from ba_agent.notion.schema import (
    NotionSchema,
    missing_properties,
    plain_property,
    require_properties,
    summary_properties,
    task_properties,
)
from ba_agent.tasks_extract.extractor import dedup_key

logger = logging.getLogger("ba_agent.notion")


@dataclass
class TaskWrite:
    created: bool
    message: str
    page_id: str | None = None
    key: str = ""


class _Limiter:
    def __init__(self, min_interval: float) -> None:
        self.min_interval = min_interval
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        if self.min_interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            if now < self._next:
                await asyncio.sleep(self._next - now)
            self._next = time.monotonic() + self.min_interval


class NotionService:
    def __init__(
        self,
        *,
        token: str,
        schema: NotionSchema,
        meeting_id: str,
        timezone_name: str = "UTC",
        summary_page_id: str = "",
        summary_url: str = "",
        transport: httpx.BaseTransport | None = None,
        min_interval: float = 1 / 3,
    ) -> None:
        self.schema = schema
        self.meeting_id = meeting_id
        self.timezone_name = timezone_name
        self.summary_page_id = summary_page_id
        self.summary_url = summary_url
        self._task_cache: dict[str, str] = {}
        self._tasks_loaded: bool = False
        self._transcript_appended: bool = False
        self.task_parent: dict = {"database_id": schema.tasks_db} if schema.tasks_db else {}
        self.summary_parent: dict = {"database_id": schema.summary_db} if schema.summary_db else {}
        self._limiter = _Limiter(min_interval)
        self._http = httpx.AsyncClient(
            base_url="https://api.notion.com/v1",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": schema.version,
                "Content-Type": "application/json",
            },
            transport=transport,
            timeout=30,
        )

    @property
    def configured(self) -> bool:
        return bool(self.schema.tasks_db or self.schema.summary_db)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def verify(self) -> None:
        if self.schema.summary_db:
            summary = await self._request("GET", f"/databases/{self.schema.summary_db}")
            self.summary_parent = _parent(summary, self.schema.summary_db)
            remote_summary = set((summary.get("properties") or {}).keys())
        else:
            remote_summary = set()
        if self.schema.tasks_db:
            tasks = await self._request("GET", f"/databases/{self.schema.tasks_db}")
            self.task_parent = _parent(tasks, self.schema.tasks_db)
            remote_tasks = set((tasks.get("properties") or {}).keys())
        else:
            remote_tasks = set()
        require_properties(missing_properties(self.schema, remote_summary, remote_tasks))

    async def ensure_summary_page(self, title: str) -> str:
        if self.summary_url:
            return self.summary_url
        if not self.schema.summary_db:
            return ""
        day = datetime.now(ZoneInfo(self.timezone_name)).date()
        payload = {
            "parent": self.summary_parent,
            "properties": summary_properties(
                self.schema,
                title=title,
                day=day,
                summary="",
                transcript_note="",
            ),
        }
        created = await self._request("POST", "/pages", payload)
        self.summary_page_id = created.get("id", "")
        self.summary_url = created.get("url", "")
        return self.summary_url

    async def create_task(self, record: TaskRecord, meeting_url: str = "") -> TaskWrite:
        key = record.key or dedup_key(record.task, record.owner)
        if not self.schema.tasks_db:
            return TaskWrite(False, task_failed(record.task), key=key)
        try:
            existing = await self.find_task(key)
        except Exception:
            logger.exception("notion task lookup failed")
            return TaskWrite(False, task_failed(record.task), key=key)
        if existing:
            return TaskWrite(
                False,
                already_tracked(record.owner, record.task),
                page_id=existing,
                key=key,
            )
        payload = {
            "parent": self.task_parent,
            "properties": task_properties(record, self.schema, meeting_url or self.summary_url),
        }
        try:
            created = await self._request(
                "POST",
                "/pages",
                payload,
                dedup_lookup=lambda: self.find_task(key, refresh=True),
            )
        except Exception:
            logger.exception("notion task create failed")
            try:
                existing = await self.find_task(key, refresh=True)
            except Exception:
                existing = None
            if existing:
                return TaskWrite(
                    False,
                    already_tracked(record.owner, record.task),
                    page_id=existing,
                    key=key,
                )
            return TaskWrite(False, task_failed(record.task), key=key)
        if created.get("_existing"):
            existing_id = created["_existing"]
            self._task_cache[key] = existing_id
            return TaskWrite(
                False,
                already_tracked(record.owner, record.task),
                page_id=existing_id,
                key=key,
            )
        page_id = created.get("id")
        if page_id:
            self._task_cache[key] = page_id
        return TaskWrite(
            True,
            task_added(record.owner, record.task, record.due_text, record.due),
            page_id=page_id,
            key=key,
        )

    async def find_task(self, key: str, *, refresh: bool = False) -> str | None:
        if not self.schema.tasks_db:
            return None
        if not refresh and key in self._task_cache:
            return self._task_cache[key]
        if not refresh and self._tasks_loaded:
            return None
        cursor = None
        title_name = self.schema.task_props["task"].name
        owner_name = self.schema.task_props["owner"].name
        while True:
            body: dict = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            data = await self._request("POST", f"/databases/{self.schema.tasks_db}/query", body)
            for page in data.get("results") or []:
                props = page.get("properties") or {}
                task = plain_property(props.get(title_name) or {})
                owner = plain_property(props.get(owner_name) or {})
                row_key = dedup_key(task, owner)
                page_id = page.get("id")
                if row_key and page_id:
                    self._task_cache[row_key] = page_id
                if row_key == key:
                    if not data.get("has_more"):
                        self._tasks_loaded = True
                    return page_id
            if not data.get("has_more"):
                self._tasks_loaded = True
                return self._task_cache.get(key)
            cursor = data.get("next_cursor")

    async def save_meeting(self, summary: str, transcript: str, tasks: list[TaskRecord]) -> str:
        if not self.schema.summary_db:
            raise StartupError("Notion summary database is not configured")
        day = datetime.now(ZoneInfo(self.timezone_name)).date()
        title = f"TechSol BA {day.isoformat()} {self.meeting_id}"
        task_lines = "\n".join(f"- {task.owner}: {task.task} ({task.due_text})" for task in tasks)
        summary_text = summary.strip()
        if task_lines:
            summary_text = f"{summary_text}\n\nTasks:\n{task_lines}".strip()
        note = transcript if len(transcript) <= 1800 else transcript[:1800] + "…"
        properties = summary_properties(
            self.schema,
            title=title,
            day=day,
            summary=summary_text,
            transcript_note=note or "Transcript is in the toggle.",
        )
        children = _transcript_toggle(transcript)
        if not self.summary_page_id:
            created = await self._request(
                "POST",
                "/pages",
                {"parent": self.summary_parent, "properties": properties, "children": children},
            )
            self.summary_page_id = created.get("id", "")
            self.summary_url = created.get("url", "")
            self._transcript_appended = True
            return self.summary_url
        await self._request(
            "PATCH",
            f"/pages/{self.summary_page_id}",
            {"properties": properties},
        )
        if not self._transcript_appended:
            await self._request(
                "PATCH",
                f"/blocks/{self.summary_page_id}/children",
                {"children": children},
            )
            self._transcript_appended = True
        return self.summary_url

    def saved_message(self) -> str:
        return notes_saved(self.summary_url)

    async def _request(self, method: str, url: str, body: dict | None = None, dedup_lookup=None):
        for attempt in range(4):
            await self._limiter.wait()
            response = await self._http.request(method, url, json=body)
            if response.status_code == 429:
                if dedup_lookup is not None:
                    existing = await dedup_lookup()
                    if existing:
                        return {"_existing": existing}
                delay = float(response.headers.get("Retry-After") or 1)
                await asyncio.sleep(delay)
                continue
            response.raise_for_status()
            if not response.content:
                return {}
            return response.json()
        raise StartupError("Notion rate limit did not clear")


def _parent(database: dict, database_id: str) -> dict:
    sources = database.get("data_sources") or []
    if sources:
        return {"type": "data_source_id", "data_source_id": sources[0]["id"]}
    return {"type": "database_id", "database_id": database_id}


def _transcript_toggle(transcript: str) -> list[dict]:
    lines = transcript.splitlines() or ["(no transcript)"]
    children = [_paragraph(line) for line in lines[:80]]
    if len(lines) > 80:
        children.append(_paragraph(f"... {len(lines) - 80} more lines are in the local transcript"))
    return [
        {
            "object": "block",
            "type": "toggle",
            "toggle": {
                "rich_text": [{"type": "text", "text": {"content": "Transcript"}}],
                "children": children,
            },
        }
    ]


def _paragraph(text: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]},
    }


def notion_today(timezone_name: str) -> date:
    return datetime.now(ZoneInfo(timezone_name)).date()
