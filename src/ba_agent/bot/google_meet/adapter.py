"""Playwright Google Meet adapter. Guest join is the fallback when sign-in is blocked."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
import zlib
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ba_agent.bot.chat_parse import ChatReader
from ba_agent.bot.google_meet.scripts import PAGE_OBSERVER_JS, REJECT_INCOMING_VIDEO_JS
from ba_agent.bot.google_meet.selectors import (
    AUDIO_ONLY,
    CAM_OFF_STATE,
    CAM_ON_ACTION,
    CAPTION_REGION,
    CHAT_BOX,
    CHAT_BUTTON,
    DISMISS_BUTTONS,
    ENDED,
    JOIN_BUTTONS,
    LEAVE_CALL,
    LOBBY_TEXTS,
    MIC_OFF_STATE,
    MIC_ON_ACTION,
    MORE_OPTIONS,
    NAME_LABEL,
    PARTICIPANT,
    PARTICIPANT_SELF,
    RECEIVE_RESOLUTION,
    REJECTED_TEXTS,
    SETTINGS_ITEM,
    TURN_OFF_CAPTIONS,
    TURN_ON_CAPTIONS,
    VIDEO_TAB,
)
from ba_agent.captions.stabilize import CaptionRow
from ba_agent.config import Settings, resolve_path, validate_meet_url
from ba_agent.models import ChatMessage, PostReason

logger = logging.getLogger("ba_agent.meet")

_PERSISTENT_BROWSERS: dict[str, tuple[Any, Any]] = {}
_SEEDED_PROFILES: set[str] = set()


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
        self._ui_lock = asyncio.Lock()
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

    def _profile_key(self) -> str:
        return str(self.profile_dir.resolve())

    def _should_keep_browser(self) -> bool:
        return bool(self.settings.browser.keep_alive) and "PYTEST_CURRENT_TEST" not in os.environ

    def _cleanup_stale_locks(self) -> None:
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            lock_path = self.profile_dir / name
            if lock_path.exists() or lock_path.is_symlink():
                try:
                    lock_path.unlink()
                except OSError:
                    pass

    async def _ensure_browser(self) -> None:
        from playwright.async_api import async_playwright

        key = self._profile_key()
        if self._context is None and self._should_keep_browser() and key in _PERSISTENT_BROWSERS:
            cached_pw, cached_ctx = _PERSISTENT_BROWSERS[key]
            try:
                _ = cached_ctx.pages
                self._playwright = cached_pw
                self._context = cached_ctx
            except Exception:
                _PERSISTENT_BROWSERS.pop(key, None)

        if self._context is None:
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            self._cleanup_stale_locks()
            self._playwright = await async_playwright().start()
            args = [
                "--disable-blink-features=AutomationControlled",
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
                "--autoplay-policy=no-user-gesture-required",
                "--disable-dev-shm-usage",
            ]
            if self.settings.browser.no_sandbox:
                args.append("--no-sandbox")
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=self.settings.browser.headless,
                ignore_default_args=["--enable-automation"],
                args=args,
                viewport={"width": 1280, "height": 720},
            )
            if not self.settings.browser.receive_video:
                await self._context.add_init_script(REJECT_INCOMING_VIDEO_JS)
            await self._context.add_init_script(PAGE_OBSERVER_JS)
            if self._should_keep_browser():
                _PERSISTENT_BROWSERS[key] = (self._playwright, self._context)

        await self._seed_google_cookies()
        if self._context.pages:
            self.page = self._context.pages[0]
            for extra in list(self._context.pages[1:]):
                try:
                    await extra.close()
                except Exception:
                    pass
        else:
            self.page = await self._context.new_page()

    async def _seed_google_cookies(self) -> None:
        if self._context is None:
            return
        key = self._profile_key()
        if key in _SEEDED_PROFILES:
            return
        secret = self.settings.meeting.google_cookies_b64
        raw = secret.get_secret_value().strip() if secret is not None else ""
        if not raw:
            return
        try:
            if raw.startswith("z:"):
                decoded = zlib.decompress(base64.b64decode(raw[2:])).decode("utf-8")
            else:
                decoded = base64.b64decode(raw).decode("utf-8")
            cookies = json.loads(decoded)
            if isinstance(cookies, list) and cookies:
                await self._context.add_cookies(cookies)
                _SEEDED_PROFILES.add(key)
                logger.info("seeded %s Google cookies into browser profile", len(cookies))
        except Exception:
            logger.warning("failed to seed Google cookies from BA_MEETING__GOOGLE_COOKIES_B64")

    async def _has_google_auth_cookies(self) -> bool:
        if self._context is None:
            return False
        try:
            cookies = await self._context.cookies(
                ["https://google.com", "https://accounts.google.com", "https://meet.google.com"]
            )
            names = {str(c.get("name") or "") for c in cookies}
            return bool(names & {"SID", "__Secure-1PSID", "__Secure-3PSID"})
        except Exception:
            return False

    def _google_credentials(self) -> tuple[str, str]:
        email = (self.settings.meeting.google_email or "").strip()
        pwd_secret = self.settings.meeting.google_password
        password = pwd_secret.get_secret_value().strip() if pwd_secret is not None else ""
        return email, password

    async def _perform_google_login(self, continue_url: str = "https://meet.google.com/") -> bool:
        if self.page is None:
            return False
        email, password = self._google_credentials()
        if not email or not password:
            return False
        login_url = (
            "https://accounts.google.com/ServiceLogin?service=wise&continue="
            + quote(continue_url, safe="")
        )
        logger.info("signing in to Google account %s", email)
        try:
            await self.page.goto(login_url, wait_until="domcontentloaded")
            await asyncio.sleep(1.5)
            chooser_item = self.page.locator(
                f'[data-identifier="{email}"], [data-email="{email}"]'
            )
            if await chooser_item.count() > 0 and await chooser_item.first.is_visible():
                await chooser_item.first.click()
                await asyncio.sleep(1.5)
            email_input = self.page.locator("input#identifierId, input[type='email']")
            if await email_input.count() == 0:
                try:
                    await email_input.first.wait_for(state="visible", timeout=5000)
                except Exception:
                    pass
            if await email_input.count() > 0 and await email_input.first.is_visible():
                await email_input.first.fill(email)
                await email_input.first.press("Enter")
            pwd_input = self.page.locator("input[name='Passwd'], input[type='password']")
            try:
                await pwd_input.first.wait_for(state="visible", timeout=12000)
            except Exception:
                pass
            if await pwd_input.count() > 0 and await pwd_input.first.is_visible():
                await pwd_input.first.fill(password)
                await pwd_input.first.press("Enter")
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                cur_url = self.page.url or ""
                if cur_url.startswith("https://meet.google.com/"):
                    await asyncio.sleep(1.0)
                    logger.info("google sign-in succeeded for %s", email)
                    return True
                not_now = self.page.locator(
                    'button:has-text("Not now"), [role="button"]:has-text("Not now"), span:has-text("Not now")'
                )
                if await not_now.count() > 0:
                    try:
                        await not_now.first.click(timeout=2000)
                        await asyncio.sleep(1.0)
                        continue
                    except Exception:
                        pass
                if (
                    not cur_url.startswith("https://accounts.google.com/")
                    and not cur_url.startswith("https://myaccount.google.com/")
                    and cur_url.startswith("https://")
                    and await self._has_google_auth_cookies()
                ):
                    logger.info("google sign-in succeeded for %s", email)
                    return True
                await asyncio.sleep(0.5)
        except Exception:
            logger.exception("google sign-in failed for %s", email)
        return await self._has_google_auth_cookies()

    async def _ensure_google_login(self, url: str) -> None:
        if not url.startswith("https://meet.google.com/"):
            return
        email, password = self._google_credentials()
        if self.settings.meeting.join_mode != "signed_in" and not (email and password):
            return
        if not await self._has_google_auth_cookies() and email and password:
            await self._perform_google_login(url)

    async def join(self, url: str) -> None:
        url = validate_meet_url(url)
        self._left = False
        self._admitted_at = None
        await self._ensure_browser()
        await self._ensure_google_login(url)
        if not (self.page.url or "").startswith("https://meet.google.com/"):
            await self.page.goto(url, wait_until="domcontentloaded")
        else:
            try:
                await self.page.goto(url, wait_until="domcontentloaded")
            except Exception:
                pass
        await self._wait_for_prejoin()
        email, password = self._google_credentials()
        if (
            url.startswith("https://meet.google.com/")
            and email
            and password
            and await self.page.get_by_label(NAME_LABEL).count() > 0
        ):
            # Cookies expired or were cleared; sign in and reload the meeting pre-join page.
            if await self._perform_google_login(url):
                if not (self.page.url or "").startswith("https://meet.google.com/"):
                    await self.page.goto(url, wait_until="domcontentloaded")
                await self._wait_for_prejoin()
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
        await self._mute()
        logger.info("admitted as %s", self.settings.meeting.bot_name)
        if not self.settings.browser.receive_video:
            await self._stop_incoming_video()

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
        if self._should_keep_browser():
            try:
                if self.page is not None:
                    await self.page.goto("about:blank")
            except Exception:
                pass
            return
        await self.close()

    async def close(self, *, force: bool = True) -> None:
        if not force and self._should_keep_browser():
            try:
                if self.page is not None:
                    await self.page.goto("about:blank")
            except Exception:
                pass
            return
        key = self._profile_key()
        _PERSISTENT_BROWSERS.pop(key, None)
        _SEEDED_PROFILES.discard(key)
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
        self.page = None

    async def enable_captions(self) -> bool:
        await self._dismiss_incall_overlays()
        await self._mute()
        if await self.page.get_by_role("button", name=TURN_OFF_CAPTIONS).count():
            self.captions_enabled = True
            logger.info("captions_enabled: true")
            return True
        await self._trigger_captions_toggle()
        deadline = time.monotonic() + 15
        next_retry = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if await self.page.get_by_role("button", name=TURN_OFF_CAPTIONS).count():
                self.captions_enabled = True
                logger.info("captions_enabled: true")
                return True
            if await self.page.locator(CAPTION_REGION).count():
                self.captions_enabled = True
                logger.info("captions_enabled: true")
                return True
            if time.monotonic() >= next_retry:
                await self._dismiss_incall_overlays()
                await self._trigger_captions_toggle()
                next_retry = time.monotonic() + 3.0
            await asyncio.sleep(0.2)
        self.captions_enabled = False
        logger.info("captions_enabled: false")
        await self.screenshot("captions-failed")
        return False

    async def _trigger_captions_toggle(self) -> None:
        button = self.page.get_by_role("button", name=TURN_ON_CAPTIONS).or_(
            self.page.locator(
                f'button[aria-label*="{TURN_ON_CAPTIONS}" i], '
                f'[role="button"][aria-label*="{TURN_ON_CAPTIONS}" i]'
            )
        )
        if await button.count():
            try:
                await button.first.click(timeout=2000)
                return
            except Exception:
                try:
                    await button.first.click(force=True, timeout=1500)
                    return
                except Exception:
                    pass
        try:
            await self.page.keyboard.press("c")
        except Exception:
            pass

    def _chat_box(self):
        return self.page.get_by_role("textbox", name=CHAT_BOX).or_(
            self.page.locator(
                'textarea[aria-label*="Send a message" i], '
                'textarea[placeholder*="Send a message" i], '
                'input[aria-label*="Send a message" i], '
                '[role="textbox"][aria-label*="Send a message" i], '
                '[contenteditable="true"][aria-label*="Send a message" i]'
            )
        )

    async def post_chat(self, text: str, reason: PostReason) -> bool:
        if not isinstance(reason, PostReason):
            return False
        async with self._ui_lock:
            try:
                await self._dismiss_incall_overlays()
                box = self._chat_box()
                if await box.count() == 0:
                    opener = self.page.get_by_role("button", name=CHAT_BUTTON).or_(
                        self.page.locator(
                            f'button[aria-label*="{CHAT_BUTTON}" i], '
                            f'[role="button"][aria-label*="{CHAT_BUTTON}" i]'
                        )
                    )
                    if await opener.count():
                        try:
                            await opener.first.click(timeout=2500)
                        except Exception:
                            await opener.first.click(force=True, timeout=1500)
                        deadline = time.monotonic() + 5.0
                        while time.monotonic() < deadline:
                            if await box.count() > 0:
                                try:
                                    if (
                                        await box.first.is_visible()
                                        and await box.first.is_enabled()
                                    ):
                                        break
                                except Exception:
                                    break
                            await asyncio.sleep(0.2)
                if await box.count() == 0:
                    self.chat_enabled = False
                    logger.info("chat_enabled: false")
                    await self.screenshot("chat-disabled")
                    return False
                self.chat_enabled = True
                target = box.first
                try:
                    await target.click(timeout=2500)
                except Exception:
                    await target.click(force=True, timeout=1500)
                lines = text.split("\n")
                if len(lines) == 1:
                    await target.fill(lines[0])
                    await target.press("Enter")
                    return True
                await target.fill("")
                keyboard = getattr(self.page, "keyboard", None)
                insert_text = getattr(keyboard, "insert_text", None)
                for index, line in enumerate(lines):
                    if line:
                        if callable(insert_text):
                            await insert_text(line)
                        else:
                            await target.type(line)
                    if index < len(lines) - 1:
                        await target.press("Shift+Enter")
                await target.press("Enter")
                return True
            except Exception:
                logger.warning("post_chat failed", exc_info=True)
                return False

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
        if self_count > 0:
            return max(0, count - self_count)
        return max(0, count - 1)

    async def meeting_closed(self) -> bool:
        if self.page is None:
            return True
        try:
            if await self.page.locator(ENDED).count() > 0:
                return True
            if self._admitted_at is not None:
                if await self.page.get_by_role("button", name=LEAVE_CALL).count() > 0:
                    return False
                if await self.page.get_by_role("button", name=TURN_ON_CAPTIONS).count() > 0:
                    return False
                if await self.page.get_by_role("button", name=TURN_OFF_CAPTIONS).count() > 0:
                    return False
                if (
                    await self.page.locator(
                        'button[aria-label*="Leave call" i], '
                        'button[aria-label*="captions" i]'
                    ).count()
                    > 0
                ):
                    return False
                return True
            return False
        except Exception:
            return True

    async def mic_is_off(self) -> bool:
        off = await self.page.get_by_role("button", name=MIC_OFF_STATE).count()
        on = await self.page.get_by_role("button", name=MIC_ON_ACTION).count()
        return off > 0 and on == 0

    async def cam_is_off(self) -> bool:
        off = await self.page.get_by_role("button", name=CAM_OFF_STATE).count()
        on = await self.page.get_by_role("button", name=CAM_ON_ACTION).count()
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

    async def _stop_incoming_video(self) -> None:
        """Ask Meet for audio only. The page script already marks video inactive."""
        if self.page is None:
            return
        if (self.page.url or "").startswith("https://meet.google.com/"):
            logger.info("incoming_video: inactive")
            return
        more = self.page.get_by_role("button", name=MORE_OPTIONS, exact=True)
        if await more.count() == 0:
            logger.info("incoming_video: inactive")
            return
        try:
            await more.first.click(timeout=1000)
            item = self.page.get_by_role("menuitem", name=SETTINGS_ITEM, exact=True)
            if await item.count() == 0:
                raise RuntimeError("settings item missing")
            await item.first.click(timeout=1000)
            tab = self.page.get_by_role("tab", name=VIDEO_TAB, exact=True)
            if await tab.count() == 0:
                tab = self.page.get_by_role("button", name=VIDEO_TAB, exact=True)
            await tab.first.click(timeout=1000)
            box = self.page.get_by_role("combobox", name=re.compile(RECEIVE_RESOLUTION, re.I))
            if await box.count() == 0:
                box = self.page.get_by_role("button", name=re.compile(RECEIVE_RESOLUTION, re.I))
            await box.first.click(timeout=1000)
            option = self.page.get_by_role("option", name=re.compile(rf"^{AUDIO_ONLY}$", re.I))
            await option.first.click(timeout=1000)
            logger.info("incoming_video: audio-only")
        except Exception:
            logger.info("incoming_video: inactive")
        await self._close_meet_menus()

    async def _close_meet_menus(self) -> None:
        if self.page is None:
            return
        for _ in range(2):
            try:
                await self.page.keyboard.press("Escape")
            except Exception:
                return

    async def _dismiss_incall_overlays(self) -> None:
        if self.page is None:
            return
        await self._dismiss_prejoin_prompts()
        try:
            dialog_close = self.page.locator(
                '[role="dialog"] button[aria-label="Close" i], '
                '[aria-modal="true"] button[aria-label="Close" i]'
            )
            if await dialog_close.count() > 0 and await dialog_close.first.is_visible():
                await dialog_close.first.click(timeout=500)
                await asyncio.sleep(0.15)
        except Exception:
            pass
        try:
            menu = self.page.locator('[role="menu"]')
            if await menu.count() > 0 and await menu.first.is_visible():
                await self.page.keyboard.press("Escape")
                await asyncio.sleep(0.15)
        except Exception:
            pass

    async def _dismiss_prejoin_prompts(self) -> None:
        if self.page is None:
            return
        for dismiss in DISMISS_BUTTONS:
            btn = self.page.get_by_role("button", name=dismiss)
            if await btn.count():
                try:
                    await btn.first.click(timeout=500)
                    await asyncio.sleep(0.2)
                except Exception:
                    pass

    async def _wait_for_prejoin(self) -> None:
        started = time.monotonic()
        deadline = started + 25
        while time.monotonic() < deadline:
            await self._dismiss_prejoin_prompts()
            leave_btn = self.page.locator('button[aria-label*="Leave call" i]')
            if (
                await leave_btn.count() > 0
                and await self.page.locator("#prejoin").count() == 0
            ):
                try:
                    if await leave_btn.first.is_visible():
                        return
                except Exception:
                    pass
            name_field = self.page.get_by_label(NAME_LABEL)
            if await name_field.count() > 0:
                try:
                    if await name_field.first.is_visible():
                        return
                except Exception:
                    return
            has_enabled_join = False
            for label in JOIN_BUTTONS:
                btn = self.page.get_by_role("button", name=label)
                if await btn.count():
                    try:
                        if await btn.first.is_enabled():
                            has_enabled_join = True
                            break
                    except Exception:
                        has_enabled_join = True
                        break
            if has_enabled_join:
                has_media = (
                    await self.page.get_by_role("button", name=MIC_ON_ACTION).count() > 0
                    or await self.page.get_by_role("button", name=MIC_OFF_STATE).count() > 0
                    or await self.page.locator("#prejoin").count() > 0
                )
                if has_media or time.monotonic() - started >= 2.5:
                    return
            await asyncio.sleep(0.25)

    async def _mute(self) -> None:
        await self._dismiss_prejoin_prompts()
        for label in (MIC_ON_ACTION, CAM_ON_ACTION):
            locator = self.page.get_by_role("button", name=label).or_(
                self.page.locator(
                    f'button[aria-label*="{label}" i], [role="button"][aria-label*="{label}" i]'
                )
            )
            count = await locator.count()
            for idx in range(count):
                candidate = locator.nth(idx)
                try:
                    if await candidate.is_visible():
                        await candidate.click(timeout=1500)
                        await asyncio.sleep(0.15)
                        break
                except Exception:
                    continue

    async def _fill_name(self) -> None:
        field = self.page.get_by_label(NAME_LABEL)
        if await field.count():
            try:
                if await field.first.is_visible():
                    await field.first.fill(self.settings.meeting.bot_name)
            except Exception:
                pass

    async def _click_join(self) -> None:
        await self._dismiss_prejoin_prompts()
        await self._fill_name()
        await self._mute()
        for label in JOIN_BUTTONS:
            button = self.page.get_by_role("button", name=label)
            if await button.count():
                await button.first.click()
                return
        leave_btn = self.page.locator('button[aria-label*="Leave call" i]')
        if await leave_btn.count() > 0:
            try:
                if await leave_btn.first.is_visible():
                    return
            except Exception:
                return
        raise RuntimeError("Join button not found")

    async def _wait_until_admitted(self) -> bool:
        started = time.monotonic()
        deadline = started + self.settings.meeting.lobby_timeout_s
        while time.monotonic() < deadline:
            try:
                if await self.page.locator("[data-ba-in-call='true']").count() > 0:
                    return True
                body_text = ""
                try:
                    body_text = await self.page.inner_text("body")
                except Exception:
                    body_text = ""
                for rejected in REJECTED_TEXTS:
                    if rejected in body_text:
                        logger.warning("Meet rejected join: %s", rejected)
                        return False
                in_lobby = any(lobby in body_text for lobby in LOBBY_TEXTS)
                if not in_lobby:
                    has_captions_btn = (
                        await self.page.get_by_role("button", name=TURN_ON_CAPTIONS).count() > 0
                        or await self.page.get_by_role("button", name=TURN_OFF_CAPTIONS).count() > 0
                    )
                    if has_captions_btn:
                        return True
                    is_real_meet = (self.page.url or "").startswith("https://meet.google.com/")
                    if not is_real_meet:
                        leave_btn = self.page.get_by_role("button", name=LEAVE_CALL)
                        if await leave_btn.count() > 0 and await leave_btn.first.is_visible():
                            return True
            except Exception:
                pass
            await asyncio.sleep(0.3)
        return False
