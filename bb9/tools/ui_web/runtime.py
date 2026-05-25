"""Minimal local web UI for image paste/upload."""

from __future__ import annotations

import base64
import json
import shlex
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bb9.core.models import Action, GuardianDecision, Observation, RunContext


HOST = "127.0.0.1"
DEFAULT_PORT = 8769
MAX_IMAGE_BYTES = 8_000_000
MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def action_from_text(text: str) -> Action:
    parts = shlex.split(text)
    op = parts[0].lower() if parts else "start"
    params = _parse_params(parts[1:])
    if op != "start":
        return Action(name="ui_web", params={"op": "invalid", "raw": text}, risk="forbidden")
    params["op"] = "start"
    return Action(name="ui_web", params=params, risk="low")


def review(action: Action, _: RunContext) -> GuardianDecision:
    if action.params.get("op") == "start":
        return GuardianDecision(verdict="allow", reason="local ui helper", action=action)
    return GuardianDecision(verdict="block", reason="invalid ui_web action", action=action)


def execute(action: Action) -> Observation:
    if action.params.get("op") != "start":
        return Observation(ok=False, summary="Invalid ui_web tool operation.")
    port = _bounded_int(action.params.get("port"), DEFAULT_PORT, 1024, 65535)
    workspace = Path.cwd()
    uploads_dir = workspace / ".bb9" / "uploads" / "web"
    server = _server(port, uploads_dir)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://{HOST}:{port}"
    if str(action.params.get("open", "true")).lower() not in {"0", "false", "no", "non"}:
        webbrowser.open(url)
    return Observation(ok=True, summary=f"BB9 web UI started: {url}", data={"url": url, "uploads_dir": str(uploads_dir)})


def _server(port: int, uploads_dir: Path) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path not in {"/", "/index.html"}:
                self.send_error(404)
                return
            _send(self, 200, "text/html; charset=utf-8", _HTML.encode("utf-8"))

        def do_POST(self):  # noqa: N802
            if self.path != "/api/upload":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("content-length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_IMAGE_BYTES * 2:
                _json(self, 413, {"ok": False, "error": "payload_too_large"})
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                _json(self, 400, {"ok": False, "error": "invalid_json"})
                return
            mime = str(payload.get("mime") or "").lower()
            if mime not in MIME_EXT:
                _json(self, 415, {"ok": False, "error": "unsupported_image_type"})
                return
            try:
                image_bytes = base64.b64decode(str(payload.get("data") or ""), validate=True)
            except Exception:
                _json(self, 400, {"ok": False, "error": "invalid_base64"})
                return
            if len(image_bytes) > MAX_IMAGE_BYTES:
                _json(self, 413, {"ok": False, "error": "image_too_large"})
                return
            uploads_dir.mkdir(parents=True, exist_ok=True)
            path = uploads_dir / f"{uuid.uuid4().hex[:10]}{MIME_EXT[mime]}"
            path.write_bytes(image_bytes)
            _json(self, 200, {"ok": True, "path": str(path), "reference": f"[image: {path}]"})

        def log_message(self, *_args):  # noqa: D401
            return

    return ThreadingHTTPServer((HOST, port), Handler)


def _send(handler: BaseHTTPRequestHandler, status: int, content_type: str, body: bytes) -> None:
    handler.send_response(status)
    handler.send_header("content-type", content_type)
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    _send(handler, status, "application/json", json.dumps(payload).encode("utf-8"))


def _parse_params(parts: list[str]) -> dict[str, str]:
    params: dict[str, str] = {}
    for part in parts:
        if "=" in part:
            key, value = part.split("=", 1)
            params[key.strip().replace("-", "_")] = value.strip()
    return params


def _bounded_int(raw, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


_HTML = """<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>BB9 image paste</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 32px; max-width: 760px; }
    textarea { box-sizing: border-box; width: 100%; min-height: 130px; font: inherit; }
    .drop { border: 1px dashed #777; padding: 22px; margin: 16px 0; }
    img { max-width: 220px; max-height: 160px; display: block; margin: 8px 0; }
    code { background: #eee; padding: 2px 4px; }
    button { padding: 8px 12px; }
  </style>
</head>
<body>
  <h1>BB9 image paste</h1>
  <textarea id="message" placeholder="Colle une image ici, ou glisse un screenshot dans la zone."></textarea>
  <div id="drop" class="drop">Dépose une image ou colle un screenshot.</div>
  <div id="items"></div>
  <button id="copy">Copier le message BB9</button>
  <pre id="output"></pre>
  <script>
    const refs = [];
    const items = document.getElementById('items');
    const output = document.getElementById('output');
    const message = document.getElementById('message');
    async function upload(file) {
      if (!file || !file.type.startsWith('image/')) return;
      const dataUrl = await new Promise((resolve) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.readAsDataURL(file);
      });
      const base64 = String(dataUrl).split(',', 2)[1] || '';
      const res = await fetch('/api/upload', {
        method: 'POST',
        headers: {'content-type': 'application/json'},
        body: JSON.stringify({mime: file.type, data: base64})
      });
      const payload = await res.json();
      if (!payload.ok) { output.textContent = payload.error || 'upload failed'; return; }
      refs.push(payload.reference);
      const img = document.createElement('img');
      img.src = dataUrl;
      items.appendChild(img);
      render();
    }
    function render() {
      output.textContent = [message.value.trim(), ...refs].filter(Boolean).join('\\n');
    }
    document.addEventListener('paste', (event) => {
      for (const item of event.clipboardData.items) {
        if (item.kind === 'file') upload(item.getAsFile());
      }
    });
    document.getElementById('drop').addEventListener('dragover', (event) => event.preventDefault());
    document.getElementById('drop').addEventListener('drop', (event) => {
      event.preventDefault();
      for (const file of event.dataTransfer.files) upload(file);
    });
    message.addEventListener('input', render);
    document.getElementById('copy').onclick = async () => {
      render();
      await navigator.clipboard.writeText(output.textContent);
    };
  </script>
</body>
</html>"""
