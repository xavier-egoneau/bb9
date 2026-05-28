"""HTTP transport for the reusable BB9 chat API."""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

HOST = "127.0.0.1"
DEFAULT_PORT = 8770
MAX_MESSAGE_BYTES = 200_000
MAX_UPLOAD_BYTES = 16_000_000


def chat_api_server(app: Any, port: int = DEFAULT_PORT, *, static_root: Any | None = None) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            path = urlparse(self.path).path
            if path == "/api/history":
                _json(self, 200, app.history_payload())
                return
            if path == "/api/status":
                _json(self, 200, app.status_payload())
                return
            if path == "/health":
                _json(self, 200, {"ok": True, "features": ["chat-api", "image-api"]})
                return
            if path == "/api/image":
                image_response = _image_response(self.path)
                if image_response is None:
                    self.send_error(404)
                    return
                content_type, body = image_response
                _send(self, 200, content_type, body)
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
            if path not in {"/api/chat", "/api/upload", "/api/approval"}:
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("content-length", "0"))
            except ValueError:
                length = 0
            max_bytes = MAX_UPLOAD_BYTES if path == "/api/upload" else MAX_MESSAGE_BYTES
            if length <= 0 or length > max_bytes:
                _json(self, 413, {"ok": False, "error": "payload_too_large"})
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                _json(self, 400, {"ok": False, "error": "invalid_json"})
                return
            if path == "/api/upload":
                result = app.upload_image(mime=str(payload.get("mime") or ""), data=str(payload.get("data") or ""))
            elif path == "/api/approval":
                result = app.resolve_approval(
                    approval_id=str(payload.get("id") or ""),
                    decision=str(payload.get("decision") or ""),
                )
            else:
                result = app.run_message(str(payload.get("message") or ""))
            _json(self, 200 if result.get("ok") else 400, result)

        def log_message(self, *_args):  # noqa: D401
            return

    return ThreadingHTTPServer((HOST, port), Handler)


def _send(handler: BaseHTTPRequestHandler, status: int, content_type: str, body: bytes) -> None:
    handler.send_response(status)
    handler.send_header("content-type", content_type)
    handler.send_header("cache-control", "no-store")
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    _send(handler, status, "application/json", json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _image_response(request_path: str) -> tuple[str, bytes] | None:
    query = parse_qs(urlparse(request_path).query)
    raw_path = (query.get("path") or [""])[0].strip()
    if not raw_path:
        return None
    workspace = Path.cwd().resolve(strict=False)
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = workspace / path
    path = path.resolve(strict=False)
    if not _is_allowed_image_path(path, workspace) or not path.is_file():
        return None
    content_type = mimetypes.guess_type(path.name)[0] or ""
    if content_type not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        return None
    return content_type, path.read_bytes()


def _is_allowed_image_path(path: Path, workspace: Path) -> bool:
    roots = (
        workspace / ".bb9" / "uploads",
        workspace / ".bb9" / "artifacts" / "screenshots",
    )
    for root in roots:
        root = root.resolve(strict=False)
        if path == root or root in path.parents:
            return True
    return _is_bb9_image_artifact_path(path)


def _is_bb9_image_artifact_path(path: Path) -> bool:
    parts = path.parts
    for index, part in enumerate(parts):
        if part != ".bb9":
            continue
        tail = parts[index + 1 :]
        if len(tail) >= 2 and tail[0] == "uploads":
            return True
        if len(tail) >= 3 and tail[0] == "artifacts" and tail[1] == "screenshots":
            return True
    return False


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
