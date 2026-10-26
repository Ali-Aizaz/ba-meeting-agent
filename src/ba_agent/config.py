"""YAML defaults with BA_ environment overrides. Secrets stay SecretStr."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

CONSENT_MESSAGE = (
    'Hi, I\'m TechSol BA, an AI assistant. I\'m using this meeting\'s captions to keep notes '
    "(who said what, when) and to record tasks in Notion. Say \"Hey BA …\" to ask me something. "
    "Tell the host if you do not consent."
)


class StartupError(Exception):
    """Startup stopped because a secret or a remote schema check failed."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class MeetingSettings(_Model):
    platform: str = "google_meet"
    join_mode: str = "signed_in"
    bot_name: str = "TechSol BA"
    consent_message: str = CONSENT_MESSAGE
    timezone: str = "UTC"
    lobby_timeout_s: int = 300
    alone_timeout_s: int = 60


class CaptionsSettings(_Model):
    stable_ms: int = 800
    language: str = "en"


class SpeakersSettings(_Model):
    aliases_file: str = "config/people.yaml"


class TriggerSettings(_Model):
    phrases: list[str] = Field(default_factory=lambda: ["hey ba"])
    fuzzy_threshold: int = 85
    followup_window_s: int = 20
    pause_wait_s: int = 5
    dedup_window_s: int = 3


class LLMSettings(_Model):
    provider: str = "openai"
    model: str = "gpt-5.5"
    api_key: SecretStr | None = None
    timeout_s: float = 25
    summary_timeout_s: float = 20
    base_url: str = "https://api.openai.com/v1"


class KnowledgeSettings(_Model):
    dir: str = "data/knowledge"
    min_score: float = 0.5


class TasksSettings(_Model):
    auto_create: bool = True


class NotionDatabaseSettings(_Model):
    summary: str = ""
    tasks: str = ""


class NotionSettings(_Model):
    enabled: bool = True
    token: SecretStr | None = None
    schema_file: str = "config/notion_schema.yaml"
    databases: NotionDatabaseSettings = Field(default_factory=NotionDatabaseSettings)
    version: str = "2022-06-28"
    parent_page_id: str = ""


class StorageSettings(_Model):
    sqlite_path: str = "data/state/meeting.db"


class ServerSettings(_Model):
    status_host: str = "127.0.0.1"
    status_port: int = 8765


class BrowserSettings(_Model):
    headless: bool = False
    profile_dir: str = "data/state/browser-profile"
    rejoin_limit: int = 3
    no_sandbox: bool = True
    receive_video: bool = False


def package_root() -> Path:
    """Repository root when running from a checkout, else the directory above the package."""
    return Path(__file__).resolve().parents[2]


def find_settings_yaml() -> Path | None:
    override = os.environ.get("BA_SETTINGS_FILE")
    if override:
        return Path(override)
    candidates = [
        Path.cwd() / "config" / "settings.yaml",
        package_root() / "config" / "settings.yaml",
        Path("/app/config/settings.yaml"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def resolve_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    cwd_path = Path.cwd() / path
    if cwd_path.exists():
        return cwd_path
    packaged = package_root() / path
    if packaged.exists():
        return packaged
    return cwd_path


def read_prompt(name: str) -> str:
    return resolve_path(f"config/prompts/{name}").read_text(encoding="utf-8")


class _YamlSource(PydanticBaseSettingsSource):
    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return None, "", False

    def __call__(self) -> dict[str, Any]:
        path = find_settings_yaml()
        if path is None:
            return {}
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            return {}
        return loaded


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BA_",
        env_nested_delimiter="__",
        env_ignore_empty=True,
        extra="ignore",
    )

    meeting: MeetingSettings = Field(default_factory=MeetingSettings)
    captions: CaptionsSettings = Field(default_factory=CaptionsSettings)
    speakers: SpeakersSettings = Field(default_factory=SpeakersSettings)
    trigger: TriggerSettings = Field(default_factory=TriggerSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    knowledge: KnowledgeSettings = Field(default_factory=KnowledgeSettings)
    tasks: TasksSettings = Field(default_factory=TasksSettings)
    notion: NotionSettings = Field(default_factory=NotionSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)
    browser: BrowserSettings = Field(default_factory=BrowserSettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            _YamlSource(settings_cls),
            file_secret_settings,
        )

    def __repr__(self) -> str:
        data = self.model_dump(mode="python")
        data["llm"]["api_key"] = "***"
        data["notion"]["token"] = "***"
        return f"Settings({data})"

    __str__ = __repr__


def _secret_text(secret: SecretStr | None) -> str | None:
    if secret is None:
        return None
    value = secret.get_secret_value().strip()
    return value or None


def load_settings() -> Settings:
    settings = Settings()
    if _secret_text(settings.llm.api_key) is None:
        alt = os.environ.get("OPENAI_API_KEY", "").strip()
        if alt:
            settings.llm.api_key = SecretStr(alt)
    return settings


def ensure_ready(settings: Settings) -> None:
    """Stop before the browser starts when a required secret is missing."""
    if _secret_text(settings.llm.api_key) is None:
        raise StartupError("Missing required secret: BA_LLM__API_KEY")
    if settings.notion.enabled and _secret_text(settings.notion.token) is None:
        raise StartupError("Missing required secret: BA_NOTION__TOKEN")


def validate_meet_url(url: str) -> str:
    """Allow Google Meet URLs (and localhost fixtures for integration tests)."""
    cleaned = url.strip()
    if not cleaned:
        raise StartupError("A Meet URL is required")
    if cleaned.startswith("https://meet.google.com/"):
        return cleaned
    if cleaned.startswith(("http://127.0.0.1:", "http://localhost:")):
        return cleaned
    raise StartupError("Meet URL must start with https://meet.google.com/")


def start_join(settings: Settings, url: str, launch_browser) -> None:
    """Validate secrets, then start the browser. Tests pass the launcher."""
    validate_meet_url(url)
    ensure_ready(settings)
    launch_browser()
