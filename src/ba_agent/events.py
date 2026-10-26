"""Small async event bus."""

from __future__ import annotations

import inspect
from collections import defaultdict


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list] = defaultdict(list)

    def subscribe(self, event: str, callback) -> None:
        self._subs[event].append(callback)

    async def publish(self, event: str, payload) -> None:
        for callback in list(self._subs[event]):
            result = callback(payload)
            if inspect.isawaitable(result):
                await result
