"""Create the sandbox summary and task databases under a parent page.

Usage:
  BA_NOTION__TOKEN=secret BA_NOTION__PARENT_PAGE_ID=page uv run python scripts/notion_setup.py
"""

from __future__ import annotations

import os
import sys

import httpx

VERSION = "2022-06-28"


def main() -> None:
    token = os.environ.get("BA_NOTION__TOKEN", "").strip()
    parent = os.environ.get("BA_NOTION__PARENT_PAGE_ID", "").strip()
    if not token or not parent:
        raise SystemExit("Set BA_NOTION__TOKEN and BA_NOTION__PARENT_PAGE_ID")
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": VERSION,
        "Content-Type": "application/json",
    }
    with httpx.Client(base_url="https://api.notion.com/v1", headers=headers, timeout=30) as client:
        summary = _database(
            client,
            parent,
            "Title",
            "Meeting notes",
            {
                "Date": {"date": {}},
                "Summary": {"rich_text": {}},
                "Transcript": {"rich_text": {}},
            },
        )
        tasks = _database(
            client,
            parent,
            "Task",
            "Tasks",
            {
                "Owner": {"rich_text": {}},
                "Due": {"date": {}},
                "Status": _status_property(),
                "Said by": {"rich_text": {}},
                "Time": {"rich_text": {}},
                "Meeting": {"url": {}},
            },
        )
    print("Paste these into config/notion_schema.yaml under databases:")
    print(f"  summary: {summary}")
    print(f"  tasks: {tasks}")


def _database(
    client: httpx.Client,
    parent: str,
    title_property: str,
    title: str,
    properties: dict,
) -> str:
    fields = {title_property: {"title": {}}, **properties}
    response = client.post(
        "/databases",
        json={
            "parent": {"type": "page_id", "page_id": parent},
            "title": [{"type": "text", "text": {"content": title}}],
            "properties": fields,
        },
    )
    response.raise_for_status()
    return response.json()["id"]


def _status_property() -> dict:
    return {
        "status": {
            "options": [
                {"name": "Not started", "color": "default"},
                {"name": "In progress", "color": "blue"},
                {"name": "Done", "color": "green"},
            ]
        }
    }


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
