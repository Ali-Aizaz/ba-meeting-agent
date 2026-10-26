"""LLM client factory. OpenAI is the built provider."""

from __future__ import annotations

from ba_agent.config import Settings, StartupError


def build_llm(settings: Settings):
    provider = settings.llm.provider.strip().lower()
    if provider == "openai":
        from ba_agent.llm.openai_client import OpenAIResponsesClient

        key = ""
        if settings.llm.api_key is not None:
            key = settings.llm.api_key.get_secret_value()
        return OpenAIResponsesClient(
            api_key=key,
            model=settings.llm.model,
            base_url=settings.llm.base_url,
            timeout_s=settings.llm.timeout_s,
        )
    if provider in {"claude", "gemini"}:
        raise NotImplementedError(
            f"Provider {provider!r} is not implemented. Set llm.provider to openai."
        )
    raise StartupError(f"Unknown llm.provider {provider!r}")
