"""T09-U1 through T09-U4."""

from __future__ import annotations

from datetime import date

from ba_agent.tasks_extract.dates import format_date, parse_due
from ba_agent.tasks_extract.extractor import (
    apply_followup,
    apply_overheard,
    parse_task_command,
    tasks_from_model_json,
)

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
