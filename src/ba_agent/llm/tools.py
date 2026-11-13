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
            "description": (
                "Search local car engineering notes ONLY if <knowledge> does not already "
                "contain the needed automotive concept. Returns (src: filename.md) chunks."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Concise automotive concept or acronym to look up.",
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "search_cars",
            "description": (
                "Search the public web for car specs, prices, or comparisons ONLY if "
                "<knowledge> and <web_results> lack the answer. Pass a standalone car-only "
                "query; never include participant names, timestamps, URLs, or meeting text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Standalone vehicle/car search query with no meeting data.",
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "create_task",
            "description": (
                "Create a new Notion task ONLY when a participant explicitly asks to add or "
                "assign an action item and task, owner, and due date are all known. "
                "Never call when listing or checking existing tasks."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": (
                            "Concise verb-first action phrase without owner or due date."
                        ),
                    },
                    "owner": {
                        "type": "string",
                        "description": "Specific person's name (resolve 'me'/'I' to speaker).",
                    },
                    "due": {
                        "type": "string",
                        "description": (
                            "Deadline (e.g., 'Friday', 'tomorrow', '28 Oct', or 'YYYY-MM-DD')."
                        ),
                    },
                },
                "required": ["task", "owner", "due"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "save_meeting",
            "description": (
                "Save the meeting summary and transcript to Notion ONLY when a participant "
                "explicitly asks to save or export the meeting notes."
            ),
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
