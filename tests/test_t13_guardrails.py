"""T13-U1 through T13-U4."""

from __future__ import annotations

from ba_agent.agent.loop import _prompt
from ba_agent.guardrails.citations import NOT_SURE, finalize_reply, reply_status
from ba_agent.guardrails.search import query_allowed, strip_meeting_text
from ba_agent.logging_setup import redact_secrets

CAPTION = "Let's use the hybrid system in town"


def test_t13_u1_uncited_price_is_blocked() -> None:
    draft = "The RAV4 hybrid is $31,000"
    assert reply_status(draft) == "regenerate"
    assert finalize_reply(draft, draft) == NOT_SURE


def test_t13_u2_citation_is_allowed() -> None:
    assert reply_status("A hybrid uses an engine and a motor (src: hybrid.md).") == "allow"
    assert reply_status("See https://example.com/rav4 for the current price.") == "allow"


def test_t13_u3_transcript_query_is_rejected() -> None:
    assert query_allowed(f"price of {CAPTION}", [CAPTION], ["Sara", "Ali"]) is False
    assert query_allowed(
        "notes at https://www.notion.so/TechSol-BA-123",
        [],
        [],
    ) is False
    assert query_allowed(
        "call at https://meet.google.com/abc-defg-hij",
        [],
        [],
    ) is False
    assert query_allowed("[01:15] what is a hybrid", [], []) is False
    assert query_allowed("Sara, Ali", [], ["Sara", "Ali"]) is False
    assert query_allowed("Sara asked what is a hybrid", [], ["Sara"]) is False
    cleaned = strip_meeting_text(
        "Sara asked what is the price of a Toyota RAV4 hybrid",
        [CAPTION],
        ["Sara", "Ali"],
    )
    assert "sara" not in cleaned.lower()
    assert query_allowed(cleaned, [CAPTION], ["Sara", "Ali"]) is True
    assert query_allowed("what is a hybrid", [CAPTION], ["Sara", "Ali"]) is True


def test_t13_u4_not_sure_without_a_number_is_allowed() -> None:
    assert reply_status("I'm not sure about that.") == "allow"


def test_t13_log_redacts_secrets() -> None:
    raw = "auth failed for Bearer secret_1234567890abcdef and key sk-proj-1234567890abcdef"
    redacted = redact_secrets(raw)
    assert "secret_1234567890abcdef" not in redacted
    assert "sk-proj-1234567890abcdef" not in redacted
    assert "[REDACTED]" in redacted


def test_t13_individual_participant_names_and_car_brands_preserved() -> None:
    participants = ["Sara Khan", "Ali", "Ford"]
    assert query_allowed("what car does Sara drive", [], participants) is False
    assert query_allowed("what car does Khan drive", [], participants) is False
    assert query_allowed("2026 Ford hybrid price", [], participants) is True
    stripped = strip_meeting_text(
        "Sara Khan asked about the 2026 Ford Camry hybrid price",
        [],
        participants,
    )
    assert "sara" not in stripped.lower()
    assert "khan" not in stripped.lower()
    assert "ford" in stripped.lower()
    assert "camry" in stripped.lower()
    assert query_allowed(stripped, [], participants) is True


def test_t13_prompt_uses_xml_delimiters_for_untrusted_context() -> None:
    rendered = _prompt(
        "You are TechSol BA.",
        "Participants: Sara",
        "what is a hybrid?",
        [],
        None,
        True,
    )
    assert "<meeting_digest>" in rendered
    assert "</meeting_digest>" in rendered
    assert "<knowledge>" in rendered
    assert "<web_results>" in rendered
    assert "<question>what is a hybrid?</question>" in rendered
    assert "untrusted data" in rendered


def test_t13_generic_speaker_labels_do_not_block_car_search() -> None:
    participants = ["Unknown speaker", "You", "Sara"]
    q = "can you check the speaker system in a 2025 Toyota Camry?"
    assert query_allowed(q, [], participants) is True
    assert query_allowed("what car does Sara drive?", [], participants) is False


def test_t13_non_car_web_search_queries_are_rejected() -> None:
    assert query_allowed("what is the weather in London today?", [], []) is False
    assert query_allowed("search the web for Bitcoin price in USD", [], []) is False
    assert query_allowed("recipe for chocolate cake", [], []) is False
    assert query_allowed("who won the UEFA Champions League?", [], []) is False


def test_t13_prompt_files_and_speaker_date_context() -> None:
    from datetime import date

    from ba_agent.config import read_prompt

    persona = read_prompt("persona.md")
    tasks_prompt = read_prompt("tasks.md")
    summary_prompt = read_prompt("summary.md")

    assert "(src: filename.md)" in persona
    assert "https://" in persona
    assert "I'm not sure — I don't have a source for that." in persona
    assert "untrusted data" in persona
    assert "no Markdown" in persona or "never use Markdown" in persona

    assert '{"tasks":[]}' in tasks_prompt or '{"tasks": []}' in tasks_prompt
    assert '"due_text"' in tasks_prompt
    assert '"said_by"' in tasks_prompt
    assert "untrusted" in tasks_prompt
    assert "28 Oct" in tasks_prompt

    assert "Plain text only" in summary_prompt
    assert "untrusted" in summary_prompt
    assert "No substantive discussion" in summary_prompt

    rendered = _prompt(
        persona,
        "Participants: Sara",
        "what tasks are assigned to me?",
        [],
        None,
        False,
        speaker="Sara",
        today=date(2026, 10, 25),
    )
    assert "Asked by: Sara" in rendered
    assert "Meeting date: 2026-10-25 (Sun)" in rendered
    assert "<web_results>\n(none)\n</web_results>" in rendered
    assert "<question>what tasks are assigned to me?</question>" in rendered


