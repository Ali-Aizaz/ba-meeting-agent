"""T13-U1 through T13-U4."""

from __future__ import annotations

from ba_agent.guardrails.citations import NOT_SURE, finalize_reply, reply_status
from ba_agent.guardrails.search import query_allowed

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
    assert query_allowed("Sara, Ali", [], ["Sara", "Ali"]) is False
    assert query_allowed("what is a hybrid", [CAPTION], ["Sara", "Ali"]) is True


def test_t13_u4_not_sure_without_a_number_is_allowed() -> None:
    assert reply_status("I'm not sure about that.") == "allow"
