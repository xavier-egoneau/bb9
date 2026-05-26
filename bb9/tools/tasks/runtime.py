"""Standalone business tasks tool runtime."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from bb9.core.models import Action, GuardianDecision, Observation, RunContext
from bb9.core.tasks import TaskStore


TASKS_PATH: Path | None = None


def action_from_text(text: str) -> Action:
    try:
        argv = shlex.split(text.strip())
    except ValueError:
        return Action(name="tasks", params={"op": "invalid", "raw": text}, risk="forbidden")
    op = argv[0].lower() if argv else ""
    params = _parse_params(argv[1:])
    if op == "create":
        args = params.pop("_args", ())
        title = str(params.get("title") or " ".join(args).strip())
        params["op"] = "create"
        params["title"] = title
        return Action(name="tasks", params=params, risk="medium" if title else "forbidden")
    if op == "list":
        params["op"] = "list"
        return Action(name="tasks", params=params, risk="low")
    if op == "update":
        params["op"] = "update"
        if not str(params.get("id") or "").strip():
            return Action(name="tasks", params=params, risk="forbidden")
        return Action(name="tasks", params=params, risk="medium")
    return Action(name="tasks", params={"op": "invalid", "raw": text}, risk="forbidden")


def review(action: Action, _: RunContext) -> GuardianDecision:
    op = str(action.params.get("op", "")).strip().lower()
    if op == "list":
        return GuardianDecision(verdict="allow", reason="tasks listing is local state read", action=action)
    if op in {"create", "update"}:
        return GuardianDecision(verdict="ask", reason="tasks store write requires confirmation", action=action)
    return GuardianDecision(verdict="block", reason="invalid tasks action", action=action)


def execute(action: Action) -> Observation:
    op = str(action.params.get("op", "")).strip().lower()
    store = TaskStore(TASKS_PATH)
    try:
        if op == "create":
            task = store.create(
                title=str(action.params.get("title") or ""),
                prompt=str(action.params.get("prompt") or ""),
                priority=str(action.params.get("priority") or "med"),
                agent=str(action.params.get("agent") or "default"),
                project_path=str(action.params.get("project_path") or action.params.get("project") or ""),
                scheduled_for=str(action.params.get("scheduled_for") or action.params.get("at") or ""),
            )
            return Observation(ok=True, summary=f"Task created: {task.as_line()}", data={"task": task.to_dict()})
        if op == "list":
            tasks = store.list(
                status=str(action.params.get("status") or ""),
                agent=str(action.params.get("agent") or ""),
                project_path=str(action.params.get("project_path") or action.params.get("project") or ""),
                include_done=_bool(action.params.get("include_done"), True),
            )
            summary = "No tasks." if not tasks else f"{len(tasks)} task(s):\n" + "\n".join(task.as_line() for task in tasks)
            return Observation(ok=True, summary=summary, data={"tasks": [task.to_dict() for task in tasks]})
        if op == "update":
            task_id = str(action.params.get("id") or "").strip()
            changes = _update_changes(action.params)
            if not changes:
                return Observation(ok=False, summary="No task changes provided.")
            task = store.update(task_id, **changes)
            if task is None:
                return Observation(ok=False, summary=f"Task not found: {task_id}")
            return Observation(ok=True, summary=f"Task updated: {task.as_line()}", data={"task": task.to_dict()})
    except ValueError as exc:
        return Observation(ok=False, summary=str(exc))
    return Observation(ok=False, summary="Invalid tasks tool operation.")


def _parse_params(parts: list[str]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    args: list[str] = []
    for part in parts:
        if "=" in part:
            key, value = part.split("=", 1)
            params[_key(key)] = value
        else:
            args.append(part)
    if args:
        params["_args"] = tuple(args)
    return params


def _update_changes(params: dict[str, Any]) -> dict[str, object]:
    allowed = {"title", "prompt", "status", "priority", "agent", "project_path", "scheduled_for"}
    changes = {key: value for key, value in params.items() if key in allowed}
    if "project" in params and "project_path" not in changes:
        changes["project_path"] = params["project"]
    if "at" in params and "scheduled_for" not in changes:
        changes["scheduled_for"] = params["at"]
    return changes


def _key(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default
