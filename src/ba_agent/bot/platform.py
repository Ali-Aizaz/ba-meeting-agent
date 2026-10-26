"""The meeting platform the rest of the agent talks to."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Protocol

from ba_agent.models import ChatMessage, PostReason


class MeetingPlatform(Protocol):
    async def join(self, url: str) -> None: ...

    async def leave(self) -> None: ...

    async def enable_captions(self) -> bool: ...

    def chat_messages(self) -> AsyncIterator[ChatMessage]: ...

    async def post_chat(self, text: str, reason: PostReason) -> bool: ...

    def ended(self) -> asyncio.Event: ...
