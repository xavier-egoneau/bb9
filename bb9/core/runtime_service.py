"""Shared runtime service used by channels."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from . import context_runtime
from .channels import intention_from_text
from .diffs import WorktreeSnapshot, capture_worktree_snapshot, diff_artifact_since
from .kernel import Kernel
from .loop import ApprovalCallback, CancelCallback, run_once
from .models import Artifact, PermissionProfile, RunContext, RunResult, Session, TraceEvent
from .provider_config import ProviderEntry
from .provider_runtime import build_provider_for_agent
from .providers import Provider
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


@dataclass(frozen=True)
class RuntimeTurn:
    context: RunContext
    result: RunResult
    answer: str
    base_artifacts: tuple[Artifact, ...]
    snapshot: WorktreeSnapshot


class _Unset:
    pass


_UNSET = _Unset()


def build_context(state: RuntimeServiceState) -> RunContext:
    return context_runtime.build_context(state)


def build_status(state: RuntimeServiceState) -> RuntimeStatus:
    context = build_context(state)
    provider = state.active_provider
    provider_label = provider.name if provider is not None else state.provider_kind
    model = provider.model if provider is not None else state.model
    reasoning_effort = str(getattr(state, "reasoning_effort", "") or "").strip()
    if provider is not None and not reasoning_effort:
        reasoning_effort = str(provider.metadata.get("reasoning_effort") or "").strip()
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
    context = build_context(state)
    agent = context.agent or context_runtime.load_current_agent(state)
    active_provider = build_provider_for_agent(state, agent) if provider is _UNSET else provider
    snapshot = capture_worktree_snapshot(Path.cwd())
    result = run_once(
        Kernel(provider=active_provider),
        intention_from_text(text),
        context,
        ask_user=ask_user,
        on_event=on_event,
        should_cancel=should_cancel,
    )
    answer = result.observation.summary if result.observation is not None else result.decision.summary
    base_artifacts = result.observation.artifacts if result.observation is not None else ()
    return RuntimeTurn(
        context=context,
        result=result,
        answer=answer,
        base_artifacts=base_artifacts,
        snapshot=snapshot,
    )


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
