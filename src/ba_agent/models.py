"""Shared records for captions, chat, tasks, triggers, and citations."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class PostReason(StrEnum):
    CONSENT = "CONSENT"
    REPLY = "REPLY"
    CONFIRMATION = "CONFIRMATION"
    MEETING_SAVED = "MEETING_SAVED"


class CaptionLine(BaseModel):
    id: str
    meeting_id: str
    speaker: str
    speaker_raw: str
    start_s: float
    end_s: float
    wall_time: datetime
    text: str


class ChatMessage(BaseModel):
    sender: str
    text: str
    wall_time: datetime
    is_self: bool = False


class TaskRecord(BaseModel):
    task: str
    owner: str
    due: date | None = None
    due_text: str = ""
    status: str = "Not started"
    said_by: str = ""
    time: str = ""
    notion_page_id: str | None = None
    key: str = ""


class TriggerEvent(BaseModel):
    trigger_id: str
    question: str
    speaker: str
    source: Literal["speech", "chat"]
    caption_id: str | None = None
    t_detected: float
    followup: bool = False


class Citation(BaseModel):
    kind: Literal["knowledge", "web"]
    source: str


class FunctionCall(BaseModel):
    name: str
    arguments: dict = Field(default_factory=dict)


class WebCitation(BaseModel):
    url: str
    title: str = ""


class LLMResponse(BaseModel):
    text: str = ""
    function_calls: list[FunctionCall] = Field(default_factory=list)
    citations: list[WebCitation] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)


class WebResult(BaseModel):
    text: str = ""
    urls: list[str] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)

    @property
    def useful(self) -> bool:
        return bool(self.text.strip() or self.urls)
