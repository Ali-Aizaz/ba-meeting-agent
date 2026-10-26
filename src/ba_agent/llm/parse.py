"""Parse a Responses API payload. Web search uses the web_search tool."""

from __future__ import annotations

import json

from ba_agent.models import FunctionCall, LLMResponse, WebCitation

SEARCH_INSTRUCTION = "Answer with public car facts and cite the source URLs."


def build_search_request(query: str, model: str) -> dict:
    """The body is the car question plus one instruction. Nothing else is attached."""
    return {
        "model": model,
        "input": f"{query.strip()}\n\n{SEARCH_INSTRUCTION}",
        "tools": [{"type": "web_search"}],
    }


def parse_response(payload: dict) -> LLMResponse:
    texts: list[str] = []
    calls: list[FunctionCall] = []
    citations: list[WebCitation] = []
    queries: list[str] = []
    for item in payload.get("output") or []:
        kind = item.get("type")
        if kind == "function_call":
            calls.append(
                FunctionCall(
                    name=item.get("name") or "",
                    arguments=_arguments(item.get("arguments")),
                )
            )
        elif kind == "web_search_call":
            action = item.get("action") or {}
            query = action.get("query") or item.get("query") or ""
            if query:
                queries.append(query)
        elif kind == "message":
            for block in item.get("content") or []:
                if block.get("type") in {"output_text", "text"}:
                    texts.append(block.get("text") or "")
                for annotation in block.get("annotations") or []:
                    if annotation.get("type") == "url_citation" and annotation.get("url"):
                        citations.append(
                            WebCitation(url=annotation["url"], title=annotation.get("title") or "")
                        )
    return LLMResponse(
        text="\n".join(part for part in texts if part).strip(),
        function_calls=calls,
        citations=citations,
        search_queries=queries,
    )


def _arguments(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}
