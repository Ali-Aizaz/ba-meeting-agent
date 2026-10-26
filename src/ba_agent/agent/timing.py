"""One log line per finished trigger. Nothing waits on this."""

from __future__ import annotations

import logging

logger = logging.getLogger("ba_agent.agent")


def log_timing(
    *,
    caption_commit_ms: int,
    model_start_ms: int,
    tool_ms: int,
    chat_posted_ms: int,
) -> None:
    logger.info(
        "trigger timing caption_commit_ms=%d model_start_ms=%d tool_ms=%d chat_posted_ms=%d",
        caption_commit_ms,
        model_start_ms,
        tool_ms,
        chat_posted_ms,
    )
