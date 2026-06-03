"""Runtime provider construction."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Protocol

from ..core.model_metadata import ModelMetadata, resolve_model_metadata
from ..core.models import AgentProfile
from .config import ProviderEntry, ProviderStore
from .providers import OpenAICompatibleProvider, Provider, ProviderError, provider_from_entry


class ProviderRuntimeState(Protocol):
    provider_kind: str
    model: str
    base_url: str
    api_key_env: str
    api_key_ref: str
    provider_config_path: Path
    active_provider: ProviderEntry | None


def build_provider_for_agent(state: ProviderRuntimeState, agent: AgentProfile) -> Provider | None:
    model_override = agent.model.strip()
    reasoning_effort = agent.reasoning_effort.strip() or str(getattr(state, "reasoning_effort", "") or "").strip()
    if state.active_provider is not None:
        entry = state.active_provider
        metadata = dict(entry.metadata)
        if reasoning_effort:
            metadata["reasoning_effort"] = reasoning_effort
        if model_override or reasoning_effort:
            entry = replace(
                entry,
                model=model_override or entry.model,
                metadata=metadata,
            )
        return provider_from_entry(entry)

    if state.provider_kind == "echo":
        return None
    if state.provider_kind == "openai-compatible":
        model = model_override or state.model
        if not model:
            raise ProviderError("model is required for openai-compatible provider")
        return OpenAICompatibleProvider(
            model=model,
            base_url=state.base_url,
            api_key_env=state.api_key_env,
            api_key_ref=state.api_key_ref,
            reasoning_effort=reasoning_effort,
        )
    raise ProviderError(f"unknown provider: {state.provider_kind}")


def load_saved_provider(state: ProviderRuntimeState) -> ProviderEntry | None:
    entry = ProviderStore(state.provider_config_path).load().active_entry()
    if entry is not None:
        set_active_provider(state, entry)
    return entry


def set_active_provider(state: ProviderRuntimeState, entry: ProviderEntry) -> None:
    state.active_provider = entry
    state.provider_kind = entry.provider
    state.model = entry.model
    state.base_url = entry.base_url
    state.api_key_ref = entry.api_key_ref


def active_model_name(state: ProviderRuntimeState, agent: AgentProfile | None = None) -> str:
    if agent is not None and agent.model.strip():
        return agent.model.strip()
    if state.active_provider is not None and state.active_provider.model.strip():
        return state.active_provider.model.strip()
    return state.model.strip()


def active_model_metadata(state: ProviderRuntimeState, agent: AgentProfile | None = None) -> ModelMetadata:
    return resolve_model_metadata(active_model_name(state, agent))
