"""SQLite transcript and tasks for the current meeting."""

from __future__ import annotations

import re
import sqlite3
import threading
import uuid
from datetime import date, datetime
from pathlib import Path

from ba_agent.captions.clock import format_meeting_time
from ba_agent.models import CaptionLine, TaskRecord

_MEET_CODE_RE = re.compile(
    r"^https://meet\.google\.com/(?:_?meet/)?([a-z]{3}-[a-z]{4}-[a-z]{3})(?:$|[/?#])",
    re.I,
)


def _canonical_url(url: str) -> str:
    cleaned = url.strip()
    m = _MEET_CODE_RE.match(cleaned)
    if m:
        return f"https://meet.google.com/{m.group(1).lower()}"
    return cleaned


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
            PRAGMA journal_mode = WAL;
            PRAGMA synchronous = NORMAL;
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
            CREATE INDEX IF NOT EXISTS idx_captions_meeting_start
                ON captions (meeting_id, start_s DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_replies_meeting
                ON replies (meeting_id, id DESC);
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

    def all_tasks(self) -> list[TaskRecord]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM tasks
                ORDER BY CASE WHEN meeting_id = ? THEN 0 ELSE 1 END, time, task
                """,
                (self.meeting_id,),
            ).fetchall()
        seen: set[str] = set()
        result: list[TaskRecord] = []
        for row in rows:
            rec = _task(row)
            key = rec.key or f"{rec.task.lower()}|{rec.owner.lower()}"
            if key not in seen:
                seen.add(key)
                result.append(rec)
        return result

    def stored_context(self, max_tasks: int = 20, max_captions: int = 15) -> str:
        sections: list[str] = []
        current_keys = {t.key for t in self.tasks() if t.key}
        past_tasks = [t for t in self.all_tasks() if t.key not in current_keys]
        if past_tasks:
            lines = [
                f"- {t.owner}: {t.task} "
                f"(due: {t.due.isoformat() if t.due else (t.due_text or 'unscheduled')}) "
                f"[{t.status}]"
                for t in past_tasks[:max_tasks]
            ]
            sections.append("Stored Local Tasks:\n" + "\n".join(lines))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM captions WHERE meeting_id != ?
                ORDER BY wall_time DESC, start_s DESC, id DESC LIMIT ?
                """,
                (self.meeting_id, max_captions),
            ).fetchall()
        if rows:
            past_lines = [
                f"{format_meeting_time(r['start_s'])} {r['speaker']}: {r['text']}"
                for r in reversed(rows)
            ]
            sections.append("Previous Local Meeting Captions:\n" + "\n".join(past_lines))
        return "\n".join(sections)

    def add_reply(self, text: str, wall_time: datetime | None = None) -> None:
        moment = wall_time or datetime.now().astimezone()
        with self._lock:
            self._conn.execute(
                "INSERT INTO replies (meeting_id, text, wall_time) VALUES (?, ?, ?)",
                (self.meeting_id, text, moment.isoformat()),
            )
            self._conn.commit()

    def recent_replies(self, limit: int = 5) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT text FROM replies WHERE meeting_id = ?
                ORDER BY id DESC LIMIT ?
                """,
                (self.meeting_id, limit),
            ).fetchall()
        return [str(row["text"]) for row in reversed(rows)]

    def participants(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT speaker FROM captions
                WHERE meeting_id = ? AND speaker != ''
                GROUP BY speaker
                ORDER BY MIN(start_s), MIN(id)
                """,
                (self.meeting_id,),
            ).fetchall()
        return [str(row["speaker"]) for row in rows]

    def digest(self, max_tokens: int = 3000) -> str:
        lines = self.last(100000)
        task_rows = self.tasks()
        speakers = self.participants()
        replies = self.recent_replies(limit=5)
        header = "Participants: " + (", ".join(speakers) if speakers else "none")
        if task_rows:
            task_block = "Tasks:\n" + "\n".join(
                f"- {task.owner}: {task.task} ({task.due_text})" for task in task_rows
            )
        else:
            task_block = "Tasks:\nnone"
        reply_block = (
            "Bot replies:\n" + "\n".join(f"- {reply}" for reply in replies) if replies else ""
        )
        rendered = [
            f"{format_meeting_time(line.start_s)} {line.speaker}: {line.text}" for line in lines
        ]
        used = (
            _tokens(header)
            + _tokens(task_block)
            + (_tokens(reply_block) if reply_block else 0)
            + _tokens("Recent:")
        )
        chosen: list[str] = []
        for row in reversed(rendered):
            cost = _tokens(row)
            if chosen and used + cost > max_tokens:
                break
            chosen.append(row)
            used += cost
        chosen.reverse()
        sections = [header, task_block]
        if reply_block:
            sections.append(reply_block)
        sections.extend(["Recent:", *chosen])
        return "\n".join(sections)

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
    if previous and _canonical_url(previous) == _canonical_url(url) and closed != "1" and existing:
        store.meeting_id = existing
        return store
    store.meeting_id = uuid.uuid4().hex
    store.set_meta("active_url", url)
    store.set_meta("active_meeting_id", store.meeting_id)
    store.set_meta("closed", "0")
    for key in (
        "summary_page_id",
        "summary_url",
        "consent_posted",
        "saved",
        "save_announced",
        "saved_sig",
    ):
        store.set_meta(key, "")
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
