"""T02 chat and output gate, T03 captions, T04 speakers."""

from __future__ import annotations

from datetime import datetime, timezone
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

UTC = timezone.utc

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


def test_t03_same_row_id_subsequent_sentences_strip_committed_prefix() -> None:
    stabilizer = Stabilizer(stable_ms=800)
    f1 = _region(_row("1", "Ali", "We need to review the spec."))
    # Pause > 0.8s commits the first sentence on row "1".
    lines = []
    lines += stabilizer.push_frame(parse_caption_html(f1), 0.0)
    lines += stabilizer.push_frame(parse_caption_html(f1), 0.9)
    assert [(line.speaker, line.text) for line in lines] == [
        ("Ali", "We need to review the spec."),
    ]

    # Google Meet appends the next sentence to the same .nMcdL DOM block (row "1").
    f2_partial = _region(_row("1", "Ali", "We need to review the spec. Sara will send"))
    f2_full = _region(
        _row("1", "Ali", "We need to review the spec. Sara will send the notes by Friday."),
        _row("2", "BA Assistant", "I should be ignored"),
    )
    lines += stabilizer.push_frame(parse_caption_html(f2_partial), 1.2)
    lines += stabilizer.push_frame(parse_caption_html(f2_full), 1.5)
    lines += stabilizer.push_frame(parse_caption_html(f2_full), 2.4)
    assert [(line.speaker, line.text) for line in lines] == [
        ("Ali", "We need to review the spec."),
        ("Ali", "Sara will send the notes by Friday."),
    ]


def test_t03_repunctuated_cumulative_row_strips_prefix_and_ignores_punctuation_only() -> None:
    stabilizer = Stabilizer(stable_ms=800)
    # First utterance committed without trailing period.
    f1 = _region(_row("1", "Ali", "We need to review the spec"))
    lines = []
    lines += stabilizer.push_frame(parse_caption_html(f1), 0.0)
    lines += stabilizer.push_frame(parse_caption_html(f1), 0.9)
    assert [(line.speaker, line.text) for line in lines] == [
        ("Ali", "We need to review the spec"),
    ]
    # Google Meet adds a period to the committed sentence — should not emit "."
    f1_punct = _region(_row("1", "Ali", "We need to review the spec."))
    lines += stabilizer.push_frame(parse_caption_html(f1_punct), 1.1)
    lines += stabilizer.push_frame(parse_caption_html(f1_punct), 2.0)
    assert len(lines) == 1

    # Next sentence appended after re-punctuated prefix.
    f2 = _region(_row("1", "Ali", "We need to review the spec. Hey, BA, what is a hybrid?"))
    lines += stabilizer.push_frame(parse_caption_html(f2), 2.2)
    lines += stabilizer.push_frame(parse_caption_html(f2), 3.1)
    assert [(line.speaker, line.text) for line in lines] == [
        ("Ali", "We need to review the spec"),
        ("Ali", "Hey, BA, what is a hybrid?"),
    ]


