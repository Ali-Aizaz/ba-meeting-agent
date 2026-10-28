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
