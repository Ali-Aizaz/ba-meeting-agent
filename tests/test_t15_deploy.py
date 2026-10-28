"""T15-U1: secrets fail before the browser, and compose has no GPU runtime."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from pathlib import Path
from typing import cast

import httpx
import pytest
from tests.conftest import clear_secrets

from ba_agent.__main__ import _run_until_stopped
from ba_agent.config import Settings, StartupError, load_settings, start_join
from ba_agent.session import _let_cleanup_finish
from ba_agent.status import StatusState, start_status_server

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


def test_t15_render_blueprint_is_a_docker_service_without_secrets() -> None:
    text = (ROOT / "render.yaml").read_text(encoding="utf-8")
    lowered = text.lower()
    assert "runtime: docker" in lowered
    assert "healthcheckpath: /status" in lowered
    assert "mountpath: /var/data" in lowered
    assert "nvidia" not in lowered
    assert "sk-" not in lowered
    assert "ntn_" not in lowered
    entry = (ROOT / "scripts" / "docker-entrypoint.sh").read_text(encoding="utf-8")
    assert "MEET_URL" in entry
    assert "BA_SERVER__STATUS_PORT" in entry


def test_t15_status_responds_on_root_and_status() -> None:
    joined_urls: list[str] = []
    activity_seen: list[tuple[str, str]] = []
    state = StatusState(
        "m1",
        on_join=joined_urls.append,
        on_activity=lambda text, kind: activity_seen.append((text, kind)),
    )
    state.captions_enabled = True
    state.record_activity('Searching knowledge base for "what is a hybrid?"…', "working")
    server = start_status_server(state, "127.0.0.1", 0)
    assert server is not None
    port = server.server_address[1]
    try:
        for path in ("/", "/status"):
            response = httpx.get(f"http://127.0.0.1:{port}{path}", timeout=2)
            assert response.status_code == 200
            body = response.json()
            assert body["meeting_id"] == "m1"
            assert body["captions_enabled"] is True
            assert body["activity"] == 'Searching knowledge base for "what is a hybrid?"…'
            assert body["activity_kind"] == "working"
            assert body["activity_seq"] == 1
            assert len(body["events"]) == 1
        state.record_activity("Replied to Sara ✓", "success")
        done_body = httpx.get(f"http://127.0.0.1:{port}/status", timeout=2).json()
        assert done_body["activity"] == ""
        assert done_body["activity_kind"] == "success"
        assert done_body["activity_seq"] == 2
        assert len(done_body["events"]) == 2
        assert len(activity_seen) == 2
        join_res = httpx.post(
            f"http://127.0.0.1:{port}/join",
            json={"url": "https://meet.google.com/abc-defg-hij"},
            timeout=2,
        )
        assert join_res.status_code == 200
        assert join_res.json()["ok"] is True
        assert joined_urls == ["https://meet.google.com/abc-defg-hij"]
        bad_join = httpx.post(
            f"http://127.0.0.1:{port}/join",
            json={"url": "https://evil.example.com/abc"},
            timeout=2,
        )
        assert bad_join.status_code == 400
        missing = httpx.get(f"http://127.0.0.1:{port}/nope", timeout=2)
        assert missing.status_code == 404
    finally:
        server.shutdown()


def test_t15_status_handles_long_container_hostname(monkeypatch) -> None:
    import socket

    def fail_idna(_name: str = "") -> str:
        raise UnicodeError("encoding with 'idna' codec failed (UnicodeError: label too long)")

    monkeypatch.setattr("ba_agent.status._orig_getfqdn", fail_idna)
    assert socket.getfqdn("0.0.0.0") == "0.0.0.0"
    state = StatusState("m-cf")
    server = start_status_server(state, "127.0.0.1", 0)
    assert server is not None
    port = server.server_address[1]
    try:
        response = httpx.get(f"http://127.0.0.1:{port}/status", timeout=2)
        assert response.status_code == 200
        assert response.json()["meeting_id"] == "m-cf"
    finally:
        server.shutdown()


def test_t15_cloudflare_container_is_standard_1_without_secrets() -> None:
    text = (ROOT / "wrangler.jsonc").read_text(encoding="utf-8")
    assert '"instance_type": "standard-1"' in text
    assert '"max_instances": 1' in text
    assert '"class_name": "BaContainer"' in text
    lowered = text.lower()
    for marker in (
        "sk-",
        "ntn_",
        "meet.google.com",
        "api_key",
        "ba_llm__api_key",
        "ba_notion__token",
    ):
        assert marker not in lowered


def test_t15_worker_sleeps_after_three_hours_and_reads_secrets_from_env() -> None:
    text = (ROOT / "cloudflare" / "src" / "index.ts").read_text(encoding="utf-8")
    assert 'sleepAfter = "3h"' in text
    assert 'idFromName("meeting")' in text
    assert "portReadyTimeoutMS" in text
    assert "enableInternet = true" in text
    assert 'pathname === "/join"' in text
    assert "handleJoin" in text
    assert "timingSafeEqual" in text
    for name in (
        "MEET_URL",
        "BA_LLM__API_KEY",
        "BA_NOTION__TOKEN",
        "BA_NOTION__DATABASES__SUMMARY",
        "BA_NOTION__DATABASES__TASKS",
    ):
        assert name in text
    assert "sk-" not in text.lower()
    assert "ntn_" not in text
    assert "meet.google.com" not in text


def test_t15_chrome_extension_manifest_and_scripts_are_valid() -> None:
    import json

    ext_dir = ROOT / "extension"
    manifest = json.loads((ext_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3
    assert "storage" in manifest["permissions"]
    assert "https://meet.google.com/*" in manifest["host_permissions"]
    assert manifest["background"]["service_worker"] == "background.js"
    for filename in ("background.js", "content.js", "popup.html", "popup.js"):
        assert (ext_dir / filename).is_file()
    bg_text = (ext_dir / "background.js").read_text(encoding="utf-8")
    content_text = (ext_dir / "content.js").read_text(encoding="utf-8")
    assert "/join" in bg_text
    assert "MEET_DETECTED" in bg_text
    assert "(?:\\/_?meet)?" in bg_text
    assert "(?:\\/_?meet)?" in content_text


def test_t15_entrypoint_forwards_stop_signals_to_python() -> None:
    entry = ROOT / "scripts" / "docker-entrypoint.sh"
    text = entry.read_text(encoding="utf-8")
    assert "exec xvfb-run" not in text
    assert "xvfb-run -a python -m ba_agent" in text
    assert "forward_signal TERM" in text
    assert "forward_signal INT" in text
    assert "MEET_URL" in text
    subprocess.run(["sh", "-n", str(entry)], check=True)


def test_t15_cloudflare_deploy_scripts_have_valid_syntax_and_safe_order() -> None:
    deploy_script = ROOT / "scripts" / "deploy-cloudflare.sh"
    set_meet_script = ROOT / "scripts" / "set-meet-url.sh"
    subprocess.run(["bash", "-n", str(deploy_script)], check=True)
    subprocess.run(["bash", "-n", str(set_meet_script)], check=True)
    text = deploy_script.read_text(encoding="utf-8")
    assert "CALLER_MEET_URL" in text
    assert "npx wrangler deploy" in text
    assert 'npx wrangler secret bulk "$tmp"' in text
    # Full deploy must run `npx wrangler deploy` before `npx wrangler secret bulk`
    # so the first deploy to a fresh Cloudflare account creates the Worker first.
    deploy_idx = text.index("npx wrangler deploy")
    bulk_after_deploy_idx = text.rindex('npx wrangler secret bulk "$tmp"')
    assert deploy_idx < bulk_after_deploy_idx


async def test_t15_sigterm_cancels_join_and_cleanup_still_awaits(monkeypatch) -> None:
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def fake_join(settings, url: str) -> None:
        del settings, url
        started.set()
        try:
            await asyncio.sleep(30)
        finally:
            _let_cleanup_finish()
            await asyncio.sleep(0)
            cleaned.set()

    monkeypatch.setattr("ba_agent.__main__.run_join", fake_join)
    task = asyncio.create_task(
        _run_until_stopped(cast(Settings, object()), "https://meet.google.com/abc-defg-hij")
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(task, timeout=2)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert cleaned.is_set()


async def test_t15_cleanup_awaits_after_the_join_task_is_cancelled() -> None:
    ran = False

    async def body() -> None:
        nonlocal ran
        try:
            await asyncio.sleep(30)
        finally:
            _let_cleanup_finish()
            await asyncio.sleep(0)
            ran = True

    task = asyncio.create_task(body())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ran
