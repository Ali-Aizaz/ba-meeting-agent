"""Playwright Google Meet adapter. Guest join is the fallback when sign-in is blocked."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from ba_agent.bot.chat_parse import ChatReader
from ba_agent.bot.google_meet.scripts import PAGE_OBSERVER_JS
from ba_agent.bot.google_meet.selectors import (
    CAM_ON_ACTION,
    CAPTION_REGION,
    CHAT_BOX,
    CHAT_BUTTON,
    ENDED,
    JOIN_BUTTONS,
    LEAVE_CALL,
    MIC_OFF_STATE,
    MIC_ON_ACTION,
    NAME_LABEL,
    PARTICIPANT,
    PARTICIPANT_SELF,
    TURN_OFF_CAPTIONS,
    TURN_ON_CAPTIONS,
)
from ba_agent.captions.stabilize import CaptionRow
from ba_agent.config import Settings, resolve_path
from ba_agent.models import ChatMessage, PostReason

logger = logging.getLogger("ba_agent.meet")


class MeetDisconnected(RuntimeError):
    """The browser left the call before we asked it to."""


def should_leave_alone(others: int | None, alone_for_s: float, timeout_s: float) -> bool:
    """Leave only when we can see that nobody else is in the call."""
    if others is None or others > 0:
        return False
    return alone_for_s >= timeout_s


class GoogleMeetAdapter:
    def __init__(self, settings: Settings, profile_dir: Path | None = None) -> None:
        self.settings = settings
        self.profile_dir = Path(profile_dir or resolve_path(settings.browser.profile_dir))
        self.chat_enabled = True
        self.captions_enabled = False
        self.ended_event = asyncio.Event()
        self._admitted_at: float | None = None
        self._left = False
        self._chat = ChatReader(settings.meeting.bot_name)
        self._playwright = None
        self._context = None
        self.page = None

    def ended(self) -> asyncio.Event:
        return self.ended_event

    def meeting_seconds(self) -> float:
        if self._admitted_at is None:
            return 0.0
        return time.monotonic() - self._admitted_at

    async def join(self, url: str) -> None:
        from playwright.async_api import async_playwright

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        args = [
            "--use-fake-ui-for-media-stream",
            "--autoplay-policy=no-user-gesture-required",
            "--disable-dev-shm-usage",
        ]
        if self.settings.browser.no_sandbox:
            args.append("--no-sandbox")
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=self.settings.browser.headless,
            args=args,
            viewport={"width": 1280, "height": 720},
        )
        if self._context.pages:
            self.page = self._context.pages[0]
        else:
            self.page = await self._context.new_page()
        await self.page.add_init_script(PAGE_OBSERVER_JS)
        await self.page.goto(url, wait_until="domcontentloaded")
        await self._mute()
        await self._fill_name()
        await self._click_join()
        admitted = await self._wait_until_admitted()
        if not admitted:
            await self.screenshot("lobby-timeout")
            raise TimeoutError(
                f"Not admitted within {self.settings.meeting.lobby_timeout_s}s"
            )
        self._admitted_at = time.monotonic()
        logger.info("admitted as %s", self.settings.meeting.bot_name)

    async def leave(self) -> None:
        if self._left:
            return
        self._left = True
        self.ended_event.set()
        try:
            if self.page is not None:
                button = self.page.get_by_role("button", name=LEAVE_CALL)
                if await button.count():
                    await button.first.click()
        except Exception:
            logger.info("leave click skipped")
        await self.close()

    async def close(self) -> None:
        try:
            if self._context is not None:
                await self._context.close()
        except Exception:
            logger.info("browser context already closed")
        try:
            if self._playwright is not None:
                await self._playwright.stop()
        except Exception:
            logger.info("playwright already stopped")
        self._context = None
        self._playwright = None

    async def enable_captions(self) -> bool:
        button = self.page.get_by_role("button", name=TURN_ON_CAPTIONS)
        if await button.count():
            await button.first.click()
        else:
            await self.page.keyboard.press("c")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if await self.page.get_by_role("button", name=TURN_OFF_CAPTIONS).count():
                self.captions_enabled = True
                logger.info("captions_enabled: true")
                return True
            if await self.page.locator(CAPTION_REGION).count():
                self.captions_enabled = True
                logger.info("captions_enabled: true")
                return True
            await asyncio.sleep(0.2)
        self.captions_enabled = False
        logger.info("captions_enabled: false")
        await self.screenshot("captions-failed")
        return False

    async def post_chat(self, text: str, reason: PostReason) -> bool:
        if not isinstance(reason, PostReason):
            return False
        box = self.page.get_by_role("textbox", name=CHAT_BOX)
        if await box.count() == 0:
            opener = self.page.get_by_role("button", name=CHAT_BUTTON)
            if await opener.count():
                await opener.first.click()
        if await box.count() == 0:
            self.chat_enabled = False
            logger.info("chat_enabled: false")
            await self.screenshot("chat-disabled")
            return False
        self.chat_enabled = True
        target = box.first
        await target.click()
        lines = text.split("\n")
        await target.fill("")
        for index, line in enumerate(lines):
            if line:
                await target.type(line)
            if index < len(lines) - 1:
                await target.press("Shift+Enter")
        await target.press("Enter")
        return True

    async def collect_chat(self) -> list[ChatMessage]:
        nodes = await self.page.evaluate(
            "() => window.__baDrainChat ? window.__baDrainChat() : []"
        )
        return self._chat.consume(nodes or [])

    async def drain_caption_frames(self) -> list[list[CaptionRow]]:
        raw = await self.page.evaluate(
            "() => window.__baDrainCaptions ? window.__baDrainCaptions() : []"
        )
        frames: list[list[CaptionRow]] = []
        for item in raw or []:
            frames.append(
                [
                    CaptionRow(
                        row_id=str(row.get("row_id") or ""),
                        speaker=row.get("speaker") or "",
                        text=row.get("text") or "",
                    )
                    for row in item.get("items") or []
                ]
            )
        return frames

    async def other_participant_count(self) -> int | None:
        count = await self.page.locator(PARTICIPANT).count()
        if count == 0:
            return None
        self_count = await self.page.locator(PARTICIPANT_SELF).count()
        return max(0, count - self_count)

    async def meeting_closed(self) -> bool:
        if self.page is None:
            return True
        try:
            return await self.page.locator(ENDED).count() > 0
        except Exception:
            return True

    async def mic_is_off(self) -> bool:
        off = await self.page.get_by_role("button", name=MIC_OFF_STATE).count()
        on = await self.page.get_by_role("button", name=MIC_ON_ACTION).count()
        return off > 0 and on == 0

    async def screenshot(self, label: str) -> None:
        if self.page is None:
            return
        folder = resolve_path("data/debug")
        folder.mkdir(parents=True, exist_ok=True)
        await self.page.screenshot(path=str(folder / f"{label}.png"))

    async def chat_messages(self):
        while not self.ended_event.is_set():
            for message in await self.collect_chat():
                yield message
            await asyncio.sleep(0.3)

    async def _mute(self) -> None:
        for label in (MIC_ON_ACTION, CAM_ON_ACTION):
            button = self.page.get_by_role("button", name=label)
            if await button.count():
                await button.first.click()

    async def _fill_name(self) -> None:
        if self.settings.meeting.join_mode != "guest":
            return
        field = self.page.get_by_label(NAME_LABEL)
        if await field.count():
            await field.first.fill(self.settings.meeting.bot_name)

    async def _click_join(self) -> None:
        for label in JOIN_BUTTONS:
            button = self.page.get_by_role("button", name=label)
            if await button.count():
                await button.first.click()
                return
        raise RuntimeError("Join button not found")

    async def _wait_until_admitted(self) -> bool:
        timeout_ms = self.settings.meeting.lobby_timeout_s * 1000
        try:
            await self.page.get_by_role("button", name=LEAVE_CALL).wait_for(
                state="visible",
                timeout=timeout_ms,
            )
            return True
        except Exception:
            return False
