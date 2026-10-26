"""Logging that always carries the current meeting id and scrubs secrets."""

from __future__ import annotations

import logging
import re
from contextvars import ContextVar

meeting_id_var: ContextVar[str] = ContextVar("meeting_id", default="-")

_SECRET_PATTERNS = (
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.I),
    re.compile(r"\b(?:sk|ntn|secret)_[A-Za-z0-9._\-]{8,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9._\-]{8,}\b"),
)


def redact_secrets(text: str) -> str:
    cleaned = text
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("[REDACTED]", cleaned)
    return cleaned


def set_meeting_id(meeting_id: str) -> None:
    meeting_id_var.set(meeting_id)


class MeetingIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.meeting_id = meeting_id_var.get()
        if isinstance(record.msg, str):
            record.msg = redact_secrets(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: redact_secrets(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    redact_secrets(a) if isinstance(a, str) else a for a in record.args
                )
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
