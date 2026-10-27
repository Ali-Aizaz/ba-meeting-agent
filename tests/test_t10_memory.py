"""T10-U1 through T10-U3."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from ba_agent.memory.store import MeetingStore
from ba_agent.models import CaptionLine, TaskRecord
from ba_agent.tasks_extract.extractor import dedup_key

UTC = timezone.utc


def _line(index: int, text: str, speaker: str = "Sara") -> CaptionLine:
    return CaptionLine(
        id=f"c{index}",
        meeting_id="m1",
        speaker=speaker,
        speaker_raw=speaker,
        start_s=float(index),
        end_s=float(index) + 0.4,
        wall_time=datetime(2026, 10, 25, 12, 0, tzinfo=UTC),
        text=text,
    )


def test_t10_u1_digest_includes_people_tasks_and_lines(tmp_path: Path) -> None:
    store = MeetingStore(tmp_path / "meeting.db", "m1")
    store.add_caption(_line(10, "Let's use the hybrid"))
    store.add_caption(_line(20, "Sounds good", speaker="Ali"))
    store.add_task(
        TaskRecord(
            task="cost sheet",
            owner="Ali",
            due=date(2026, 10, 30),
            due_text="Fri",
            key=dedup_key("cost sheet", "Ali"),
        )
    )
    digest = store.digest()
    assert "Participants:" in digest
    assert "Sara" in digest
    assert "Ali" in digest
    assert "cost sheet" in digest
    assert "[00:10] Sara: Let's use the hybrid" in digest
    store.close()


def test_t10_u2_reload_restores_lines_and_tasks(tmp_path: Path) -> None:
    path = tmp_path / "meeting.db"
    store = MeetingStore(path, "m1")
    store.add_caption(_line(10, "Let's use the hybrid"))
    store.add_task(
        TaskRecord(
            task="cost sheet",
            owner="Ali",
            due=date(2026, 10, 30),
            due_text="Fri",
            said_by="Sara",
            time="[00:10]",
            key=dedup_key("cost sheet", "Ali"),
        )
    )
    store.close()
    reloaded = MeetingStore(path, "m1")
    assert reloaded.last(10)[0].text == "Let's use the hybrid"
    assert reloaded.tasks()[0].owner == "Ali"
    assert reloaded.tasks()[0].due == date(2026, 10, 30)
    reloaded.close()


def test_t10_u3_long_transcript_is_stored_and_digest_is_capped(tmp_path: Path) -> None:
    store = MeetingStore(tmp_path / "meeting.db", "m1")
    filler = "word " * 40
    for index in range(400):
        marker = ""
        if index == 0:
            marker = "OLDTOKEN "
        elif index == 399:
            marker = "NEWTOKEN "
        store.add_caption(_line(index, marker + filler))
    assert store.count_captions() == 400
    digest = store.digest()
    assert "NEWTOKEN" in digest
    assert "OLDTOKEN" not in digest
    assert len(digest) // 4 < 4000
    store.close()


def test_t10_participants_order_wal_indexes_and_replies(tmp_path: Path) -> None:
    store = MeetingStore(tmp_path / "meeting.db", "m1")
    store.add_caption(_line(5, "First from Omar", speaker="Omar"))
    store.add_caption(_line(10, "Second from Sara", speaker="Sara"))
    store.add_caption(_line(15, "Again from Omar", speaker="Omar"))
    store.add_caption(_line(20, "Third from Ali", speaker="Ali"))
    assert store.participants() == ["Omar", "Sara", "Ali"]

    mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"
    indexes = {
        row["name"]
        for row in store._conn.execute("PRAGMA index_list('captions')").fetchall()
    }
    assert "idx_captions_meeting_start" in indexes

    for idx in range(7):
        store.add_reply(f"Bot answer {idx}")
    recent = store.recent_replies(limit=5)
    assert recent == [f"Bot answer {idx}" for idx in range(2, 7)]
    digest = store.digest()
    assert "Bot replies:" in digest
    assert "Bot answer 6" in digest
    assert "Bot answer 0" not in digest
    store.close()
