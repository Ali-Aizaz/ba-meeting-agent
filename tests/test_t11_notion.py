"""T11 payload map, dedup, and a recorded HTTP mock."""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from ba_agent.config import StartupError, resolve_path
from ba_agent.models import TaskRecord
from ba_agent.notion.client import NotionService
from ba_agent.notion.schema import Prop, load_notion_schema, task_properties
from ba_agent.tasks_extract.extractor import dedup_key

SUMMARY_PROPS = ["Title", "Date", "Summary", "Transcript"]
TASK_PROPS = ["Task", "Owner", "Due", "Status", "Said by", "Time", "Meeting"]


def test_t11_u1_payload_uses_schema_names() -> None:
    schema = load_notion_schema(resolve_path("config/notion_schema.yaml"))
    record = TaskRecord(
        task="cost sheet",
        owner="Ali",
        due=date(2026, 10, 30),
        due_text="Fri",
        status="Not started",
        said_by="Sara",
        time="[01:15]",
        key=dedup_key("cost sheet", "Ali"),
    )
    payload = task_properties(record, schema, "https://meet.google.com/abc-defg-hij")
    assert set(payload) == set(TASK_PROPS)
    assert payload["Task"]["title"][0]["text"]["content"] == "cost sheet"
    assert payload["Owner"]["rich_text"][0]["text"]["content"] == "Ali"
    assert payload["Due"]["date"]["start"] == "2026-10-30"

    schema.task_props = dict(schema.task_props)
    schema.task_props["task"] = Prop("Action item", "title")
    swapped = task_properties(record, schema)
    assert "Action item" in swapped
    assert "Task" not in swapped


def test_t11_u2_dedup_key_ignores_case_and_space() -> None:
    assert dedup_key("Update the cost sheet", "Ali") == dedup_key("update the cost sheet", "Ali")
    assert dedup_key("Update   the   cost sheet", "Ali") == dedup_key(
        "update the cost sheet", "Ali"
    )


class _FakeNotion:
    def __init__(self, *, omit: set[str] | None = None) -> None:
        self.omit = omit or set()
        self.tasks: list[dict] = []
        self.summaries: list[dict] = []
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        path = request.url.path
        self.calls.append((request.method, path, body))
        if request.method == "GET" and "/databases/" in path:
            name = path.rstrip("/").split("/")[-1]
            props = TASK_PROPS if name == "db_tasks" else SUMMARY_PROPS
            source = "ds_tasks" if name == "db_tasks" else "ds_summary"
            return httpx.Response(
                200,
                json={
                    "id": name,
                    "properties": {
                        prop: {"type": "rich_text"} for prop in props if prop not in self.omit
                    },
                    "data_sources": [{"id": source}],
                },
            )
        if path.endswith("/query"):
            return httpx.Response(200, json={"results": self.tasks, "has_more": False})
        if request.method == "POST" and path.endswith("/pages"):
            parent = body.get("parent") or {}
            page_id = f"page-{len(self.tasks) + len(self.summaries) + 1}"
            page = {
                "id": page_id,
                "url": f"https://www.notion.so/{page_id}",
                "properties": _read_props(body.get("properties") or {}),
            }
            if parent.get("data_source_id") == "ds_tasks":
                self.tasks.append(page)
            else:
                self.summaries.append(page)
            return httpx.Response(200, json=page)
        if request.method == "PATCH" and path.endswith("/children"):
            parts = path.rstrip("/").split("/")
            page_id = parts[-2] if len(parts) >= 2 else ""
            for page in self.summaries:
                if page["id"] == page_id:
                    page.setdefault("children", []).extend(body.get("children") or [])
                    return httpx.Response(200, json={"results": page["children"]})
            return httpx.Response(404, json={"message": "missing"})
        if request.method == "PATCH":
            page_id = path.rstrip("/").split("/")[-1]
            for page in self.summaries:
                if page["id"] == page_id:
                    return httpx.Response(200, json=page)
            return httpx.Response(404, json={"message": "missing"})
        return httpx.Response(404, json={"message": path})


def _read_props(properties: dict) -> dict:
    read: dict = {}
    for name, value in properties.items():
        if "title" in value:
            text = value["title"][0]["text"]["content"]
            read[name] = {"type": "title", "title": [{"plain_text": text}]}
        elif "rich_text" in value and value["rich_text"]:
            text = value["rich_text"][0]["text"]["content"]
            read[name] = {"type": "rich_text", "rich_text": [{"plain_text": text}]}
    return read


def _service(fake: _FakeNotion) -> NotionService:
    schema = load_notion_schema(resolve_path("config/notion_schema.yaml"))
    schema.summary_db = "db_summary"
    schema.tasks_db = "db_tasks"
    return NotionService(
        token="test-token",
        schema=schema,
        meeting_id="m1",
        transport=httpx.MockTransport(fake),
        min_interval=0,
    )


