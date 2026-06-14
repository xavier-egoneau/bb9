"""Runtime provider construction."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Protocol

from ..core.model_metadata import ModelMetadata, resolve_model_metadata
from ..core.models import AgentProfile
from .config import ModelFetchError, ProviderEntry, ProviderStore, fetch_models
from .providers import OpenAICompatibleProvider, Provider, ProviderError, provider_from_entry


class ProviderRuntimeState(Protocol):
    provider_kind: str
    model: str
    base_url: str
    api_key_env: str
    api_key_ref: str
    provider_config_path: Path
    active_provider: ProviderEntry | None


def build_provider_for_agent(state: ProviderRuntimeState, agent: AgentProfile | None) -> Provider | None:
    if agent is None:
        return None
    model_override = agent.model.strip()
    reasoning_effort = agent.reasoning_effort.strip() or str(getattr(state, "reasoning_effort", "") or "").strip()
    entry = effective_provider_entry(state, agent)
    if entry is not None:
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
    provider = effective_provider_entry(state, agent)
    if provider is not None and provider.model.strip():
        return provider.model.strip()
    return state.model.strip()


def active_model_metadata(state: ProviderRuntimeState, agent: AgentProfile | None = None) -> ModelMetadata:
    return resolve_model_metadata(active_model_name(state, agent))


def active_provider_is_local_ollama(state: ProviderRuntimeState, agent: AgentProfile | None = None) -> bool:
    provider = effective_provider_entry(state, agent)
    return provider is not None and provider.provider == "ollama"


def effective_provider_entry(state: ProviderRuntimeState, agent: AgentProfile | None = None) -> ProviderEntry | None:
    entries = _configured_entries(state)
    if agent is not None:
        provider_id = agent.provider_id.strip()
        if provider_id:
            match = _find_provider_entry(entries, provider_id)
            if match is not None:
                return match
        model = agent.model.strip()
        if model:
            inferred = _infer_provider_for_model(entries, model)
            if inferred is not None:
                return inferred
    if state.active_provider is not None:
        return state.active_provider
    return None


def _configured_entries(state: ProviderRuntimeState) -> tuple[ProviderEntry, ...]:
    config = ProviderStore(state.provider_config_path).load()
    entries = list(config.entries)
    if state.active_provider is not None and not any(entry.id == state.active_provider.id for entry in entries):
        entries.insert(0, state.active_provider)
    return tuple(entries)


def _find_provider_entry(entries: tuple[ProviderEntry, ...], provider_id: str) -> ProviderEntry | None:
    wanted = provider_id.strip()
    if not wanted:
        return None
    for entry in entries:
        if wanted in {entry.id, entry.name, entry.provider}:
            return entry
    return None


def _infer_provider_for_model(entries: tuple[ProviderEntry, ...], model: str) -> ProviderEntry | None:
    direct = [entry for entry in entries if entry.model.strip() == model]
    if direct:
        return direct[0]
    for entry in entries:
        if not _cheap_model_lookup_allowed(entry):
            continue
        try:
            if model in fetch_models(entry, timeout=0.75, autostart=False):
                return entry
        except (ModelFetchError, OSError, TimeoutError):
            continue
    return None


def _cheap_model_lookup_allowed(entry: ProviderEntry) -> bool:
    base = entry.base_url.lower()
    return entry.provider == "ollama" or "127.0.0.1" in base or "localhost" in base
