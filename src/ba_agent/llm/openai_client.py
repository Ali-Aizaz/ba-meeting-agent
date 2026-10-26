"""OpenAI Responses API client."""

from __future__ import annotations

import logging
import time

from ba_agent.llm.parse import build_search_request, parse_response
from ba_agent.models import LLMResponse, WebResult

logger = logging.getLogger("ba_agent.llm")


class OpenAIResponsesClient:
    def __init__(self, *, api_key: str, model: str, base_url: str, timeout_s: float) -> None:
        self.model = model
        self.timeout_s = timeout_s
        self._api_key = api_key
        self._base_url = base_url
        self._client = None

    def _sdk(self):
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=self.timeout_s,
            )
        return self._client

    async def respond(
        self,
        input: str,
        tools: list[dict] | None = None,
        *,
        enable_web_search: bool = False,
        timeout_s: float | None = None,
    ) -> LLMResponse:
        tool_list = list(tools or [])
        if enable_web_search and not any(tool.get("type") == "web_search" for tool in tool_list):
            tool_list.append({"type": "web_search"})
        started = time.perf_counter()
        response = await self._sdk().responses.create(
            model=self.model,
            input=input,
            tools=tool_list or None,
            timeout=timeout_s or self.timeout_s,
        )
        logger.info(
            "llm respond model=%s web_search=%s duration_ms=%d",
            self.model,
            enable_web_search,
            int((time.perf_counter() - started) * 1000),
        )
        payload = response.model_dump() if hasattr(response, "model_dump") else dict(response)
        return parse_response(payload)

    async def search_cars(self, query: str) -> WebResult:
        body = build_search_request(query, self.model)
        response = await self.respond(
            input=body["input"],
            tools=body["tools"],
            enable_web_search=True,
            timeout_s=self.timeout_s,
        )
        urls = [citation.url for citation in response.citations]
        return WebResult(text=response.text, urls=urls, queries=response.search_queries or [query])
