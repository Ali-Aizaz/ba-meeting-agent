"""T05-U1 through T05-U6."""

from __future__ import annotations

from ba_agent.trigger.detector import TriggerDetector
from ba_agent.trigger.match import match_wake


def test_t05_u1_positives() -> None:
    cases = [
        ("Hey BA, what is a hybrid?", "what is a hybrid"),
        ("hey ba what is an ev", "what is an ev"),
        ("Hey, BA — compare the packs", "compare the packs"),
        ("hey bea, what is a hybrid?", "what is a hybrid"),
        ("HEY BA! what is a hybrid?", "what is a hybrid"),
        ("ok ba, what is a hybrid?", "what is a hybrid"),
        ("okay ba, what is a hybrid?", "what is a hybrid"),
        ("hi ba, what is a hybrid?", "what is a hybrid"),
    ]
    for text, expected in cases:
        hit = match_wake(text)
        assert hit is not None, text
        assert hit.question == expected


def test_t05_u1_fuzzy_and_chat() -> None:
    hit = match_wake("hey baah what is a hybrid", fuzzy=True)
    assert hit is not None
    assert "hybrid" in hit.question
    chat = match_wake("@ba what is a hybrid", fuzzy=False)
    assert chat is not None
    assert chat.question == "what is a hybrid"


def test_t05_u2_lookalikes() -> None:
    for text in (
        "hey bay, see you later",
        "hey bob, see you later",
        "hey bae, see you later",
        "We're in the bay area",
        "the BA team will review this",
        "BAE systems joined",
        "BA",
        "the ba",
    ):
        assert match_wake(text, fuzzy=True) is None, text


def test_t05_u3_pause_merges_same_speaker() -> None:
    detector = TriggerDetector(pause_wait_s=5, dedup_window_s=3, followup_window_s=20)
    first = detector.on_line("Sara", "Hey BA", "speech", 0.0, caption_id="a")
    assert first == []
    second = detector.on_line("Sara", "what is a hybrid", "speech", 2.0, caption_id="b")
    assert len(second) == 1
    assert second[0].question == "what is a hybrid"
    assert second[0].speaker == "Sara"


def test_t05_u4_other_speaker_does_not_merge() -> None:
    detector = TriggerDetector(pause_wait_s=5, dedup_window_s=3, followup_window_s=20)
    assert detector.on_line("Sara", "Hey BA", "speech", 0.0, caption_id="a") == []
    assert detector.on_line("Ali", "what is a hybrid", "speech", 1.0, caption_id="b") == []
    expired = detector.tick(6.0)
    assert len(expired) == 1
    assert expired[0].question == ""
    assert expired[0].speaker == "Sara"


def test_t05_u5_dedup_speech_and_chat() -> None:
    detector = TriggerDetector(pause_wait_s=5, dedup_window_s=3, followup_window_s=20)
    speech = detector.on_line(
        "Sara", "Hey BA, what is a hybrid?", "speech", 0.0, caption_id="a"
    )
    chat = detector.on_line("Sara", "Hey BA, what is a hybrid?", "chat", 1.0, caption_id="b")
    assert len(speech) == 1
    assert chat == []


def test_t05_u6_followup_window() -> None:
    detector = TriggerDetector(pause_wait_s=5, dedup_window_s=3, followup_window_s=20)
    detector.arm_followup("Sara", 0.0)
    own = detector.on_line("Sara", "the hybrid one", "speech", 5.0, caption_id="a")
    assert len(own) == 1
    assert own[0].followup is True
    assert own[0].question == "the hybrid one"

    detector.arm_followup("Sara", 0.0)
    other = detector.on_line("Ali", "the hybrid one", "speech", 5.0, caption_id="b")
    assert other == []

    detector.arm_followup("Sara", 0.0)
    late = detector.on_line("Sara", "the hybrid one", "speech", 21.0, caption_id="c")
    assert late == []
