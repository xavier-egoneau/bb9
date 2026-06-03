from __future__ import annotations

import io
import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from bb9.core.tool_runtime import load_tool_module


def _response(body: dict):
    payload = json.dumps(body).encode("utf-8")
    mock = unittest.mock.MagicMock()
    mock.read.return_value = payload
    mock.__enter__ = lambda s: s
    mock.__exit__ = unittest.mock.MagicMock(return_value=False)
    return mock


class VisionToolTests(unittest.TestCase):
    def test_vision_sends_think_disabled_payload(self) -> None:
        module = load_tool_module("vision", "runtime")
        self.assertIsNotNone(module)
        captured = {}

        def fake_urlopen(request, timeout):
            captured["timeout"] = timeout
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return _response({"message": {"content": "Un carre colore."}, "done_reason": "stop"})

        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "image.png"
            image.write_bytes(b"png")
            with patch.object(
                module,
                "_vision_config",
                return_value={"model": "gemma4:latest", "url": "http://ollama.test", "timeout": 12, "num_predict": 80},
            ), patch.object(module, "urlopen", fake_urlopen):
                observation = module.execute(module.action_from_text(f"describe path={image}"))

        self.assertTrue(observation.ok)
        self.assertEqual("Un carre colore.", observation.summary)
        self.assertEqual(12, captured["timeout"])
        self.assertEqual("gemma4:latest", captured["payload"]["model"])
        self.assertFalse(captured["payload"]["think"])
        self.assertFalse(captured["payload"]["stream"])
        self.assertEqual(80, captured["payload"]["options"]["num_predict"])
        self.assertEqual(0, captured["payload"]["options"]["temperature"])

    def test_vision_reports_http_error_body(self) -> None:
        module = load_tool_module("vision", "runtime")
        self.assertIsNotNone(module)
        headers = Message()
        fp = io.BytesIO(b'{"error":"failed to process inputs"}')
        error = HTTPError("http://ollama.test/api/chat", 500, "Internal Server Error", headers, fp)

        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "image.png"
            image.write_bytes(b"png")
            with patch.object(
                module,
                "_vision_config",
                return_value={"model": "gemma4:latest", "url": "http://ollama.test", "timeout": 12, "num_predict": 80},
            ), patch.object(module, "urlopen", side_effect=error):
                observation = module.execute(module.action_from_text(f"describe path={image}"))

        self.assertFalse(observation.ok)
        self.assertIn("HTTP 500", observation.summary)
        self.assertIn("failed to process inputs", observation.summary)
        self.assertEqual(500, observation.data["returncode"])


if __name__ == "__main__":
    unittest.main()
