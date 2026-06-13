"""Bounded subagent delegation tool."""

from __future__ import annotations

import shlex
from typing import Any

from bb9.core.agents import AgentNotFoundError, load_subagent, spawn_ephemeral_worker
from bb9.core.delegation import delegate
from bb9.core.kernel import Kernel
from bb9.core.loop import run_once
from bb9.core.models import Action, GuardianDecision, Observation, PermissionProfile, RunContext, Task
from bb9.core.paths import default_content_dir

PROFILES = {"safe", "limited", "power"}


def action_from_text(text: str) -> Action:
    raw = text.strip()
    try:
        argv = shlex.split(raw)
    except ValueError as exc:
        return Action(name="delegate", params={"parse_error": str(exc)}, risk="forbidden")
    op = argv[0].lower() if argv else "run"
    params = _parse_params(argv[1:])
    params["op"] = op
    if op != "run":
        return Action(name="delegate", params=params, risk="forbidden")
    return Action(name="delegate", params=params, risk="low")


def review(action: Action, context: RunContext) -> GuardianDecision:
    if str(action.params.get("op") or "").strip().lower() != "run":
        return GuardianDecision(verdict="block", reason="invalid delegate action", action=action)
    task = _task_from_action(action)
    missing = []
    if not task.id.strip():
        missing.append("id")
    if not task.goal.strip():
        missing.append("goal")
    if not task.context.strip():
        missing.append("context")
    if not task.expected_output.strip():
        missing.append("expected")
    if missing:
        return GuardianDecision(verdict="block", reason="delegate missing: " + ", ".join(missing), action=action)
    return GuardianDecision(verdict="allow", reason="bounded subagent delegation", action=action)


def execute(action: Action, context: RunContext) -> Observation:
    task = _task_from_action(action)
    worker = str(action.params.get("worker") or task.suggested_worker or "dev").strip() or "dev"
    try:
        subagent = _load_worker(context, worker)
    except AgentNotFoundError:
        subagent = _load_worker_or_ephemeral(context, worker)

    result = delegate(
        task,
        subagent,
        context,
        lambda intention, delegated_context: run_once(
            Kernel(provider=context.provider_for_agent(delegated_context.agent) if context.provider_for_agent and delegated_context.agent else None),
            intention,
            delegated_context,
        ),
    )
    ok = result.status == "done"
    return Observation(
        ok=ok,
        summary=result.summary,
        data={
            "task_id": result.task_id,
            "status": result.status,
            "changed": result.changed,
            "observed": result.observed,
            "blockers": result.blockers,
            "evidence": result.evidence,
            "next_suggestion": result.next_suggestion,
        },
    )


def _task_from_action(action: Action) -> Task:
    params = action.params
    profile = _profile(str(params.get("profile") or ""))
    return Task(
        id=str(params.get("id") or "delegated").strip(),
        title=str(params.get("title") or params.get("goal") or "Delegated task").strip(),
        goal=str(params.get("goal") or "").strip(),
        context=str(params.get("context") or "").strip(),
        inputs=_items(params.get("inputs")),
        paths=_items(params.get("paths")),
        expected_output=str(params.get("expected") or params.get("expected_output") or "").strip(),
        done_criteria=_items(params.get("done") or params.get("done_criteria")),
        dependencies=_items(params.get("dependencies")),
        parallelizable=str(params.get("parallelizable") or "").lower() in {"1", "true", "yes", "oui"},
        suggested_worker=str(params.get("worker") or "dev").strip() or "dev",
        permission_profile=profile,
        tool_scope=str(params.get("tool_scope") or params.get("scope") or "dev").strip() or "dev",
        max_iterations=_positive_int(params.get("max_iterations"), default=1),
    )


def _load_worker(context: RunContext, worker: str):
    agents_dir = context.agents_dir or default_content_dir("agents")
    parent = (context.agent.name if context.agent is not None else "default").split("/", 1)[0]
    if "/" in worker:
        parent, _, subagent = worker.partition("/")
        return load_subagent(agents_dir, parent, subagent)
    return load_subagent(agents_dir, parent, worker)


def _load_worker_or_ephemeral(context: RunContext, worker: str):
    """Fallback : tente 'dev', sinon retourne un worker éphémère basé sur l'identity dev."""
    agents_dir = context.agents_dir or default_content_dir("agents")
    parent = (context.agent.name if context.agent is not None else "default").split("/", 1)[0]
    if worker != "dev":
        try:
            return load_subagent(agents_dir, parent, "dev")
        except AgentNotFoundError:
            pass
    return spawn_ephemeral_worker(agents_dir, parent)


def _parse_params(parts: list[str]) -> dict[str, str]:
    params: dict[str, str] = {}
    for part in parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        params[key.strip().lower().replace("-", "_")] = value
    return params


def _items(value: object) -> tuple[str, ...]:
    return tuple(item.strip() for item in str(value or "").split(",") if item.strip())


def _profile(value: str) -> PermissionProfile | None:
    profile = value.strip().lower()
    return profile if profile in PROFILES else None  # type: ignore[return-value]


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
