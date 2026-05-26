"""Minimal synchronous subagent delegation contract."""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Iterable

from .channels import intention_from_text
from .models import AgentProfile, Intention, PermissionProfile, RunContext, RunResult, Session, Task, TaskResult


DelegationRunner = Callable[[Intention, RunContext], RunResult]

PROFILE_ORDER: dict[PermissionProfile, int] = {
    "safe": 0,
    "limited": 1,
    "power": 2,
}


def delegate(
    task: Task,
    subagent: AgentProfile,
    parent_context: RunContext,
    runner: DelegationRunner,
) -> TaskResult:
    blockers = validate_task(task)
    if blockers:
        return TaskResult(
            task_id=task.id,
            status="error",
            summary="Task is not delegable.",
            blockers=blockers,
            next_suggestion="Complete the task contract before delegating.",
        )

    context = build_delegation_context(parent_context, subagent, task)
    try:
        result = runner(intention_from_text(task_prompt(task)), context)
    except Exception as exc:
        return TaskResult(
            task_id=task.id,
            status="error",
            summary=f"Delegation failed: {exc}",
            blockers=(exc.__class__.__name__,),
        )
    return task_result_from_run(task, result)


def build_delegation_context(parent_context: RunContext, subagent: AgentProfile, task: Task) -> RunContext:
    return replace(
        parent_context,
        session=Session(source=f"delegation:{task.id}"),
        permission_profile=effective_permission(parent_context.permission_profile, task.permission_profile),
        agent=subagent,
        subagents_index="",
    )


def task_prompt(task: Task) -> str:
    parts = [
        "# Delegated Task",
        f"TaskId: {task.id}",
        f"Title: {task.title}",
        "",
        "## Goal",
        task.goal.strip(),
        "",
        "## Context",
        task.context.strip(),
        "",
        "## Expected Output",
        task.expected_output.strip(),
    ]
    if task.inputs:
        parts.extend(["", "## Inputs", *_bullets(task.inputs)])
    if task.paths:
        parts.extend(["", "## Paths", *_bullets(task.paths)])
    if task.done_criteria:
        parts.extend(["", "## Done Criteria", *_bullets(task.done_criteria)])
    if task.dependencies:
        parts.extend(["", "## Dependencies Already Satisfied", *_bullets(task.dependencies)])
    parts.extend(
        [
            "",
            "## Return Contract",
            "Return a concise result for the parent agent.",
            "Mention status done or error, evidence, blockers and next suggestion when useful.",
            "Do not address the user directly.",
        ]
    )
    return "\n".join(parts).strip()


def task_result_from_run(task: Task, result: RunResult) -> TaskResult:
    observation = result.observation
    if observation is None:
        return TaskResult(
            task_id=task.id,
            status="error",
            summary=result.decision.summary,
            blockers=("missing observation",),
            evidence=trace_evidence(result),
        )
    data = observation.data
    return TaskResult(
        task_id=task.id,
        status="done" if observation.ok else "error",
        summary=observation.summary,
        changed=_tuple_data(data.get("changed")),
        observed=_tuple_data(data.get("observed")),
        blockers=_tuple_data(data.get("blockers")),
        evidence=_tuple_data(data.get("evidence")) or trace_evidence(result),
        next_suggestion=str(data.get("next_suggestion") or ""),
    )


def validate_task(task: Task) -> tuple[str, ...]:
    blockers: list[str] = []
    if not task.id.strip():
        blockers.append("missing task id")
    if not task.goal.strip():
        blockers.append("missing goal")
    if not task.context.strip():
        blockers.append("missing context")
    if not task.expected_output.strip():
        blockers.append("missing expected output")
    return tuple(blockers)


def effective_permission(parent: PermissionProfile, requested: PermissionProfile | None) -> PermissionProfile:
    if requested is None:
        return parent
    return requested if PROFILE_ORDER[requested] <= PROFILE_ORDER[parent] else parent


def trace_evidence(result: RunResult, *, limit: int = 5) -> tuple[str, ...]:
    evidence = [event.summary for event in result.trace if event.summary.strip()]
    return tuple(evidence[-limit:])


def _tuple_data(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, Iterable):
        return tuple(str(item) for item in value if str(item).strip())
    return (str(value),)


def _bullets(values: tuple[str, ...]) -> list[str]:
    return [f"- {value}" for value in values if value.strip()]
