"""T09-U1 through T09-U4."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from ba_agent.config import load_settings
from ba_agent.memory.store import MeetingStore
from ba_agent.models import CaptionLine, LLMResponse
from ba_agent.session import _Extractor
from ba_agent.tasks_extract.dates import format_date, parse_due
from ba_agent.tasks_extract.extractor import (
    apply_followup,
    apply_overheard,
    might_contain_task,
    parse_task_command,
    tasks_from_model_json,
)

UTC = timezone.utc

TODAY = date(2026, 10, 25)


def test_t09_u1_dates() -> None:
    assert parse_due("Friday", today=TODAY) == date(2026, 10, 30)
    assert parse_due("next Wednesday", today=TODAY) == date(2026, 11, 4)
    assert parse_due("24 Oct", today=TODAY) == date(2026, 10, 24)
    assert parse_due("tomorrow", today=TODAY) == date(2026, 10, 26)
    assert parse_due("2026-10-30", today=TODAY) == date(2026, 10, 30)
    assert format_date(date(2026, 10, 24)) == "24 Oct"


def test_t09_followup_fills_owner_and_due() -> None:
    record = parse_task_command(
        "add a task to review the charging port",
        said_by="Sara",
        time="[00:20]",
        today=TODAY,
    )
    assert record is not None
    assert record.owner == ""
    assert record.due is None
    completed = apply_followup(record, "for Ali by Friday", today=TODAY)
    assert completed.owner == "Ali"
    assert completed.due == date(2026, 10, 30)


def test_t09_u2_json_to_task() -> None:
    payload = {
        "tasks": [
            {
                "task": "update the cost sheet",
                "owner": "Ali",
                "due_text": "Friday",
                "said_by": "Sara",
            }
        ]
    }
    records = tasks_from_model_json(
        payload,
        said_by="Sara",
        time="[01:15]",
        today=TODAY,
        overheard=True,
    )
    assert len(records) == 1
    assert records[0].owner == "Ali"
    assert records[0].due == date(2026, 10, 30)
    assert records[0].said_by == "Sara"
    assert records[0].due_text == "Friday"


def test_t09_u3_missing_owner_is_dropped() -> None:
    payload = {"tasks": [{"task": "update the cost sheet", "owner": "", "due_text": "Friday"}]}
    records, chats = apply_overheard(
        "Please update the cost sheet by Friday",
        payload,
        said_by="Sara",
        time="[01:15]",
        today=TODAY,
    )
    assert records == []
    assert chats == []


def test_t09_u4_hey_ba_question_is_not_a_task() -> None:
    payload = {
        "tasks": [
            {
                "task": "explain hybrid",
                "owner": "Ali",
                "due_text": "Friday",
            }
        ]
    }
    records, chats = apply_overheard(
        "Hey BA, what is a hybrid?",
        payload,
        said_by="Sara",
        time="[00:10]",
        today=TODAY,
    )
    assert records == []
    assert chats == []


def test_t09_might_contain_task_prefilter() -> None:
    assert might_contain_task("", today=TODAY) is False
    assert might_contain_task("Sounds good to me", today=TODAY) is False
    assert might_contain_task("The battery is 60 kWh", today=TODAY) is False
    assert might_contain_task("What is the current price today?", today=TODAY) is False
    assert (
        might_contain_task("Hey BA, please update the cost sheet by Friday", today=TODAY) is False
    )
    assert might_contain_task("Ali will update the cost sheet by Friday", today=TODAY) is True
    assert might_contain_task("Please review the spec tomorrow", today=TODAY) is True
    assert might_contain_task("Target date is 24 Oct", today=TODAY) is True
    assert might_contain_task("Can you send the deck by Monday?", today=TODAY) is True


@pytest.mark.asyncio
async def test_t09_extractor_batches_and_skips_non_task_lines(tmp_path) -> None:
    settings = load_settings()
    settings.llm.task_model = "gpt-5.5-mini"
    store = MeetingStore(tmp_path / "meeting.db", "m1")
    llm_calls: list[tuple[str, str | None]] = []

    class _FakeLLM:
        async def respond(
            self,
            input: str,
            tools=None,
            *,
            enable_web_search: bool = False,
            timeout_s: float | None = None,
            model_override: str | None = None,
        ) -> LLMResponse:
            del tools, enable_web_search, timeout_s
            llm_calls.append((input, model_override))
            return LLMResponse(
                text=(
                    '{"tasks":[{"task":"send deck","owner":"Ali",'
                    '"due_text":"Friday","said_by":"Sara"}]}'
                )
            )

    class _FakeNotionSchema:
        tasks_db = "db_tasks"

    class _FakeNotion:
        schema = _FakeNotionSchema()
        created: list = []

        async def create_task(self, record):
            from ba_agent.notion.client import TaskWrite

            self.created.append(record)
            return TaskWrite(
                True,
                "✓ Task added: Ali — send deck — Fri",
                page_id="p1",
                key=record.key,
            )

    class _FakePoster:
        posted: list = []

        async def post(self, text, reason, trigger_id=None):
            self.posted.append((text, reason, trigger_id))
            return True

    notion = _FakeNotion()
    poster = _FakePoster()
    extractor = _Extractor(settings, _FakeLLM(), store, poster, notion)

    def _cap(idx: int, text: str) -> CaptionLine:
        return CaptionLine(
            id=f"c{idx}",
            meeting_id="m1",
            speaker="Sara",
            speaker_raw="Sara",
            start_s=float(idx),
            end_s=float(idx) + 0.5,
            wall_time=datetime(2026, 10, 25, tzinfo=UTC),
            text=text,
        )

    # First burst: casual non-task speech should not invoke the LLM at all.
    extractor.note(_cap(1, "Good morning everyone"))
    extractor.note(_cap(2, "The audio is clear"))
    extractor.stop()
    await extractor.run()
    assert llm_calls == []

    # Second burst: multiple queued lines coalesce into a single LLM call using task_model,
    # and trailing non-task filler does not override the task line's timestamp/speaker.
    extractor._stopped = False
    extractor.note(_cap(3, "We talked about the slides"))
    extractor.note(_cap(4, "Ali will send the deck by Friday"))
    extractor.note(
        CaptionLine(
            id="c5",
            meeting_id="m1",
            speaker="Omar",
            speaker_raw="Omar",
            start_s=9.0,
            end_s=9.5,
            wall_time=datetime(2026, 10, 25, tzinfo=UTC),
            text="Sounds good to me",
        )
    )
    extractor.stop()
    await extractor.run()
    assert len(llm_calls) == 1
    assert llm_calls[0][1] == "gpt-5.5-mini"
    assert len(notion.created) == 1
    assert notion.created[0].owner == "Ali"
    assert notion.created[0].time == "[00:04]"
    store.close()
