"""T06 and T07."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pydantic import BaseModel

from ba_agent.config import load_settings
from ba_agent.knowledge.policy import gather_car_context
from ba_agent.knowledge.search import KnowledgeIndex
from ba_agent.llm.openai_client import OpenAIResponsesClient
from ba_agent.llm.parse import build_search_request, parse_response
from ba_agent.llm.tools import coerce_args
from ba_agent.models import WebResult

TRANSCRIPT = "UNIQUE_TRANSCRIPT_FIXTURE Sara said the budget is locked"


def test_t06_u1_function_call() -> None:
    payload = {
        "output": [
            {
                "type": "function_call",
                "name": "create_task",
                "arguments": '{"task":"cost sheet","owner":"Ali"}',
                "call_id": "call_1",
            }
        ]
    }
    parsed = parse_response(payload)
    assert parsed.function_calls[0].name == "create_task"
    assert parsed.function_calls[0].arguments["owner"] == "Ali"


def test_t06_u2_url_citation() -> None:
    payload = {
        "output": [
            {
                "type": "web_search_call",
                "action": {"query": "toyota rav4 hybrid price"},
            },
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "See the page.",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url": "https://example.com/rav4",
                                "title": "RAV4",
                            }
                        ],
                    }
                ],
            }
        ]
    }
    parsed = parse_response(payload)
    assert parsed.text == "See the page."
    assert parsed.search_queries == ["toyota rav4 hybrid price"]
    assert parsed.citations[0].url == "https://example.com/rav4"


def test_t06_u3_search_body_excludes_transcript() -> None:
    body = build_search_request(
        "what is the current price of a Toyota RAV4 hybrid?",
        "gpt-5.5",
    )
    dumped = json.dumps(body)
    assert TRANSCRIPT not in dumped
    assert "Toyota RAV4 hybrid" in dumped
    assert body["tools"] == [{"type": "web_search"}]
    assert "web_search_preview" not in dumped


class _TaskArgs(BaseModel):
    task: str
    owner: str


def test_t06_coerce_repairs_once() -> None:
    calls = {"n": 0}

    def repair(raw: dict) -> dict:
        calls["n"] += 1
        return {"task": "cost sheet", "owner": "Ali"}

    args = coerce_args({}, _TaskArgs, repair)
    assert args is not None
    assert args.task == "cost sheet"
    assert calls["n"] == 1
    assert coerce_args({}, _TaskArgs, lambda raw: {}) is None


@pytest.mark.live
@pytest.mark.asyncio
async def test_t06_i1_live_web_search() -> None:
    key = os.environ.get("OPENAI_API_KEY") or os.environ.get("BA_LLM__API_KEY")
    if not key or os.environ.get("BA_RUN_LIVE_TESTS") != "1":
        pytest.skip("live OpenAI web search is optional")
    settings = load_settings()
    client = OpenAIResponsesClient(settings)
    result = await client.search_cars("what is the current price of a Toyota RAV4 hybrid?")
    assert result.urls or result.text


def _index() -> KnowledgeIndex:
    return KnowledgeIndex(Path("data/knowledge"), min_score=0.5)


def test_t07_u1_hybrid_chunk() -> None:
    index = _index()
    hits = index.search("hybrid")
    assert hits
    assert hits[0].file == "hybrid.md"
    assert "hybrid.md" in hits[0].source


def test_t07_u2_empty_overlap() -> None:
    index = _index()
    assert index.search("zzzzqqqq") == []


@pytest.mark.asyncio
async def test_t07_u3_weak_score_calls_search_cars() -> None:
    index = _index()
    seen: list[str] = []

    async def search_cars(query: str) -> WebResult:
        seen.append(query)
        return WebResult(text="listed at the manufacturer", urls=["https://example.com/rav4"])

    question = "what is the current price of a Toyota RAV4 hybrid?"
    await gather_car_context(question, index, search_cars, min_score=0.5)
    assert seen == [question]
    assert TRANSCRIPT not in seen[0]


def test_knowledge_files_have_no_wake_table() -> None:
    root = Path("data/knowledge")
    for path in root.glob("*.md"):
        text = path.read_text(encoding="utf-8").lower()
        assert "hey ba" not in text


def test_t07_natural_language_knowledge_queries_and_policy() -> None:
    from ba_agent.knowledge.policy import (
        is_car_question,
        is_meeting_question,
        needs_current_fact,
        should_search_web,
    )

    index = _index()
    regen_hits = index.search("how does regenerative braking work in a hybrid car?")
    assert regen_hits
    assert regen_hits[0].file in {"safety.md", "hybrid.md"}

    lfp_hits = index.search("what is the difference between LFP and NMC battery chemistry?")
    assert lfp_hits
    assert lfp_hits[0].file == "ev.md"

    # Database, task, and past-meeting questions are classified as meeting questions.
    for q in [
        "what tasks are in Notion?",
        "what are Ali's tasks?",
        "what did we discuss in the powertrain-kickoff meeting?",
        "who is working on the brake pad supplier shortlist?",
        "Ali, please finish the cost sheet by Friday",
    ]:
        assert is_meeting_question(q), q
        assert not is_car_question(q, []), q

    # Specific car models, prices, and comparisons trigger web search even with local hits.
    price_q = "what is the current price of a Toyota RAV4 hybrid?"
    price_hits = index.search(price_q)
    assert is_car_question(price_q, price_hits)
    assert needs_current_fact(price_q)
    assert should_search_web(price_q, price_hits, min_score=0.5)


def test_bm25_acronym_and_four_letter_plural_queries_hit_knowledge() -> None:
    from ba_agent.knowledge.policy import needs_current_fact, should_search_web

    index = KnowledgeIndex(Path("data/knowledge"), 0.5)
    for query in [
        "what does AEB stand for in modern cars?",
        "what is ESC in SUVs?",
        "WLTP vs EPA range",
        "how does lane keeping assist work?",
    ]:
        hits = index.search(query)
        assert hits, query
        assert 0.5 <= hits[0].score <= 1.0, (query, hits[0].score)
        assert needs_current_fact(query) is False, query

    assert needs_current_fact("2025 Tesla Model 3 EPA range") is True
    assert needs_current_fact("why do 800V EVs draw lower current when charging?") is False
    assert needs_current_fact("what safety aspects matter in crumple zones?") is False

    # Unseen technical noun ("camshaft") must not be masked by low default IDF on "engine".
    cam_q = "how does a camshaft work in an engine?"
    cam_hits = index.search(cam_q)
    assert should_search_web(cam_q, cam_hits, min_score=0.5) is True


@pytest.mark.asyncio
async def test_search_cars_ttl_cache_and_warm_up_model_fallback() -> None:
    from ba_agent.models import LLMResponse

    client = OpenAIResponsesClient(api_key="test-key", model="gpt-6-luna")

    class _FakeNotFoundError(Exception):
        status_code = 404

    class _FakeModels:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def retrieve(self, model_name: str) -> dict:
            self.calls.append(model_name)
            raise _FakeNotFoundError("model not found")

    class _FakeSDK:
        def __init__(self) -> None:
            self.models = _FakeModels()

        async def close(self) -> None:
            return None

    fake_sdk = _FakeSDK()
    client._client = fake_sdk
    await client.warm_up()
    assert fake_sdk.models.calls == ["gpt-6-luna"]
    assert client._fallback_model == "gpt-4o-mini"

    respond_calls = 0

    async def fake_respond(*_args, **_kwargs) -> LLMResponse:
        nonlocal respond_calls
        respond_calls += 1
        return LLMResponse(
            text="Toyota RAV4 Hybrid starts around $32,850 (https://example.com/rav4).",
            search_queries=["2025 Toyota RAV4 hybrid price"],
        )

    client.respond = fake_respond  # type: ignore[method-assign]
    first = await client.search_cars("2025 Toyota RAV4 hybrid price")
    second = await client.search_cars("  2025 toyota   rav4 hybrid price ")
    assert first.useful is True
    assert second.text == first.text
    assert respond_calls == 1
    await client.aclose()


