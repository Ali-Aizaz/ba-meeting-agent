"""Poll the page and commit stable caption lines."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ba_agent.captions.stabilize import CaptionRow, RawCaption, Stabilizer


class CaptionPump:
    def __init__(
        self,
        drain: Callable[[], Awaitable[list[list[CaptionRow]]]],
        stabilizer: Stabilizer,
    ) -> None:
        self.drain = drain
        self.stabilizer = stabilizer

    async def poll(self, now: float) -> list[RawCaption]:
        frames = await self.drain()
        committed: list[RawCaption] = []
        for frame in frames:
            committed.extend(self.stabilizer.push_frame(frame, now))
        committed.extend(self.stabilizer.push_frame([], now))
        return committed
