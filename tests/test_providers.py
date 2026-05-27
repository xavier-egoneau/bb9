from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bb9.core.models import AgentProfile
from bb9.core.provider_config import AUTH_API, ProviderEntry, ProviderStore, fetch_models, normalize_api_key_ref_input, normalize_base_url
from bb9.core.provider_runtime import (
    active_model_name,
    build_provider_for_agent,
    load_saved_provider,
    set_active_provider,
)
from bb9.core.providers import OllamaProvider, OpenAICompatibleProvider, provider_from_entry


class ProviderTests(unittest.TestCase):
    def test_provider_secret_ref_input_keeps_env_names(self) -> None:
        ref, notice = normalize_api_key_ref_input("OPENAI_API_KEY", default_ref="env:OPENAI_API_KEY")

        self.assertEqual("env:OPENAI_API_KEY", ref)
        self.assertEqual("", notice)

    def test_provider_secret_ref_input_stores_raw_keys(self) -> None:
        class FakeStore:
            stored: tuple[str, str] | None = None

            def set(self, name: str, value: str) -> str:
                self.stored = (name, value)
                return name

        store = FakeStore()

        ref, notice = normalize_api_key_ref_input(
            "96de21f0ce264e88bc4fc8d8c2b068e6.Xh9c7EPPFQe-CdhwhpIrweW5",
            secret_name="OPENAI_API_KEY",
            store=store,
        )

        self.assertEqual("secret:OPENAI_API_KEY", ref)
        self.assertIn("Secret stocke localement", notice)
        self.assertEqual(
            ("OPENAI_API_KEY", "96de21f0ce264e88bc4fc8d8c2b068e6.Xh9c7EPPFQe-CdhwhpIrweW5"),
            store.stored,
        )

    def test_fetch_models_lists_openai_compatible_models(self) -> None:
        requests = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self) -> bytes:
                return json.dumps({"data": [{"id": "model-b"}, {"id": "model-a"}]}).encode("utf-8")

        def fake_urlopen(request, timeout=0):
            requests.append(request)
            return Response()

        entry = ProviderEntry(
            id="manual",
            name="Manual",
            provider="openai-compatible",
            auth_type=AUTH_API,
            base_url="https://example.test/v1",
            api_key_ref="env:EXAMPLE_API_KEY",
        )

        with patch.dict("os.environ", {"EXAMPLE_API_KEY": "secret"}):
            with patch("bb9.core.provider_config.urlopen", fake_urlopen):
                models = fetch_models(entry)

        self.assertEqual(["model-a", "model-b"], models)
        self.assertEqual("Bearer secret", requests[0].headers["Authorization"])

    def test_ollama_base_url_is_normalized_to_local_openai_endpoint(self) -> None:
        self.assertEqual("http://localhost:11434/v1", normalize_base_url("ollama", "http://localhost:11434"))
        self.assertEqual("https://ollama.com", normalize_base_url("ollama-cloud", "https://ollama.com/"))
        self.assertEqual("https://ollama.com", normalize_base_url("openai-compatible", "https://ollama.com/"))

    def test_fetch_models_lists_ollama_models_without_api_key(self) -> None:
        requests = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self) -> bytes:
                return json.dumps({"data": [{"id": "llama3.2"}, {"id": "qwen2.5"}]}).encode("utf-8")

        def fake_urlopen(request, timeout=0):
            requests.append(request)
            return Response()

        entry = ProviderEntry(
            id="ollama",
            name="Ollama",
            provider="ollama",
            auth_type=AUTH_API,
            base_url="http://localhost:11434",
        )

        with patch("bb9.core.provider_config.urlopen", fake_urlopen):
            models = fetch_models(entry)

        self.assertEqual(["llama3.2", "qwen2.5"], models)
        self.assertEqual("http://localhost:11434/v1/models", requests[0].full_url)
        self.assertNotIn("Authorization", requests[0].headers)

    def test_fetch_models_lists_ollama_cloud_models_with_api_key(self) -> None:
        requests = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self) -> bytes:
                return json.dumps({"models": [{"name": "gpt-oss:120b"}, {"name": "gpt-oss:20b"}]}).encode("utf-8")

        def fake_urlopen(request, timeout=0):
            requests.append(request)
            return Response()

        entry = ProviderEntry(
            id="ollama-cloud",
            name="Ollama Cloud",
            provider="ollama-cloud",
            auth_type=AUTH_API,
            base_url="https://ollama.com",
            api_key_ref="env:OLLAMA_API_KEY",
        )

        with patch.dict("os.environ", {"OLLAMA_API_KEY": "secret"}):
            with patch("bb9.core.provider_config.urlopen", fake_urlopen):
                models = fetch_models(entry)

        self.assertEqual(["gpt-oss:120b", "gpt-oss:20b"], models)
        self.assertEqual("https://ollama.com/api/tags", requests[0].full_url)
        self.assertEqual("Bearer secret", requests[0].headers["Authorization"])

    def test_provider_runtime_builds_ollama_without_api_key(self) -> None:
        entry = ProviderEntry(
            id="ollama",
            name="Ollama",
            provider="ollama",
            auth_type=AUTH_API,
            base_url="http://localhost:11434",
            model="llama3.2",
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

        provider = build_provider_for_agent(state, AgentProfile(name="default"))

        self.assertIsInstance(provider, OpenAICompatibleProvider)
        self.assertEqual("http://localhost:11434/v1", provider.base_url)
        self.assertFalse(provider.require_api_key)

    def test_provider_runtime_builds_ollama_cloud_provider(self) -> None:
        entry = ProviderEntry(
            id="ollama-cloud",
            name="Ollama Cloud",
            provider="ollama-cloud",
            auth_type=AUTH_API,
            base_url="https://ollama.com",
            api_key_ref="env:OLLAMA_API_KEY",
            model="gpt-oss:120b",
        )

        provider = provider_from_entry(entry)

        self.assertIsInstance(provider, OllamaProvider)
        self.assertEqual("https://ollama.com", provider.base_url)

    def test_ollama_cloud_provider_calls_api_chat(self) -> None:
        requests = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self) -> bytes:
                return json.dumps({"message": {"content": "ok cloud"}}).encode("utf-8")

        def fake_urlopen(request, timeout=0):
            requests.append(request)
            return Response()

        provider = OllamaProvider(model="gpt-oss:120b", api_key_ref="env:OLLAMA_API_KEY")

        with patch.dict("os.environ", {"OLLAMA_API_KEY": "secret"}):
            with patch("bb9.core.providers.urlopen", fake_urlopen):
                result = provider.complete("bonjour")

        self.assertEqual("ok cloud", result)
        self.assertEqual("https://ollama.com/api/chat", requests[0].full_url)
        self.assertEqual("Bearer secret", requests[0].headers["Authorization"])

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
