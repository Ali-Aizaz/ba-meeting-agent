"""Local /status for captions_enabled and chat_enabled."""

from __future__ import annotations

import json
import socket
import socketserver
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ba_agent.config import StartupError, validate_meet_url

_orig_getfqdn = socket.getfqdn


def _safe_getfqdn(name: str = "") -> str:
    try:
        return _orig_getfqdn(name)
    except (OSError, UnicodeError):
        return name or "localhost"


socket.getfqdn = _safe_getfqdn


class _StatusHTTPServer(ThreadingHTTPServer):
    def server_bind(self) -> None:
        # Avoid HTTPServer.server_bind's socket.getfqdn(host), which raises
        # UnicodeError("label too long") on 64-char container hostnames.
        socketserver.TCPServer.server_bind(self)
        bind_host, bind_port = self.server_address[:2]
        self.server_name = str(bind_host)
        self.server_port = int(bind_port)


class StatusState:
    def __init__(
        self,
        meeting_id: str = "",
        on_join: Callable[[str], None] | None = None,
    ) -> None:
        self.meeting_id = meeting_id
        self.captions_enabled = False
        self.chat_enabled = False
        self.on_join = on_join

    def as_dict(self) -> dict:
        return {
            "meeting_id": self.meeting_id,
            "captions_enabled": self.captions_enabled,
            "chat_enabled": self.chat_enabled,
        }


def start_status_server(state: StatusState, host: str, port: int) -> ThreadingHTTPServer | None:
    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            # "/" is the Render health check and the public service URL.
            if self.path.split("?", 1)[0] not in {"/", "/status"}:
                self.send_response(404)
                self.end_headers()
                return
            self._send_json(state.as_dict(), 200)

        def do_POST(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0] != "/join":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw.decode("utf-8"))
            except Exception:
                self._send_json({"ok": False, "error": "Invalid JSON body"}, 400)
                return
            url = data.get("url") if isinstance(data, dict) else ""
            try:
                cleaned = validate_meet_url(str(url or ""))
            except StartupError as exc:
                self._send_json({"ok": False, "error": str(exc)}, 400)
                return
            if state.on_join is not None:
                state.on_join(cleaned)
            self._send_json({"ok": True, "meet_url": cleaned, **state.as_dict()}, 200)

        def log_message(self, fmt: str, *args) -> None:
            return

    try:
        server = _StatusHTTPServer((host, port), Handler)
    except (OSError, UnicodeError):
        return None
    thread = threading.Thread(target=server.serve_forever, name="ba-status", daemon=True)
    thread.start()
    return server
