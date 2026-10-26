"""SQLite transcript and tasks for the current meeting."""

from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import date, datetime
from pathlib import Path

from ba_agent.captions.clock import format_meeting_time
from ba_agent.models import CaptionLine, TaskRecord


class MeetingStore:
    def __init__(self, path: Path, meeting_id: str) -> None:
        self.path = Path(path)
        self.meeting_id = meeting_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS captions (
                id TEXT PRIMARY KEY,
                meeting_id TEXT NOT NULL,
                speaker TEXT NOT NULL,
                speaker_raw TEXT NOT NULL,
                start_s REAL NOT NULL,
                end_s REAL NOT NULL,
                wall_time TEXT NOT NULL,
                text TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
                key TEXT NOT NULL,
                meeting_id TEXT NOT NULL,
                task TEXT NOT NULL,
                owner TEXT NOT NULL,
                due TEXT,
                due_text TEXT,
                status TEXT,
                said_by TEXT,
                time TEXT,
                notion_page_id TEXT,
                PRIMARY KEY (meeting_id, key)
            );
            CREATE TABLE IF NOT EXISTS replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meeting_id TEXT NOT NULL,
                text TEXT NOT NULL,
                wall_time TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def add_caption(self, line: CaptionLine) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO captions
                (id, meeting_id, speaker, speaker_raw, start_s, end_s, wall_time, text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    line.id,
                    line.meeting_id,
                    line.speaker,
                    line.speaker_raw,
                    line.start_s,
                    line.end_s,
                    line.wall_time.isoformat(),
                    line.text,
                ),
            )
            self._conn.commit()

    def last(self, n: int) -> list[CaptionLine]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM captions WHERE meeting_id = ?
                ORDER BY start_s DESC, id DESC LIMIT ?
                """,
                (self.meeting_id, n),
            ).fetchall()
        return [_caption(row) for row in reversed(rows)]

    def since(self, t: float) -> list[CaptionLine]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM captions WHERE meeting_id = ? AND start_s >= ?
                ORDER BY start_s, id
                """,
                (self.meeting_id, t),
            ).fetchall()
        return [_caption(row) for row in rows]

    def by_speaker(self, name: str) -> list[CaptionLine]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM captions WHERE meeting_id = ? AND speaker = ?
                ORDER BY start_s, id
                """,
                (self.meeting_id, name),
            ).fetchall()
        return [_caption(row) for row in rows]

    def count_captions(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM captions WHERE meeting_id = ?",
                (self.meeting_id,),
            ).fetchone()
        return int(row["n"])

    def add_task(self, task: TaskRecord) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO tasks
                (key, meeting_id, task, owner, due, due_text, status, said_by, time, notion_page_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.key,
                    self.meeting_id,
                    task.task,
                    task.owner,
                    task.due.isoformat() if task.due else "",
                    task.due_text,
                    task.status,
                    task.said_by,
                    task.time,
                    task.notion_page_id or "",
                ),
            )
            self._conn.commit()

    def tasks(self) -> list[TaskRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE meeting_id = ? ORDER BY time, task",
                (self.meeting_id,),
            ).fetchall()
        return [_task(row) for row in rows]

    def add_reply(self, text: str, wall_time: datetime | None = None) -> None:
        moment = wall_time or datetime.now().astimezone()
        with self._lock:
            self._conn.execute(
                "INSERT INTO replies (meeting_id, text, wall_time) VALUES (?, ?, ?)",
                (self.meeting_id, text, moment.isoformat()),
            )
            self._conn.commit()

    def participants(self) -> list[str]:
        names: list[str] = []
        for line in self.last(100000):
            if line.speaker and line.speaker not in names:
                names.append(line.speaker)
        return names

    def digest(self, max_tokens: int = 3000) -> str:
        lines = self.last(100000)
        task_rows = self.tasks()
        speakers = self.participants()
        header = "Participants: " + (", ".join(speakers) if speakers else "none")
        if task_rows:
            task_block = "Tasks:\n" + "\n".join(
                f"- {task.owner}: {task.task} ({task.due_text})" for task in task_rows
            )
        else:
            task_block = "Tasks:\nnone"
        rendered = [
            f"{format_meeting_time(line.start_s)} {line.speaker}: {line.text}" for line in lines
        ]
        used = _tokens(header) + _tokens(task_block) + _tokens("Recent:")
        chosen: list[str] = []
        for row in reversed(rendered):
            cost = _tokens(row)
            if chosen and used + cost > max_tokens:
                break
            chosen.append(row)
            used += cost
        chosen.reverse()
        return "\n".join([header, task_block, "Recent:", *chosen])

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (key, value),
            )
            self._conn.commit()

    def get_meta(self, key: str) -> str:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return "" if row is None else str(row["value"])

    def close(self) -> None:
        self._conn.close()


def open_meeting(path: Path, url: str) -> MeetingStore:
    """Reuse the open meeting for this URL after a crash. A finished meeting starts fresh."""
    store = MeetingStore(path, meeting_id="pending")
    previous = store.get_meta("active_url")
    closed = store.get_meta("closed")
    existing = store.get_meta("active_meeting_id")
    if previous == url and closed != "1" and existing:
        store.meeting_id = existing
        return store
    store.meeting_id = uuid.uuid4().hex
    store.set_meta("active_url", url)
    store.set_meta("active_meeting_id", store.meeting_id)
    store.set_meta("closed", "0")
    return store


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _caption(row: sqlite3.Row) -> CaptionLine:
    return CaptionLine(
        id=row["id"],
        meeting_id=row["meeting_id"],
        speaker=row["speaker"],
        speaker_raw=row["speaker_raw"],
        start_s=row["start_s"],
        end_s=row["end_s"],
        wall_time=datetime.fromisoformat(row["wall_time"]),
        text=row["text"],
    )


def _task(row: sqlite3.Row) -> TaskRecord:
    return TaskRecord(
        task=row["task"],
        owner=row["owner"],
        due=date.fromisoformat(row["due"]) if row["due"] else None,
        due_text=row["due_text"] or "",
        status=row["status"] or "Not started",
        said_by=row["said_by"] or "",
        time=row["time"] or "",
        notion_page_id=row["notion_page_id"] or None,
        key=row["key"],
    )
