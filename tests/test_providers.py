from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from bb9.core.providers import OpenAICompatibleProvider


class ProviderTests(unittest.TestCase):
    def test_openai_compatible_provider_sends_reasoning_effort_when_set(self) -> None:
        payloads: list[dict[str, object]] = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self) -> bytes:
                return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")

        def fake_urlopen(request, timeout=0):
            payloads.append(json.loads(request.data.decode("utf-8")))
            return Response()

        with patch.dict("os.environ", {"OPENAI_API_KEY": "secret"}):
            with patch("bb9.core.providers.urlopen", fake_urlopen):
                result = OpenAICompatibleProvider(
                    model="gpt-5.5",
                    reasoning_effort="high",
                ).complete("bonjour")

        self.assertEqual("ok", result)
        self.assertEqual("high", payloads[0]["reasoning_effort"])


if __name__ == "__main__":
    unittest.main()
