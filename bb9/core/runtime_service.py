"""Shared runtime service used by channels."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from bb9.providers.config import ProviderEntry
from bb9.providers.providers import Provider
from bb9.providers.runtime import (
    active_model_metadata,
    active_model_name,
    build_provider_for_agent,
    effective_provider_entry,
)

from . import context_runtime
from .channels import intention_from_text
from .compaction import estimate_session_tokens
from .diffs import WorktreeSnapshot, capture_worktree_snapshot, diff_artifact_since
from .kernel import Kernel
from .loop import ApprovalCallback, CancelCallback, run_once
from .models import Artifact, PermissionProfile, RunContext, RunResult, Session, TraceEvent
from .sessions import AGENT_HOME_SOURCE, SessionStore
from .trace import decision_trace_artifact, tool_trace_artifact


class RuntimeServiceState(Protocol):
    profile: PermissionProfile
    session: Session
    provider_kind: str
    model: str
    reasoning_effort: str
    base_url: str
    api_key_env: str
    api_key_ref: str
    provider_config_path: Path
    active_provider: ProviderEntry | None
    agent_name: str
    subagent_name: str
    agents_dir: Path
    skills_dir: Path
    tools_dir: Path


@dataclass(frozen=True)
class RuntimeStatus:
    session_id: str
    source: str
    workspace: str
    profile: PermissionProfile
    provider: str
    model: str
    reasoning_effort: str
    agent: str
    subagent: str
    workspace_status: str
    context_window_tokens: int = 0
    context_window_source: str = "fallback"
    estimated_tokens: int = 0


@dataclass(frozen=True)
class RuntimeTurn:
    context: RunContext
    result: RunResult
    answer: str
    base_artifacts: tuple[Artifact, ...]
    snapshot: WorktreeSnapshot
    timings: dict[str, int]


class _Unset:
    pass


_UNSET = _Unset()


def build_context(state: RuntimeServiceState) -> RunContext:
    return context_runtime.build_context(state)


def build_status(state: RuntimeServiceState, *, light: bool = True) -> RuntimeStatus:
    context = context_runtime.build_context(state, light=light)
    agent = context.agent
    provider = effective_provider_entry(state, agent)
    provider_label = provider.name if provider is not None else state.provider_kind
    model = active_model_name(state, agent)
    reasoning_effort = str(agent.reasoning_effort if agent is not None else "").strip()
    if not reasoning_effort:
        reasoning_effort = str(getattr(state, "reasoning_effort", "") or "").strip()
    if provider is not None and not reasoning_effort:
        reasoning_effort = str(provider.metadata.get("reasoning_effort") or "").strip()
    try:
        metadata = active_model_metadata(state, context.agent)
        context_window_tokens = metadata.context_window_tokens
        context_window_source = metadata.source
    except Exception:
        context_window_tokens = 0
        context_window_source = "fallback"
    return RuntimeStatus(
        session_id=state.session.id,
        source=state.session.source,
        workspace=str(context.workspace.root),
        profile=state.profile,
        provider=provider_label or state.provider_kind,
        model=model or "",
        reasoning_effort=reasoning_effort,
        agent=state.agent_name,
        subagent=state.subagent_name,
        workspace_status=context.workspace_status,
        context_window_tokens=context_window_tokens,
        context_window_source=context_window_source,
        estimated_tokens=estimate_session_tokens(state.session),
    )


def run_message(
    state: RuntimeServiceState,
    text: str,
    *,
    ask_user: ApprovalCallback | None = None,
    on_event: Callable[[TraceEvent], None] | None = None,
    should_cancel: CancelCallback | None = None,
    provider: Provider | None | _Unset = _UNSET,
) -> RuntimeTurn:
    timings: dict[str, int] = {}
    _refresh_agent_home_session(state)
    light_context = _is_simple_chat(text)
    started = time.perf_counter()
    context = context_runtime.build_context(state, light=light_context)
    timings["context_ms"] = _elapsed_ms(started)
    timings["light_context"] = int(light_context)

    started = time.perf_counter()
    agent = context.agent or context_runtime.load_current_agent(state)
    active_provider = build_provider_for_agent(state, agent) if isinstance(provider, _Unset) else provider
    context_window_tokens = 0
    try:
        context_window_tokens = active_model_metadata(state, agent).context_window_tokens
    except Exception:
        context_window_tokens = 0
    context = replace(
        context,
        provider_for_agent=lambda worker: build_provider_for_agent(state, worker),
        context_window_tokens=context_window_tokens,
    )
    timings["provider_build_ms"] = _elapsed_ms(started)

    started = time.perf_counter()
    snapshot = capture_worktree_snapshot(context.workspace.root) if _should_capture_worktree_snapshot(text) else WorktreeSnapshot(root=None, dirty_hashes={}, dirty_statuses={})
    timings["snapshot_ms"] = _elapsed_ms(started)

    started = time.perf_counter()
    result = run_once(
        Kernel(provider=active_provider),
        intention_from_text(text),
        context,
        ask_user=ask_user,
        on_event=on_event,
        should_cancel=should_cancel,
    )
    timings["loop_ms"] = _elapsed_ms(started)
    answer = result.observation.summary if result.observation is not None else result.decision.summary
    base_artifacts = result.observation.artifacts if result.observation is not None else ()
    return RuntimeTurn(
        context=context,
        result=result,
        answer=answer,
        base_artifacts=base_artifacts,
        snapshot=snapshot,
        timings=timings,
    )


def _refresh_agent_home_session(state: RuntimeServiceState) -> None:
    """Reload a shared agent-home session from the store before the turn.

    The agent-home session is the canonical conversation of an agent, mirrored
    across surfaces (web "accueil", Telegram). Each surface keeps an in-memory
    copy; without this reload, a surface would run on a stale copy and its next
    persist would erase the turns added by the other surface.
    """
    session = state.session
    if session.source != AGENT_HOME_SOURCE:
        return
    store_path = getattr(state, "session_store_path", None)
    if store_path is None:
        return
    try:
        store = SessionStore(store_path)
    except Exception:
        return
    try:
        stored = store.get(session.id)
    except Exception:
        return
    finally:
        store.close()
    if stored is not None:
        state.session = stored.as_session()


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _is_simple_chat(text: str) -> bool:
    value = " ".join(text.strip().split())
    if not value or len(value) > 180 or "\n" in text or value.startswith("/") or "[image:" in value:
        return False
    markers = (
        "crée",
        "cree",
        "corrige",
        "modifie",
        "écris",
        "ecris",
        "ajoute",
        "supprime",
        "fichier",
        "code",
        "screenshot",
        "vision",
        "check",
        "regarde",
        "analyse",
        "cherche",
        "lis ",
        "skill",
        "critique",
        "optimise",
        "optimiser",
        "lent",
        "parcours",
        "run",
        "vois",
        "build",
        "test",
    )
    lower = value.lower()
    return not any(marker in lower for marker in markers)


def _should_capture_worktree_snapshot(text: str) -> bool:
    value = text.strip().lower()
    if not value:
        return False
    if value.startswith(('/build', '/action files', '/action shell')):
        return True
    if value.startswith("/") and not value.startswith(
        ("/help", "/context", "/history", "/new", "/compact", "/model", "/profil", "/profile", "/exit", "/quit")
    ):
        return True
    markers = (
        "crée",
        "cree",
        "corrige",
        "modifie",
        "écris",
        "ecris",
        "ajoute",
        "supprime",
        "implémente",
        "implemente",
        "refactor",
        "fix",
    )
    return any(marker in value for marker in markers)


def turn_artifacts(
    turn: RuntimeTurn,
    *,
    include_tool_trace: bool = True,
    include_decision_trace: bool = False,
    include_diff: bool = True,
) -> tuple[Artifact, ...]:
    return artifacts_from_parts(
        turn.base_artifacts,
        turn.result.trace,
        turn.snapshot,
        include_tool_trace=include_tool_trace,
        include_decision_trace=include_decision_trace,
        include_diff=include_diff,
    )


def artifacts_from_parts(
    artifacts: tuple[Artifact, ...],
    trace_events: tuple[TraceEvent, ...],
    snapshot: WorktreeSnapshot,
    *,
    include_tool_trace: bool = True,
    include_decision_trace: bool = False,
    include_diff: bool = True,
) -> tuple[Artifact, ...]:
    if include_tool_trace:
        tool_trace = tool_trace_artifact(trace_events)
        if tool_trace is not None:
            artifacts = (*artifacts, tool_trace)
    if include_decision_trace:
        decision_trace = decision_trace_artifact(trace_events)
        if decision_trace is not None:
            artifacts = (*artifacts, decision_trace)
    if include_diff:
        diff = diff_artifact_since(snapshot)
        if diff is not None:
            artifacts = (*artifacts, diff)
    return artifacts
