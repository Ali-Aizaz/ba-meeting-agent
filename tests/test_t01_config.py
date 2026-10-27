"""T01-U1 through T01-U4."""

from __future__ import annotations

import pytest
from pydantic import SecretStr
from tests.conftest import clear_secrets

from ba_agent.config import (
    CONSENT_MESSAGE,
    StartupError,
    ensure_ready,
    load_settings,
    validate_meet_url,
)


def test_t01_u1_yaml_defaults(monkeypatch) -> None:
    clear_secrets(monkeypatch)
    settings = load_settings()
    assert settings.meeting.platform == "google_meet"
    assert settings.meeting.join_mode == "signed_in"
    assert settings.meeting.bot_name == "TechSol BA"
    assert settings.meeting.consent_message == CONSENT_MESSAGE
    assert settings.meeting.timezone == "UTC"
    assert settings.meeting.lobby_timeout_s == 300
    assert settings.meeting.alone_timeout_s == 60
    assert settings.captions.stable_ms == 800
    assert settings.captions.language == "en"
    assert settings.speakers.aliases_file == "config/people.yaml"
    assert settings.trigger.phrases == ["hey ba"]
    assert settings.trigger.fuzzy_threshold == 85
    assert settings.trigger.followup_window_s == 20
    assert settings.trigger.pause_wait_s == 5
    assert settings.trigger.dedup_window_s == 3
    assert settings.llm.provider == "openai"
    assert settings.llm.model == "gpt-5.5"
    assert settings.llm.timeout_s == 25
    assert settings.llm.base_url == "https://api.openai.com/v1"
    assert settings.knowledge.dir == "data/knowledge"
    assert settings.knowledge.min_score == 0.5
    assert settings.tasks.auto_create is True
    assert settings.notion.enabled is True
    assert settings.notion.schema_file == "config/notion_schema.yaml"
    assert settings.notion.databases.summary == ""
    assert settings.notion.databases.tasks == ""
    assert settings.storage.sqlite_path == "data/state/meeting.db"
    assert settings.llm.api_key is None
    assert settings.notion.token is None


def test_t01_u2_env_overrides_model(monkeypatch) -> None:
    clear_secrets(monkeypatch)
    monkeypatch.setenv("BA_LLM__MODEL", "gpt-test-model")
    settings = load_settings()
    assert settings.llm.model == "gpt-test-model"


def test_t01_u3_missing_secret_names_the_key(monkeypatch) -> None:
    clear_secrets(monkeypatch)
    settings = load_settings()
    with pytest.raises(StartupError, match="BA_LLM__API_KEY"):
        ensure_ready(settings)

    settings.llm.api_key = SecretStr("present")
    settings.notion.enabled = True
    settings.notion.token = None
    with pytest.raises(StartupError, match="BA_NOTION__TOKEN"):
        ensure_ready(settings)


def test_t01_u4_repr_hides_secrets(monkeypatch) -> None:
    clear_secrets(monkeypatch)
    settings = load_settings()
    settings.llm.api_key = SecretStr("super-secret-token-xyz")
    settings.notion.token = SecretStr("notion-secret-abc")
    settings.meeting.google_password = SecretStr("google-secret-pwd")
    settings.meeting.google_cookies_b64 = SecretStr("google-secret-cookies")
    text = repr(settings)
    assert "super-secret-token-xyz" not in text
    assert "notion-secret-abc" not in text
    assert "google-secret-pwd" not in text
    assert "google-secret-cookies" not in text
    assert "super-secret-token-xyz" not in str(settings)
    assert "notion-secret-abc" not in str(settings)
    assert "google-secret-pwd" not in str(settings)
    assert "google-secret-cookies" not in str(settings)


def test_t01_validate_meet_url_blocks_ssrf() -> None:
    assert validate_meet_url("https://meet.google.com/abc-defg-hij") == (
        "https://meet.google.com/abc-defg-hij"
    )
    for bad in (
        "",
        "file:///etc/passwd",
        "http://169.254.169.254/latest/meta-data/",
        "https://evil.example.com/meet",
    ):
        with pytest.raises(StartupError):
            validate_meet_url(bad)
