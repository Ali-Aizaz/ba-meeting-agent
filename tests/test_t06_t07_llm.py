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