@pytest.mark.asyncio
async def test_t11_i1_http_mock_create_duplicate_and_save() -> None:
    fake = _FakeNotion()
    service = _service(fake)
    await service.verify()
    record = TaskRecord(
        task="cost sheet",
        owner="Ali",
        due=date(2026, 10, 30),
        due_text="Friday",
        said_by="Sara",
        time="[01:15]",
        key=dedup_key("cost sheet", "Ali"),
    )
    created = await service.create_task(record)
    duplicate = await service.create_task(record)
    assert created.created is True
    assert created.message == "✓ Task added: Ali — cost sheet — Fri"
    assert duplicate.created is False
    assert duplicate.message == "Already tracked: Ali — cost sheet"
    task_posts = [
        call
        for call in fake.calls
        if call[0] == "POST"
        and call[1].endswith("/pages")
        and (call[2].get("parent") or {}).get("data_source_id") == "ds_tasks"
    ]
    assert len(task_posts) == 1

    first = await service.save_meeting("We picked the hybrid.", "[00:10] Sara: hello", [])
    second = await service.save_meeting("We picked the hybrid.", "[00:10] Sara: hello", [])
    assert first == second
    assert first.startswith("https://www.notion.so/")
    summary_posts = [
        call
        for call in fake.calls
        if call[0] == "POST"
        and call[1].endswith("/pages")
        and (call[2].get("parent") or {}).get("data_source_id") == "ds_summary"
    ]
    patches = [call for call in fake.calls if call[0] == "PATCH"]
    assert len(summary_posts) == 1
    assert len(patches) == 1
    assert "Sara: hello" in json.dumps(summary_posts[0][2])
    await service.aclose()


@pytest.mark.asyncio
async def test_t11_missing_property_refuses_startup() -> None:
    fake = _FakeNotion(omit={"Due"})
    service = _service(fake)
    with pytest.raises(StartupError, match="Due"):
        await service.verify()
    await service.aclose()


@pytest.mark.asyncio
async def test_t11_ensure_summary_page_then_save_appends_transcript_toggle() -> None:
    fake = _FakeNotion()
    service = _service(fake)
    await service.verify()
    initial_url = await service.ensure_summary_page("TechSol BA m1")
    assert initial_url.startswith("https://www.notion.so/")
    assert service._transcript_appended is False

    saved_url = await service.save_meeting("Summary text", "[00:05] Sara: hi there", [])
    assert saved_url == initial_url
    assert service._transcript_appended is True
    child_patches = [
        call
        for call in fake.calls
        if call[0] == "PATCH" and call[1].endswith(f"/blocks/{service.summary_page_id}/children")
    ]
    assert len(child_patches) == 1
    assert "Sara: hi there" in json.dumps(child_patches[0][2])

    # A second save updates page properties without re-appending duplicate transcript toggles.
    await service.save_meeting("Updated summary", "[00:05] Sara: hi there", [])
    child_patches_after = [
        call
        for call in fake.calls
        if call[0] == "PATCH" and call[1].endswith(f"/blocks/{service.summary_page_id}/children")
    ]
    assert len(child_patches_after) == 1
    await service.aclose()


@pytest.mark.asyncio
async def test_t11_task_cache_avoids_redundant_database_queries() -> None:
    fake = _FakeNotion()
    service = _service(fake)
    await service.verify()
    record = TaskRecord(
        task="prepare slides",
        owner="Sara",
        due=date(2026, 10, 30),
        due_text="Friday",
        said_by="Ali",
        time="[02:00]",
        key=dedup_key("prepare slides", "Sara"),
    )
    first = await service.create_task(record)
    second = await service.create_task(record)
    third = await service.create_task(record)
    other = await service.create_task(
        TaskRecord(
            task="book test drive",
            owner="Ali",
            due=date(2026, 10, 30),
            due_text="Friday",
            said_by="Sara",
            time="[02:30]",
            key=dedup_key("book test drive", "Ali"),
        )
    )
    assert first.created is True
    assert second.created is False
    assert third.created is False
    assert other.created is True
    query_calls = [call for call in fake.calls if call[1].endswith("/query")]
    assert len(query_calls) == 1
    await service.aclose()


@pytest.mark.asyncio
async def test_t11_live_save_without_children_then_final_save_with_llm_fallback(tmp_path) -> None:
    from datetime import datetime, timezone
    from ba_agent.memory.store import MeetingStore
    from ba_agent.models import CaptionLine
    from ba_agent.session import _save, _save_live

    fake = _FakeNotion()
    service = _service(fake)
    await service.verify()
    await service.ensure_summary_page("TechSol BA m1")

    store = MeetingStore(tmp_path / "meeting.db", "m1")
    store.add_caption(
        CaptionLine(
            id="c1",
            meeting_id="m1",
            speaker="Ali",
            speaker_raw="Ali",
            start_s=5.0,
            end_s=6.0,
            wall_time=datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc),
            text="We decided to launch the beta on Friday.",
        )
    )

    # Live save updates properties without appending children block yet.
    await _save_live(store, service)
    assert service._transcript_appended is False
    child_patches = [
        call
        for call in fake.calls
        if call[0] == "PATCH" and call[1].endswith(f"/blocks/{service.summary_page_id}/children")
    ]
    assert len(child_patches) == 0

    class _FailingLLM:
        async def respond(self, *args, **kwargs):
            raise RuntimeError("OpenAI error")

    class _FakePoster:
        posted: list = []

        async def post(self, text, reason, trigger_id=None):
            self.posted.append((text, reason, trigger_id))
            return True

    poster = _FakePoster()
    from ba_agent.config import load_settings

    url = await _save(
        load_settings(),
        store,
        service,
        _FailingLLM(),
        poster=poster,
        announce=True,
    )
    assert url.startswith("https://www.notion.so/")
    assert service._transcript_appended is True
    assert store.get_meta("saved") == "1"
    assert len(poster.posted) == 1
    await service.aclose()
    store.close()

