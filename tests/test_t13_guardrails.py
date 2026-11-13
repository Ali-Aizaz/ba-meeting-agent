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



