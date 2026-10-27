"""Seed the Notion Summary and Tasks databases with realistic TechSol BA sample data.

Usage:
  uv run python scripts/notion_seed.py
  uv run python scripts/notion_seed.py --reset
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ba_agent.config import resolve_path
from ba_agent.models import TaskRecord
from ba_agent.notion.client import NotionService, _transcript_toggle
from ba_agent.notion.schema import (
    NotionSchema,
    load_notion_schema,
    plain_property,
    summary_properties,
)
from ba_agent.tasks_extract.extractor import dedup_key


@dataclass(frozen=True)
class SeedTask:
    task: str
    owner: str
    due: date
    due_text: str
    status: str
    said_by: str
    time: str


@dataclass(frozen=True)
class SeedMeeting:
    title: str
    day: date
    summary: str
    transcript: str
    tasks: tuple[SeedTask, ...]


SEED_MEETINGS: tuple[SeedMeeting, ...] = (
    SeedMeeting(
        title="TechSol BA 2026-10-20 powertrain-kickoff",
        day=date(2026, 10, 20),
        summary=(
            "Reviewed mid-size crossover powertrain options (full HEV vs 48V mild hybrid vs BEV). "
            "Team aligned on evaluating the full hybrid (HEV) architecture with regenerative braking "
            "for city stop-and-go efficiency while keeping highway engine efficiency, and comparing "
            "LFP vs NMC battery packs for the EV variant.\n\n"
            "Tasks:\n"
            "- Ali: benchmark LFP vs NMC cold-weather range curves (2026-10-22)\n"
            "- Sara: compile regenerative braking efficiency metrics from city cycle tests (2026-10-23)\n"
            "- Omar: request inverter thermal specs from Tier-1 supplier (2026-10-24)"
        ),
        transcript=(
            "[00:12] Sara: Welcome everyone to the Q4 crossover powertrain kickoff.\n"
            "[00:28] Ali: Today we need to narrow down whether our baseline trim uses a 48V mild hybrid or a full HEV.\n"
            "[00:54] Omar: Hey BA, what is the difference between a mild hybrid and a full hybrid?\n"
            "[00:58] TechSol BA: A mild hybrid (MHEV) uses a small 48V battery and starter-generator to assist the engine "
            "but cannot drive on electric power alone, whereas a full hybrid (HEV) can propel the car short distances at low speed "
            "on electric power alone (src: hybrid.md).\n"
            "[01:35] Sara: That electric-only low-speed mode is critical for our urban fleet targets.\n"
            "[02:10] Sara: Ali, please benchmark LFP vs NMC cold-weather range curves by Thursday.\n"
            "[02:45] Ali: Will do. Sara, can you compile regenerative braking efficiency metrics from city cycle tests by Friday?\n"
            "[03:20] Sara: Sure. Also, Omar, please request inverter thermal specs from the Tier-1 supplier by Oct 24."
        ),
        tasks=(
            SeedTask(
                task="benchmark LFP vs NMC cold-weather range curves",
                owner="Ali",
                due=date(2026, 10, 22),
                due_text="Thu",
                status="Done",
                said_by="Sara",
                time="[02:10]",
            ),
            SeedTask(
                task="compile regenerative braking efficiency metrics from city cycle tests",
                owner="Sara",
                due=date(2026, 10, 23),
                due_text="Fri",
                status="Done",
                said_by="Ali",
                time="[02:45]",
            ),
            SeedTask(
                task="request inverter thermal specs from Tier-1 supplier",
                owner="Omar",
                due=date(2026, 10, 24),
                due_text="Oct 24",
                status="Done",
                said_by="Sara",
                time="[03:20]",
            ),
        ),
    ),
    SeedMeeting(
        title="TechSol BA 2026-10-23 chassis-and-safety-review",
        day=date(2026, 10, 23),
        summary=(
            "Evaluated unibody vs body-on-frame chassis tradeoffs and Euro NCAP 5-star active safety requirements. "
            "Confirmed unibody (monocoque) construction with MacPherson front and multi-link rear suspension, "
            "plus standard ADAS suite (AEB, ACC, LKA, BSM) across all trims.\n\n"
            "Tasks:\n"
            "- Nadia: finalize Euro NCAP vulnerable road user sensor placement (2026-10-28)\n"
            "- Omar: validate multi-link rear suspension packaging with AWD drivetrain (2026-10-29)\n"
            "- Ali: review AEB radar calibration budget with procurement (2026-10-29)"
        ),
        transcript=(
            "[00:18] Ali: Let's review the body and chassis architecture and safety targets for the crossover.\n"
            "[00:45] Nadia: We are targeting a 5-star Euro NCAP rating across adult occupant, child occupant, pedestrian, and safety assist.\n"
            "[01:15] Omar: For the chassis, unibody gives us lower weight and better crash energy absorption with crumple zones.\n"
            "[01:52] Ali: Nadia, please finalize Euro NCAP vulnerable road user sensor placement by October 28.\n"
            "[02:30] Nadia: Got it. Omar, please validate multi-link rear suspension packaging with the AWD drivetrain by October 29.\n"
            "[03:05] Sara: Ali, please review the AEB radar calibration budget with procurement by Thursday."
        ),
        tasks=(
            SeedTask(
                task="finalize Euro NCAP vulnerable road user sensor placement",
                owner="Nadia",
                due=date(2026, 10, 28),
                due_text="Oct 28",
                status="In progress",
                said_by="Ali",
                time="[01:52]",
            ),
            SeedTask(
                task="validate multi-link rear suspension packaging with AWD drivetrain",
                owner="Omar",
                due=date(2026, 10, 29),
                due_text="Oct 29",
                status="In progress",
                said_by="Nadia",
                time="[02:30]",
            ),
            SeedTask(
                task="review AEB radar calibration budget with procurement",
                owner="Ali",
                due=date(2026, 10, 29),
                due_text="Thu",
                status="Not started",
                said_by="Sara",
                time="[03:05]",
            ),
        ),
    ),
    SeedMeeting(
        title="TechSol BA 2026-10-26 competitor-benchmark-sync",
        day=date(2026, 10, 26),
        summary=(
            "Benchmarked Toyota RAV4 Hybrid and Nissan Leaf reference specs against our target BOM. "
            "Discussed Level 2 AC onboard charging vs DC fast charging curves and cabin acoustics.\n\n"
            "Tasks:\n"
            "- Sara: prepare Q4 executive comparison deck for Toyota RAV4 Hybrid vs our HEV trim (2026-10-30)\n"
            "- Nadia: schedule DC fast-charger thermal validation lab slot (2026-11-02)"
        ),
        transcript=(
            "[00:10] Sara: Quick sync on competitor benchmarking before Friday's steering committee.\n"
            "[00:42] Ali: We compared the Toyota RAV4 Hybrid AWD system and the Nissan Leaf BEV charging architecture.\n"
            "[01:20] Nadia: On the BEV side, bypassing the onboard charger during DC fast charging keeps our 10-to-80% target under 30 minutes.\n"
            "[02:05] Ali: Sara, please prepare the Q4 executive comparison deck for Toyota RAV4 Hybrid vs our HEV trim by Friday.\n"
            "[02:50] Sara: Nadia, can you schedule the DC fast-charger thermal validation lab slot by November 2?"
        ),
        tasks=(
            SeedTask(
                task="prepare Q4 executive comparison deck for Toyota RAV4 Hybrid vs our HEV trim",
                owner="Sara",
                due=date(2026, 10, 30),
                due_text="Fri",
                status="In progress",
                said_by="Ali",
                time="[02:05]",
            ),
            SeedTask(
                task="schedule DC fast-charger thermal validation lab slot",
                owner="Nadia",
                due=date(2026, 11, 2),
                due_text="Nov 2",
                status="Not started",
                said_by="Sara",
                time="[02:50]",
            ),
        ),
    ),
)


def _load_dotenv(env_path: Path) -> None:
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key, value)


async def _existing_summaries(service: NotionService, schema: NotionSchema) -> dict[str, tuple[str, str]]:
    """Return {title: (page_id, url)} for pages currently in the summary database."""
    title_prop = schema.summary_props["title"].name
    found: dict[str, tuple[str, str]] = {}
    cursor = None
    while True:
        body: dict = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        data = await service._request("POST", f"/databases/{schema.summary_db}/query", body)
        for page in data.get("results") or []:
            props = page.get("properties") or {}
            title = plain_property(props.get(title_prop) or {})
            page_id = page.get("id", "")
            url = page.get("url", "")
            if title and page_id:
                found[title] = (page_id, url)
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return found


async def _archive_all(service: NotionService, database_id: str) -> int:
    count = 0
    cursor = None
    page_ids: list[str] = []
    while True:
        body: dict = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        data = await service._request("POST", f"/databases/{database_id}/query", body)
        for page in data.get("results") or []:
            if page.get("id"):
                page_ids.append(page["id"])
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    for page_id in page_ids:
        await service._request("PATCH", f"/pages/{page_id}", {"archived": True})
        count += 1
    return count


async def seed_notion(*, reset: bool = False) -> None:
    _load_dotenv(Path(".env"))
    token = os.environ.get("BA_NOTION__TOKEN", "").strip()
    schema = load_notion_schema(resolve_path("config/notion_schema.yaml"))
    schema.summary_db = os.environ.get("BA_NOTION__DATABASES__SUMMARY", "").strip() or schema.summary_db
    schema.tasks_db = os.environ.get("BA_NOTION__DATABASES__TASKS", "").strip() or schema.tasks_db

    if not token or not schema.summary_db or not schema.tasks_db:
        raise SystemExit(
            "Missing BA_NOTION__TOKEN, BA_NOTION__DATABASES__SUMMARY, or BA_NOTION__DATABASES__TASKS"
        )

    service = NotionService(token=token, schema=schema, meeting_id="seed")
    try:
        await service.verify()
        if reset:
            archived_summaries = await _archive_all(service, schema.summary_db)
            archived_tasks = await _archive_all(service, schema.tasks_db)
            print(f"Archived {archived_summaries} summary row(s) and {archived_tasks} task row(s).")
            service._task_cache.clear()
            service._tasks_loaded = False

        existing_summaries = await _existing_summaries(service, schema)
        summaries_created = 0
        summaries_skipped = 0
        tasks_created = 0
        tasks_skipped = 0

        for meeting in SEED_MEETINGS:
            if meeting.title in existing_summaries:
                _, meeting_url = existing_summaries[meeting.title]
                summaries_skipped += 1
                print(f"• Summary exists: {meeting.title} -> {meeting_url}")
            else:
                note = (
                    meeting.transcript
                    if len(meeting.transcript) <= 1800
                    else meeting.transcript[:1800] + "…"
                )
                props = summary_properties(
                    schema,
                    title=meeting.title,
                    day=meeting.day,
                    summary=meeting.summary,
                    transcript_note=note,
                )
                created = await service._request(
                    "POST",
                    "/pages",
                    {
                        "parent": service.summary_parent,
                        "properties": props,
                        "children": _transcript_toggle(meeting.transcript),
                    },
                )
                meeting_url = created.get("url", "")
                summaries_created += 1
                print(f"✓ Summary seeded: {meeting.title} -> {meeting_url}")

            for item in meeting.tasks:
                record = TaskRecord(
                    task=item.task,
                    owner=item.owner,
                    due=item.due,
                    due_text=item.due_text,
                    status=item.status,
                    said_by=item.said_by,
                    time=item.time,
                    key=dedup_key(item.task, item.owner),
                )
                result = await service.create_task(record, meeting_url=meeting_url)
                if result.created:
                    tasks_created += 1
                    print(f"  {result.message} [{item.status}]")
                else:
                    tasks_skipped += 1
                    print(f"  • {result.message}")

        print(
            f"\nDone! Summaries: {summaries_created} created, {summaries_skipped} existing | "
            f"Tasks: {tasks_created} created, {tasks_skipped} existing."
        )
    finally:
        await service.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Notion Summary and Tasks databases.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Archive existing rows in both databases before seeding.",
    )
    args = parser.parse_args()
    asyncio.run(seed_notion(reset=args.reset))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
