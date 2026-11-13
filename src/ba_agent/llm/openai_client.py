"""OpenAI Responses API client."""

from __future__ import annotations

import html
import inspect
import logging
import re
import time
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx

from ba_agent.config import Settings
from ba_agent.knowledge.policy import has_car_topic
from ba_agent.llm.parse import build_search_request, parse_response
from ba_agent.models import LLMResponse, WebResult

logger = logging.getLogger("ba_agent.llm")

_FALLBACK_MODEL = "gpt-4o-mini"
_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")
_WEB_CACHE_TTL_S = 600.0
_WEB_CACHE_MAX = 64
_INLINE_URL = re.compile(r"https?://[^\s)>\]\"']+", re.I)
_HTML_TAG = re.compile(r"<[^>]+>")
_DDG_SNIPPET = re.compile(r'class="result__snippet[^"]*"[^>]*>(.*?)</a>', re.S | re.I)
_DDG_LINK = re.compile(r'class="result__a"[^>]*href="([^"]+)"', re.I)
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _is_model_rejection(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    return status in (400, 404) or "model" in str(exc).lower()


def _is_auth_error(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status == 401:
        return True
    low = str(exc).lower()
    return (
        "expired_secret_key" in low
        or "invalid_api_key" in low
        or "incorrect api key" in low
        or "error code: 401" in low
    )


class OpenAIResponsesClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        api_key: str = "",
        model: str = "gpt-6-luna",
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
        self.auth_error: str | None = None
        self._no_reasoning_models: set[str] = set()
        self._web_cache: dict[str, tuple[float, WebResult]] = {}
        self._web_http: httpx.AsyncClient | None = None
        self._web_http_factory: object | None = None

    def _sdk(self):
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=self.timeout_s,
            )
        return self._client

    def _get_web_http(self, timeout_s: float) -> httpx.AsyncClient:
        current_factory = httpx.AsyncClient
        if (
            self._web_http is None
            or self._web_http_factory is not current_factory
            or bool(getattr(self._web_http, "is_closed", False))
        ):
            self._web_http = current_factory(
                headers={"User-Agent": _USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
                follow_redirects=True,
                timeout=httpx.Timeout(timeout_s, connect=5.0),
            )
            self._web_http_factory = current_factory
        return self._web_http

    async def aclose(self) -> None:
        if self._web_http is not None:
            try:
                await self._web_http.aclose()
            except Exception:
                pass
            self._web_http = None
            self._web_http_factory = None
        if self._client is not None and hasattr(self._client, "close"):
            try:
                res = self._client.close()
                if inspect.isawaitable(res):
                    await res
            except Exception:
                pass

    async def warm_up(self) -> None:
        """Warm the OpenAI TLS connection with zero tokens and probe model availability."""
        chosen_model = self._fallback_model or self.model
        try:
            models_api = getattr(self._sdk(), "models", None)
            retrieve = getattr(models_api, "retrieve", None) if models_api is not None else None
            if not callable(retrieve):
                return
            res = retrieve(chosen_model)
            if inspect.isawaitable(res):
                await res
            self.auth_error = None
            logger.info("llm warm_up ok model=%s", chosen_model)
        except Exception as exc:
            if _is_auth_error(exc):
                self.auth_error = "OpenAI API key is invalid or expired (HTTP 401)"
                logger.error("llm warm_up authentication failed (check BA_LLM__API_KEY): %s", exc)
            elif chosen_model != _FALLBACK_MODEL and _is_model_rejection(exc):
                logger.warning(
                    "warm_up model %s rejected (%s); falling back to %s",
                    chosen_model,
                    exc,
                    _FALLBACK_MODEL,
                )
                self._fallback_model = _FALLBACK_MODEL
            else:
                logger.debug("llm warm_up probe skipped/failed for %s: %s", chosen_model, exc)

    def _extra_create_kwargs(
        self,
        chosen_model: str,
        *,
        max_output_tokens: int | None,
        enable_web_search: bool,
    ) -> dict:
        extra: dict = {}
        if max_output_tokens is not None and not enable_web_search:
            extra["max_output_tokens"] = max(400, int(max_output_tokens))
        if (
            chosen_model.startswith(_REASONING_PREFIXES)
            and chosen_model not in self._no_reasoning_models
        ):
            extra["reasoning"] = {"effort": "low"}
        return extra

    async def respond(
        self,
        input: str,
        tools: list[dict] | None = None,
        *,
        enable_web_search: bool = False,
        timeout_s: float | None = None,
        model_override: str | None = None,
        max_output_tokens: int | None = None,
    ) -> LLMResponse:
        tool_list = list(tools or [])
        if enable_web_search and not any(tool.get("type") == "web_search" for tool in tool_list):
            tool_list.append({"type": "web_search"})
        chosen_model = model_override or self._fallback_model or self.model
        started = time.perf_counter()
        create_fn = self._sdk().responses.create
        extra = self._extra_create_kwargs(
            chosen_model,
            max_output_tokens=max_output_tokens,
            enable_web_search=enable_web_search,
        )
        try:
            response = await create_fn(
                model=chosen_model,
                input=input,
                tools=tool_list or None,
                store=False,
                timeout=timeout_s or self.timeout_s,
                **extra,
            )
        except Exception as exc:
            err_msg = str(exc).lower()
            if "reasoning" in extra and "reasoning" in err_msg and "model" not in err_msg:
                self._no_reasoning_models.add(chosen_model)
                extra_no_reasoning = {k: v for k, v in extra.items() if k != "reasoning"}
                response = await create_fn(
                    model=chosen_model,
                    input=input,
                    tools=tool_list or None,
                    store=False,
                    timeout=timeout_s or self.timeout_s,
                    **extra_no_reasoning,
                )
            elif chosen_model != _FALLBACK_MODEL and _is_model_rejection(exc):
                logger.warning(
                    "model %s rejected (%s); retrying with %s",
                    chosen_model,
                    exc,
                    _FALLBACK_MODEL,
                )
                if model_override is None:
                    self._fallback_model = _FALLBACK_MODEL
                chosen_model = _FALLBACK_MODEL
                retry_extra = self._extra_create_kwargs(
                    chosen_model,
                    max_output_tokens=max_output_tokens,
                    enable_web_search=enable_web_search,
                )
                response = await create_fn(
                    model=chosen_model,
                    input=input,
                    tools=tool_list or None,
                    store=False,
                    timeout=timeout_s or self.timeout_s,
                    **retry_extra,
                )
            else:
                if _is_auth_error(exc):
                    self.auth_error = "OpenAI API key is invalid or expired (HTTP 401)"
                    logger.error("OpenAI respond failed with HTTP 401 (check BA_LLM__API_KEY)")
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
        if not has_car_topic(query):
            logger.info("search_cars blocked non-car query: %r", query)
            return WebResult()
        cache_key = " ".join(query.lower().split())
        cached = self._web_cache.get(cache_key)
        if cached is not None:
            cached_at, cached_result = cached
            if (time.monotonic() - cached_at) < _WEB_CACHE_TTL_S:
                return cached_result
            self._web_cache.pop(cache_key, None)

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
                self._cache_web_result(cache_key, result)
                return result
        except Exception:
            logger.warning(
                "OpenAI web_search failed for %r; falling back to public web search",
                query,
                exc_info=True,
            )
        fallback = await _public_web_search(
            query,
            timeout_s=min(self.timeout_s, 10.0),
            http_client=self._get_web_http(min(self.timeout_s, 10.0)),
        )
        if fallback.useful:
            self._cache_web_result(cache_key, fallback)
        return fallback

    def _cache_web_result(self, key: str, result: WebResult) -> None:
        if len(self._web_cache) >= _WEB_CACHE_MAX and key not in self._web_cache:
            oldest_key = next(iter(self._web_cache))
            self._web_cache.pop(oldest_key, None)
        self._web_cache[key] = (time.monotonic(), result)


async def _public_web_search(
    query: str,
    timeout_s: float = 10.0,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> WebResult:
    cleaned = query.strip()
    if not cleaned:
        return WebResult()
    headers = {"User-Agent": _USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(
        headers=headers,
        follow_redirects=True,
        timeout=httpx.Timeout(timeout_s, connect=5.0),
    )
    try:
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
    finally:
        if owns_client:
            await client.aclose()


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
