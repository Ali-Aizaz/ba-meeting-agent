"""python -m ba_agent join <meet-url>"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys

import httpx

from ba_agent.config import Settings, load_settings, start_join
from ba_agent.session import run_join


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ba_agent")
    sub = parser.add_subparsers(dest="command", required=True)
    join = sub.add_parser("join", help="Join a Google Meet URL")
    join.add_argument("url")
    join.add_argument("--headless", action="store_true")
    join.add_argument("--once", action="store_true", help="Close browser after one meeting")
    sub.add_parser("login", help="Sign in to Google in the persistent browser profile")
    sub.add_parser("status", help="Read the local /status endpoint")
    args = parser.parse_args(argv)
    settings = load_settings()
    if args.command == "status":
        _print_status(settings)
        return
    if args.command == "login":
        asyncio.run(_run_login(settings))
        return
    if args.headless:
        settings.browser.headless = True
    if args.once:
        settings.browser.keep_alive = False
    start_join(settings, args.url, launch_browser=lambda: None)
    if _delegate_to_running_instance(settings, args.url):
        return
    try:
        asyncio.run(_run_until_stopped(settings, args.url))
    except KeyboardInterrupt:
        sys.exit(130)


def _delegate_to_running_instance(settings: Settings, url: str) -> bool:
    """If a local ba_agent process is already running with an open browser, switch it to url."""
    base = f"http://{settings.server.status_host}:{settings.server.status_port}"
    try:
        res = httpx.post(f"{base}/join", json={"url": url}, timeout=2.0)
        if res.status_code == 200 and res.json().get("ok"):
            print(f"Sent join request to running browser session: {url}")
            return True
    except Exception:
        return False
    return False


async def _run_login(settings: Settings) -> None:
    from ba_agent.bot.google_meet.adapter import GoogleMeetAdapter

    adapter = GoogleMeetAdapter(settings)
    try:
        await adapter._ensure_browser()
        email, password = adapter._google_credentials()
        if email and password:
            await adapter._perform_google_login("https://meet.google.com/")
            print(f"Signed in as {email} in {adapter.profile_dir}")
        else:
            await adapter.page.goto("https://accounts.google.com/", wait_until="domcontentloaded")
            print(f"Browser opened at {adapter.profile_dir}. Sign in, then press Ctrl+C.")
            while True:
                await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        await adapter.close(force=True)


async def _run_until_stopped(settings: Settings, url: str) -> None:
    """Cancel the join task on SIGTERM or SIGINT so run_join's finally can finish."""
    loop = asyncio.get_running_loop()
    task: asyncio.Task[None] | None = None
    caught: list[signal.Signals] = []

    def stop(signum: signal.Signals) -> None:
        caught.append(signum)
        if task is not None and not task.done():
            task.cancel()

    installed: list[signal.Signals] = []
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop, sig)
            installed.append(sig)
    except NotImplementedError:
        for sig in installed:
            loop.remove_signal_handler(sig)
        await run_join(settings, url)
        return

    task = asyncio.create_task(run_join(settings, url))
    try:
        await task
    except asyncio.CancelledError:
        if signal.SIGINT in caught:
            raise KeyboardInterrupt
        return
    finally:
        for sig in installed:
            loop.remove_signal_handler(sig)


def _print_status(settings) -> None:
    url = f"http://{settings.server.status_host}:{settings.server.status_port}/status"
    try:
        response = httpx.get(url, timeout=2)
    except httpx.HTTPError as exc:
        raise SystemExit(f"status unavailable: {exc}") from exc
    print(response.text)


if __name__ == "__main__":
    main()
