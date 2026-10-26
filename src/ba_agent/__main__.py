"""python -m ba_agent join <meet-url>"""

from __future__ import annotations

import argparse
import asyncio
import sys

import httpx

from ba_agent.config import load_settings, start_join
from ba_agent.session import run_join


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ba_agent")
    sub = parser.add_subparsers(dest="command", required=True)
    join = sub.add_parser("join", help="Join a Google Meet URL")
    join.add_argument("url")
    join.add_argument("--headless", action="store_true")
    sub.add_parser("status", help="Read the local /status endpoint")
    args = parser.parse_args(argv)
    settings = load_settings()
    if args.command == "status":
        _print_status(settings)
        return
    if args.headless:
        settings.browser.headless = True
    start_join(settings, args.url, launch_browser=lambda: None)
    try:
        asyncio.run(run_join(settings, args.url))
    except KeyboardInterrupt:
        sys.exit(130)


def _print_status(settings) -> None:
    url = f"http://{settings.server.status_host}:{settings.server.status_port}/status"
    try:
        response = httpx.get(url, timeout=2)
    except httpx.HTTPError as exc:
        raise SystemExit(f"status unavailable: {exc}") from exc
    print(response.text)


if __name__ == "__main__":
    main()