def test_t03_wake_sentence_commits_immediately_when_speaker_continues_talking() -> None:
    stabilizer = Stabilizer(stable_ms=800)
    # Speaker says "Hey BA, add a task..." and keeps talking without an 800ms pause.
    f1 = _region(
        _row(
            "1",
            "Ali",
            "Hey BA, add a task for Sara to review the budget by Friday",
        )
    )
    f2 = _region(
        _row(
            "1",
            "Ali",
            "Hey BA, add a task for Sara to review the budget by Friday. "
            "Now it's going to generate out",
        )
    )
    f3 = _region(
        _row(
            "1",
            "Ali",
            "Hey BA, add a task for Sara to review the budget by Friday. "
            "Now it's going to generate out and I am still speaking.",
        )
    )
    lines = []
    lines += stabilizer.push_frame(parse_caption_html(f1), 0.0)
    assert lines == []
    # At t=0.3s (before 0.8s silence), speaker starts a new sentence after the task:
    # the wake sentence is immediately split & committed!
    lines += stabilizer.push_frame(parse_caption_html(f2), 0.3)
    assert [(line.speaker, line.text) for line in lines] == [
        ("Ali", "Hey BA, add a task for Sara to review the budget by Friday."),
    ]
    # Continued speech updates the open tail without re-emitting "Hey BA"
    lines += stabilizer.push_frame(parse_caption_html(f3), 0.6)
    lines += stabilizer.push_frame(parse_caption_html(f3), 1.5)
    assert [(line.speaker, line.text) for line in lines] == [
        ("Ali", "Hey BA, add a task for Sara to review the budget by Friday."),
        ("Ali", "Now it's going to generate out and I am still speaking."),
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
        assert await adapter.cam_is_off()
        tracks_locked_off = await adapter.page.evaluate(
            """async () => {
              const stream = await navigator.mediaDevices.getUserMedia(
                { audio: true, video: true }
              );
              const tracks = stream.getTracks();
              tracks.forEach((t) => { t.enabled = true; });
              return tracks.length > 0 && tracks.every((t) => t.enabled === false);
            }"""
        )
        assert tracks_locked_off is True
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


def test_stabilizer_fast_commits_question_mark_wake_only() -> None:
    from ba_agent.captions.stabilize import CaptionRow

    stab = Stabilizer(stable_ms=800)
    assert stab.push_frame(
        [CaptionRow("r1", "Sara", "Hey BA, what is a hybrid?")],
        0.0,
    ) == []
    # 460ms >= wake_stable_ms (450ms) commits explicit ?-terminated >=3-word wake question.
    fast_hits = stab.push_frame(
        [CaptionRow("r1", "Sara", "Hey BA, what is a hybrid?")],
        0.46,
    )
    assert len(fast_hits) == 1
    assert fast_hits[0].text == "Hey BA, what is a hybrid?"

    # Unpunctuated wake prefix waits the full 800ms so breath pauses do not split questions.
    stab2 = Stabilizer(stable_ms=800)
    assert stab2.push_frame([CaptionRow("r2", "Sara", "Hey BA, what is the")], 0.0) == []
    assert stab2.push_frame([CaptionRow("r2", "Sara", "Hey BA, what is the")], 0.46) == []
    delayed = stab2.push_frame([CaptionRow("r2", "Sara", "Hey BA, what is the")], 0.85)
    assert len(delayed) == 1
    assert delayed[0].text == "Hey BA, what is the"


def test_stabilizer_leading_word_asr_revision_does_not_split_or_clobber() -> None:
    from ba_agent.captions.stabilize import CaptionRow

    stab = Stabilizer(stable_ms=800)
    assert stab.push_frame([CaptionRow("r1", "Ali", "Think we should use LFP")], 0.0) == []
    assert stab.push_frame([CaptionRow("r1", "Ali", "I think we should use LFP")], 0.3) == []
    committed = stab.push_frame([CaptionRow("r1", "Ali", "I think we should use LFP")], 1.2)
    assert len(committed) == 1
    assert committed[0].text == "I think we should use LFP"

    # A sharp length drop (10 words -> 3 words) commits the 10-word line rather than overwriting it.
    long_line = "We agreed to validate the thermal valve inverter benchmark tomorrow"
    assert stab.push_frame([CaptionRow("r2", "Omar", long_line)], 2.0) == []
    split_hits = stab.push_frame([CaptionRow("r2", "Omar", "validate the thermal")], 2.4)
    assert [hit.text for hit in split_hits] == [long_line]


@pytest.mark.asyncio
async def test_post_chat_opens_collapsed_drawer_when_hidden_box_exists() -> None:
    from ba_agent.bot.google_meet.adapter import GoogleMeetAdapter
    from ba_agent.models import PostReason

    drawer_open = {"value": False}
    sent: list[str] = []

    class _FakeTarget:
        async def is_visible(self) -> bool:
            return drawer_open["value"]

        async def is_enabled(self) -> bool:
            return drawer_open["value"]

        async def click(self, **_kwargs) -> None:
            if not drawer_open["value"]:
                raise RuntimeError("element is not visible")

        async def fill(self, text: str, **_kwargs) -> None:
            sent.append(text)

        async def press(self, _key: str) -> None:
            return None

    class _FakeBox:
        first = _FakeTarget()

        async def count(self) -> int:
            return 1

    class _FakeOpener:
        @property
        def first(self):
            return self

        def or_(self, _other):
            return self

        async def count(self) -> int:
            return 1

        async def click(self, **_kwargs) -> None:
            drawer_open["value"] = True

    class _FakePage:
        def get_by_role(self, *_args, **_kwargs):
            return _FakeOpener()

        def locator(self, *_args, **_kwargs):
            return _FakeOpener()

    adapter = GoogleMeetAdapter(_guest_settings(), profile_dir=Path("/tmp/ba-unused"))
    adapter.page = _FakePage()  # type: ignore[assignment]
    adapter._chat_box = lambda: _FakeBox()  # type: ignore[method-assign]

    async def _noop_overlays() -> None:
        return None

    adapter._dismiss_incall_overlays = _noop_overlays  # type: ignore[method-assign]
    ok = await adapter.post_chat("Grounded reply (src: ev.md)", PostReason.REPLY)
    assert ok is True
    assert drawer_open["value"] is True
    assert sent == ["Grounded reply (src: ev.md)"]


def test_chat_reader_does_not_poison_human_messages_after_bot_self_post() -> None:
    from ba_agent.bot.chat_parse import ChatReader

    reader = ChatReader(bot_name="TechSol BA")
    # 1. Bot posts opening consent message ("You")
    consent_batch = reader.consume([{"sender": "You", "text": "Hi — I'm the TechSol BA."}])
    assert consent_batch[0].is_self is True

    # 2. Human sends chat message whose updated Meet DOM group lacked a matched sender class ("")
    human_batch = reader.consume([{"sender": "", "text": "Hey BA, what is a hybrid?"}])
    assert len(human_batch) == 1
    assert human_batch[0].is_self is False
    assert human_batch[0].sender == "Unknown speaker"


@pytest.mark.asyncio
async def test_post_chat_detects_offscreen_transformed_drawer_and_clicks_send_fallback() -> None:
    from ba_agent.bot.google_meet.adapter import GoogleMeetAdapter

    drawer_open = {"value": False}
    sent_via_button: list[str] = []
    current_val = {"text": ""}

    class _OffscreenThenOpenTarget:
        async def is_visible(self) -> bool:
            # CSS translateX(100%) drawer stays "visible" to DOM layout even when off-screen
            return True

        async def is_enabled(self) -> bool:
            return True

        async def bounding_box(self) -> dict[str, float]:
            return (
                {"x": 920.0, "y": 640.0, "width": 320.0, "height": 40.0}
                if drawer_open["value"]
                else {"x": 1320.0, "y": 640.0, "width": 320.0, "height": 40.0}
            )

        async def click(self, **_kwargs) -> None:
            if not drawer_open["value"]:
                raise TimeoutError("element is outside of the viewport")

        async def fill(self, text: str, **_kwargs) -> None:
            current_val["text"] = text

        async def press(self, _key: str) -> None:
            # Simulate Meet build where Enter alone leaves text unsent until Send button click
            return None

        async def input_value(self) -> str:
            return current_val["text"]

    class _FakeBox:
        first = _OffscreenThenOpenTarget()

        async def count(self) -> int:
            return 1

    class _FakeSendBtn:
        @property
        def first(self):
            return self

        async def count(self) -> int:
            return 1

        async def click(self, **_kwargs) -> None:
            sent_via_button.append(current_val["text"])
            current_val["text"] = ""

    class _FakeOpener:
        @property
        def first(self):
            return self

        def or_(self, _other):
            return self

        async def count(self) -> int:
            return 1

        async def click(self, **_kwargs) -> None:
            drawer_open["value"] = True

    class _FakePage:
        def get_by_role(self, *_args, **_kwargs):
            return _FakeOpener()

        def locator(self, selector: str, *_args, **_kwargs):
            if "SoqoBf" in selector:
                return _FakeSendBtn()
            return _FakeOpener()

    adapter = GoogleMeetAdapter(_guest_settings(), profile_dir=Path("/tmp/ba-unused"))
    adapter.page = _FakePage()  # type: ignore[assignment]
    adapter._chat_box = lambda: _FakeBox()  # type: ignore[method-assign]

    async def _noop_overlays() -> None:
        return None

    adapter._dismiss_incall_overlays = _noop_overlays  # type: ignore[method-assign]
    ok = await adapter.post_chat("Hybrid reply (src: hybrid.md)", PostReason.REPLY)
    assert ok is True
    assert drawer_open["value"] is True
    assert sent_via_button == ["Hybrid reply (src: hybrid.md)"]

    # Bot's own posted text is tagged is_self even if Meet renders a custom Google account name
    from ba_agent.models import ChatMessage

    echoed = [
        ChatMessage(
            sender="Ali Personal",
            text="Hybrid reply (src: hybrid.md)",
            wall_time=datetime.now(UTC),
            is_self=False,
        )
    ]
    marked = adapter._mark_self_messages(echoed)
    assert marked[0].is_self is True


@pytest.mark.asyncio
async def test_incall_admission_and_show_captions_and_polite_region_isolation(
    fake_meet_url: str, tmp_path: Path, monkeypatch
) -> None:
    clear_secrets(monkeypatch)
    settings = _guest_settings()
    adapter = await _join_or_skip(settings, fake_meet_url, tmp_path)
    try:
        # Simulate updated Google Meet DOM:
        # 1) Rename captions button to "Show captions" -> "Hide captions"
        # 2) Prepend a non-chat [aria-live="polite"] banner before a Meet [jsname="dTKtvb"] node
        await adapter.page.evaluate(
            """() => {
              const oldLog = document.querySelector('[data-ba-chat-log]');
              if (oldLog) oldLog.remove();

              const politeToast = document.createElement('div');
              politeToast.setAttribute('aria-live', 'polite');
              politeToast.textContent = 'Ali joined the call';
              document.body.prepend(politeToast);

              const panel = document.createElement('aside');
              const group = document.createElement('div');
              group.className = 'Ss4fHf';
              const header = document.createElement('div');
              header.textContent = 'Ali';
              const msg = document.createElement('div');
              msg.setAttribute('jsname', 'dTKtvb');
              msg.textContent = 'Hey BA, what is a hybrid?';
              group.appendChild(header);
              group.appendChild(msg);
              panel.appendChild(group);
              const input = document.querySelector('textarea[aria-label*="Send a message"]');
              if (input) panel.appendChild(input);
              document.getElementById('incall').appendChild(panel);

              const capBtn = document.getElementById('captions-btn');
              capBtn.setAttribute('aria-label', 'Show captions');
              capBtn.addEventListener('click', () => {
                capBtn.setAttribute('aria-label', 'Hide captions');
              });
            }"""
        )
        assert await adapter._is_admitted_in_call(is_real_meet=True) is True
        assert await adapter.enable_captions() is True
        _frames, chats = await adapter.drain_poll_state()
        assert any(c.text == "Hey BA, what is a hybrid?" and not c.is_self for c in chats)
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_google_login_rejection_aborts_immediately_Even_with_stale_cookies() -> None:
    from pydantic import SecretStr

    from ba_agent.bot.google_meet.adapter import GoogleMeetAdapter

    settings = _guest_settings()
    settings.meeting.google_email = "bot@example.com"
    settings.meeting.google_password = SecretStr("secret-pass")

    navigated: list[str] = []

    class _EmptyLocator:
        @property
        def first(self):
            return self

        async def count(self) -> int:
            return 0

        async def is_visible(self) -> bool:
            return False

        async def wait_for(self, **_kwargs) -> None:
            return None

    class _RejectedPage:
        url = "https://accounts.google.com/v3/signin/rejected"

        async def goto(self, target_url: str, **_kwargs) -> None:
            navigated.append(target_url)
            if "accounts.google.com" in target_url:
                self.url = "https://accounts.google.com/v3/signin/rejected"
            else:
                self.url = target_url

        def locator(self, *_args, **_kwargs):
            return _EmptyLocator()

        async def inner_text(self, _sel: str) -> str:
            return "Couldn't sign you in — This browser or app may not be secure."

    adapter = GoogleMeetAdapter(settings, profile_dir=Path("/tmp/ba-unused"))
    adapter.page = _RejectedPage()  # type: ignore[assignment]

    async def _stale_cookies_true() -> bool:
        return True

    adapter._has_google_auth_cookies = _stale_cookies_true  # type: ignore[method-assign]
    ok = await adapter._perform_google_login("https://meet.google.com/gya-vqog-xwn")
    assert ok is False
    assert adapter._google_login_blocked is True



