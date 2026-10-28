"""OpenAI Responses API client."""

from __future__ import annotations

import html
import logging
import re
import time
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx

from ba_agent.config import Settings
from ba_agent.llm.parse import build_search_request, parse_response
from ba_agent.models import LLMResponse, WebResult

logger = logging.getLogger("ba_agent.llm")

_INLINE_URL = re.compile(r"https?://[^\s)>\]\"']+", re.I)
_HTML_TAG = re.compile(r"<[^>]+>")
_DDG_SNIPPET = re.compile(r'class="result__snippet[^"]*"[^>]*>(.*?)</a>', re.S | re.I)
_DDG_LINK = re.compile(r'class="result__a"[^>]*href="([^"]+)"', re.I)
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class OpenAIResponsesClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        api_key: str = "",
        model: str = "gpt-5.5",
        base_url: str = "https://api.openai.com/v1",
        timeout_s: float = 25.0,
    ) -> None:
        if settings is not None:
            api_key = (
                settings.llm.api_key.get_secret_value()
                if settings.llm.api_key is not None
                else api_key
            )
            model = settings.llm.model
            base_url = settings.llm.base_url
            timeout_s = settings.llm.timeout_s
        self.model = model
        self.timeout_s = timeout_s
        self._api_key = api_key
        self._base_url = base_url
        self._client = None
        self._fallback_model: str | None = None

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
        model_override: str | None = None,
    ) -> LLMResponse:
        tool_list = list(tools or [])
        if enable_web_search and not any(tool.get("type") == "web_search" for tool in tool_list):
            tool_list.append({"type": "web_search"})
        chosen_model = model_override or self._fallback_model or self.model
        started = time.perf_counter()
        try:
            response = await self._sdk().responses.create(
                model=chosen_model,
                input=input,
                tools=tool_list or None,
                store=False,
                timeout=timeout_s or self.timeout_s,
            )
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if chosen_model != "gpt-4o-mini" and (
                status in (400, 404) or "model" in str(exc).lower()
            ):
                logger.warning(
                    "model %s rejected (%s); retrying with gpt-4o-mini",
                    chosen_model,
                    exc,
                )
                if model_override is None:
                    self._fallback_model = "gpt-4o-mini"
                chosen_model = "gpt-4o-mini"
                response = await self._sdk().responses.create(
                    model=chosen_model,
                    input=input,
                    tools=tool_list or None,
                    store=False,
                    timeout=timeout_s or self.timeout_s,
                )
            else:
                raise
        logger.info(
            "llm respond model=%s web_search=%s duration_ms=%d",
            chosen_model,
            enable_web_search,
            int((time.perf_counter() - started) * 1000),
        )
        payload = response.model_dump() if hasattr(response, "model_dump") else dict(response)
        return parse_response(payload)

    async def search_cars(self, query: str) -> WebResult:
        body = build_search_request(query, self.model)
        try:
            response = await self.respond(
                input=body["input"],
                tools=body["tools"],
                enable_web_search=True,
                timeout_s=self.timeout_s,
            )
            urls = [citation.url for citation in response.citations if citation.url]
            if not urls and response.text:
                urls = [url.rstrip(".,;:") for url in _INLINE_URL.findall(response.text)]
            dedup_urls = list(dict.fromkeys(urls))
            result = WebResult(
                text=response.text,
                urls=dedup_urls,
                queries=response.search_queries or [query],
            )
            if result.useful:
                return result
        except Exception:
            logger.warning(
                "OpenAI web_search failed for %r; falling back to public web search",
                query,
                exc_info=True,
            )
        return await _public_web_search(query, timeout_s=min(self.timeout_s, 10.0))


async def _public_web_search(query: str, timeout_s: float = 10.0) -> WebResult:
    cleaned = query.strip()
    if not cleaned:
        return WebResult()
    headers = {"User-Agent": _USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    async with httpx.AsyncClient(
        headers=headers,
        follow_redirects=True,
        timeout=httpx.Timeout(timeout_s, connect=5.0),
    ) as client:
        try:
            resp = await client.get("https://html.duckduckgo.com/html/", params={"q": cleaned})
            if resp.status_code == 200 and resp.text:
                raw_snippets = _DDG_SNIPPET.findall(resp.text)
                raw_links = _DDG_LINK.findall(resp.text)
                urls: list[str] = []
                for lnk in raw_links:
                    url = _extract_ddg_url(lnk)
                    if url and url not in urls:
                        urls.append(url)
                snippets: list[str] = []
                for raw in raw_snippets:
                    text = " ".join(html.unescape(_HTML_TAG.sub(" ", raw)).split()).strip()
                    if text and text not in snippets:
                        snippets.append(text)
                if snippets or urls:
                    summary = " ".join(snippets[:2]).strip()
                    return WebResult(text=summary, urls=urls[:5], queries=[cleaned])
        except Exception:
            logger.debug("DuckDuckGo HTML search failed for %r", cleaned, exc_info=True)

        try:
            wiki_resp = await client.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": cleaned,
                    "utf8": "1",
                    "format": "json",
                    "srlimit": "3",
                },
            )
            if wiki_resp.status_code == 200:
                data = wiki_resp.json()
                search_items = (data.get("query") or {}).get("search") or []
                wiki_snippets: list[str] = []
                wiki_urls: list[str] = []
                for item in search_items:
                    title = str(item.get("title") or "").strip()
                    raw_snip = str(item.get("snippet") or "")
                    snip = " ".join(html.unescape(_HTML_TAG.sub(" ", raw_snip)).split()).strip()
                    if title:
                        url = f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
                        if url not in wiki_urls:
                            wiki_urls.append(url)
                    if snip:
                        wiki_snippets.append(f"{title}: {snip}" if title else snip)
                if wiki_snippets or wiki_urls:
                    return WebResult(
                        text=" ".join(wiki_snippets[:2]).strip(),
                        urls=wiki_urls[:3],
                        queries=[cleaned],
                    )
        except Exception:
            logger.debug("Wikipedia fallback search failed for %r", cleaned, exc_info=True)

    return WebResult(queries=[cleaned])


def _extract_ddg_url(raw_href: str) -> str:
    href = html.unescape(raw_href.strip())
    if "uddg=" in href:
        qs = parse_qs(urlparse(href).query)
        if "uddg" in qs and qs["uddg"]:
            candidate = unquote(qs["uddg"][0]).strip()
            if (
                candidate.startswith(("http://", "https://"))
                and "duckduckgo.com/y.js" not in candidate
            ):
                return candidate
    if href.startswith(("http://", "https://")) and "duckduckgo.com" not in href:
        return href
    return ""
