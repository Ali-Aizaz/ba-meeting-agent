"""T15-U1: secrets fail before the browser, and compose has no GPU runtime."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.conftest import clear_secrets

from ba_agent.config import StartupError, load_settings, start_join

ROOT = Path(__file__).resolve().parents[1]


def test_t15_u1_missing_key_fails_before_the_browser(monkeypatch) -> None:
    clear_secrets(monkeypatch)
    settings = load_settings()
    launched = {"called": False}

    def launch() -> None:
        launched["called"] = True

    with pytest.raises(StartupError, match="BA_LLM__API_KEY"):
        start_join(settings, "https://meet.google.com/abc-defg-hij", launch)
    assert launched["called"] is False


def test_t15_u1_compose_has_no_gpu_runtime() -> None:
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8").lower()
    assert "nvidia" not in text
    assert "cuda" not in text
    assert "ollama" not in text
