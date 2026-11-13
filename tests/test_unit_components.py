"""Comprehensive unit tests covering all core Python modules and edge cases."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel, SecretStr
from tests.conftest import clear_secrets

from ba_agent.__main__ import _delegate_to_running_instance, main
from ba_agent.bot.chat_parse import ChatReader, parse_chat_html
from ba_agent.bot.output_gate import OutputGate
from ba_agent.captions.clock import format_meeting_time
from ba_agent.captions.pump import CaptionPump
from ba_agent.captions.stabilize import CaptionRow, Stabilizer, parse_caption_html
from ba_agent.config import (
    StartupError,
    ensure_ready,
    load_settings,
    read_prompt,
    resolve_path,
    validate_meet_url,
)
from ba_agent.events import EventBus
from ba_agent.guardrails.citations import (
    NOT_SURE,
    finalize_reply,
    has_citation,
    has_precise_number,
    reply_status,
)
from ba_agent.guardrails.search import query_allowed, strip_meeting_text
from ba_agent.knowledge.policy import is_car_question, needs_current_fact, should_search_web
from ba_agent.knowledge.search import KnowledgeIndex
from ba_agent.llm.openai_client import OpenAIResponsesClient
from ba_agent.llm.parse import parse_response
from ba_agent.llm.tools import coerce_args, local_tools
from ba_agent.logging_setup import MeetingIdFilter, redact_secrets, set_meeting_id
from ba_agent.memory.store import MeetingStore, open_meeting
from ba_agent.models import CaptionLine, PostReason, TaskRecord
from ba_agent.speakers import is_bot_speaker, normalize_speaker
from ba_agent.status import StatusState, _safe_getfqdn, start_status_server
from ba_agent.tasks_extract.dates import parse_due, short_due, today_in
from ba_agent.tasks_extract.extractor import (
    apply_overheard,
    dedup_key,
    extract_json,
    fallback_overheard_tasks,
    might_contain_task,
)
from ba_agent.trigger.detector import TriggerDetector
from ba_agent.trigger.match import match_wake

UTC = timezone.utc


# ---------------------------------------------------------------------------
# 1. Config, Secrets, Prompts, Logging & CLI
# ---------------------------------------------------------------------------


def test_config_openai_api_key_fallback_and_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_secrets(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fallback-1234567890")
    s1 = load_settings()
    assert s1.llm.api_key is not None
    assert s1.llm.api_key.get_secret_value() == "sk-fallback-1234567890"

    monkeypatch.setenv("BA_LLM__API_KEY", "sk-explicit-0987654321")
    s2 = load_settings()
    assert s2.llm.api_key is not None
    assert s2.llm.api_key.get_secret_value() == "sk-explicit-0987654321"


def test_ensure_ready_allows_disabled_notion_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_secrets(monkeypatch)
    settings = load_settings()
    settings.llm.api_key = SecretStr("sk-valid-key-12345678")
    settings.notion.enabled = False
    settings.notion.token = None
    ensure_ready(settings)


def test_validate_meet_url_accepts_valid_variants_and_rejects_bad_urls() -> None:
    valid = [
        "  https://meet.google.com/abc-defg-hij  ",
        "https://meet.google.com/_meet/abc-defg-hij?authuser=0",
        "https://meet.google.com/lookup/daily-standup",
        "http://127.0.0.1:8123/fake_meet.html",
    ]
    for url in valid:
        cleaned = validate_meet_url(url)
        assert cleaned == url.strip()

    invalid = [
        "",
        "   ",
        "http://meet.google.com/abc-defg-hij",
        "https://user:pass@meet.google.com/abc-defg-hij",
        "https://meet.google.com",
        "https://meet.google.com:8443/abc-defg-hij",
        "https://sub.meet.google.com/abc-defg-hij",
        "https://meet.google.com.evil.org/abc-defg-hij",
    ]
    for bad in invalid:
        with pytest.raises(StartupError):
            validate_meet_url(bad)


def test_resolve_path_and_all_prompts_readable(tmp_path: Path) -> None:
    abs_target = tmp_path / "custom.yaml"
    assert resolve_path(abs_target) == abs_target
    rel_target = resolve_path("config/settings.yaml")
    assert rel_target.is_absolute()
    assert rel_target.exists()

    for prompt_file in ("persona.md", "summary.md", "tasks.md"):
        content = read_prompt(prompt_file)
        assert len(content.strip()) > 20


def test_logging_secret_redaction_and_meeting_id_filter() -> None:
    raw = (
        "Auth Bearer eyJhbGciOiJIUzI1NiJ9.secret "
        "openai=sk-proj1234567890abcdef "
        "notion=ntn_1234567890abcdef "
        "legacy=secret_abcdef1234567890"
    )
    redacted = redact_secrets(raw)
    assert "eyJhbGciOiJIUzI1NiJ9" not in redacted
    assert "sk-proj1234567890abcdef" not in redacted
    assert "ntn_1234567890abcdef" not in redacted
    assert "secret_abcdef1234567890" not in redacted
    assert redacted.count("[REDACTED]") == 4

    set_meeting_id("m-test-42")
    filt = MeetingIdFilter()
    rec_tuple = logging.LogRecord(
        name="ba",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="key=%s count=%d",
        args=("sk-live1234567890", 3),
        exc_info=None,
    )
    assert filt.filter(rec_tuple) is True
    assert getattr(rec_tuple, "meeting_id") == "m-test-42"
    assert "sk-live1234567890" not in rec_tuple.getMessage()

    rec_dict = logging.LogRecord(
        name="ba",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="tok=%(tok)s n=%(n)d",
        args={"tok": "ntn_secret987654321", "n": 7},
        exc_info=None,
    )
    assert filt.filter(rec_dict) is True
    assert "ntn_secret987654321" not in rec_dict.getMessage()


def test_cli_status_and_delegate_to_running_instance(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    clear_secrets(monkeypatch)
    joined_urls: list[tuple[str, bool]] = []
    state = StatusState(
        meeting_id="m-cli",
        on_join=lambda u, f=False: joined_urls.append((u, f)),
    )
    server = start_status_server(state, "127.0.0.1", 0)
    assert server is not None
    port = server.server_address[1]
    try:
        monkeypatch.setenv("BA_SERVER__STATUS_HOST", "127.0.0.1")
        monkeypatch.setenv("BA_SERVER__STATUS_PORT", str(port))
        settings = load_settings()

        # Delegate join to running instance without launching a second browser.
        url = "https://meet.google.com/abc-defg-hij"
        assert _delegate_to_running_instance(settings, url) is True
        assert joined_urls == [(url, False)]

        # CLI `status` prints live status JSON.
        main(["status"])
        captured = capsys.readouterr().out
        assert '"meeting_id": "m-cli"' in captured
    finally:
        server.shutdown()

    # Once server is shut down, delegation returns False cleanly and `status` exits cleanly.
    settings_dead = load_settings()
    assert _delegate_to_running_instance(settings_dead, url) is False
    with pytest.raises(SystemExit, match="status unavailable"):
        main(["status"])


def test_safe_getfqdn_handles_unicode_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import ba_agent.status as status_mod

    def boom(_name: str = "") -> str:
        raise UnicodeError("label too long")

    monkeypatch.setattr(status_mod, "_orig_getfqdn", boom)
    assert _safe_getfqdn("a" * 64) == "a" * 64
    assert _safe_getfqdn("") == "localhost"


# ---------------------------------------------------------------------------
# 2. Chat Parsing, OutputGate, Captions, Pump, Clock, Speakers & EventBus
# ---------------------------------------------------------------------------


def test_chat_reader_carries_sender_entities_and_custom_bot_name() -> None:
    html = (
        '<div data-chat-message="1" data-sender="Sara">'
        "<span>Check <b>LFP &amp; NMC</b> specs</span></div>"
        '<div data-chat-message="2" data-sender="">Follow&#39;up line</div>'
        '<div data-chat-message="3" data-sender="Custom Bot">Ack</div>'
        '<div data-chat-message="4" data-sender="Sara">   </div>'
    )
    msgs = parse_chat_html(html, bot_name="Custom Bot")
    assert [(m.sender, m.text, m.is_self) for m in msgs] == [
        ("Sara", "Check LFP & NMC specs", False),
        ("Sara", "Follow'up line", False),
        ("Custom Bot", "Ack", True),
    ]

    reader = ChatReader(bot_name="TechSol BA")
    batch1 = reader.consume([{"sender": "Omar", "text": "First message"}])
    batch2 = reader.consume([{"sender": "", "text": "Second bubble in same group"}])
    assert batch1[0].sender == "Omar"
    assert batch2[0].sender == "Omar"


def test_output_gate_edge_cases() -> None:
    gate = OutputGate()
    assert gate.allow(PostReason.REPLY, None) is False
    assert gate.allow(PostReason.REPLY, "") is False
    assert gate.allow("reply") is False  # type: ignore[arg-type]
    assert gate.allow(PostReason.CONSENT) is True
    assert gate.allow(PostReason.CONSENT) is True


@pytest.mark.asyncio
async def test_stabilizer_bot_filtering_disappearance_revision_and_pump() -> None:
    # Unlabeled div is ignored; data-ba-captions is parsed with nested tags/entities.
    assert parse_caption_html("<div>ignored</div>") == []
    parsed = parse_caption_html(
        '<div data-ba-captions="1">'
        '<div data-row="r1" data-speaker="Sara"><span>Hello &amp; welcome</span></div>'
        "</div>"
    )
    assert parsed == [CaptionRow(row_id="r1", speaker="Sara", text="Hello & welcome")]

    stab = Stabilizer(stable_ms=800, bot_names=("techsol ba", "ba assistant", "you"))
    # Bot rows ("You", "TechSol BA") are ignored completely.
    assert stab.push_frame(
        [
            CaptionRow("b1", "You", "Hi I'm the BA"),
            CaptionRow("b2", "TechSol BA", "Saved notes"),
        ],
        0.0,
    ) == []

    # Leading-word ASR revision ("Wait..." -> "Well...") updates in place via _same_utterance.
    assert stab.push_frame([CaptionRow("1", "Sara", "Wait let us check the range")], 1.0) == []
    assert stab.push_frame([CaptionRow("1", "Sara", "Well let us check the range")], 1.2) == []

    # Row disappearing from DOM before stable_ms elapses commits immediately.
    disappeared = stab.push_frame([], 1.3)
    assert [(c.speaker, c.text) for c in disappeared] == [
        ("Sara", "Well let us check the range"),
    ]

    # CaptionPump drains multiple frames and flushes trailing disappearance.
    frames = [
        [CaptionRow("9", "Ali", "Testing pump")],
    ]

    async def drain() -> list[list[CaptionRow]]:
        out = list(frames)
        frames.clear()
        return out

    pump = CaptionPump(drain, Stabilizer(stable_ms=800))
    committed = await pump.poll(5.0)
    assert [(c.speaker, c.text) for c in committed] == [("Ali", "Testing pump")]


def test_clock_speakers_and_event_bus() -> None:
    assert format_meeting_time(-12.5) == "[00:00]"
    assert format_meeting_time(59.9) == "[00:59]"
    assert format_meeting_time(7265) == "[2:01:05]"

    aliases = {"Omar M.": "Omar"}
    assert normalize_speaker("Omar M.’s presentation", aliases) == "Omar"
    assert is_bot_speaker("You") is True
    assert is_bot_speaker("TechSol BA") is True
    assert is_bot_speaker("Custom Helper", bot_name="Custom Helper") is True
    assert is_bot_speaker("Sara") is False


@pytest.mark.asyncio
async def test_event_bus_sync_and_async_subscribers() -> None:
    bus = EventBus()
    seen: list[str] = []

    def sync_sub(val: str) -> None:
        seen.append(f"sync:{val}")

    async def async_sub(val: str) -> None:
        seen.append(f"async:{val}")

    bus.subscribe("topic_a", sync_sub)
    bus.subscribe("topic_a", async_sub)
    await bus.publish("topic_b", "ignored")
    await bus.publish("topic_a", "hit")
    assert seen == ["sync:hit", "async:hit"]


# ---------------------------------------------------------------------------
# 3. Wake Matcher & Trigger Detector Edge Cases
# ---------------------------------------------------------------------------


def test_wake_matcher_collapsed_tokens_abbreviations_and_preambles() -> None:
    # Spoken collapsed letter spellings ("bee a", "b a")
    hit1 = match_wake("hey bee a, what is regenerative braking?")
    assert hit1 is not None
    assert hit1.question == "what is regenerative braking"

    hit2 = match_wake("hey b a compare LFP and NMC")
    assert hit2 is not None
    assert hit2.question == "compare LFP and NMC"

    # Abbreviations ("U.S.", "vs.", "Dr.") must NOT prematurely truncate the question
    hit3 = match_wake("Hey BA, compare U.S. vs. EU charging standards.")
    assert hit3 is not None
    assert hit3.question == "compare U.S. vs. EU charging standards"

    # Short conversational preamble before the real question does NOT truncate the question!
    hit4 = match_wake("Hey BA, quick question. What is regenerative braking?")
    assert hit4 is not None
    assert "What is regenerative braking" in hit4.question

    # Custom wake phrase support via extra_phrases
    hit5 = match_wake("hey jarvis, list open tasks", extra_phrases=["hey jarvis"])
    assert hit5 is not None
    assert hit5.question == "list open tasks"


def test_trigger_detector_explicit_wake_inside_followup_and_chat_strictness() -> None:
    detector = TriggerDetector(pause_wait_s=5, dedup_window_s=3, followup_window_s=20)
    detector.arm_followup("Sara", 10.0)

    # Explicit wake inside an active follow-up window parses wake cleanly (followup=False)
    explicit = detector.on_line("Sara", "Hey BA, save the meeting", "speech", 12.0, caption_id="1")
    assert len(explicit) == 1
    assert explicit[0].followup is False
    assert explicit[0].question == "save the meeting"

    # Chat disables fuzzy matching ("heyy ba" ignored in chat, accepted via fuzzy in speech)
    assert detector.on_line("Ali", "heyy ba what is LFP", "chat", 20.0) == []
    fuzzy_speech = detector.on_line(
        "Ali", "heyy ba what is LFP", "speech", 21.0, caption_id="2"
    )
    assert len(fuzzy_speech) == 1
    assert "LFP" in fuzzy_speech[0].question


# ---------------------------------------------------------------------------
# 4. LLM Client, Parser, Tool Validation & Public Web Search Fallback
# ---------------------------------------------------------------------------


def test_parse_response_annotations_and_malformed_function_json() -> None:
    raw = {
        "output": [
            {
                "type": "web_search_call",
                "action": {
                    "query": "hyundai ioniq 5 price",
                    "queries": ["hyundai ioniq 5 price", "ioniq 5 msrp"],
                    "sources": [
                        {"url": "https://www.hyundaiusa.com/ioniq5", "title": "Hyundai USA"}
                    ],
                },
            },
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "The Ioniq 5 starts around $42,000.",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url": "https://www.edmunds.com/ioniq-5",
                                "title": "Edmunds",
                            },
                        ],
                    }
                ],
            },
            {
                "type": "function_call",
                "name": "save_meeting",
                "arguments": "{}",
            },
            {
                "type": "function_call",
                "name": "create_task",
                "arguments": "{unclosed json",
            },
        ],
    }
    parsed = parse_response(raw)
    assert parsed.text == "The Ioniq 5 starts around $42,000."
    assert parsed.search_queries == ["hyundai ioniq 5 price", "ioniq 5 msrp"]
    assert [(c.title, c.url) for c in parsed.citations] == [
        ("Hyundai USA", "https://www.hyundaiusa.com/ioniq5"),
        ("Edmunds", "https://www.edmunds.com/ioniq-5"),
    ]
    assert len(parsed.function_calls) == 2
    assert parsed.function_calls[0].name == "save_meeting"
    assert parsed.function_calls[1].arguments == {}


def test_coerce_args_direct_repair_and_failure() -> None:
    class _ArgModel(BaseModel):
        count: int

    assert len(local_tools()) == 4
    ok = coerce_args({"count": 3}, _ArgModel, lambda d: d)
    assert ok is not None and ok.count == 3

    repaired = coerce_args({"count": "bad"}, _ArgModel, lambda _d: {"count": 9})
    assert repaired is not None and repaired.count == 9

    failed = coerce_args({"count": "bad"}, _ArgModel, lambda _d: {"count": "still_bad"})
    assert failed is None


@pytest.mark.asyncio
async def test_openai_client_model_fallback_caching_and_non_model_error() -> None:
    client = OpenAIResponsesClient(api_key="sk-test-1234567890", model="gpt-6-luna")
    sent_models: list[str] = []

    async def fake_create(**kwargs):
        sent_models.append(kwargs["model"])
        if kwargs["model"] == "gpt-6-luna":
            raise RuntimeError("Error code: 404 - {'error': {'code': 'model_not_found'}}")
        return {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Fallback answer"}],
                }
            ]
        }

    client._client = SimpleNamespace(responses=SimpleNamespace(create=fake_create))

    first = await client.respond("hi")
    assert first.text == "Fallback answer"
    assert client._fallback_model == "gpt-4o-mini"
    assert sent_models == ["gpt-6-luna", "gpt-4o-mini"]

    # Subsequent call uses cached _fallback_model directly in a single request!
    second = await client.respond("hi again")
    assert second.text == "Fallback answer"
    assert sent_models == ["gpt-6-luna", "gpt-4o-mini", "gpt-4o-mini"]

    # Non-model auth error is NOT swallowed by fallback logic.
    client_auth = OpenAIResponsesClient(api_key="sk-bad", model="gpt-4o-mini")

    async def auth_fail(**_kwargs):
        raise RuntimeError("Error code: 401 - invalid_api_key")

    client_auth._client = SimpleNamespace(responses=SimpleNamespace(create=auth_fail))
    with pytest.raises(RuntimeError, match="invalid_api_key"):
        await client_auth.respond("hi")


@pytest.mark.asyncio
async def test_openai_client_search_cars_guardrail_and_ddg_wikipedia_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = OpenAIResponsesClient(api_key="sk-test-1234567890", model="gpt-4o-mini")

    # Non-car queries are blocked before any network call.
    blocked = await client.search_cars("best pizza recipe in Rome")
    assert blocked.useful is False

    # Force OpenAI web_search to fail so _public_web_search runs.
    async def web_fail(**_kwargs):
        raise RuntimeError("web_search tool unavailable")

    client._client = SimpleNamespace(responses=SimpleNamespace(create=web_fail))

    ddg_html = (
        '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.edmunds.com'
        '%2Ftoyota%2Frav4%2F">2026 Toyota RAV4 Hybrid Review</a>'
        '<a class="result__snippet">EPA combined 39 mpg and 219 hp AWD.</a>'
    )

    def handler_ddg(request: httpx.Request) -> httpx.Response:
        assert "duckduckgo.com" in str(request.url)
        return httpx.Response(200, text=ddg_html)

    orig_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **_kw: orig_async_client(transport=httpx.MockTransport(handler_ddg)),
    )
    res_ddg = await client.search_cars("2026 Toyota RAV4 hybrid mpg")
    assert res_ddg.useful is True
    assert "39 mpg" in res_ddg.text
    assert res_ddg.urls[0] == "https://www.edmunds.com/toyota/rav4/"

    # When DDG returns empty HTML, _public_web_search falls back to Wikipedia API.
    def handler_wiki(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "duckduckgo.com" in url:
            return httpx.Response(200, text="<html><body>No results</body></html>")
        return httpx.Response(
            200,
            json=[
                "regenerative braking car",
                ["Regenerative braking"],
                [" kinetic energy recovery in EVs "],
                ["https://en.wikipedia.org/wiki/Regenerative_braking"],
            ],
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **_kw: orig_async_client(transport=httpx.MockTransport(handler_wiki)),
    )
    res_wiki = await client.search_cars("regenerative braking in electric cars")
    assert res_wiki.useful is True
    assert res_wiki.urls[0] == "https://en.wikipedia.org/wiki/Regenerative_braking"


# ---------------------------------------------------------------------------
# 5. Knowledge Index, Policy & Citations/Search Guardrails
# ---------------------------------------------------------------------------


def test_knowledge_index_policy_and_guardrails(tmp_path: Path) -> None:
    # Missing knowledge directory degrades gracefully without raising.
    empty_idx = KnowledgeIndex(tmp_path / "nonexistent", min_score=0.5)
    assert empty_idx.search("what is a hybrid") == []

    kb_dir = tmp_path / "kb"
    kb_dir.mkdir()
    (kb_dir / "a_first.md").write_text(
        "# Solid State Battery\nSolid state cells replace liquid electrolyte.\n",
        encoding="utf-8",
    )
    (kb_dir / "b_second.md").write_text(
        "# General Notes\nSolid state cells are still expensive to manufacture.\n",
        encoding="utf-8",
    )
    idx = KnowledgeIndex(kb_dir, min_score=0.5)
    assert idx.search("the and or") == []
    hits = idx.search("solid state battery")
    assert len(hits) >= 1
    # Heading match boosts a_first.md above body-only match in b_second.md.
    assert hits[0].file == "a_first.md"
    assert hits[0].score > hits[1].score

    # Policy classification checks
    assert is_car_question("Compare Tesla Model Y and Ford Mustang Mach-E range", []) is True
    assert is_car_question("Who owns the Q4 marketing slide deck?", []) is False
    assert needs_current_fact("2026 Toyota Camry MSRP") is True
    assert should_search_web("what is solid state battery", hits, min_score=0.5) is False
    assert should_search_web("2026 solid state battery price", hits, min_score=0.5) is True

    # Citations guardrail checks
    assert has_precise_number("Starts at $42,500") is True
    assert has_precise_number("Delivers 75 kWh battery capacity") is True
    assert has_precise_number("There are 2 drivetrain layouts") is False
    assert has_citation("LFP packs last longer (src: ev.md#LFP)") is True
    assert has_citation("See https://www.edmunds.com/rav4") is True
    assert reply_status("It gets 48 mpg.") == "regenerate"
    assert reply_status("I'm not sure about that exact spec.") == "allow"

    # Search privacy guardrail checks
    cleaned = strip_meeting_text(
        "[01:15] Sara: Toyota RAV4 hybrid price",
        transcript_lines=["We discussed internal budget yesterday"],
        participants=["Sara"],
    )
    assert "Sara" not in cleaned
    assert "[01:15]" not in cleaned
    assert query_allowed(cleaned, [], ["Sara"]) is True
    assert query_allowed("site:internal.corp toyota rav4", [], []) is False
    assert query_allowed("[01:15] toyota rav4", [], []) is False


def test_finalize_reply_second_chance_recovery_and_hard_refusal() -> None:
    recovered = finalize_reply(
        "It gets 48 mpg combined.",
        "It gets 48 mpg combined (src: hybrids.md#MPG).",
    )
    assert "(src: hybrids.md#MPG)" in recovered

    refused = finalize_reply("It costs $39,995 MSRP.", "Still $39,995 without citation.")
    assert refused == NOT_SURE


# ---------------------------------------------------------------------------
# 6. Dates, Timezones & Overheard Task Extraction
# ---------------------------------------------------------------------------


def test_dates_timezones_rollover_and_short_due() -> None:
    from zoneinfo import ZoneInfoNotFoundError

    assert isinstance(today_in("Asia/Karachi"), date)
    with pytest.raises(ZoneInfoNotFoundError):
        today_in("Invalid/Timezone_Name")

    ref = date(2026, 11, 11)  # Wednesday
    assert parse_due("2026-11-20", ref) == date(2026, 11, 20)
    assert parse_due("2026-02-30", ref) is None
    assert parse_due("today", ref) == ref
    assert parse_due("eod", ref) == ref
    assert parse_due("tomorrow", ref) == date(2026, 11, 12)
    assert parse_due("eow", ref) == date(2026, 11, 13)
    assert parse_due("this Friday", ref) == date(2026, 11, 13)
    assert parse_due("next Monday", ref) == date(2026, 11, 16)
    assert parse_due("in 3 days", ref) == date(2026, 11, 14)
    assert parse_due("in 2 weeks", ref) == date(2026, 11, 25)
    assert parse_due("Nov 20", ref) == date(2026, 11, 20)
    assert parse_due("20 Nov", ref) == date(2026, 11, 20)
    # Month/day more than 7 days in the past rolls into next calendar year.
    assert parse_due("Jan 15", ref) == date(2027, 1, 15)

    assert short_due(ref, ref) == "Today"
    assert short_due(date(2026, 11, 13), ref) == "Fri"
    assert short_due(date(2026, 12, 25), ref) == "Dec 25"


def test_extract_json_and_overheard_fallbacks() -> None:
    fenced = 'Sure! Here is JSON:\n```json\n{"tasks": [{"task": "check tires"}]}\n```'
    assert extract_json(fenced) == '{"tasks": [{"task": "check tires"}]}'

    ref = date(2026, 11, 11)
    # First-person commitment from known speaker succeeds; Unknown speaker is ignored.
    own = fallback_overheard_tasks(
        "I'll send the battery BOM by tomorrow.",
        said_by="Sara",
        time="[02:00]",
        today=ref,
    )
    assert len(own) == 1
    assert own[0].owner == "Sara"
    assert own[0].due == date(2026, 11, 12)

    assert (
        fallback_overheard_tasks(
            "I'll send the battery BOM by tomorrow.",
            said_by="Unknown speaker",
            time="[02:00]",
            today=ref,
        )
        == []
    )

    # Direct request ("Omar, can you ...") assigns Omar.
    direct = fallback_overheard_tasks(
        "Omar, can you validate the inverter thermal curve by Friday?",
        said_by="Sara",
        time="[03:10]",
        today=ref,
    )
    assert len(direct) == 1
    assert direct[0].owner == "Omar"
    assert direct[0].due == date(2026, 11, 13)

    # Wake-addressed lines are skipped by might_contain_task; apply_overheard parses model JSON.
    assert (
        might_contain_task("Hey BA, add a task for Ali to check pricing by tomorrow", today=ref)
        is False
    )
    assert might_contain_task("What is the weather?", today=ref) is False
    assert might_contain_task("Can you send the thermal deck by Friday?", today=ref) is True

    parsed_records = apply_overheard(
        '```json\n{"tasks": [{"task": "check brake supplier", "owner": "Ali", "due": "tomorrow"}]}\n```',
        said_by="Sara",
        time="[04:00]",
        today=ref,
    )
    assert len(parsed_records) == 1
    assert parsed_records[0].owner == "Ali"
    assert parsed_records[0].due == date(2026, 11, 12)


# ---------------------------------------------------------------------------
# 7. SQLite Memory Store Cross-Meeting Lifecycle
# ---------------------------------------------------------------------------


def test_memory_store_closed_meeting_reopen_and_cross_meeting_task_dedup(tmp_path: Path) -> None:
    db_path = tmp_path / "meeting.db"
    url_a = "https://meet.google.com/_meet/abc-defg-hij?pli=1"
    url_b = "https://meet.google.com/abc-defg-hij#frag"

    first = open_meeting(db_path, url_a)
    first_id = first.meeting_id
    first.add_task(
        TaskRecord(
            task=" inverter spec ",
            owner="Ali",
            due=date(2026, 11, 13),
            status="Todo",
            key=dedup_key("inverter spec", "Ali"),
        )
    )
    first.set_meta("closed", "1")
    first.close()

    # Because first meeting has closed="1", re-joining the same canonical Meet URL
    # opens a brand-new meeting_id instead of reusing the closed call!
    second = open_meeting(db_path, url_b)
    assert second.meeting_id != first_id
    second.add_task(
        TaskRecord(
            task="inverter spec",
            owner="Ali",
            due=date(2026, 11, 20),
            status="In progress",
            key=dedup_key("inverter spec", "Ali"),
        )
    )
    # all_tasks() deduplicates by key, prioritizing current meeting's updated status/due!
    merged = second.all_tasks()
    assert len(merged) == 1
    assert merged[0].status == "In progress"
    assert merged[0].due == date(2026, 11, 20)
    second.close()

    # Standalone MeetingStore verification
    direct = MeetingStore(db_path, second.meeting_id)
    assert len(direct.tasks()) == 1
    direct.close()
