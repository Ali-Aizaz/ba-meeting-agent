"""Local /status for captions_enabled and chat_enabled."""

from __future__ import annotations

import json
import socket
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
    def __init__(self, meeting_id: str = "") -> None:
        self.meeting_id = meeting_id
        self.captions_enabled = False
        self.chat_enabled = False

    def as_dict(self) -> dict:
        return {
            "meeting_id": self.meeting_id,
            "captions_enabled": self.captions_enabled,
            "chat_enabled": self.chat_enabled,
        }


def start_status_server(state: StatusState, host: str, port: int) -> ThreadingHTTPServer | None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            # "/" is the Render health check and the public service URL.
            if self.path.split("?", 1)[0] not in {"/", "/status"}:
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps(state.as_dict()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args) -> None:
            return

    try:
        server = _StatusHTTPServer((host, port), Handler)
    except (OSError, UnicodeError):
        return None
    thread = threading.Thread(target=server.serve_forever, name="ba-status", daemon=True)
    thread.start()
    return server
