"""Local /status for captions_enabled and chat_enabled."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


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
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args) -> None:
            return

    try:
        server = ThreadingHTTPServer((host, port), Handler)
    except OSError:
        return None
    thread = threading.Thread(target=server.serve_forever, name="ba-status", daemon=True)
    thread.start()
    return server
