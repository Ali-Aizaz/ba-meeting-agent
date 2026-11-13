"""One meeting: join, captions, Hey BA, overheard tasks, save on the way out."""

from __future__ import annotations

import asyncio
import inspect
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
from ba_agent.knowledge.policy import normalize_asr_query
from ba_agent.knowledge.search import KnowledgeIndex
from ba_agent.llm import build_llm
from ba_agent.logging_setup import configure_logging, set_meeting_id
from ba_agent.memory.store import MeetingStore, _canonical_url, open_meeting
from ba_agent.models import (
    CaptionLine,
    PostReason,
    StoredChatMessage,
    TaskRecord,
    TriggerEvent,
    WebResult,
)
from ba_agent.notion.client import NotionService, TaskWrite
from ba_agent.notion.messages import notes_saved, task_failed
from ba_agent.notion.schema import load_notion_schema
from ba_agent.speakers import is_bot_speaker, load_aliases, normalize_speaker
from ba_agent.status import StatusState, start_status_server
from ba_agent.tasks_extract.dates import parse_due, today_in
from ba_agent.tasks_extract.extractor import (
    apply_overheard,
    fallback_overheard_tasks,
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
        *,
        bot_name: str = "TechSol BA",
        on_change=None,
    ) -> None:
        self.adapter = adapter
        self.gate = gate
        self.store = store
        self.bot_name = bot_name
        self.on_change = on_change

    async def post(self, text: str, reason: PostReason, trigger_id: str | None = None) -> bool:
        if not self.gate.allow(reason, trigger_id):
            return False
        posted = await self.adapter.post_chat(text, reason)
        if posted and self.store is not None:
            if reason in {
                PostReason.REPLY,
                PostReason.CONFIRMATION,
            }:
                self.store.add_reply(text)
            now_s = 0.0
            meeting_seconds = getattr(self.adapter, "meeting_seconds", None)
            if callable(meeting_seconds):
                try:
                    now_s = max(0.0, float(meeting_seconds()))
                except Exception:
                    now_s = 0.0
            self.store.add_chat_message(
                StoredChatMessage(
                    id=uuid.uuid4().hex,
                    meeting_id=self.store.meeting_id,
                    sender=self.bot_name,
                    start_s=now_s,
                    wall_time=datetime.now(UTC),
                    text=text.strip(),
                    is_bot=True,
                )
            )
            if self.on_change is not None and reason is not PostReason.MEETING_SAVED:
                self.on_change()
        return posted


async def run_join(settings: Settings, url: str) -> None:
    configure_logging()
    ensure_ready(settings)
    loop = asyncio.get_running_loop()
    next_url_event = asyncio.Event()
    queued_url: list[str] = []
    background_saves: set[asyncio.Task[None]] = set()
    current_url = url
    adapter = GoogleMeetAdapter(settings)

    def on_join_request(new_url: str, force: bool = False) -> None:
        def _schedule() -> None:
            same_meeting = _canonical_url(new_url) == _canonical_url(current_url)
            if (
                not force
                and same_meeting
                and status.stage in {"joining", "in_call"}
                and not adapter._left
                and not adapter.ended_event.is_set()
            ):
                notify_host = getattr(adapter, "notify_host_present", None)
                if callable(notify_host):
                    notify_host()
                logger.info("ignoring duplicate /join for active meeting %s", new_url)
                return
            queued_url.clear()
            queued_url.append(new_url)
            next_url_event.set()
            adapter.ended_event.set()

        loop.call_soon_threadsafe(_schedule)

    def on_activity_update(text: str, kind: str) -> None:
        show_toast = getattr(adapter, "show_toast", None)
        if callable(show_toast):
            try:
                loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(show_toast(text, kind))
                )
            except RuntimeError:
                pass

    status = StatusState("", on_join=on_join_request, on_activity=on_activity_update)
    server = start_status_server(status, settings.server.status_host, settings.server.status_port)
    keep_alive = bool(settings.browser.keep_alive) and "PYTEST_CURRENT_TEST" not in os.environ
    try:
        while True:
            if not queued_url:
                next_url_event.clear()
            adapter.ended_event = asyncio.Event()
            adapter._left = False
            adapter._admitted_at = None
            try:
                await _run_single_meeting(
                    settings,
                    adapter,
                    status,
                    current_url,
                    next_url_event=next_url_event,
                    background_saves=background_saves if keep_alive else None,
                )
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
                idle_timeout = getattr(settings.browser, "idle_timeout_s", 0)
                if idle_timeout and idle_timeout > 0:
                    logger.info(
                        "browser kept alive and signed in; waiting up to %ds for next /join",
                        idle_timeout,
                    )
                    try:
                        await asyncio.wait_for(next_url_event.wait(), timeout=idle_timeout)
                    except asyncio.TimeoutError:
                        logger.info(
                            "idle timeout (%ds) reached with no new meeting; exiting container",
                            idle_timeout,
                        )
                        break
                else:
                    logger.info(
                        "browser kept alive and signed in; waiting for next meeting on /join"
                    )
                    await next_url_event.wait()
            if queued_url:
                current_url = queued_url.pop(0)
                if not queued_url:
                    next_url_event.clear()
    finally:
        _let_cleanup_finish()
        if background_saves:
            await asyncio.gather(*list(background_saves), return_exceptions=True)
        await adapter.close(force=True)
        if server is not None:
            server.shutdown()