def test_citation_source_verification_and_grounded_auto_citation() -> None:
    import asyncio
    from datetime import date

    from ba_agent.agent.loop import AgentLoop
    from ba_agent.guardrails.citations import (
        numbers_grounded_in_context,
        verify_reply_sources,
    )
    from ba_agent.knowledge.search import Chunk
    from ba_agent.models import LLMResponse, TriggerEvent, WebResult
    from ba_agent.notion.client import TaskWrite
    from ba_agent.trigger.detector import TriggerDetector

    # Rejects fabricated local file citations not present in retrieved hits.
    assert verify_reply_sources("BEV packs run at 400V (src: fake.md).", ["ev.md"], []) is False
    assert verify_reply_sources("BEV packs run at 400V (src: ev.md).", ["ev.md"], []) is True
    assert (
        verify_reply_sources(
            "RAV4 starts at $32,850 (src: https://edmunds.com/toyota/rav4).",
            [],
            ["https://www.edmunds.com/toyota/rav4/?utm_source=openai"],
        )
        is True
    )
    assert (
        verify_reply_sources(
            "RAV4 overview (https://en.wikipedia.org/wiki/Toyota_RAV4_(XA50)).",
            [],
            ["https://en.wikipedia.org/wiki/Toyota_RAV4_(XA50)"],
        )
        is True
    )
    assert (
        verify_reply_sources(
            "CR-V starts at $31,000 (https://edmunds.com/honda/cr-v).",
            [],
            ["https://www.edmunds.com/toyota/rav4/?utm_source=openai"],
        )
        is False
    )

    # "Model 3 v Model Y" must not trigger numeric voltage grounding on "3 v".
    from ba_agent.guardrails.citations import has_precise_number

    assert has_precise_number("I'm not sure about Model 3 v Model Y.") is False
    assert has_precise_number("Architecture runs at 400V.") is True
    assert has_precise_number("Mild hybrids run at 48 volts.") is True

    # Rejects ungrounded numeric claims while accepting numbers grounded in context.
    ctx = "800V EV architectures cut DC fast-charging losses by 40% vs 400V systems."
    assert numbers_grounded_in_context("800V packs charge faster at $29,990.", ctx) is False
    assert numbers_grounded_in_context("800V cuts losses by 40% over 400V.", ctx) is True

    # Deterministic auto-citation in AgentLoop when a single hit grounds all numbers.
    class _SingleChunkKB:
        def search(self, _query: str):
            return [Chunk(file="ev.md", heading="800V", text=ctx, score=0.9)]

    class _UncitedOnceLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def respond(self, input, tools=None, *, enable_web_search=False, timeout_s=None):
            del input, tools, enable_web_search, timeout_s
            self.calls += 1
            return LLMResponse(text="800V packs cut losses by 40% compared to 400V systems.")

    posts: list[str] = []

    async def fake_post(text: str, _reason, trigger_id=None) -> bool:
        del trigger_id
        posts.append(text)
        return True

    async def no_web(_q: str) -> WebResult:
        return WebResult()

    async def no_task(_rec):
        return TaskWrite(True, "ok")

    async def no_save() -> str:
        return "https://www.notion.so/p1"

    async def no_leave() -> None:
        return None

    llm = _UncitedOnceLLM()
    agent = AgentLoop(
        knowledge=_SingleChunkKB(),  # type: ignore[arg-type]
        search_cars=no_web,
        llm=llm,
        create_task=no_task,
        save_meeting=no_save,
        post=fake_post,
        trigger=TriggerDetector(pause_wait_s=5, dedup_window_s=3, followup_window_s=20),
        leave=no_leave,
        digest=lambda: "",
        today=lambda: date(2026, 4, 15),
        min_score=0.5,
        timeout_s=5,
    )
    asyncio.run(
        agent.handle(
            TriggerEvent(
                trigger_id="t_cite",
                question="what are the benefits of an 800V EV architecture?",
                speaker="Sara",
                source="speech",
                t_detected=1.0,
            )
        )
    )
    assert llm.calls == 1
    assert "(src: ev.md)" in posts[-1]

    # Rejects fabricated citation even on second pass.
    rejected = finalize_reply(
        "The model starts at $32,000 (src: hallucinated.md).",
        "Still $32,000 (src: hallucinated.md).",
        allowed_files=["ev.md"],
        allowed_urls=[],
        context_text=ctx,
    )
    assert rejected == NOT_SURE




