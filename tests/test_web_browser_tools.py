from __future__ import annotations

import base64
import builtins
import json
import tempfile
import threading
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from bb9.core.models import Action, RunContext, Session, Workspace
from bb9.core.tool_runtime import load_tool_module


def _response(body: bytes, *, content_type: str = "text/plain", status: int = 200):
    headers = Message()
    headers["content-type"] = content_type
    mock = MagicMock()
    mock.status = status
    mock.code = status
    mock.headers = headers
    mock.read.return_value = body
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    return mock


class WebToolTests(unittest.TestCase):
    def test_web_fetch_extracts_readable_html(self) -> None:
        module = load_tool_module("web", "runtime")
        self.assertIsNotNone(module)
        html = b"<html><head><title>T</title><script>x()</script></head><body><main><h1>Bonjour BB9</h1><p>Texte utile.</p></main></body></html>"

        with patch.object(module, "urlopen", return_value=_response(html, content_type="text/html; charset=utf-8")):
            observation = module.execute(module.action_from_text("fetch url=https://example.org"))

        self.assertTrue(observation.ok)
        self.assertIn("Bonjour BB9", observation.data["text"])
        self.assertNotIn("x()", observation.data["text"])

    def test_web_fetch_blocks_private_urls(self) -> None:
        module = load_tool_module("web", "runtime")
        self.assertIsNotNone(module)
        context = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()))

        decision = module.review(module.action_from_text("fetch url=http://127.0.0.1:3000"), context)

        self.assertEqual("block", decision.verdict)
        self.assertIn("private", decision.reason)

    def test_web_fetch_reports_network_error(self) -> None:
        module = load_tool_module("web", "runtime")
        self.assertIsNotNone(module)

        with patch.object(module, "urlopen", side_effect=URLError("down")):
            observation = module.execute(module.action_from_text("fetch url=https://example.org"))

        self.assertFalse(observation.ok)
        self.assertIn("failed", observation.summary)

    def test_web_search_uses_searxng_json(self) -> None:
        module = load_tool_module("web", "runtime")
        self.assertIsNotNone(module)
        payload = {"results": [{"title": "BB9", "url": "https://example.org/bb9", "content": "ok"}]}

        with patch.object(module, "urlopen", return_value=_response(json.dumps(payload).encode(), content_type="application/json")):
            observation = module.execute(module.action_from_text('search query="bb9 minimal" limit=3'))

        self.assertTrue(observation.ok)
        self.assertIn("BB9", observation.summary)
        self.assertEqual("https://example.org/bb9", observation.data["results"][0]["url"])


class BrowserToolTests(unittest.TestCase):
    def test_browser_open_rejects_file_url(self) -> None:
        module = load_tool_module("browser", "runtime")
        self.assertIsNotNone(module)
        context = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()), permission_profile="limited")

        decision = module.review(module.action_from_text("open url=file:///etc/passwd"), context)

        self.assertEqual("block", decision.verdict)

    def test_browser_reports_missing_playwright(self) -> None:
        module = load_tool_module("browser", "runtime")
        self.assertIsNotNone(module)
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "playwright.sync_api":
                raise ModuleNotFoundError(name)
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", fake_import):
            observation = module.execute(module.action_from_text("check url=https://example.org text=Hello"))

        self.assertFalse(observation.ok)
        self.assertIn("Playwright missing", observation.summary)

    def test_browser_screenshot_path_stays_under_workspace_artifacts(self) -> None:
        module = load_tool_module("browser", "runtime")
        self.assertIsNotNone(module)
        with tempfile.TemporaryDirectory() as tmp:
            session = module.BrowserSession(Path(tmp))
            path = session._screenshot_path("../outside.png")

            self.assertEqual(Path(tmp) / ".bb9" / "artifacts" / "screenshots" / "outside.png", path)


class UiWebToolTests(unittest.TestCase):
    def test_ui_web_upload_accepts_image_and_returns_reference(self) -> None:
        module = load_tool_module("ui_web", "runtime")
        self.assertIsNotNone(module)
        with tempfile.TemporaryDirectory() as tmp:
            uploads = Path(tmp) / ".bb9" / "uploads" / "web"
            server = module._server(0, uploads)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                payload = json.dumps({
                    "mime": "image/png",
                    "data": base64.b64encode(b"png").decode("ascii"),
                }).encode("utf-8")
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/upload",
                    data=payload,
                    headers={"content-type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=5) as response:
                    data = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

            self.assertTrue(data["ok"])
            self.assertTrue(Path(data["path"]).is_file())
            self.assertIn("[image:", data["reference"])

    def test_ui_web_upload_rejects_non_image(self) -> None:
        module = load_tool_module("ui_web", "runtime")
        self.assertIsNotNone(module)
        with tempfile.TemporaryDirectory() as tmp:
            server = module._server(0, Path(tmp) / "uploads")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                payload = json.dumps({"mime": "text/plain", "data": ""}).encode("utf-8")
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/upload",
                    data=payload,
                    headers={"content-type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as caught:
                    urlopen(request, timeout=5)
            finally:
                server.shutdown()
                server.server_close()

            self.assertEqual(415, caught.exception.code)


if __name__ == "__main__":
    unittest.main()
