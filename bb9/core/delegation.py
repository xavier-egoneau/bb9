"""Minimal synchronous subagent delegation contract."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import replace

from .channels import intention_from_text
from .models import (
    AgentProfile,
    Intention,
    PermissionProfile,
    RunContext,
    RunResult,
    Session,
    Task,
    TaskResult,
    ToolSpec,
)
from .tools import build_tools_index
from .trust import TrustedRoots

DelegationRunner = Callable[[Intention, RunContext], RunResult]

PROFILE_ORDER: dict[PermissionProfile, int] = {
    "safe": 0,
    "limited": 1,
    "power": 2,
}
DEV_TOOL_NAMES = ("shell", "files", "browser", "web", "vision")
STATUS_RE = re.compile(r"^\s*(?:[-*]\s*)?status\s*:\s*(done|error)\b", re.IGNORECASE | re.MULTILINE)


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
    tools = scoped_tools(parent_context.tools, task.tool_scope)
    return replace(
        parent_context,
        session=Session(source=f"delegation:{task.id}"),
        permission_profile=effective_permission(parent_context.permission_profile, task.permission_profile),
        trusted_roots=TrustedRoots(),
        agent=subagent,
        tools=tools,
        tools_index=build_tools_index(tools) if tools else "",
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
    if task.tool_scope.strip():
        parts.extend(
            [
                "",
                "## Worker Rules",
                f"ToolScope: {task.tool_scope.strip()}",
                "Work only inside the active workspace and the paths given by the parent.",
                "Do not try to access files outside the active workspace.",
                "Use the available dev tools directly when useful; do not answer timidly that you could do it.",
            ]
        )
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
    summary = observation.summary
    status = task_status_from_observation(observation.ok, data, summary)
    blockers = _tuple_data(data.get("blockers"))
    if status == "error" and not blockers:
        blockers = implicit_blockers(summary)
    return TaskResult(
        task_id=task.id,
        status=status,
        summary=summary,
        changed=_tuple_data(data.get("changed")),
        observed=_tuple_data(data.get("observed")),
        blockers=blockers,
        evidence=_tuple_data(data.get("evidence")) or trace_evidence(result),
        next_suggestion=str(data.get("next_suggestion") or ""),
    )


def task_status_from_observation(ok: bool, data: dict, summary: str) -> str:
    data_status = str(data.get("status") or "").strip().lower()
    explicit_status = explicit_status_from_summary(summary)
    if not ok:
        return "error"
    if data_status == "error" or explicit_status == "error" or summary_has_error_marker(summary):
        return "error"
    return "done"


def explicit_status_from_summary(summary: str) -> str:
    match = STATUS_RE.search(summary)
    if not match:
        return ""
    return match.group(1).lower()


def summary_has_error_marker(summary: str) -> bool:
    text = summary.lower()
    markers = (
        "action not executed",
        "providererror",
        "delegation failed",
        "validation guardian non interactive",
        "tool step limit reached",
        "request timed out",
    )
    return any(marker in text for marker in markers)


def implicit_blockers(summary: str) -> tuple[str, ...]:
    text = summary.lower()
    if "providererror" in text:
        return ("ProviderError",)
    if "request timed out" in text:
        return ("Provider timeout",)
    if "action not executed" in text:
        return ("Action not executed",)
    if "validation guardian non interactive" in text:
        return ("guardian validation unavailable",)
    if "tool step limit reached" in text:
        return ("tool step limit reached",)
    first_line = next((line.strip() for line in summary.splitlines() if line.strip()), "task returned error")
    return (first_line,)


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


def scoped_tools(tools: tuple[ToolSpec, ...], scope: str) -> tuple[ToolSpec, ...]:
    normalized = scope.strip().lower() or "dev"
    if normalized != "dev":
        return ()
    allowed = set(DEV_TOOL_NAMES)
    return tuple(tool for tool in tools if tool.name in allowed)


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
