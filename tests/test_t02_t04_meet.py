"""T02 chat and output gate, T03 captions, T04 speakers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.conftest import FIXTURES, clear_secrets

from ba_agent.bot.chat_parse import parse_chat_html
from ba_agent.bot.google_meet.video import reject_incoming_video_sdp
from ba_agent.bot.output_gate import OutputGate
from ba_agent.captions.clock import format_meeting_time
from ba_agent.captions.pump import CaptionPump
from ba_agent.captions.stabilize import Stabilizer, parse_caption_html
from ba_agent.config import Settings, load_settings
from ba_agent.events import EventBus
from ba_agent.memory.store import MeetingStore
from ba_agent.models import CaptionLine, PostReason
from ba_agent.speakers import load_aliases, normalize_speaker

CHAT_HTML = (FIXTURES / "chat_sample.html").read_text(encoding="utf-8")


def _region(*rows: str) -> str:
    body = "".join(rows)
    return f'<div role="region" aria-label="Captions">{body}</div>'


def _row(row_id: str, speaker: str, text: str) -> str:
    return f'<div data-row="{row_id}" data-speaker="{speaker}">{text}</div>'


def test_t02_u1_chat_html_carries_sender() -> None:
    messages = parse_chat_html(CHAT_HTML)
    assert [(m.sender, m.text, m.is_self) for m in messages] == [
        ("Sara", "Hello team", False),
        ("Sara", "Still talking", False),
        ("You", "Hi from the bot", True),
    ]


def test_t02_u2_output_gate() -> None:
    gate = OutputGate()
    assert gate.allow(None) is False
    assert gate.allow(PostReason.REPLY, "t1") is True
    assert gate.allow(PostReason.REPLY, "t1") is False
    assert gate.allow(PostReason.REPLY, "t2") is True
    assert gate.allow(PostReason.CONSENT) is True
    assert gate.allow(PostReason.CONFIRMATION) is True


def test_t03_u1_growing_caption_commits_once() -> None:
    stabilizer = Stabilizer(stable_ms=800)
    first = _region(_row("1", "Sara", "Let"))
    grown = _region(_row("1", "Sara", "Let's use the hybrid"))
    both = _region(
        _row("1", "Sara", "Let's use the hybrid"),
        _row("2", "Ali", "Sounds good"),
    )
    lines = []
    lines += stabilizer.push_frame(parse_caption_html(first), 0.0)
    lines += stabilizer.push_frame(parse_caption_html(grown), 0.3)
    lines += stabilizer.push_frame(parse_caption_html(both), 0.4)
    lines += stabilizer.push_frame(parse_caption_html(both), 1.3)
    assert [(line.speaker, line.text) for line in lines] == [
        ("Sara", "Let's use the hybrid"),
        ("Ali", "Sounds good"),
    ]


def test_t03_u2_meeting_clock() -> None:
    assert format_meeting_time(0) == "[00:00]"
    assert format_meeting_time(90) == "[01:30]"


def test_t03_u3_transcript_store_reloads(tmp_path: Path) -> None:
    path = tmp_path / "meeting.db"
    line = CaptionLine(
        id="c1",
        meeting_id="m1",
        speaker="Sara",
        speaker_raw="Sara",
        start_s=1.5,
        end_s=2.0,
        wall_time=datetime(2026, 10, 25, 12, 0, tzinfo=UTC),
        text="Let's use the hybrid",
    )
    store = MeetingStore(path, "m1")
    store.add_caption(line)
    store.close()
    reloaded = MeetingStore(path, "m1")
    assert reloaded.last(10) == [line]
    assert reloaded.since(1.0) == [line]
    assert reloaded.by_speaker("Sara") == [line]
    reloaded.close()


def test_t04_u1_alias() -> None:
    aliases = load_aliases(FIXTURES.parents[1] / "config" / "people.yaml")
    assert aliases["Ali K."] == "Ali"
    assert normalize_speaker("Ali K.", aliases) == "Ali"
    assert normalize_speaker("Sara's presentation", aliases) == "Sara"


def test_t04_u2_blank_speaker() -> None:
    assert normalize_speaker("   ") == "Unknown speaker"
    assert normalize_speaker(None) == "Unknown speaker"


def test_t04_u3_clock_formats() -> None:
    assert format_meeting_time(75) == "[01:15]"
    assert format_meeting_time(3700) == "[1:01:40]"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_t02_i1_fake_meet(fake_meet_url: str, tmp_path: Path, monkeypatch) -> None:
    clear_secrets(monkeypatch)
    settings = _guest_settings()
    adapter = await _join_or_skip(settings, fake_meet_url, tmp_path)
    try:
        assert await adapter.mic_is_off()
        messages = await adapter.collect_chat()
        sara = [(m.sender, m.text, m.is_self) for m in messages]
        assert ("Sara", "Hello team", False) in sara
        assert ("Sara", "Still talking", False) in sara
        assert any(m.sender == "You" and m.is_self for m in messages)
        assert await adapter.post_chat("Reply line", PostReason.CONSENT) is True
        await adapter.page.wait_for_timeout(200)
        posted = await adapter.collect_chat()
        assert any(m.text == "Reply line" and m.is_self for m in posted)
        assert await adapter.page.evaluate("() => window.__baVideoOff === true")
        sample = (
            "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
            "a=sendrecv\r\n"
            "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
            "a=recvonly\r\n"
        )
        rewritten = await adapter.page.evaluate("(sdp) => window.__baRejectVideoSdp(sdp)", sample)
        assert rewritten == reject_incoming_video_sdp(sample)
        assert await adapter.enable_captions() is True
    finally:
        await adapter.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_t03_i1_caption_events(fake_meet_url: str, tmp_path: Path, monkeypatch) -> None:
    clear_secrets(monkeypatch)
    settings = _guest_settings()
    adapter = await _join_or_skip(settings, fake_meet_url, tmp_path)
    bus = EventBus()
    received = []

    async def collect(line) -> None:
        received.append(line)

    bus.subscribe("caption_committed", collect)
    try:
        assert await adapter.enable_captions() is True
        await adapter.page.evaluate(
            """() => {
              const region = document.querySelector('[role="region"]');
              const row = document.createElement('div');
              row.setAttribute('data-row', '1');
              row.setAttribute('data-speaker', 'Sara');
              row.textContent = 'Hey BA, what is a hybrid?';
              region.appendChild(row);
            }"""
        )
        await adapter.page.wait_for_timeout(150)
        pump = CaptionPump(adapter.drain_caption_frames, Stabilizer(stable_ms=0))
        raws = await pump.poll(1.0)
        for raw in raws:
            await bus.publish("caption_committed", raw)
        assert any(raw.speaker == "Sara" and "hybrid" in raw.text for raw in received)
    finally:
        await adapter.close()


def _guest_settings() -> Settings:
    settings = load_settings()
    settings.meeting.join_mode = "guest"
    settings.meeting.lobby_timeout_s = 15
    settings.browser.headless = True
    return settings


async def _join_or_skip(settings: Settings, url: str, tmp_path: Path):
    from ba_agent.bot.google_meet.adapter import GoogleMeetAdapter

    adapter = GoogleMeetAdapter(settings, profile_dir=tmp_path / "profile")
    try:
        await adapter.join(url)
    except Exception as exc:
        await adapter.close()
        text = str(exc)
        if "Executable doesn't exist" in text or "BrowserType.launch" in text:
            pytest.skip(f"Chromium is not installed ({exc})")
        raise
    return adapter
