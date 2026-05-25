from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bb9.core.models import AgentProfile
from bb9.core.provider_config import AUTH_API, ProviderEntry, ProviderStore
from bb9.core.provider_runtime import (
    active_model_name,
    build_provider_for_agent,
    load_saved_provider,
    set_active_provider,
)
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

    def test_provider_runtime_builds_legacy_openai_compatible_provider(self) -> None:
        state = SimpleNamespace(
            provider_kind="openai-compatible",
            model="base-model",
            base_url="https://example.test/v1",
            api_key_env="EXAMPLE_API_KEY",
            api_key_ref="",
            provider_config_path=Path("providers.json"),
            active_provider=None,
        )

        provider = build_provider_for_agent(
            state,
            AgentProfile(name="default", model="agent-model", reasoning_effort="medium"),
        )

        self.assertIsInstance(provider, OpenAICompatibleProvider)
        self.assertEqual("agent-model", provider.model)
        self.assertEqual("medium", provider.reasoning_effort)
        self.assertEqual("https://example.test/v1", provider.base_url)

    def test_provider_runtime_applies_agent_override_to_active_provider(self) -> None:
        entry = ProviderEntry(
            id="openai",
            name="OpenAI",
            provider="openai",
            auth_type=AUTH_API,
            base_url="https://api.openai.com",
            api_key_ref="env:OPENAI_API_KEY",
            model="base-model",
        )
        state = SimpleNamespace(
            provider_kind="echo",
            model="",
            base_url="",
            api_key_env="OPENAI_API_KEY",
            api_key_ref="",
            provider_config_path=Path("providers.json"),
            active_provider=entry,
        )

        provider = build_provider_for_agent(
            state,
            AgentProfile(name="default", model="agent-model", reasoning_effort="high"),
        )

        self.assertIsInstance(provider, OpenAICompatibleProvider)
        self.assertEqual("agent-model", provider.model)
        self.assertEqual("high", provider.reasoning_effort)

    def test_provider_runtime_loads_and_exposes_active_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "providers.json"
            entry = ProviderEntry(
                id="openai",
                name="OpenAI",
                provider="openai",
                auth_type=AUTH_API,
                base_url="https://api.openai.com/v1",
                api_key_ref="env:OPENAI_API_KEY",
                model="gpt-5",
            )
            ProviderStore(path).upsert(entry, active=True)
            state = SimpleNamespace(
                provider_kind="echo",
                model="",
                base_url="",
                api_key_env="OPENAI_API_KEY",
                api_key_ref="",
                provider_config_path=path,
                active_provider=None,
            )

            loaded = load_saved_provider(state)

        self.assertEqual(entry, loaded)
        self.assertEqual("openai", state.provider_kind)
        self.assertEqual("gpt-5", active_model_name(state))

    def test_provider_runtime_set_active_provider_updates_state(self) -> None:
        state = SimpleNamespace(
            provider_kind="echo",
            model="",
            base_url="",
            api_key_env="OPENAI_API_KEY",
            api_key_ref="",
            provider_config_path=Path("providers.json"),
            active_provider=None,
        )
        entry = ProviderEntry(
            id="openrouter",
            name="OpenRouter",
            provider="openrouter",
            auth_type=AUTH_API,
            base_url="https://openrouter.ai/api/v1",
            api_key_ref="env:OPENROUTER_API_KEY",
            model="openai/gpt-5",
        )

        set_active_provider(state, entry)

        self.assertEqual(entry, state.active_provider)
        self.assertEqual("openrouter", state.provider_kind)
        self.assertEqual("openai/gpt-5", state.model)


if __name__ == "__main__":
    unittest.main()
