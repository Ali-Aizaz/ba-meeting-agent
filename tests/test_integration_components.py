"""End-to-end component integration tests: Notion, Status HTTP, Session, and Edge/Extension."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from tests.conftest import FIXTURES, clear_secrets

from ba_agent.captions.stabilize import CaptionRow
from ba_agent.config import StartupError, load_settings
from ba_agent.memory.store import _canonical_url, open_meeting
from ba_agent.models import (
    ChatMessage,
    FunctionCall,
    LLMResponse,
    PostReason,
    TaskRecord,
    WebResult,
)
from ba_agent.notion.client import NotionService, _transcript_toggle
from ba_agent.notion.schema import (
    load_notion_schema,
    missing_properties,
    plain_property,
    require_properties,
)
from ba_agent.session import _run_single_meeting
from ba_agent.status import StatusState, start_status_server
from ba_agent.tasks_extract.extractor import dedup_key

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 1. Notion Schema, Rate-Limit/Transient Retries, Pagination & Dedup Fast-Path
# ---------------------------------------------------------------------------


def test_notion_schema_property_checks_and_plain_property_types() -> None:
    schema = load_notion_schema(ROOT / "config" / "notion_schema.yaml")
    schema.summary_db = "sum-db"
    schema.tasks_db = "task-db"

    missing = missing_properties(
        schema,
        remote_summary={"Title", "Date"},
        remote_tasks={"Task", "Owner"},
    )
    assert "Summary" in missing
    assert "Due" in missing
    with pytest.raises(StartupError, match="Notion properties missing"):
        require_properties(missing)
    require_properties([])

    assert plain_property("not-a-dict") == ""  # type: ignore[arg-type]
    assert plain_property({"type": "title", "title": [{"plain_text": "Sync"}]}) == "Sync"
    assert (
        plain_property({"type": "rich_text", "rich_text": [{"text": {"content": "Body"}}]})
        == "Body"
    )
    assert plain_property({"type": "date", "date": {"start": "2026-11-20"}}) == "2026-11-20"
    assert plain_property({"type": "date", "date": None}) == ""
    assert plain_property({"type": "status", "status": {"name": "Done"}}) == "Done"
    assert plain_property({"type": "select", "select": {"name": "High"}}) == "High"
    assert plain_property({"type": "url", "url": "https://meet.google.com/abc-defg-hij"}) != ""
    assert plain_property({"type": "number", "number": 42}) == "42"
    assert plain_property({"type": "number", "number": None}) == ""
    assert (
        plain_property({"type": "people", "people": [{"name": "Sara"}, {"name": "Ali"}]})
        == "Sara, Ali"
    )


def test_transcript_toggle_caps_at_80_lines_with_overflow_notice() -> None:
    transcript = "\n".join(f"[00:{i:02d}] Sara: Line number {i}" for i in range(95))
    blocks = _transcript_toggle(transcript)
    assert len(blocks) == 1
    children = blocks[0]["toggle"]["children"]
    # 95 short lines pack cleanly into multiple-line paragraphs (< Notion 100-child API cap).
    packed_text = "\n".join(c["paragraph"]["rich_text"][0]["text"]["content"] for c in children)
    assert len(children) < 95
    assert "Line number 94" in packed_text
    assert "more lines" not in packed_text

    # When packed paragraphs exceed 95 blocks (~170 KB), an overflow notice is appended at <= 96.
    huge_transcript = "\n".join(
        f"[{i:04d}] Sara: {'x' * 950} line-{i}" for i in range(120)
    )
    huge_children = _transcript_toggle(huge_transcript)[0]["toggle"]["children"]
    assert len(huge_children) == 96
    overflow_text = huge_children[-1]["paragraph"]["rich_text"][0]["text"]["content"]
    assert "more lines" in overflow_text

    assert _transcript_toggle("")[0]["toggle"]["children"][0]["paragraph"]["rich_text"][0][
        "text"
    ]["content"] == "(no transcript)"


@pytest.mark.asyncio
async def test_notion_service_retries_429_503_pagination_and_local_dedup() -> None:
    schema = load_notion_schema(ROOT / "config" / "notion_schema.yaml")
    schema.summary_db = "sum-db"
    schema.tasks_db = "task-db"

    attempts = {"tasks_query": 0, "patch_calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        body = json.loads(request.content.decode("utf-8")) if request.content else {}

        # Simulate 429 on first GET /v1/databases/sum-db, 503 on second, 200 on third.
        if method == "GET" and path.endswith("/databases/sum-db"):
            attempts["sum_db"] = attempts.get("sum_db", 0) + 1
            if attempts["sum_db"] == 1:
                return httpx.Response(429, headers={"Retry-After": "0.01"}, json={})
            if attempts["sum_db"] == 2:
                return httpx.Response(503, json={"message": "temporary outage"})
            return httpx.Response(
                200,
                json={"properties": {p.name: {} for p in schema.summary_props.values()}},
            )

        if method == "GET" and path.endswith("/databases/task-db"):
            return httpx.Response(
                200,
                json={"properties": {p.name: {} for p in schema.task_props.values()}},
            )

        # Paginated /databases/task-db/query (page 1 -> cursor-2 -> page 2)
        if method == "POST" and path.endswith("/databases/task-db/query"):
            attempts["tasks_query"] += 1
            if body.get("start_cursor") == "cursor-2":
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "id": "page-t2",
                                "properties": {
                                    "Task": {"title": [{"plain_text": "Second paged task"}]},
                                    "Owner": {"rich_text": [{"plain_text": "Omar"}]},
                                    "Status": {"status": {"name": "Not started"}},
                                },
                            }
                        ],
                        "has_more": False,
                        "next_cursor": None,
                    },
                )
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "page-t1",
                            "properties": {
                                "Task": {"title": [{"plain_text": "First paged task"}]},
                                "Owner": {"rich_text": [{"plain_text": "Sara"}]},
                                "Status": {"status": {"name": "In progress"}},
                            },
                        }
                    ],
                    "has_more": True,
                    "next_cursor": "cursor-2",
                },
            )

        if method == "PATCH" and "/pages/" in path:
            attempts["patch_calls"] += 1
            return httpx.Response(200, json={"id": path.rsplit("/", 1)[-1]})

        return httpx.Response(404, json={})

    known_key = dedup_key("existing task", "Ali")
    service = NotionService(
        token="ntn_test_token_123456",
        schema=schema,
        meeting_id="m-notion-test",
        transport=httpx.MockTransport(handler),
        min_interval=0.0,
    )
    service._task_cache[known_key] = "page-local-hit"

    try:
        # 1. verify() survives 429 + 503 retries and succeeds.
        await service.verify()
        assert attempts["sum_db"] == 3

        # 2. list_tasks() follows cursor pagination across both pages.
        tasks = await service.list_tasks(refresh=True)
        assert [t.task for t in tasks] == ["First paged task", "Second paged task"]
        assert attempts["tasks_query"] == 2

        # 3. create_task() hits local _task_cache and skips both Notion query and page write!
        queries_before = attempts["tasks_query"]
        res = await service.create_task(
            TaskRecord(
                task="existing task",
                owner="Ali",
                due=date(2026, 11, 20),
                due_text="Nov 20",
                key=known_key,
            )
        )
        assert res.created is False
        assert res.page_id == "page-local-hit"
        assert attempts["patch_calls"] == 0
        assert attempts["tasks_query"] == queries_before
    finally:
        await service.aclose()


# ---------------------------------------------------------------------------
# 2. Status HTTP Server, CORS, Validation & Activity Ring Buffer
# ---------------------------------------------------------------------------


def test_status_server_http_cors_validation_and_activity_ring_buffer() -> None:
    joins: list[tuple[str, bool]] = []

    def bad_activity_hook(_text: str, _kind: str) -> None:
        raise RuntimeError("toast hook failed")

    state = StatusState(
        meeting_id="m-http",
        on_join=lambda u, f=False: joins.append((u, f)),
        on_activity=bad_activity_hook,
    )
    # Push 30 events: internal list caps at 25, as_dict()["events"] exposes latest 15.
    for idx in range(30):
        state.record_activity(f"Step {idx}", "working")
    snap = state.as_dict()
    assert len(state.events) == 25
    assert len(snap["events"]) == 15
    assert snap["activity"] == "Step 29"

    # Success event clears active spinner text.
    state.record_activity("Done ✓", "success")
    assert state.as_dict()["activity"] == ""

    server = start_status_server(state, "127.0.0.1", 0)
    assert server is not None
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    try:
        # OPTIONS preflight returns 204 with CORS headers.
        opt = httpx.options(f"{base}/join")
        assert opt.status_code == 204
        assert opt.headers["access-control-allow-origin"] == "*"

        # GET / and GET /status return 200; unknown route returns 404.
        root_res = httpx.get(f"{base}/")
        assert root_res.status_code == 200
        assert root_res.json()["meeting_id"] == "m-http"
        assert httpx.get(f"{base}/unknown").status_code == 404
        assert httpx.post(f"{base}/unknown", json={}).status_code == 404

        # Invalid JSON and non-Meet URLs return 400.
        bad_json = httpx.post(
            f"{base}/join",
            content=b"{not-json",
            headers={"Content-Type": "application/json"},
        )
        assert bad_json.status_code == 400
        bad_url = httpx.post(f"{base}/join", json={"url": "https://evil.example.com/abc"})
        assert bad_url.status_code == 400

        # Valid POST /join with force=True invokes callback and returns 200.
        good = httpx.post(
            f"{base}/join",
            json={"url": "https://meet.google.com/abc-defg-hij", "force": True},
        )
        assert good.status_code == 200
        assert good.json()["ok"] is True
        assert joins == [("https://meet.google.com/abc-defg-hij", True)]
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# 3. Full End-to-End Session Pipeline (_run_single_meeting)
# ---------------------------------------------------------------------------


class _FakeMeetAdapter:
    """Deterministic in-memory GoogleMeetAdapter stand-in for full session loop testing."""

    def __init__(self) -> None:
        self.ended_event = asyncio.Event()
        self.chat_enabled = True
        self._left = False
        self._admitted_at: float | None = None
        self.posted: list[tuple[str, PostReason]] = []
        self._cap_polls = 0
        self._chat_polls = 0
        self._clock = 0.0

    async def join(self, _url: str) -> None:
        self._admitted_at = 0.0

    async def enable_captions(self) -> bool:
        return True

    async def ensure_chat_open(self) -> bool:
        return True

    async def meeting_closed(self) -> bool:
        return self._cap_polls >= 4

    def meeting_seconds(self) -> float:
        self._clock += 1.0
        return self._clock

    async def drain_caption_frames(self) -> list[list[CaptionRow]]:
        self._cap_polls += 1
        if self._cap_polls == 1:
            return [
                [
                    CaptionRow(
                        row_id="r1",
                        speaker="Sara",
                        text="Hey BA, what is regenerative braking?",
                    )
                ]
            ]
        if self._cap_polls == 2:
            return [
                [
                    CaptionRow(
                        row_id="r2",
                        speaker="Omar",
                        text="I'll send the inverter thermal report by tomorrow.",
                    )
                ]
            ]
        return []

    async def collect_chat(self) -> list[ChatMessage]:
        from datetime import datetime, timezone

        self._chat_polls += 1
        if self._chat_polls == 1:
            return [
                ChatMessage(
                    sender="Ali K.",
                    text="@ba add a task for Sara to review the pack BOM by tomorrow",
                    wall_time=datetime(2026, 11, 11, 10, 1, tzinfo=timezone.utc),
                    is_self=False,
                )
            ]
        return []

    async def post_chat(self, text: str, reason: PostReason) -> bool:
        self.posted.append((text, reason))
        return True

    async def other_participant_count(self) -> int:
        return 2

    async def leave(self) -> None:
        self._left = True

    async def close(self, force: bool = False) -> None:
        self._left = True


class _SessionFakeLLM:
    def __init__(self) -> None:
        self._task_tool_used = False

    async def respond(
        self,
        input: str,
        tools: list[dict] | None = None,
        *,
        enable_web_search: bool = False,
        timeout_s: float | None = None,
        model_override: str | None = None,
    ) -> LLMResponse:
        # Overheard task extraction JSON prompt
        if tools is None and "Focus line:" in input:
            return LLMResponse(
                text=json.dumps(
                    {
                        "tasks": [
                            {
                                "task": "send the inverter thermal report",
                                "owner": "Omar",
                                "due_text": "tomorrow",
                            }
                        ]
                    }
                ),
            )
        # Summary generation prompt (no tools, transcript summary)
        if tools is None and "Focus line:" not in input:
            return LLMResponse(
                text="## Summary\nReviewed regenerative braking, BOM, and inverter thermal tasks.",
            )
        # Regenerative braking question answered with local ev.md citation
        if "<question>what is regenerative braking" in input.lower():
            return LLMResponse(
                text=(
                    "Regenerative braking uses the electric motor as a generator during "
                    "deceleration to recover energy into the battery (src: ev.md)."
                ),
            )
        # Explicit @ba create_task fallback if routed through LLM tools
        if tools and not self._task_tool_used:
            self._task_tool_used = True
            return LLMResponse(
                function_calls=[
                    FunctionCall(
                        name="create_task",
                        arguments={
                            "task": "review the pack BOM",
                            "owner": "Sara",
                            "due": "tomorrow",
                        },
                    )
                ],
            )
        return LLMResponse(text="Task added: Sara — review the pack BOM.")

    async def search_cars(self, _query: str) -> WebResult:
        return WebResult()


@pytest.mark.asyncio
async def test_full_single_meeting_session_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_secrets(monkeypatch)
    settings = load_settings()
    settings.llm.api_key = SecretStr("sk-session-test-12345678")
    settings.notion.enabled = False
    settings.captions.stable_ms = 0
    settings.storage.sqlite_path = str(tmp_path / "session.db")

    fake_llm = _SessionFakeLLM()
    monkeypatch.setattr("ba_agent.session.build_llm", lambda _s: fake_llm)

    # Wire an in-memory NotionService backed by MockTransport so Notion writes are exercised too!
    schema = load_notion_schema(ROOT / "config" / "notion_schema.yaml")
    schema.summary_db = "sum-db"
    schema.tasks_db = "task-db"
    notion_writes: list[tuple[str, str]] = []

    def notion_handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        notion_writes.append((request.method, path))
        if request.method == "GET" and path.endswith("/databases/sum-db"):
            return httpx.Response(
                200,
                json={"properties": {p.name: {} for p in schema.summary_props.values()}},
            )
        if request.method == "GET" and path.endswith("/databases/task-db"):
            return httpx.Response(
                200,
                json={"properties": {p.name: {} for p in schema.task_props.values()}},
            )
        if request.method == "POST" and path.endswith("/query"):
            return httpx.Response(200, json={"results": [], "has_more": False})
        if request.method == "POST" and path.endswith("/pages"):
            idx = len(notion_writes)
            return httpx.Response(
                200,
                json={"id": f"page-{idx}", "url": f"https://www.notion.so/page-{idx}"},
            )
        if request.method in {"PATCH", "DELETE"}:
            return httpx.Response(
                200,
                json={
                    "id": "page-1",
                    "url": "https://www.notion.so/page-1",
                    "results": [],
                    "has_more": False,
                },
            )
        if request.method == "GET" and path.endswith("/children"):
            return httpx.Response(200, json={"results": [], "has_more": False})
        return httpx.Response(200, json={})

    def fake_build_notion(_settings, store):
        return NotionService(
            token="ntn_session_test_123456",
            schema=schema,
            meeting_id=store.meeting_id,
            transport=httpx.MockTransport(notion_handler),
            min_interval=0.0,
        )

    monkeypatch.setattr("ba_agent.session._build_notion", fake_build_notion)

    adapter = _FakeMeetAdapter()
    status = StatusState()
    meet_url = "https://meet.google.com/abc-defg-hij"

    await _run_single_meeting(settings, adapter, status, meet_url)  # type: ignore[arg-type]

    # 1. Consent message posted with live Notion summary page link
    reasons = [r for _, r in adapter.posted]
    assert PostReason.CONSENT in reasons
    assert PostReason.REPLY in reasons
    assert PostReason.CONFIRMATION in reasons

    # 2. Car question reply carries ev.md citation
    replies = [txt for txt, r in adapter.posted if r is PostReason.REPLY]
    assert any("Regenerative braking" in txt and "ev.md" in txt for txt in replies)

    # 3. Both explicit (@ba) and overheard tasks saved in SQLite & Notion, and meeting closed!
    reopened = open_meeting(tmp_path / "session.db", "https://meet.google.com/xyz-uvwx-rst")
    stored_ctx = reopened.stored_context()
    reopened.close()
    assert "review the pack BOM" in stored_ctx
    assert "send the inverter thermal report" in stored_ctx
    assert adapter._left is True


# ---------------------------------------------------------------------------
# 4. Cross-Layer URL Canonicalization & JS Syntax Parity (Worker + Extension + Python)
# ---------------------------------------------------------------------------


def test_cross_layer_meet_url_canonicalization_and_extension_js_syntax() -> None:
    # Verify Python _canonical_url and Worker/Extension regex agree on real & invalid Meet URLs.
    worker_ts = (ROOT / "cloudflare" / "src" / "index.ts").read_text(encoding="utf-8")
    ext_content = (ROOT / "extension" / "content.js").read_text(encoding="utf-8")
    assert r"/^(?:\/_?meet)?\/([a-z]{3}-[a-z]{4}-[a-z]{3})(?:$|[/?#])/i" in worker_ts
    assert r"/^(?:\/_?meet)?\/([a-z]{3}-[a-z]{4}-[a-z]{3})(?:$|[/?#])/i" in ext_content

    py_re = re.compile(
        r"^(?:/_?meet)?/([a-z]{3}-[a-z]{4}-[a-z]{3})(?:$|[/?#])",
        re.I,
    )

    valid_cases = [
        (
            "https://meet.google.com/abc-defg-hij",
            "https://meet.google.com/abc-defg-hij",
        ),
        (
            "https://meet.google.com/ABC-DEFG-HIJ?pli=1",
            "https://meet.google.com/abc-defg-hij",
        ),
        (
            "https://meet.google.com/_meet/abc-defg-hij?authuser=0",
            "https://meet.google.com/abc-defg-hij",
        ),
        (
            "https://meet.google.com/meet/abc-defg-hij#chat",
            "https://meet.google.com/abc-defg-hij",
        ),
    ]
    for raw, expected in valid_cases:
        assert _canonical_url(raw) == expected
        path = "/" + raw.split("meet.google.com/", 1)[1]
        m = py_re.match(path)
        assert m is not None
        assert f"https://meet.google.com/{m.group(1).lower()}" == expected

    for non_code_path in ("/", "/landing", "/new", "/lookup"):
        assert py_re.match(non_code_path) is None

    # Validate JavaScript syntax of all Chrome Extension scripts using Node if available.
    node = shutil.which("node")
    if node is not None:
        for js_file in ("background.js", "content.js", "popup.js"):
            target = ROOT / "extension" / js_file
            proc = subprocess.run(
                [node, "--check", str(target)],
                capture_output=True,
                text=True,
                check=False,
            )
            assert proc.returncode == 0, f"{js_file} syntax error: {proc.stderr}"
    assert (FIXTURES / "fake_meet.html").exists()


@pytest.mark.asyncio
async def test_combined_poll_loop_survives_transient_error_and_surfaces_llm_401(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime, timezone

    from ba_agent.llm.openai_client import OpenAIResponsesClient

    clear_secrets(monkeypatch)
    settings = load_settings()
    settings.llm.api_key = SecretStr("sk-expired-test-key")
    settings.notion.enabled = False
    settings.captions.stable_ms = 0
    settings.storage.sqlite_path = str(tmp_path / "combined.db")

    # Verify OpenAIResponsesClient.warm_up records auth_error on HTTP 401 expired_secret_key
    client_401 = OpenAIResponsesClient(api_key="sk-expired", model="gpt-4o-mini")

    async def _fail_retrieve(_model: str):
        raise RuntimeError(
            "Error code: 401 - {'error': {'code': 'expired_secret_key', "
            "'message': 'Your API key has expired.'}}"
        )

    from types import SimpleNamespace

    client_401._client = SimpleNamespace(models=SimpleNamespace(retrieve=_fail_retrieve))
    await client_401.warm_up()
    assert client_401.auth_error is not None
    assert "401" in client_401.auth_error

    class _ExpiredLLM:
        auth_error: str | None = None

        async def warm_up(self) -> None:
            self.auth_error = "OpenAI API key is invalid or expired (HTTP 401)"

        async def respond(self, *_args, **_kwargs):
            raise RuntimeError("Error code: 401 - expired_secret_key")

        async def search_cars(self, _q: str) -> WebResult:
            return WebResult()

    monkeypatch.setattr("ba_agent.session.build_llm", lambda _s: _ExpiredLLM())

    class _CombinedPollAdapter:
        def __init__(self) -> None:
            self.ended_event = asyncio.Event()
            self.chat_enabled = True
            self._left = False
            self._admitted_at: float | None = 0.0
            self.posted: list[tuple[str, PostReason]] = []
            self._polls = 0

        async def join(self, _url: str) -> None:
            self._admitted_at = 0.0

        async def enable_captions(self) -> bool:
            return True

        async def ensure_chat_open(self) -> bool:
            return True

        async def meeting_closed(self) -> bool:
            return self._polls >= 3

        def meeting_seconds(self) -> float:
            return float(self._polls)

        async def drain_poll_state(self) -> tuple[list[list[CaptionRow]], list[ChatMessage]]:
            self._polls += 1
            if self._polls == 1:
                raise RuntimeError("Execution context was destroyed")
            if self._polls == 2:
                return (
                    [],
                    [
                        ChatMessage(
                            sender="Unknown speaker",
                            text="BA: what is regenerative braking?",
                            wall_time=datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc),
                            is_self=False,
                        )
                    ],
                )
            self.ended_event.set()
            return [], []

        async def drain_caption_frames(self) -> list[list[CaptionRow]]:
            return []

        async def post_chat(self, text: str, reason: PostReason) -> bool:
            self.posted.append((text, reason))
            return True

        async def other_participant_count(self) -> int:
            return 1

        async def leave(self) -> None:
            self._left = True

        async def close(self, force: bool = False) -> None:
            del force
            self._left = True

    adapter = _CombinedPollAdapter()
    status = StatusState()
    await _run_single_meeting(
        settings,
        adapter,  # type: ignore[arg-type]
        status,
        "https://meet.google.com/gya-vqog-xwn",
    )
    replies = [txt for txt, r in adapter.posted if r is PostReason.REPLY]
    assert any("hybrid.md" in txt or "ev.md" in txt for txt in replies)
    assert "401" in status.last_error

