"""T12 agent loop and T14 timing log."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from ba_agent.agent.loop import AgentLoop, deliver_caption
from ba_agent.knowledge.search import KnowledgeIndex
from ba_agent.llm.tools import local_tools
from ba_agent.models import (
    CaptionLine,
    FunctionCall,
    LLMResponse,
    PostReason,
    TriggerEvent,
    WebResult,
)
from ba_agent.notion.client import TaskWrite
from ba_agent.trigger.detector import TriggerDetector

UTC = timezone.utc

TRANSCRIPT = "UNIQUE_TRANSCRIPT_FIXTURE Sara said the budget is locked"
QUESTION = "what's the current price of a Toyota RAV4 hybrid?"


class FakeLLM:
    def __init__(self, text: str) -> None:
        self.text = text
        self.web = []

    async def respond(self, input, tools=None, *, enable_web_search=False, timeout_s=None):
        self.web.append(enable_web_search)
        assert enable_web_search is False
        return LLMResponse(text=self.text)


class FakePoster:
    def __init__(self) -> None:
        self.posts = []

    async def __call__(self, text, reason, trigger_id=None):
        self.posts.append((text, reason, trigger_id))
        return True


def _agent(text: str, search):
    created = []

    async def create_task(record):
        created.append(record)
        raise AssertionError("create_task should not run")

    poster = FakePoster()
    agent = AgentLoop(
        knowledge=KnowledgeIndex(Path("data/knowledge"), 0.5),
        search_cars=search,
        llm=FakeLLM(text),
        create_task=create_task,
        save_meeting=_unused,
        post=poster,
        trigger=TriggerDetector(),
        leave=_unused,
        digest=lambda: TRANSCRIPT,
        today=lambda: date(2026, 10, 25),
        min_score=0.5,
        timeout_s=25,
        persona="You are TechSol BA.",
    )
    return agent, poster, created


async def _unused(*_args, **_kwargs):
    return ""


def _event(question: str, trigger_id: str = "t1") -> TriggerEvent:
    return TriggerEvent(
        trigger_id=trigger_id,
        question=question,
        speaker="Sara",
        source="speech",
        caption_id="c1",
        t_detected=1.0,
    )


@pytest.mark.asyncio
async def test_t12_u1_hybrid_reply_does_not_search() -> None:
    seen = []

    async def search_cars(query: str) -> WebResult:
        seen.append(query)
        raise AssertionError(query)

    agent, poster, created = _agent(
        "A hybrid uses two power sources (src: hybrid.md).",
        search_cars,
    )
    await agent.handle(_event("what is a hybrid?"))
    assert seen == []
    assert created == []
    assert len(poster.posts) == 1
    assert poster.posts[0][1] is PostReason.REPLY
    assert "(src: hybrid.md)" in poster.posts[0][0]
    names = [tool["name"] for tool in local_tools()]
    assert "build_bom" not in names
    assert "compare" not in names


@pytest.mark.asyncio
async def test_t12_u2_price_question_searches_the_question_only() -> None:
    seen = []

    async def search_cars(query: str) -> WebResult:
        seen.append(query)
        return WebResult(text="Listed on the manufacturer page.", urls=["https://example.com/rav4"])

    agent, poster, _created = _agent(
        "The current price is on the manufacturer page. https://example.com/rav4",
        search_cars,
    )
    await agent.handle(_event(QUESTION))
    assert seen == [QUESTION]
    assert TRANSCRIPT not in seen[0]
    assert len(poster.posts) == 1
    assert "https://example.com/rav4" in poster.posts[0][0]


@pytest.mark.asyncio
async def test_t12_u3_missing_due_date_asks_once() -> None:
    called = {"n": 0}

    async def create_task(record):
        called["n"] += 1

    async def search_cars(query: str) -> WebResult:
        raise AssertionError(query)

    agent, poster, _created = _agent("unused", search_cars)
    agent.create_task = create_task
    await agent.handle(_event("add a task for Sara to book the test drive"))
    assert called["n"] == 0
    assert len(poster.posts) == 1
    assert poster.posts[0][1] is PostReason.REPLY
    assert "due" in poster.posts[0][0].lower()


@pytest.mark.asyncio
async def test_t12_u4_caption_without_wake_is_silent() -> None:
    async def search_cars(query: str) -> WebResult:
        raise AssertionError(query)

    agent, poster, _created = _agent("unused", search_cars)
    line = CaptionLine(
        id="c1",
        meeting_id="m1",
        speaker="Sara",
        speaker_raw="Sara",
        start_s=1,
        end_s=2,
        wall_time=datetime(2026, 10, 25, tzinfo=UTC),
        text="Let's review the agenda later",
    )
    await deliver_caption(line, agent.trigger, agent)
    assert poster.posts == []


@pytest.mark.asyncio
async def test_t14_u1_timing_log(caplog) -> None:
    async def search_cars(query: str) -> WebResult:
        raise AssertionError(query)

    agent, _poster, _created = _agent(
        "A hybrid uses two power sources (src: hybrid.md).",
        search_cars,
    )
    caplog.set_level(logging.INFO, logger="ba_agent.agent")
    await agent.handle(_event("what is a hybrid?"))
    text = caplog.text
    assert "caption_commit_ms=" in text
    assert "model_start_ms=" in text
    assert "tool_ms=" in text
    assert "chat_posted_ms=" in text


@pytest.mark.asyncio
async def test_t12_meeting_question_with_car_word_does_not_search_web() -> None:
    async def search_cars(query: str) -> WebResult:
        raise AssertionError(f"search_cars should not run for meeting question: {query}")

    agent, poster, _created = _agent(
        "You decided to use the 60 kWh hybrid setup for city driving.",
        search_cars,
    )
    await agent.handle(_event("what did we decide about the 2026 Ford hybrid?"))
    assert len(poster.posts) == 1
    assert "decided" in poster.posts[0][0]


@pytest.mark.asyncio
async def test_t12_function_call_create_task_and_search_knowledge() -> None:
    created = []

    async def create_task(record):
        created.append(record)
        return TaskWrite(True, "✓ Task added: Ali — cost sheet — Fri", page_id="p1", key=record.key)

    class ToolLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def respond(self, input, tools=None, *, enable_web_search=False, timeout_s=None):
            self.calls += 1
            if "assign Ali" in input and tools:
                return LLMResponse(
                    function_calls=[
                        FunctionCall(
                            name="create_task",
                            arguments={"task": "cost sheet", "owner": "Ali", "due": "Friday"},
                        )
                    ]
                )
            if self.calls == 1 and tools:
                return LLMResponse(
                    text="Let me check the knowledge base.",
                    function_calls=[
                        FunctionCall(name="search_knowledge", arguments={"query": "hybrid"})
                    ],
                )
            return LLMResponse(text="A hybrid uses an engine and motor (src: hybrid.md).")

    async def search_cars(query: str) -> WebResult:
        raise AssertionError(query)

    agent, poster, _ = _agent("unused", search_cars)
    agent.llm = ToolLLM()
    agent.create_task = create_task

    await agent.handle(_event("what is a hybrid?", trigger_id="t_kb"))
    assert len(poster.posts) == 1
    assert "(src: hybrid.md)" in poster.posts[0][0]

    agent.llm = ToolLLM()
    await agent.handle(_event("could you assign Ali to finish the cost sheet by Friday?", "t_task"))
    assert len(created) == 1
    assert created[0].owner == "Ali"
    assert poster.posts[-1][0] == "✓ Task added: Ali — cost sheet — Fri"
    assert poster.posts[-1][1] is PostReason.CONFIRMATION


@pytest.mark.asyncio
async def test_t12_llm_failure_fallback_answers_knowledge_digest_and_tasks() -> None:
    created = []

    async def create_task(record):
        created.append(record)
        return TaskWrite(True, "✓ Task added: Ali — cost sheet — Fri", page_id="p1", key=record.key)

    class FailingLLM:
        async def respond(self, *args, **kwargs):
            raise RuntimeError("401 Invalid API Key")

    async def search_cars(query: str) -> WebResult:
        raise RuntimeError("401 Invalid API Key")

    agent, poster, _ = _agent("unused", search_cars)
    agent.llm = FailingLLM()
    agent.create_task = create_task

    await agent.handle(_event("what is a hybrid?", trigger_id="t_fb_kb"))
    assert "(src: hybrid.md)" in poster.posts[-1][0]

    await agent.handle(_event("what did Sara say?", trigger_id="t_fb_digest"))
    assert "Sara said the budget is locked" in poster.posts[-1][0]

    await agent.handle(_event("Ali, please finish the cost sheet by Friday", trigger_id="t_fb_task"))
    assert len(created) == 1
    assert created[0].owner == "Ali"
    assert poster.posts[-1][1] is PostReason.CONFIRMATION


@pytest.mark.asyncio
async def test_t12_activity_events_emitted_during_agent_actions() -> None:
    activities: list[tuple[str, str]] = []

    async def search_cars(query: str) -> WebResult:
        return WebResult(text="Listed on the manufacturer page.", urls=["https://example.com/rav4"])

    agent, _poster, _created = _agent(
        "The current price is on the manufacturer page. https://example.com/rav4",
        search_cars,
    )
    agent.on_activity = lambda text, kind: activities.append((text, kind))

    await agent.handle(_event(QUESTION, trigger_id="t_act_q"))
    kinds = [kind for _text, kind in activities]
    texts = [text.lower() for text, _kind in activities]
    assert "working" in kinds
    assert kinds[-1] == "success"
    assert any("searching knowledge base" in t for t in texts)
    assert any("searching web" in t for t in texts)
    assert any("thinking" in t for t in texts)
    assert any("posting reply" in t for t in texts)
    assert any("replied to sara" in t for t in texts)

    activities.clear()
    await agent.handle(_event("", trigger_id="t_act_empty"))
    assert any("asking for clarification" in t.lower() for t, _k in activities)
    assert activities[-1][1] == "info"
    assert "follow-up" in activities[-1][0].lower()


@pytest.mark.asyncio
async def test_t12_stored_database_context_and_web_fallback_priority() -> None:
    class FailingLLM:
        async def respond(self, *args, **kwargs):
            raise RuntimeError("401 Invalid API Key")

    async def search_cars(query: str) -> WebResult:
        return WebResult(
            text="The 2025 Toyota RAV4 Hybrid starts at $32,850 MSRP.",
            urls=["https://www.toyota.com/rav4hybrid"],
        )

    db_ctx = (
        "Stored Notion Tasks:\n"
        "- Ali: Send competitor pricing table for 3 compact SUVs (due 2026-04-16) [Todo]\n"
        "- Omar: Finalize brake pad supplier shortlist (due 2026-04-17) [Done]\n"
        "Stored Notion Meeting Notes:\n"
        "- powertrain-kickoff (2026-04-10): Agreed to target a 60 kWh LFP pack for the entry trim."
    )

    agent, poster, _ = _agent("unused", search_cars)
    agent.llm = FailingLLM()
    agent.digest = lambda: "Participants: none\nTasks:\nnone\nRecent:"
    agent.fetch_db_context = lambda: db_ctx

    # 1. Price question prefers live web result over partial examples.md hit when LLM fails.
    await agent.handle(
        _event("what is the current price of a Toyota RAV4 hybrid?", trigger_id="t_web_fb")
    )
    reply_web = poster.posts[-1][0]
    assert "$32,850" in reply_web
    assert "https://www.toyota.com/rav4hybrid" in reply_web

    # 2. Stored task query returns tasks from Notion/SQLite database context.
    await agent.handle(_event("what tasks are assigned to Ali in Notion?", trigger_id="t_db_tasks"))
    reply_tasks = poster.posts[-1][0]
    assert "Ali: Send competitor pricing table for 3 compact SUVs" in reply_tasks

    # 3. Past meeting summary query returns stored Notion meeting notes.
    await agent.handle(
        _event("what did we decide in the powertrain-kickoff meeting?", trigger_id="t_db_notes")
    )
    reply_notes = poster.posts[-1][0]
    assert "60 kWh LFP pack" in reply_notes


@pytest.mark.asyncio
async def test_t12_off_topic_non_car_questions_are_refused() -> None:
    from ba_agent.agent.loop import OFF_TOPIC

    searched: list[str] = []

    async def search_cars(query: str) -> WebResult:
        searched.append(query)
        raise AssertionError(f"search_cars must not be called for non-car query: {query}")

    agent, poster, _ = _agent("unused", search_cars)
    for idx, q in enumerate(
        [
            "what is the capital of France?",
            "search the web for Bitcoin price today",
            "look up the weather in Tokyo",
            "write me a recipe for chocolate cake",
        ]
    ):
        await agent.handle(_event(q, trigger_id=f"t_off_{idx}"))
        assert poster.posts[-1][0] == OFF_TOPIC
    assert searched == []


@pytest.mark.asyncio
async def test_t12_checks_previous_meetings_and_tasks_db_even_when_llm_misses_or_calls_tool() -> None:
    db_ctx = (
        "Stored Notion Tasks:\n"
        "- Ali: Send competitor pricing table for 3 compact SUVs (due: 2026-04-16) [Todo]\n"
        "- Omar: Finalize brake pad supplier shortlist (due: 2026-04-17) [Done]\n"
        "Stored Local Tasks:\n"
        "- Sara: Prepare Q2 warranty slide deck (due: 2026-04-20) [Todo]\n"
        "Stored Notion Meeting Notes:\n"
        "- powertrain-kickoff (2026-04-10): Agreed to target a 60 kWh LFP pack for the entry trim.\n"
        "Previous Local Meeting Captions:\n"
        "[02:10] Omar: We also locked the thermal valve supplier for the prototype."
    )

    class EmptyOrFalseEmptyLLM:
        def __init__(self, mode: str) -> None:
            self.mode = mode

        async def respond(self, input, tools=None, *, enable_web_search=False, timeout_s=None):
            if self.mode == "empty":
                return LLMResponse(text="")
            if self.mode == "false_empty":
                return LLMResponse(text="There are no tasks in this meeting.")
            if self.mode == "spurious_create_task" and tools:
                return LLMResponse(
                    function_calls=[
                        FunctionCall(name="create_task", arguments={"task": "", "owner": "Ali"})
                    ]
                )
            return LLMResponse(
                text="Ali has Send competitor pricing table, Omar has Finalize brake pad supplier shortlist, and Sara has Prepare Q2 warranty slide deck."
            )

    async def search_cars(query: str) -> WebResult:
        return WebResult()

    agent, poster, _ = _agent("unused", search_cars)
    agent.digest = lambda: "Participants: Ali\nTasks:\nnone\nRecent:"
    agent.fetch_db_context = lambda: db_ctx

    # 1. When LLM returns empty string on "can you tell me the tasks that I have for everyone?",
    # agent falls back to full_digest (including Notion & local tasks DB).
    agent.llm = EmptyOrFalseEmptyLLM("empty")
    await agent.handle(
        _event("can you tell me the tasks that I have for everyone?", trigger_id="t_all_tasks")
    )
    reply1 = poster.posts[-1][0]
    assert "Ali: Send competitor pricing table" in reply1
    assert "Omar: Finalize brake pad supplier shortlist" in reply1
    assert "Sara: Prepare Q2 warranty slide deck" in reply1

    # 2. When LLM falsely claims "There are no tasks in this meeting", agent overrides with DB tasks.
    agent.llm = EmptyOrFalseEmptyLLM("false_empty")
    await agent.handle(_event("what tasks do we have?", trigger_id="t_false_empty"))
    reply2 = poster.posts[-1][0]
    assert "Ali: Send competitor pricing table" in reply2

    # 3. When LLM spuriously calls create_task with missing fields on a lookup question,
    # agent ignores the bad tool call instead of arming follow-up / nagging for due date.
    agent.llm = EmptyOrFalseEmptyLLM("spurious_create_task")
    await agent.handle(_event("what tasks are assigned to Ali?", trigger_id="t_spurious_tool"))
    reply3 = poster.posts[-1][0]
    assert "When is that due?" not in reply3
    assert "Who should own that" not in reply3
    assert "Send competitor pricing table" in reply3
    assert agent.pending_task is None
    assert agent.trigger.followup is None

    # 4. When user gives a task command matching an existing task in the DB without repeating the due date,
    # agent resolves the existing task from the tasks DB instead of asking "When is that due?".
    created_records = []

    async def record_create_task(rec):
        created_records.append(rec)
        return TaskWrite(
            False,
            f"↻ Already tracked for {rec.owner}: {rec.task}",
            page_id="existing_p1",
            key=rec.key,
        )

    agent.create_task = record_create_task
    await agent.handle(
        _event(
            "add a task for Ali to send competitor pricing table for 3 compact SUVs",
            trigger_id="t_existing_task_cmd",
        )
    )
    assert len(created_records) == 1
    assert created_records[0].owner == "Ali"
    assert created_records[0].due == date(2026, 4, 16)
    assert "Already tracked" in poster.posts[-1][0]
    assert agent.trigger.followup is None



