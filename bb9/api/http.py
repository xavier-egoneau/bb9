"""HTTP transport for the reusable BB9 chat API."""

from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlparse

HOST = "127.0.0.1"
DEFAULT_PORT = 8770
MAX_MESSAGE_BYTES = 200_000


def chat_api_server(app: Any, port: int = DEFAULT_PORT, *, static_root: Any | None = None) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            path = urlparse(self.path).path
            if path == "/api/history":
                _json(self, 200, app.history_payload())
                return
            if path == "/health":
                _json(self, 200, {"ok": True})
                return
            if static_root is not None:
                static_response = _static_response(static_root, path)
                if static_response is not None:
                    content_type, body = static_response
                    _send(self, 200, content_type, body)
                    return
            self.send_error(404)

        def do_POST(self):  # noqa: N802
            path = urlparse(self.path).path
            if path != "/api/chat":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("content-length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_MESSAGE_BYTES:
                _json(self, 413, {"ok": False, "error": "payload_too_large"})
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                _json(self, 400, {"ok": False, "error": "invalid_json"})
                return
            result = app.run_message(str(payload.get("message") or ""))
            _json(self, 200 if result.get("ok") else 400, result)

        def log_message(self, *_args):  # noqa: D401
            return

    return ThreadingHTTPServer((HOST, port), Handler)


def _send(handler: BaseHTTPRequestHandler, status: int, content_type: str, body: bytes) -> None:
    handler.send_response(status)
    handler.send_header("content-type", content_type)
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    _send(handler, status, "application/json", json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _static_response(static_root: Any, request_path: str) -> tuple[str, bytes] | None:
    relative = unquote(request_path).lstrip("/")
    if not relative:
        relative = "index.html"
    parts = [part for part in relative.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        return None
    target = static_root.joinpath(*parts)
    if target.is_dir():
        target = target.joinpath("index.html")
    if not target.is_file():
        return None
    content_type = mimetypes.guess_type(parts[-1] if parts else "index.html")[0] or "application/octet-stream"
    if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
        content_type = f"{content_type}; charset=utf-8"
    return content_type, target.read_bytes()
