"""One meeting: join, captions, Hey BA, overheard tasks, save on the way out."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone

from ba_agent.agent.loop import AgentLoop
from ba_agent.bot.google_meet.adapter import GoogleMeetAdapter, should_leave_alone
from ba_agent.bot.output_gate import OutputGate
from ba_agent.captions.clock import format_meeting_time
from ba_agent.captions.pump import CaptionPump
from ba_agent.captions.stabilize import Stabilizer
from ba_agent.config import Settings, ensure_ready, read_prompt, resolve_path
from ba_agent.events import EventBus
from ba_agent.guardrails.search import query_allowed, strip_meeting_text
from ba_agent.knowledge.search import KnowledgeIndex
from ba_agent.llm import build_llm
from ba_agent.logging_setup import configure_logging, set_meeting_id
from ba_agent.memory.store import MeetingStore, open_meeting
from ba_agent.models import CaptionLine, PostReason, TaskRecord, TriggerEvent, WebResult
from ba_agent.notion.client import NotionService, TaskWrite
from ba_agent.notion.messages import notes_saved, task_failed
from ba_agent.notion.schema import load_notion_schema
from ba_agent.speakers import is_bot_speaker, load_aliases, normalize_speaker
from ba_agent.status import StatusState, start_status_server
from ba_agent.tasks_extract.dates import today_in
from ba_agent.tasks_extract.extractor import (
    apply_overheard,
    is_addressed_line,
    might_contain_task,
)
from ba_agent.trigger.detector import TriggerDetector

logger = logging.getLogger("ba_agent.session")
UTC = timezone.utc


def _let_cleanup_finish() -> None:
    """Drop a SIGTERM cancel so the awaits that save notes and leave Meet still run."""
    task = asyncio.current_task()
    if task is None:
        return
    cancelling = getattr(task, "cancelling", None)
    if cancelling is not None:
        while cancelling():
            task.uncancel()


class SessionPoster:
    def __init__(
        self,
        adapter: GoogleMeetAdapter,
        gate: OutputGate,
        store: MeetingStore | None = None,
    ) -> None:
        self.adapter = adapter
        self.gate = gate
        self.store = store

    async def post(self, text: str, reason: PostReason, trigger_id: str | None = None) -> bool:
        if not self.gate.allow(reason, trigger_id):
            return False
        posted = await self.adapter.post_chat(text, reason)
        if posted and self.store is not None and reason in {
            PostReason.REPLY,
            PostReason.CONFIRMATION,
        }:
            self.store.add_reply(text)
        return posted


async def run_join(settings: Settings, url: str) -> None:
    configure_logging()
    ensure_ready(settings)
    loop = asyncio.get_running_loop()
    next_url_event = asyncio.Event()
    queued_url: list[str] = []
    current_url = url
    adapter = GoogleMeetAdapter(settings)

    def on_join_request(new_url: str) -> None:
        def _schedule() -> None:
            queued_url.clear()
            queued_url.append(new_url)
            next_url_event.set()
            if new_url != current_url or adapter._admitted_at is None:
                adapter.ended_event.set()

        loop.call_soon_threadsafe(_schedule)

    status = StatusState("", on_join=on_join_request)
    server = start_status_server(status, settings.server.status_host, settings.server.status_port)
    keep_alive = bool(settings.browser.keep_alive) and "PYTEST_CURRENT_TEST" not in os.environ
    try:
        while True:
            next_url_event.clear()
            adapter.ended_event = asyncio.Event()
            adapter._left = False
            adapter._admitted_at = None
            try:
                await _run_single_meeting(settings, adapter, status, current_url)
            except asyncio.CancelledError:
                raise
            except Exception:
                if not keep_alive:
                    raise
                logger.exception(
                    "meeting session for %s ended with error; browser stays alive",
                    current_url,
                )
            if not keep_alive:
                break
            status.captions_enabled = False
            status.chat_enabled = False
            status.stage = "idle"
            if not queued_url:
                logger.info("browser kept alive and signed in; waiting for next meeting on /join")
                await next_url_event.wait()
            if queued_url:
                current_url = queued_url.pop(0)
    finally:
        _let_cleanup_finish()
        await adapter.close(force=True)
        if server is not None:
            server.shutdown()


async def _run_single_meeting(
    settings: Settings,
    adapter: GoogleMeetAdapter,
    status: StatusState,
    url: str,
) -> None:
    store = open_meeting(resolve_path(settings.storage.sqlite_path), url)
    set_meeting_id(store.meeting_id)
    status.meeting_id = store.meeting_id
    status.captions_enabled = False
    status.chat_enabled = False
    status.stage = "joining"
    status.last_error = ""
    notion = _build_notion(settings, store)
    poster = SessionPoster(adapter, OutputGate(), store)
    llm = None
    joined = False
    try:
        if notion and notion.configured:
            await notion.verify()
        llm = build_llm(settings)
        knowledge = KnowledgeIndex(
            resolve_path(settings.knowledge.dir), settings.knowledge.min_score
        )
        aliases = load_aliases(resolve_path(settings.speakers.aliases_file))
        trigger = TriggerDetector(
            pause_wait_s=settings.trigger.pause_wait_s,
            dedup_window_s=settings.trigger.dedup_window_s,
            followup_window_s=settings.trigger.followup_window_s,
            fuzzy_threshold=settings.trigger.fuzzy_threshold,
            extra_phrases=settings.trigger.phrases,
        )

        addressed_caption_ids: set[str] = set()

        async def guarded_search(query: str) -> WebResult:
            cleaned_query = " ".join(query.split()).lower()
            lines = [
                line.text
                for line in store.last(500)
                if line.id not in addressed_caption_ids
                and not is_addressed_line(line.text)
                and " ".join(line.text.split()).lower() != cleaned_query
            ]
            people = store.participants()
            if not query_allowed(query, lines, people):
                cleaned = strip_meeting_text(query, lines, people)
                if not cleaned or not query_allowed(cleaned, lines, people):
                    logger.info("search_cars rejected because the query contained meeting text")
                    return WebResult()
                query = cleaned
            return await llm.search_cars(query)

        async def create_task(record: TaskRecord):
            if notion is None or not notion.schema.tasks_db:
                return TaskWrite(False, task_failed(record.task), key=record.key)
            result = await notion.create_task(record)
            if result.created or result.page_id:
                record.notion_page_id = result.page_id
                record.key = result.key or record.key
                store.add_task(record)
            return result

        async def save_meeting() -> str:
            url = await _save(settings, store, notion, llm, announce=False, poster=poster)
            if url:
                store.set_meta("save_announced", "1")
            return url

        async def leave() -> None:
            adapter.ended_event.set()

        agent = AgentLoop(
            knowledge=knowledge,
            search_cars=guarded_search,
            llm=llm,
            create_task=create_task,
            save_meeting=save_meeting,
            post=poster.post,
            trigger=trigger,
            leave=leave,
            digest=store.digest,
            today=lambda: today_in(settings.meeting.timezone),
            min_score=settings.knowledge.min_score,
            timeout_s=settings.llm.timeout_s,
        )
        bus = EventBus()
        pump = CaptionPump(adapter.drain_caption_frames, Stabilizer(settings.captions.stable_ms))
        extractor = _Extractor(settings, llm, store, poster, notion)
        consent_posted = store.get_meta("consent_posted") == "1"
        attempts = 0
        while attempts < settings.browser.rejoin_limit:
            attempts += 1
            try:
                await adapter.join(url)
                joined = True
                status.stage = "in_call"
                status.last_error = ""
                if notion and notion.configured and not notion.summary_url:
                    title = f"TechSol BA {store.meeting_id}"
                    notes_url = await notion.ensure_summary_page(title)
                    store.set_meta("summary_page_id", notion.summary_page_id)
                    store.set_meta("summary_url", notes_url)
                if not consent_posted:
                    message = settings.meeting.consent_message
                    if notion and notion.summary_url:
                        message = f"{message} Notes will be here: {notion.summary_url}"
                    await poster.post(message, PostReason.CONSENT)
                    consent_posted = True
                    store.set_meta("consent_posted", "1")
                status.captions_enabled = await adapter.enable_captions()
                status.chat_enabled = adapter.chat_enabled
                logger.info(
                    "captions_enabled: %s chat_enabled: %s",
                    status.captions_enabled,
                    status.chat_enabled,
                )
                await _loops(
                    settings,
                    adapter,
                    pump,
                    store,
                    aliases,
                    trigger,
                    agent,
                    extractor,
                    bus,
                    addressed_caption_ids,
                )
                break
            except Exception as exc:
                status.last_error = f"{type(exc).__name__}: {exc}"
                status.stage = "retrying"
                logger.exception("meeting attempt %s failed", attempts)
                await adapter.close(force=False)
                adapter.ended_event = asyncio.Event()
                if attempts >= settings.browser.rejoin_limit:
                    status.stage = "failed"
                    raise
    finally:
        _let_cleanup_finish()
        try:
            if llm is not None and (
                joined or store.count_captions() > 0 or len(store.tasks()) > 0
            ):
                current_sig = f"{store.count_captions()}:{len(store.tasks())}"
                if (
                    store.get_meta("save_announced") != "1"
                    or store.get_meta("saved_sig") != current_sig
                ):
                    await _save(settings, store, notion, llm, announce=True, poster=poster)
        except Exception:
            logger.exception("final save failed")
        store.set_meta("closed", "1")
        await adapter.leave()
        if notion is not None:
            await notion.aclose()
        store.close()


async def _loops(
    settings,
    adapter,
    pump,
    store,
    aliases,
    trigger,
    agent,
    extractor,
    bus,
    addressed_caption_ids: set[str] | None = None,
) -> None:
    trigger_queue: asyncio.Queue[TriggerEvent | None] = asyncio.Queue()

    async def on_caption(raw) -> None:
        speaker = normalize_speaker(raw.speaker, aliases)
        if is_bot_speaker(speaker, settings.meeting.bot_name):
            return
        line = CaptionLine(
            id=uuid.uuid4().hex,
            meeting_id=store.meeting_id,
            speaker=speaker,
            speaker_raw=raw.speaker,
            start_s=raw.start_s,
            end_s=raw.end_s,
            wall_time=datetime.now(UTC),
            text=raw.text,
        )
        store.add_caption(line)
        await bus.publish("caption_committed", line)
        events = trigger.on_line(speaker, raw.text, "speech", raw.start_s, line.id)
        if addressed_caption_ids is not None and (
            events
            or is_addressed_line(raw.text)
            or getattr(trigger, "_pending", None) is not None
        ):
            addressed_caption_ids.add(line.id)
        for event in events:
            trigger_queue.put_nowait(event)
        if not events and not is_addressed_line(raw.text):
            extractor.note(line)

    async def captions() -> None:
        while not adapter.ended_event.is_set():
            if await adapter.meeting_closed():
                adapter.ended_event.set()
                return
            for raw in await pump.poll(adapter.meeting_seconds()):
                await on_caption(raw)
            await asyncio.sleep(0.3)

    async def chats() -> None:
        while not adapter.ended_event.is_set():
            for message in await adapter.collect_chat():
                if message.is_self:
                    continue
                now = adapter.meeting_seconds()
                for event in trigger.on_line(message.sender, message.text, "chat", now, None):
                    trigger_queue.put_nowait(event)
            await asyncio.sleep(0.3)

    async def ticks() -> None:
        while not adapter.ended_event.is_set():
            for event in trigger.tick(adapter.meeting_seconds()):
                trigger_queue.put_nowait(event)
            await asyncio.sleep(0.5)

    async def replies() -> None:
        while not adapter.ended_event.is_set():
            event = await trigger_queue.get()
            if event is None or adapter.ended_event.is_set():
                return
            await agent.handle(event)

    async def alone() -> None:
        alone_for = 0.0
        while not adapter.ended_event.is_set():
            others = await adapter.other_participant_count()
            alone_for = 0.0 if others != 0 else alone_for + 1
            if should_leave_alone(others, alone_for, settings.meeting.alone_timeout_s):
                logger.info("alone for %ss", settings.meeting.alone_timeout_s)
                adapter.ended_event.set()
                return
            await asyncio.sleep(1)

    poller_tasks = [
        asyncio.create_task(captions()),
        asyncio.create_task(chats()),
        asyncio.create_task(ticks()),
        asyncio.create_task(alone()),
    ]
    worker_tasks = [
        asyncio.create_task(replies()),
        asyncio.create_task(extractor.run()),
    ]
    try:
        await adapter.ended_event.wait()
    finally:
        _let_cleanup_finish()
        for task in poller_tasks:
            task.cancel()
        await asyncio.gather(*poller_tasks, return_exceptions=True)
        trigger_queue.put_nowait(None)
        extractor.stop()
        _done, pending = await asyncio.wait(worker_tasks, timeout=5.0)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


async def _save(settings, store, notion, llm, *, announce: bool, poster: SessionPoster) -> str:
    if notion is None or not notion.schema.summary_db:
        return ""
    lines = store.last(100000)
    tasks = store.tasks()
    transcript = "\n".join(
        f"{format_meeting_time(line.start_s)} {line.speaker}: {line.text}" for line in lines
    )
    summary = await _summarize(llm, transcript, settings.llm.summary_timeout_s)
    url = await notion.save_meeting(summary, transcript, tasks)
    store.set_meta("summary_page_id", notion.summary_page_id)
    store.set_meta("summary_url", url)
    store.set_meta("saved", "1")
    store.set_meta("saved_sig", f"{len(lines)}:{len(tasks)}")
    if announce and url and store.get_meta("save_announced") != "1":
        await poster.post(notes_saved(url), PostReason.MEETING_SAVED)
        store.set_meta("save_announced", "1")
    return url


async def _summarize(llm, transcript: str, timeout_s: float) -> str:
    prompt = read_prompt("summary.md") + "\n\n" + (transcript or "(empty transcript)")
    response = await llm.respond(prompt, enable_web_search=False, timeout_s=timeout_s)
    return response.text.strip() or "No summary."


def _build_notion(settings: Settings, store: MeetingStore) -> NotionService | None:
    if not settings.notion.enabled or settings.notion.token is None:
        return None
    schema = load_notion_schema(resolve_path(settings.notion.schema_file))
    if settings.notion.databases.summary:
        schema.summary_db = settings.notion.databases.summary
    if settings.notion.databases.tasks:
        schema.tasks_db = settings.notion.databases.tasks
    if settings.notion.version:
        schema.version = settings.notion.version
    service = NotionService(
        token=settings.notion.token.get_secret_value(),
        schema=schema,
        meeting_id=store.meeting_id,
        timezone_name=settings.meeting.timezone,
        summary_page_id=store.get_meta("summary_page_id"),
        summary_url=store.get_meta("summary_url"),
    )
    return service


class _Extractor:
    def __init__(self, settings, llm, store, poster, notion) -> None:
        self.settings = settings
        self.llm = llm
        self.store = store
        self.poster = poster
        self.notion = notion
        self.queue: asyncio.Queue = asyncio.Queue()
        self.recent: list[CaptionLine] = []
        self.seen: set[str] = set()
        self._stopped = False

    def note(self, line: CaptionLine) -> None:
        self.queue.put_nowait(line)

    def stop(self) -> None:
        self._stopped = True
        self.queue.put_nowait(None)

    async def run(self) -> None:
        while True:
            first = await self.queue.get()
            if first is None:
                return
            batch = [first]
            stop_after = False
            while not self.queue.empty():
                nxt = self.queue.get_nowait()
                if nxt is None:
                    stop_after = True
                    break
                batch.append(nxt)
            self.recent.extend(batch)
            self.recent = self.recent[-8:]
            if (
                self.settings.tasks.auto_create
                and self.notion is not None
                and self.notion.schema.tasks_db
            ):
                candidates = [item for item in batch if not is_addressed_line(item.text)]
                today = today_in(self.settings.meeting.timezone)
                matching = [
                    item for item in candidates if might_contain_task(item.text, today=today)
                ]
                if matching:
                    line = matching[-1]
                    try:
                        window = "\n".join(f"{item.speaker}: {item.text}" for item in self.recent)
                        prompt = read_prompt("tasks.md") + "\n\n" + window
                        kwargs: dict = {
                            "enable_web_search": False,
                            "timeout_s": self.settings.llm.summary_timeout_s,
                        }
                        if self.settings.llm.task_model:
                            kwargs["model_override"] = self.settings.llm.task_model
                        response = await self.llm.respond(prompt, **kwargs)
                        records, _chats = apply_overheard(
                            line.text,
                            response.text,
                            said_by=line.speaker,
                            time=format_meeting_time(line.start_s),
                            today=today,
                        )
                    except Exception:
                        logger.exception("task extract failed")
                        records = []
                    if records:
                        self.recent.clear()
                    for record in records:
                        result = await self.notion.create_task(record)
                        if result.page_id:
                            record.notion_page_id = result.page_id
                            self.store.add_task(record)
                        await self.poster.post(result.message, PostReason.CONFIRMATION)
            if stop_after or self._stopped:
                return
