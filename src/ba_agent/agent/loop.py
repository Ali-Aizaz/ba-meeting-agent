"""Persona, tools, and one chat reply per Hey BA."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from datetime import date

from ba_agent.agent.timing import log_timing
from ba_agent.captions.clock import format_meeting_time
from ba_agent.config import read_prompt
from ba_agent.guardrails.citations import NOT_SURE, finalize_reply, reply_status
from ba_agent.knowledge.policy import is_car_question, should_search_web
from ba_agent.knowledge.search import Chunk, KnowledgeIndex
from ba_agent.llm.tools import local_tools
from ba_agent.models import CaptionLine, PostReason, TriggerEvent, WebResult
from ba_agent.notion.messages import notes_saved
from ba_agent.tasks_extract.extractor import apply_followup, is_task_request, parse_task_command
from ba_agent.trigger.detector import TriggerDetector

logger = logging.getLogger("ba_agent.agent")

CLARIFY = "Yes — what would you like me to do?"
STILL_NEED = "I still need an owner and a due date."


class AgentLoop:
    def __init__(
        self,
        *,
        knowledge: KnowledgeIndex,
        search_cars: Callable[[str], Awaitable[WebResult]],
        llm,
        create_task: Callable,
        save_meeting: Callable[[], Awaitable[str]],
        post: Callable,
        trigger: TriggerDetector,
        leave: Callable[[], Awaitable[None]],
        digest: Callable[[], str],
        today: Callable[[], date],
        min_score: float,
        timeout_s: float,
        persona: str | None = None,
    ) -> None:
        self.knowledge = knowledge
        self.search_cars = search_cars
        self.llm = llm
        self.create_task = create_task
        self.save_meeting = save_meeting
        self.post = post
        self.trigger = trigger
        self.leave = leave
        self.digest = digest
        self.today = today
        self.min_score = min_score
        self.timeout_s = timeout_s
        self.persona = persona if persona is not None else read_prompt("persona.md")
        self.pending_task = None

    async def handle(self, event: TriggerEvent) -> None:
        started = time.perf_counter()
        model_at = started
        tool_at = started
        text = ""
        reason = PostReason.REPLY
        try:
            text, reason, model_at, tool_at = await self._dispatch(event)
        except Exception:
            logger.exception("reply failed")
            text = "I couldn't complete that."
            reason = PostReason.REPLY
        try:
            if text:
                await self.post(text, reason, event.trigger_id)
        finally:
            posted = time.perf_counter()
            log_timing(
                caption_commit_ms=0,
                model_start_ms=int((model_at - started) * 1000),
                tool_ms=int((tool_at - started) * 1000),
                chat_posted_ms=int((posted - started) * 1000),
            )

    async def _dispatch(self, event: TriggerEvent):
        started = time.perf_counter()
        if event.followup and self.pending_task is not None:
            return await self._complete_pending(event, started)
        question = event.question.strip()
        if not question:
            self.trigger.arm_followup(event.speaker, event.t_detected)
            return CLARIFY, PostReason.REPLY, started, started
        if _is_leave(question):
            url = await self._save()
            await self.leave()
            return _saved(url), PostReason.MEETING_SAVED, started, started
        if _is_save(question):
            url = await self._save()
            return _saved(url), PostReason.MEETING_SAVED, started, started
        if is_task_request(question):
            return await self._requested_task(question, event, started)
        return await self._answer(question, started)

    async def _complete_pending(self, event: TriggerEvent, started: float):
        record = apply_followup(self.pending_task, event.question, today=self.today())
        self.pending_task = None
        if not record.owner or record.due is None:
            return STILL_NEED, PostReason.REPLY, started, started
        result = await self.create_task(record)
        return result.message, PostReason.CONFIRMATION, started, time.perf_counter()

    async def _requested_task(self, question: str, event: TriggerEvent, started: float):
        record = parse_task_command(
            question,
            said_by=event.speaker,
            time=format_meeting_time(event.t_detected),
            today=self.today(),
        )
        if record is None or not record.owner or record.due is None:
            self.pending_task = record
            self.trigger.arm_followup(event.speaker, event.t_detected)
            return _ask(record), PostReason.REPLY, started, started
        result = await self.create_task(record)
        return result.message, PostReason.CONFIRMATION, started, time.perf_counter()

    async def _answer(self, question: str, started: float):
        hits = self.knowledge.search(question)
        web: WebResult | None = None
        car = is_car_question(question, hits)
        tool_at = time.perf_counter()
        if car and should_search_web(question, hits, self.min_score):
            web = await self.search_cars(question)
            tool_at = time.perf_counter()
        if car and not hits and not (web and web.useful):
            return NOT_SURE, PostReason.REPLY, started, tool_at
        model_at = time.perf_counter()
        prompt = _prompt(self.persona, self.digest(), question, hits, web, car)
        first = await self.llm.respond(
            prompt,
            tools=local_tools(),
            enable_web_search=False,
            timeout_s=self.timeout_s,
        )
        if not car:
            text = first.text or "I don't have that in this meeting."
            return text, PostReason.REPLY, model_at, tool_at
        if reply_status(first.text) == "allow":
            return first.text, PostReason.REPLY, model_at, tool_at
        second = await self.llm.respond(
            prompt + "\n\nCite a source you were given, or say you are not sure. "
            "Do not invent a number.",
            tools=local_tools(),
            enable_web_search=False,
            timeout_s=self.timeout_s,
        )
        return finalize_reply(first.text, second.text), PostReason.REPLY, model_at, tool_at

    async def _save(self) -> str:
        try:
            return await self.save_meeting()
        except Exception:
            logger.exception("save_meeting failed")
            return ""


def route_caption(line: CaptionLine, trigger: TriggerDetector) -> list[TriggerEvent]:
    """Wake phrases only. Ordinary captions do not become replies."""
    return trigger.on_line(line.speaker, line.text, "speech", line.start_s, line.id)


async def deliver_caption(line: CaptionLine, trigger: TriggerDetector, agent: AgentLoop) -> None:
    for event in route_caption(line, trigger):
        await agent.handle(event)


def _prompt(persona, digest, question, hits: list[Chunk], web: WebResult | None, car: bool) -> str:
    notes = "\n\n".join(f"(src: {hit.file}) {hit.text}" for hit in hits) or "(none)"
    urls = "\n".join(web.urls) if web else ""
    web_text = web.text if web else ""
    car_rule = (
        "This is a car question. Use only the knowledge and web sections. "
        "Cite a knowledge file as (src: filename.md) or include a URL. "
        "If those sections are empty, say you are not sure."
        if car
        else "This is a question about the meeting. Use the digest. Do not search the web."
    )
    return (
        f"{persona.strip()}\n\n{car_rule}\n\nMeeting digest:\n{digest}\n\n"
        f"Knowledge:\n{notes}\n\nWeb:\n{web_text}\n{urls}\n\n"
        f"Question: {question}\n\nReply in a few plain-text sentences."
    )


def _is_save(question: str) -> bool:
    text = question.lower().strip(" .")
    return "save the meeting" in text or text in {"save", "save meeting", "save notes"}


def _is_leave(question: str) -> bool:
    text = question.lower()
    return "you can go" in text or text.strip(" .") in {"leave", "please leave"}


def _ask(record) -> str:
    if record is None or (not record.owner and record.due is None):
        return "Who should own that, and when is it due?"
    if not record.owner:
        return "Who should own that?"
    return "When is that due?"


def _saved(url: str) -> str:
    if not url:
        return "✗ Couldn't save the meeting notes."
    return notes_saved(url)

