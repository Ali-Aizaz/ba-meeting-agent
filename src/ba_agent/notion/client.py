"""Notion HTTP client: summary page, tasks, dedup, and a small rate limit."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date

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
from ba_agent.tasks_extract.dates import today_in
from ba_agent.tasks_extract.extractor import dedup_key

logger = logging.getLogger("ba_agent.notion")

_FRESH_CACHE_TTL_S = 120.0
_STALE_CACHE_TTL_S = 600.0


@dataclass
class TaskWrite:
    created: bool
    message: str
    page_id: str | None = None
    key: str = ""


class _Limiter:
    """Token-bucket rate limiter (burst=2) keeping 3 req/s sustained average."""

    def __init__(self, min_interval: float, burst: int = 2) -> None:
        self.min_interval = min_interval
        self.capacity = float(max(1, burst))
        self.tokens = self.capacity
        self.updated_at = time.monotonic()
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        if self.min_interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            elapsed = max(0.0, now - self.updated_at)
            self.tokens = min(self.capacity, self.tokens + elapsed / self.min_interval)
            self.updated_at = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return
            sleep_for = (1.0 - self.tokens) * self.min_interval
            await asyncio.sleep(sleep_for)
            self.tokens = 0.0
            self.updated_at = time.monotonic()


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
        self._task_records: dict[str, TaskRecord] = {}
        self._tasks_loaded: bool = False
        self._tasks_loaded_at: float = 0.0
        self._summaries_cache: list[dict] = []
        self._summaries_loaded_at: float = 0.0
        self._bg_tasks_refresh: asyncio.Task[list[TaskRecord]] | None = None
        self._bg_summaries_refresh: asyncio.Task[list[dict]] | None = None
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
            timeout=httpx.Timeout(25.0, connect=8.0),
        )

    @property
    def configured(self) -> bool:
        return bool(self.schema.tasks_db or self.schema.summary_db)

    async def aclose(self) -> None:
        for task in (self._bg_tasks_refresh, self._bg_summaries_refresh):
            if task is not None and not task.done():
                task.cancel()
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
        day = today_in(self.timezone_name)
        payload = {
            "parent": self.summary_parent,
            "properties": summary_properties(
                self.schema,
                title=title,
                day=day,
                summary="Meeting in progress…",
                transcript_note="Waiting for captions…",
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
        stored_record = TaskRecord(
            task=record.task,
            owner=record.owner,
            due=record.due,
            due_text=record.due_text,
            status=record.status or self.schema.status_default,
            said_by=record.said_by,
            time=record.time,
            notion_page_id=page_id,
            key=key,
        )
        self._task_records[key] = stored_record
        return TaskWrite(
            True,
            task_added(record.owner, record.task, record.due_text, record.due),
            page_id=page_id,
            key=key,
        )

    def _parse_task_page(self, page: dict) -> tuple[str, str | None, TaskRecord | None]:
        props = page.get("properties") or {}
        t_props = self.schema.task_props
        title_name = t_props["task"].name if "task" in t_props else "Task"
        owner_name = t_props["owner"].name if "owner" in t_props else "Owner"
        due_name = t_props["due"].name if "due" in t_props else "Due"
        status_name = t_props["status"].name if "status" in t_props else "Status"
        said_by_name = t_props["said_by"].name if "said_by" in t_props else "Said by"
        time_name = t_props["time"].name if "time" in t_props else "Time"

        task = plain_property(props.get(title_name) or {}).strip()
        owner = plain_property(props.get(owner_name) or {}).strip()
        due_raw = plain_property(props.get(due_name) or {}).strip()
        status = (
            plain_property(props.get(status_name) or {}).strip()
            or self.schema.status_default
        )
        said_by = plain_property(props.get(said_by_name) or {}).strip()
        time_val = plain_property(props.get(time_name) or {}).strip()
        page_id = page.get("id")
        row_key = dedup_key(task, owner) if task and owner else ""
        if not task:
            return row_key, page_id, None
        due_date: date | None = None
        if due_raw:
            try:
                due_date = date.fromisoformat(due_raw[:10])
            except ValueError:
                due_date = None
        rec = TaskRecord(
            task=task,
            owner=owner,
            due=due_date,
            due_text=due_raw,
            status=status,
            said_by=said_by,
            time=time_val,
            notion_page_id=page_id,
            key=row_key,
        )
        return row_key, page_id, rec

    def _parse_summary_page(self, page: dict) -> dict | None:
        props = page.get("properties") or {}
        s_props = self.schema.summary_props
        title_name = s_props["title"].name if "title" in s_props else "Title"
        date_name = s_props["date"].name if "date" in s_props else "Date"
        summary_name = s_props["summary"].name if "summary" in s_props else "Summary"
        transcript_name = s_props["transcript"].name if "transcript" in s_props else "Transcript"

        title = plain_property(props.get(title_name) or {}).strip()
        day_raw = plain_property(props.get(date_name) or {}).strip()
        summary = plain_property(props.get(summary_name) or {}).strip()
        transcript = plain_property(props.get(transcript_name) or {}).strip()
        page_id = str(page.get("id") or "")
        url = str(page.get("url") or "")
        if not title and not summary and not transcript:
            return None
        return {
            "id": page_id,
            "url": url,
            "title": title,
            "date": day_raw,
            "summary": summary,
            "transcript": transcript,
        }

    async def find_task(self, key: str, *, refresh: bool = False) -> str | None:
        if not self.schema.tasks_db:
            return None
        if not refresh and key in self._task_cache:
            return self._task_cache[key]
        if not refresh and self._tasks_loaded:
            return None
        cursor = None
        matched_page_id: str | None = None
        while True:
            body: dict = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            data = await self._request("POST", f"/databases/{self.schema.tasks_db}/query", body)
            for page in data.get("results") or []:
                row_key, page_id, rec = self._parse_task_page(page)
                if row_key and page_id:
                    self._task_cache[row_key] = page_id
                if row_key and rec is not None:
                    self._task_records[row_key] = rec
                if row_key == key and matched_page_id is None:
                    matched_page_id = page_id
            if matched_page_id is not None:
                if not data.get("has_more"):
                    self._tasks_loaded = True
                    self._tasks_loaded_at = time.monotonic()
                return matched_page_id
            if not data.get("has_more"):
                self._tasks_loaded = True
                self._tasks_loaded_at = time.monotonic()
                return self._task_cache.get(key)
            cursor = data.get("next_cursor")

    async def list_tasks(self, *, refresh: bool = False) -> list[TaskRecord]:
        if not self.schema.tasks_db:
            return []
        now = time.monotonic()
        if not refresh and self._tasks_loaded:
            age = now - self._tasks_loaded_at
            if self._tasks_loaded_at == 0.0 or age < _FRESH_CACHE_TTL_S:
                return list(self._task_records.values())
            if age < _STALE_CACHE_TTL_S:
                if self._bg_tasks_refresh is None or self._bg_tasks_refresh.done():
                    self._bg_tasks_refresh = asyncio.create_task(self.list_tasks(refresh=True))
                return list(self._task_records.values())
        cursor = None
        records: dict[str, TaskRecord] = {}
        try:
            while True:
                body: dict = {"page_size": 100}
                if cursor:
                    body["start_cursor"] = cursor
                data = await self._request("POST", f"/databases/{self.schema.tasks_db}/query", body)
                for page in data.get("results") or []:
                    row_key, page_id, rec = self._parse_task_page(page)
                    if row_key and page_id:
                        self._task_cache[row_key] = page_id
                    if rec is not None:
                        k = row_key or (page_id or rec.task)
                        records[k] = rec
                if not data.get("has_more"):
                    break
                cursor = data.get("next_cursor")
            self._task_records = dict(records)
            self._tasks_loaded = True
            self._tasks_loaded_at = time.monotonic()
        except Exception:
            logger.warning("notion list_tasks failed; returning cached tasks", exc_info=True)
            return list(self._task_records.values())
        return list(records.values())

    async def list_summaries(self, *, refresh: bool = False, limit: int = 25) -> list[dict]:
        if not self.schema.summary_db:
            return []
        now = time.monotonic()
        if not refresh and self._summaries_loaded_at > 0:
            age = now - self._summaries_loaded_at
            if age < _FRESH_CACHE_TTL_S:
                return self._summaries_cache[:limit]
            if age < _STALE_CACHE_TTL_S:
                if self._bg_summaries_refresh is None or self._bg_summaries_refresh.done():
                    self._bg_summaries_refresh = asyncio.create_task(
                        self.list_summaries(refresh=True, limit=max(limit, 25))
                    )
                return self._summaries_cache[:limit]
        summaries: list[dict] = []
        fetch_limit = max(limit, 25)
        try:
            body: dict = {
                "page_size": min(100, max(fetch_limit * 2, 20)),
                "sorts": [{"timestamp": "created_time", "direction": "descending"}],
            }
            data = await self._request("POST", f"/databases/{self.schema.summary_db}/query", body)
            for page in data.get("results") or []:
                parsed = self._parse_summary_page(page)
                if parsed is None:
                    continue
                if (
                    parsed["id"] == self.summary_page_id
                    and parsed["summary"] in {"", "Meeting in progress…"}
                    and parsed["transcript"] in {"", "Waiting for captions…"}
                ):
                    continue
                if not parsed["summary"] and not parsed["transcript"]:
                    continue
                summaries.append(parsed)
                if len(summaries) >= fetch_limit:
                    break
            self._summaries_cache = summaries
            self._summaries_loaded_at = time.monotonic()
        except Exception:
            logger.warning(
                "notion list_summaries failed; returning cached summaries",
                exc_info=True,
            )
            return self._summaries_cache[:limit]
        return summaries[:limit]

    async def database_context(
        self,
        *,
        refresh: bool = False,
        max_tasks: int = 50,
        max_summaries: int = 15,
        exclude_current_meeting: bool = False,
    ) -> str:
        sections: list[str] = []
        extra_current = 1 if exclude_current_meeting and self.summary_page_id else 0
        summary_limit = max_summaries + extra_current
        tasks, summaries = await asyncio.gather(
            self.list_tasks(refresh=refresh),
            self.list_summaries(refresh=refresh, limit=summary_limit),
        )
        if tasks:
            lines: list[str] = []
            for t in tasks[:max_tasks]:
                due_str = t.due.isoformat() if t.due else (t.due_text or "unscheduled")
                status_str = f" [{t.status}]" if t.status else ""
                by_str = f" (said by {t.said_by})" if t.said_by else ""
                lines.append(f"- {t.owner}: {t.task} (due: {due_str}){status_str}{by_str}")
            sections.append("Stored Notion Tasks:\n" + "\n".join(lines))
        if exclude_current_meeting and self.summary_page_id:
            summaries = [s for s in summaries if s.get("id") != self.summary_page_id]
        summaries = summaries[:max_summaries]
        if summaries:
            s_lines: list[str] = []
            for item in summaries:
                header = f"{item['title']} ({item['date']})" if item["date"] else item["title"]
                summary_clean = " ".join(item["summary"].split())
                transcript_clean = " ".join(item["transcript"].split())
                if len(transcript_clean) > 1200:
                    transcript_clean = transcript_clean[:1200] + "…"
                entry = f"- {header}: {summary_clean}"
                if transcript_clean and transcript_clean != "Waiting for captions…":
                    entry += f" | Transcript: {transcript_clean}"
                s_lines.append(entry)
            sections.append("Stored Notion Meeting Notes:\n" + "\n".join(s_lines))
        return "\n".join(sections)

    def _upsert_summary_cache(
        self,
        *,
        page_id: str,
        url: str,
        title: str,
        day: date,
        summary_text: str,
        note: str,
    ) -> None:
        if not page_id:
            return
        entry = {
            "id": page_id,
            "url": url,
            "title": title,
            "date": day.isoformat(),
            "summary": summary_text,
            "transcript": note or "",
        }
        updated = [item for item in self._summaries_cache if item.get("id") != page_id]
        self._summaries_cache = [entry, *updated]
        self._summaries_loaded_at = time.monotonic()

    async def save_meeting(
        self,
        summary: str,
        transcript: str,
        tasks: list[TaskRecord],
        *,
        append_children: bool = True,
    ) -> str:
        if not self.schema.summary_db:
            raise StartupError("Notion summary database is not configured")
        day = today_in(self.timezone_name)
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
            payload: dict = {"parent": self.summary_parent, "properties": properties}
            if append_children:
                payload["children"] = children
            created = await self._request("POST", "/pages", payload)
            self.summary_page_id = created.get("id", "")
            self.summary_url = created.get("url", "")
            if append_children:
                self._transcript_appended = True
                if self._summaries_loaded_at > 0:
                    self._upsert_summary_cache(
                        page_id=self.summary_page_id,
                        url=self.summary_url,
                        title=title,
                        day=day,
                        summary_text=summary_text,
                        note=note,
                    )
            return self.summary_url
        await self._request(
            "PATCH",
            f"/pages/{self.summary_page_id}",
            {"properties": properties},
        )
        if append_children and not self._transcript_appended:
            await self._request(
                "PATCH",
                f"/blocks/{self.summary_page_id}/children",
                {"children": children},
            )
            self._transcript_appended = True
        if append_children and self._summaries_loaded_at > 0:
            self._upsert_summary_cache(
                page_id=self.summary_page_id,
                url=self.summary_url,
                title=title,
                day=day,
                summary_text=summary_text,
                note=note,
            )
        return self.summary_url

    def saved_message(self) -> str:
        return notes_saved(self.summary_url)

    async def _request(self, method: str, url: str, body: dict | None = None, dedup_lookup=None):
        last_exc: Exception | None = None
        for attempt in range(4):
            await self._limiter.wait()
            try:
                response = await self._http.request(method, url, json=body)
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt < 3:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise
            if response.status_code == 429:
                if dedup_lookup is not None:
                    existing = await dedup_lookup()
                    if existing:
                        return {"_existing": existing}
                delay = float(response.headers.get("Retry-After") or 1)
                await asyncio.sleep(delay)
                continue
            if response.status_code in {502, 503, 504} and attempt < 3:
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            response.raise_for_status()
            if not response.content:
                return {}
            return response.json()
        if last_exc is not None:
            raise last_exc
        raise StartupError("Notion rate limit did not clear")


def _parent(database: dict, database_id: str) -> dict:
    sources = database.get("data_sources") or []
    if sources:
        return {"type": "data_source_id", "data_source_id": sources[0]["id"]}
    return {"type": "database_id", "database_id": database_id}


def _transcript_toggle(transcript: str) -> list[dict]:
    lines = transcript.splitlines() or ["(no transcript)"]
    if len(lines) <= 80:
        children = [_paragraph(line) for line in lines]
    else:
        children = []
        buf: list[str] = []
        buf_len = 0
        for idx, line in enumerate(lines):
            chunk_line = line[:1800]
            sep = 1 if buf else 0
            if buf and buf_len + sep + len(chunk_line) > 1800:
                children.append(_paragraph("\n".join(buf)))
                buf = []
                buf_len = 0
                if len(children) >= 95:
                    children.append(
                        _paragraph(
                            f"... {len(lines) - idx} more lines are in the local transcript"
                        )
                    )
                    break
                sep = 0
            buf.append(chunk_line)
            buf_len += sep + len(chunk_line)
        else:
            if buf and len(children) < 95:
                children.append(_paragraph("\n".join(buf)))
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
    return today_in(timezone_name)
