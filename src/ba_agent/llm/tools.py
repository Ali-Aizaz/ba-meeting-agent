"""Local function tools. The model does not post to Meet itself."""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, ValidationError

LOCAL_TOOL_NAMES = ("search_knowledge", "search_cars", "create_task", "save_meeting")


def local_tools() -> list[dict]:
    return [
        {
            "type": "function",
            "name": "search_knowledge",
            "description": "Search the local car notes. Returns file and heading citations.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "search_cars",
            "description": (
                "Search the public web for car specs, prices, comparisons, or current facts."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "create_task",
            "description": "Create a Notion task. Requires task, owner, and due date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "owner": {"type": "string"},
                    "due": {"type": "string"},
                },
                "required": ["task", "owner", "due"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "save_meeting",
            "description": "Save the meeting summary and transcript to Notion.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    ]


def coerce_args(raw: dict, model: type[BaseModel], repair: Callable[[dict], dict]):
    """Validate tool arguments. One repair round, then give up."""
    try:
        return model.model_validate(raw)
    except ValidationError:
        pass
    try:
        fixed = repair(raw)
        return model.model_validate(fixed)
    except ValidationError:
        return None
