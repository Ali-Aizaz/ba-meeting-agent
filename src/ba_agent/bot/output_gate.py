"""Chat posts need a reason. One reply is allowed per trigger."""

from __future__ import annotations

from ba_agent.models import PostReason


class OutputGate:
    def __init__(self) -> None:
        self._replied: set[str] = set()

    def allow(self, reason: PostReason | None, trigger_id: str | None = None) -> bool:
        if not isinstance(reason, PostReason):
            return False
        if reason is PostReason.REPLY:
            if not trigger_id or trigger_id in self._replied:
                return False
            self._replied.add(trigger_id)
        return True
