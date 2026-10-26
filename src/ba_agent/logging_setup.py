"""Logging that always carries the current meeting id."""

from __future__ import annotations

import logging
from contextvars import ContextVar

meeting_id_var: ContextVar[str] = ContextVar("meeting_id", default="-")


def set_meeting_id(meeting_id: str) -> None:
    meeting_id_var.set(meeting_id)


class MeetingIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.meeting_id = meeting_id_var.get()
        return True


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.addFilter(MeetingIdFilter())
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s meeting=%(meeting_id)s %(name)s %(message)s")
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