async def _run_single_meeting(
    settings: Settings,
    adapter: GoogleMeetAdapter,
    status: StatusState,
    url: str,
    *,
    next_url_event: asyncio.Event | None = None,
    background_saves: set[asyncio.Task[None]] | None = None,
) -> None:
    store = open_meeting(resolve_path(settings.storage.sqlite_path), url)
    set_meeting_id(store.meeting_id)
    status.meeting_id = store.meeting_id
    status.meet_url = _canonical_url(url)
    status.captions_enabled = False
    status.chat_enabled = False
    status.stage = "joining"
    status.last_error = ""
    notion = _build_notion(settings, store)
    notes_dirty = asyncio.Event()
    poster = SessionPoster(
        adapter,
        OutputGate(),
        store,
        bot_name=settings.meeting.bot_name,
        on_change=notes_dirty.set,
    )
    llm = None
    joined = False
    store_closed = False
    warm_tasks: list[asyncio.Task[None]] = []
    try:
        if notion and notion.configured:
            await notion.verify()

            async def _warm_db() -> None:
                try:
                    await notion.database_context(refresh=True, exclude_current_meeting=True)
                except Exception:
                    pass

            warm_tasks.append(asyncio.create_task(_warm_db()))
        llm = build_llm(settings)
        warm_up_fn = getattr(llm, "warm_up", None)
        if callable(warm_up_fn):

            async def _warm_llm() -> None:
                try:
                    await warm_up_fn()
                    auth_err = getattr(llm, "auth_error", None)
                    if auth_err:
                        status.last_error = str(auth_err)
                        status.record_activity(
                            "OpenAI API key expired/invalid — using local & Notion fallback",
                            "warn",
                        )
                except Exception:
                    pass

            warm_tasks.append(asyncio.create_task(_warm_llm()))
        knowledge = KnowledgeIndex(
            resolve_path(settings.knowledge.dir), settings.knowledge.min_score
        )
        aliases = load_aliases(resolve_path(settings.speakers.aliases_file))
        stabilizer = Stabilizer(
            settings.captions.stable_ms,
            aliases=aliases,
            extra_phrases=settings.trigger.phrases,
        )
        trigger = TriggerDetector(
            pause_wait_s=settings.trigger.pause_wait_s,
            dedup_window_s=settings.trigger.dedup_window_s,
            followup_window_s=settings.trigger.followup_window_s,
            fuzzy_threshold=settings.trigger.fuzzy_threshold,
            extra_phrases=settings.trigger.phrases,
            is_speaker_active=stabilizer.has_open_row_for,
        )

        addressed_caption_ids: set[str] = set()

        async def guarded_search(query: str) -> WebResult:
            cleaned_query = " ".join(query.split()).lower().strip(".,!?;:")
            lines = [
                line.text
                for line in store.last(500)
                if line.id not in addressed_caption_ids
                and not is_addressed_line(line.text)
                and " ".join(line.text.split()).lower().strip(".,!?;:") != cleaned_query
                and normalize_asr_query(" ".join(line.text.split())).lower().strip(".,!?;:")
                != cleaned_query
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
                notes_dirty.set()
            return result

        async def save_meeting() -> str:
            url = await _save(settings, store, notion, llm, announce=False, poster=poster)
            if url:
                store.set_meta("save_announced", "1")
            return url

        async def leave() -> None:
            adapter.ended_event.set()

        async def fetch_db_context() -> str:
            parts: list[str] = []
            if notion is not None and notion.configured:
                notion_ctx = await notion.database_context(exclude_current_meeting=True)
                if notion_ctx:
                    parts.append(notion_ctx)
            local_ctx = store.stored_context()
            if local_ctx:
                parts.append(local_ctx)
            return "\n".join(parts)

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
            on_activity=status.record_activity,
            fetch_db_context=fetch_db_context,
            participants=store.participants,
            aliases=aliases,
        )
        bus = EventBus()
        pump = CaptionPump(adapter.drain_caption_frames, stabilizer)
        extractor = _Extractor(
            settings,
            llm,
            store,
            poster,
            notion,
            on_change=notes_dirty.set,
            on_activity=status.record_activity,
            aliases=aliases,
        )
        consent_posted = store.get_meta("consent_posted") == "1"
        attempts = 0
        while attempts < settings.browser.rejoin_limit:
            if next_url_event is not None and next_url_event.is_set():
                break
            attempts += 1
            try:
                status.record_activity("Joining Google Meet call…", "working")
                await adapter.join(url)
                joined = True
                status.stage = "in_call"
                status.last_error = str(getattr(llm, "auth_error", "") or "")
                status.record_activity(
                    "Admitted to Google Meet — enabling captions & chat…",
                    "working",
                )
                status.captions_enabled = await adapter.enable_captions()
                await adapter.ensure_chat_open()
                if notion and notion.configured and not notion.summary_url:
                    title = f"TechSol BA {store.meeting_id}"
                    notes_url = await notion.ensure_summary_page(title)
                    store.set_meta("summary_page_id", notion.summary_page_id)
                    store.set_meta("summary_url", notes_url)
                if not consent_posted:
                    message = settings.meeting.consent_message
                    if notion and notion.summary_url:
                        message = f"{message} Notes will be here: {notion.summary_url}"
                    if await poster.post(message, PostReason.CONSENT):
                        consent_posted = True
                        store.set_meta("consent_posted", "1")
                status.chat_enabled = adapter.chat_enabled
                status.record_activity(
                    "TechSol BA ready — listening for 'Hey BA' ✓",
                    "success",
                )
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
                    notion=notion,
                    notes_dirty=notes_dirty,
                    on_activity=status.record_activity,
                    next_url_event=next_url_event,
                )
                break
            except Exception as exc:
                if (
                    next_url_event is not None and next_url_event.is_set()
                ) or adapter.ended_event.is_set():
                    logger.info(
                        "aborting join attempt %s for %s because meeting ended or switched",
                        attempts,
                        url,
                    )
                    break
                status.last_error = f"{type(exc).__name__}: {exc}"
                status.stage = "retrying"
                logger.exception("meeting attempt %s failed", attempts)
                await adapter.close(force=False)
                if next_url_event is not None and next_url_event.is_set():
                    break
                adapter.ended_event = asyncio.Event()
                if attempts >= settings.browser.rejoin_limit:
                    status.stage = "failed"
                    raise
    finally:
        _let_cleanup_finish()
        for task in warm_tasks:
            if not task.done():
                task.cancel()
        if warm_tasks:
            await asyncio.gather(*warm_tasks, return_exceptions=True)
        status.stage = "leaving"
        switching = next_url_event is not None and next_url_event.is_set()
        has_data = (
            joined
            or store.count_captions() > 0
            or len(store.tasks()) > 0
            or store.count_chat_messages() > 0
        )
        current_sig = (
            f"{store.count_captions()}:{len(store.tasks())}:{store.count_chat_messages()}"
        )
        needs_final_save = bool(
            llm is not None
            and has_data
            and (
                store.get_meta("save_announced") != "1"
                or store.get_meta("saved_sig") != current_sig
            )
        )
        if switching and background_saves is not None and needs_final_save:
            # Leave the old call and release the SQLite store immediately so the browser
            # can join the new meeting without waiting 15-35s for LLM + Notion.
            lines_snapshot = store.last(100000)
            tasks_snapshot = store.tasks()
            chats_snapshot = store.chat_messages(100000)
            store.set_meta("saved", "1")
            store.set_meta("saved_sig", current_sig)
            store.set_meta("closed", "1")
            store.close()
            store_closed = True
            await adapter.leave()
            bg_task = asyncio.create_task(
                _save_snapshot_and_close_notion(
                    settings,
                    notion,
                    llm,
                    lines_snapshot,
                    tasks_snapshot,
                    chats_snapshot,
                )
            )
            background_saves.add(bg_task)
            bg_task.add_done_callback(background_saves.discard)
            notion = None
        else:
            try:
                if needs_final_save:
                    await _save(
                        settings,
                        store,
                        notion,
                        llm,
                        announce=not switching,
                        poster=poster,
                    )
            except Exception:
                logger.exception("final save failed")
            store.set_meta("closed", "1")
            await adapter.leave()
            if notion is not None:
                await notion.aclose()
            if llm is not None and callable(getattr(llm, "aclose", None)):
                await llm.aclose()
            if not store_closed:
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
    *,
    notion: NotionService | None = None,
    notes_dirty: asyncio.Event | None = None,
    on_activity=None,
    next_url_event: asyncio.Event | None = None,
) -> None:
    trigger_queue: asyncio.Queue[TriggerEvent | None] = asyncio.Queue()
    last_addressed_at: dict[str, float] = {}

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
        if notes_dirty is not None:
            notes_dirty.set()
        await bus.publish("caption_committed", line)
        if isinstance(trigger, TriggerDetector):
            events = trigger.on_line(
                speaker,
                raw.text,
                "speech",
                raw.start_s,
                line.id,
                committed_at=raw.end_s,
            )
        else:
            events = trigger.on_line(speaker, raw.text, "speech", raw.start_s, line.id)
        has_pending = (
            getattr(trigger, "pending", None) is not None
            or getattr(trigger, "_pending", None) is not None
        )
        addressed_now = bool(events or is_addressed_line(raw.text) or has_pending)
        if addressed_caption_ids is not None and addressed_now:
            addressed_caption_ids.add(line.id)
        if addressed_now:
            last_addressed_at[speaker] = raw.end_s
            extractor.recent.clear()
        if has_pending and not events and on_activity is not None:
            on_activity(
                f'Heard "Hey BA" from {speaker} — listening for question (5s)…',
                "working",
            )
        for event in events:
            if on_activity is not None:
                preview = event.question or "Hey BA"
                on_activity(
                    f'Heard {speaker}: "{preview[:60]}" — processing…',
                    "working",
                )
            trigger_queue.put_nowait(event)
        if not addressed_now:
            since_trig = raw.start_s - last_addressed_at.get(speaker, -999.0)
            if since_trig > 6.0 or parse_due(
                raw.text, today=today_in(settings.meeting.timezone)
            ) is not None:
                extractor.note(line)
            elif callable(getattr(extractor, "append_recent", None)):
                extractor.append_recent(line)

    async def process_chat_batch(messages) -> None:
        for message in messages:
            if message.is_self:
                continue
            speaker = normalize_speaker(message.sender, aliases)
            if is_bot_speaker(speaker, settings.meeting.bot_name):
                continue
            text = message.text.strip()
            if not text or text.startswith(settings.meeting.consent_message):
                continue
            now = adapter.meeting_seconds()
            msg_wall_time = getattr(message, "wall_time", None) or datetime.now(UTC)
            recent_chats = store.chat_messages(limit=5)
            if not any(
                c.sender == speaker and c.text == text and abs(now - c.start_s) <= 3.0
                for c in recent_chats
            ):
                store.add_chat_message(
                    StoredChatMessage(
                        id=uuid.uuid4().hex,
                        meeting_id=store.meeting_id,
                        sender=speaker,
                        start_s=max(0.0, float(now)),
                        wall_time=msg_wall_time,
                        text=text,
                        is_bot=False,
                    )
                )
                if notes_dirty is not None:
                    notes_dirty.set()
            line = CaptionLine(
                id=uuid.uuid4().hex,
                meeting_id=store.meeting_id,
                speaker=speaker,
                speaker_raw=message.sender,
                start_s=now,
                end_s=now,
                wall_time=datetime.now(UTC),
                text=text,
            )
            recent = store.last(3)
            if not any(r.speaker == speaker and r.text == text for r in recent):
                store.add_caption(line)
                if notes_dirty is not None:
                    notes_dirty.set()
            events = trigger.on_line(speaker, text, "chat", now, line.id)
            if addressed_caption_ids is not None and (events or is_addressed_line(text)):
                addressed_caption_ids.add(line.id)
            for event in events:
                if on_activity is not None:
                    preview = event.question or "Hey BA"
                    on_activity(
                        f'Chat from {speaker}: "{preview[:60]}" — processing…',
                        "working",
                    )
                trigger_queue.put_nowait(event)
            if not events and not is_addressed_line(text):
                extractor.note(line)

    use_combined_poll = callable(getattr(adapter, "drain_poll_state", None))

    async def captions() -> None:
        if use_combined_poll:
            while not adapter.ended_event.is_set():
                try:
                    frames, chat_msgs = await adapter.drain_poll_state()
                    now = adapter.meeting_seconds()
                    push_frames = getattr(pump, "push_frames", None)
                    if callable(push_frames):
                        committed = push_frames(frames, now)
                    else:
                        committed = await pump.poll(now)
                    for raw in committed:
                        await on_caption(raw)
                    if chat_msgs:
                        await process_chat_batch(chat_msgs)
                    for event in trigger.tick(adapter.meeting_seconds()):
                        trigger_queue.put_nowait(event)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning("combined poll iteration failed", exc_info=True)
                await asyncio.sleep(0.22)
            return
        while not adapter.ended_event.is_set():
            try:
                if await adapter.meeting_closed():
                    adapter.ended_event.set()
                    return
                for raw in await pump.poll(adapter.meeting_seconds()):
                    await on_caption(raw)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("caption poll iteration failed", exc_info=True)
            await asyncio.sleep(0.3)

    async def chats() -> None:
        while not adapter.ended_event.is_set():
            try:
                await process_chat_batch(await adapter.collect_chat())
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("chat poll iteration failed", exc_info=True)
            await asyncio.sleep(0.3)

    async def ticks() -> None:
        while not adapter.ended_event.is_set():
            try:
                for event in trigger.tick(adapter.meeting_seconds()):
                    trigger_queue.put_nowait(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("trigger tick iteration failed", exc_info=True)
            await asyncio.sleep(0.5)

    async def replies() -> None:
        while not adapter.ended_event.is_set():
            event = await trigger_queue.get()
            if event is None or adapter.ended_event.is_set():
                return
            try:
                await agent.handle(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("agent.handle failed for trigger %s", event.trigger_id)

    async def alone() -> None:
        alone_for = 0.0
        while not adapter.ended_event.is_set():
            if await adapter.meeting_closed():
                adapter.ended_event.set()
                return
            others = await adapter.other_participant_count()
            alone_for = 0.0 if others != 0 else alone_for + 1
            if should_leave_alone(others, alone_for, settings.meeting.alone_timeout_s):
                logger.info("alone for %ss", settings.meeting.alone_timeout_s)
                adapter.ended_event.set()
                return
            await asyncio.sleep(1)

    async def live_notes() -> None:
        if notion is None or not notion.schema.summary_db or notes_dirty is None:
            return
        sync_interval = max(1.5, float(getattr(settings.notion, "live_sync_interval_s", 15.0)))
        while not adapter.ended_event.is_set():
            await notes_dirty.wait()
            if adapter.ended_event.is_set():
                return
            await asyncio.sleep(sync_interval)
            notes_dirty.clear()
            try:
                await _save_live(store, notion)
            except Exception:
                logger.warning("live notion notes sync failed", exc_info=True)

    poller_tasks = [
        asyncio.create_task(captions()),
        asyncio.create_task(alone()),
    ]
    if not use_combined_poll:
        poller_tasks.append(asyncio.create_task(chats()))
        poller_tasks.append(asyncio.create_task(ticks()))
    worker_tasks = [
        asyncio.create_task(replies()),
        asyncio.create_task(extractor.run()),
    ]
    if notion is not None and notion.schema.summary_db and notes_dirty is not None:
        poller_tasks.append(asyncio.create_task(live_notes()))
    try:
        await adapter.ended_event.wait()
    finally:
        _let_cleanup_finish()
        for task in poller_tasks:
            task.cancel()
        await asyncio.gather(*poller_tasks, return_exceptions=True)
        trigger_queue.put_nowait(None)
        extractor.stop()
        wait_timeout = (
            0.2 if (next_url_event is not None and next_url_event.is_set()) else 5.0
        )
        _done, pending = await asyncio.wait(worker_tasks, timeout=wait_timeout)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


def _format_chat_log(chat_msgs: list[StoredChatMessage]) -> str:
    return "\n".join(
        f"{format_meeting_time(msg.start_s)} {msg.sender}: {msg.text}" for msg in chat_msgs
    )


def _combined_summary_input(transcript: str, chat_log: str) -> str:
    t_clean = transcript.strip()
    c_clean = chat_log.strip()
    if t_clean and c_clean:
        return f"{t_clean}\n\nLive Chat:\n{c_clean}"
    return t_clean or c_clean


async def _call_save_meeting(
    notion: NotionService,
    summary: str,
    transcript: str,
    tasks: list[TaskRecord],
    *,
    chat_log: str = "",
    append_children: bool = True,
) -> str:
    kwargs: dict = {"append_children": append_children}
    if _supports_kwarg(notion.save_meeting, "chat_log"):
        kwargs["chat_log"] = chat_log
    return await notion.save_meeting(summary, transcript, tasks, **kwargs)


async def _save_snapshot_and_close_notion(
    settings,
    notion: NotionService | None,
    llm,
    lines: list[CaptionLine],
    tasks: list[TaskRecord],
    chat_msgs: list[StoredChatMessage] | None = None,
) -> None:
    try:
        if notion is None or not notion.schema.summary_db or llm is None:
            return
        transcript = "\n".join(
            f"{format_meeting_time(line.start_s)} {line.speaker}: {line.text}"
            for line in lines
        )
        chat_log = _format_chat_log(chat_msgs or [])
        summary = await _summarize(
            llm,
            _combined_summary_input(transcript, chat_log),
            settings.llm.summary_timeout_s,
        )
        await _call_save_meeting(
            notion,
            summary,
            transcript,
            tasks,
            chat_log=chat_log,
            append_children=True,
        )
    except Exception:
        logger.exception("background final save failed")
    finally:
        if notion is not None:
            try:
                await notion.aclose()
            except Exception:
                pass
        if llm is not None and callable(getattr(llm, "aclose", None)):
            try:
                await llm.aclose()
            except Exception:
                pass


async def _save_live(store: MeetingStore, notion: NotionService) -> str:
    if not notion.schema.summary_db:
        return ""
    lines = store.last(1500)
    tasks = store.tasks()
    chat_msgs = store.chat_messages(1500)
    if not lines and not tasks and not chat_msgs:
        return notion.summary_url
    transcript = "\n".join(
        f"{format_meeting_time(line.start_s)} {line.speaker}: {line.text}" for line in lines
    )
    chat_log = _format_chat_log(chat_msgs)
    summary = _fallback_summary(_combined_summary_input(transcript, chat_log))
    url = await _call_save_meeting(
        notion,
        summary,
        transcript,
        tasks,
        chat_log=chat_log,
        append_children=False,
    )
    store.set_meta("summary_page_id", notion.summary_page_id)
    store.set_meta("summary_url", url)
    return url


async def _save(settings, store, notion, llm, *, announce: bool, poster: SessionPoster) -> str:
    if notion is None or not notion.schema.summary_db:
        return ""
    lines = store.last(100000)
    tasks = store.tasks()
    chat_msgs = store.chat_messages(100000)
    transcript = "\n".join(
        f"{format_meeting_time(line.start_s)} {line.speaker}: {line.text}" for line in lines
    )
    chat_log = _format_chat_log(chat_msgs)
    summary = await _summarize(
        llm,
        _combined_summary_input(transcript, chat_log),
        settings.llm.summary_timeout_s,
    )
    url = await _call_save_meeting(
        notion,
        summary,
        transcript,
        tasks,
        chat_log=chat_log,
        append_children=True,
    )
    store.set_meta("summary_page_id", notion.summary_page_id)
    store.set_meta("summary_url", url)
    store.set_meta("saved", "1")
    store.set_meta("saved_sig", f"{len(lines)}:{len(tasks)}:{store.count_chat_messages()}")
    if announce and url and store.get_meta("save_announced") != "1":
        await poster.post(notes_saved(url), PostReason.MEETING_SAVED)
        store.set_meta("save_announced", "1")
        store.set_meta("saved_sig", f"{len(lines)}:{len(tasks)}:{store.count_chat_messages()}")
    return url


def _supports_kwarg(fn, name: str) -> bool:
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    return name in sig.parameters or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )


async def _summarize(llm, transcript: str, timeout_s: float) -> str:
    body = transcript.strip() if transcript and transcript.strip() else "(empty transcript)"
    prompt = f"{read_prompt('summary.md').strip()}\n\n<transcript>\n{body}\n</transcript>"
    try:
        kwargs: dict = {"enable_web_search": False, "timeout_s": timeout_s}
        if _supports_kwarg(llm.respond, "max_output_tokens"):
            kwargs["max_output_tokens"] = 600
        response = await llm.respond(prompt, **kwargs)
        text = response.text.strip()
        if text:
            return text
    except Exception:
        logger.warning("llm summarize failed; using extractive fallback", exc_info=True)
    return _fallback_summary(transcript)


def _fallback_summary(transcript: str) -> str:
    cleaned = [line.strip() for line in transcript.splitlines() if line.strip()]
    if not cleaned:
        return "No summary."
    snippets: list[str] = []
    for line in cleaned[-6:]:
        if line.startswith("[") and "] " in line:
            line = line.split("] ", 1)[1]
        snippets.append(line)
    joined = " | ".join(snippets)
    return joined[:600] if len(joined) > 600 else joined


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
    def __init__(
        self,
        settings,
        llm,
        store,
        poster,
        notion,
        *,
        on_change=None,
        on_activity=None,
        aliases: dict[str, str] | None = None,
    ) -> None:
        self.settings = settings
        self.llm = llm
        self.store = store
        self.poster = poster
        self.notion = notion
        self.on_change = on_change
        self.on_activity = on_activity
        self.aliases = aliases or {}
        self.queue: asyncio.Queue = asyncio.Queue()
        self.recent: list[CaptionLine] = []
        self.seen: set[str] = set()
        self._stopped = False

    def append_recent(self, line: CaptionLine) -> None:
        self.recent.append(line)
        self.recent = self.recent[-8:]

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
                has_due_in_window = any(
                    parse_due(item.text, today=today) is not None for item in self.recent
                )
                if matching and has_due_in_window:
                    line = matching[-1]
                    if self.on_activity is not None:
                        self.on_activity(
                            f"Checking overheard action item from {line.speaker}…",
                            "working",
                        )
                    try:
                        window = "\n".join(f"{item.speaker}: {item.text}" for item in self.recent)
                        prompt = (
                            f"{read_prompt('tasks.md').strip()}\n\n"
                            f"Meeting date: {today.isoformat()} ({today.strftime('%A')})\n"
                            f"<captions>\n{window}\n</captions>"
                        )
                        kwargs: dict = {
                            "enable_web_search": False,
                            "timeout_s": self.settings.llm.summary_timeout_s,
                        }
                        if _supports_kwarg(self.llm.respond, "max_output_tokens"):
                            kwargs["max_output_tokens"] = 450
                        if self.settings.llm.task_model:
                            kwargs["model_override"] = self.settings.llm.task_model
                        response = await self.llm.respond(prompt, **kwargs)
                        records, _chats = apply_overheard(
                            line.text,
                            response.text,
                            said_by=line.speaker,
                            time=format_meeting_time(line.start_s),
                            today=today,
                            aliases=self.aliases,
                        )
                    except Exception:
                        logger.exception("task extract failed")
                        records = fallback_overheard_tasks(
                            line.text,
                            said_by=line.speaker,
                            time=format_meeting_time(line.start_s),
                            today=today,
                            aliases=self.aliases,
                        )
                    if records:
                        self.recent.clear()
                    elif self.on_activity is not None:
                        self.on_activity("", "info")
                    for record in records:
                        if self.on_activity is not None:
                            self.on_activity(
                                f"Creating overheard task in Notion: "
                                f"{record.task} ({record.owner})…",
                                "working",
                            )
                        result = await self.notion.create_task(record)
                        if result.page_id:
                            record.notion_page_id = result.page_id
                            self.store.add_task(record)
                            if self.on_change is not None:
                                self.on_change()
                        await self.poster.post(result.message, PostReason.CONFIRMATION)
                        if self.on_activity is not None:
                            self.on_activity(
                                f"Overheard task saved: {record.task} ✓",
                                "success",
                            )
            if stop_after or self._stopped:
                return
