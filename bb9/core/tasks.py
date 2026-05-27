"""Minimal business task persistence for BB9."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from .paths import bb9_home

TaskStatus = Literal["backlog", "queued", "running", "done", "failed", "paused"]
TaskPriority = Literal["high", "med", "low"]

TASKS_FILE = "tasks.json"
STATUSES: tuple[str, ...] = ("backlog", "queued", "running", "done", "failed", "paused")
PRIORITIES: tuple[str, ...] = ("high", "med", "low")


@dataclass(frozen=True)
class TaskRecord:
    id: str
    title: str
    prompt: str = ""
    status: TaskStatus = "backlog"
    priority: TaskPriority = "med"
    agent: str = "default"
    project_path: str = ""
    scheduled_for: str = ""
    created_at: str = ""
    updated_at: str = ""
    events: tuple[dict[str, str], ...] = ()

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["events"] = list(self.events)
        return data

    @staticmethod
    def from_dict(data: dict[str, object]) -> TaskRecord:
        return TaskRecord(
            id=str(data.get("id") or ""),
            title=str(data.get("title") or ""),
            prompt=str(data.get("prompt") or ""),
            status=_status(data.get("status")),
            priority=_priority(data.get("priority")),
            agent=str(data.get("agent") or "default"),
            project_path=str(data.get("project_path") or data.get("projectPath") or ""),
            scheduled_for=str(data.get("scheduled_for") or data.get("scheduledFor") or ""),
            created_at=str(data.get("created_at") or data.get("createdAt") or ""),
            updated_at=str(data.get("updated_at") or data.get("updatedAt") or ""),
            events=tuple(
                {str(key): str(value) for key, value in item.items()}
                for item in data.get("events", ())
                if isinstance(item, dict)
            ),
        )

    def as_line(self) -> str:
        planned = f" @ {self.scheduled_for}" if self.scheduled_for else ""
        project = f" [{self.project_path}]" if self.project_path else ""
        return f"- `{self.id}` [{self.status}/{self.priority}] {self.title}{planned}{project}"


def default_tasks_path() -> Path:
    return bb9_home() / "tasks" / TASKS_FILE


class TaskStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_tasks_path()

    def load(self) -> tuple[TaskRecord, ...]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ()
        items = raw.get("tasks", raw) if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            return ()
        tasks = []
        for item in items:
            if isinstance(item, dict):
                task = TaskRecord.from_dict(item)
                if task.id and task.title:
                    tasks.append(task)
        return tuple(tasks)

    def save(self, tasks: tuple[TaskRecord, ...]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"tasks": [task.to_dict() for task in tasks]}
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def create(
        self,
        *,
        title: str,
        prompt: str = "",
        status: str = "",
        priority: str = "med",
        agent: str = "default",
        project_path: str | Path = "",
        scheduled_for: str = "",
    ) -> TaskRecord:
        clean_title = " ".join(title.split())
        if not clean_title:
            raise ValueError("title is required")
        clean_schedule = _scheduled_for(scheduled_for)
        initial_status = _required_status(status or ("queued" if clean_schedule else "backlog"))
        now = _now()
        task = TaskRecord(
            id=f"task-{uuid.uuid4().hex[:8]}",
            title=clean_title,
            prompt=prompt.strip(),
            status=initial_status,
            priority=_required_priority(priority),
            agent=agent.strip() or "default",
            project_path=_project_path(project_path),
            scheduled_for=clean_schedule,
            created_at=now,
            updated_at=now,
            events=({"at": now, "kind": "created", "status": initial_status},),
        )
        self.save((*self.load(), task))
        return task

    def update(self, task_id: str, **changes: object) -> TaskRecord | None:
        tasks = list(self.load())
        index = next((idx for idx, task in enumerate(tasks) if task.id == task_id), -1)
        if index < 0:
            return None
        current = tasks[index]
        now = _now()
        next_status = _required_status(changes.get("status", current.status))
        next_priority = _required_priority(changes.get("priority", current.priority))
        scheduled_for = (
            _scheduled_for(str(changes["scheduled_for"]))
            if "scheduled_for" in changes
            else current.scheduled_for
        )
        title = _clean_optional(changes.get("title"), current.title)
        prompt = _clean_optional(changes.get("prompt"), current.prompt)
        agent = _clean_optional(changes.get("agent"), current.agent) or "default"
        project_path = (
            _project_path(str(changes["project_path"]))
            if "project_path" in changes
            else current.project_path
        )
        events = current.events
        if next_status != current.status:
            events = (*events, {"at": now, "kind": "status_changed", "from": current.status, "to": next_status})
        updated = TaskRecord(
            id=current.id,
            title=title,
            prompt=prompt,
            status=next_status,
            priority=next_priority,
            agent=agent,
            project_path=project_path,
            scheduled_for=scheduled_for,
            created_at=current.created_at,
            updated_at=now,
            events=events,
        )
        tasks[index] = updated
        self.save(tuple(tasks))
        return updated

    def get(self, task_id: str) -> TaskRecord | None:
        return next((task for task in self.load() if task.id == task_id), None)

    def list(
        self,
        *,
        status: str = "",
        agent: str = "",
        project_path: str | Path = "",
        include_done: bool = True,
    ) -> tuple[TaskRecord, ...]:
        tasks = self.load()
        selected_status = status.strip()
        selected_agent = agent.strip()
        selected_project = _project_path(project_path) if project_path else ""
        if selected_status:
            tasks = tuple(task for task in tasks if task.status == _required_status(selected_status))
        elif not include_done:
            tasks = tuple(task for task in tasks if task.status != "done")
        if selected_agent:
            tasks = tuple(task for task in tasks if task.agent == selected_agent)
        if selected_project:
            tasks = tuple(task for task in tasks if task.project_path == selected_project)
        by_recent_update = sorted(tasks, key=lambda task: task.updated_at, reverse=True)
        return tuple(sorted(by_recent_update, key=lambda task: task.status == "done"))


def _status(value: object) -> TaskStatus:
    text = str(value or "").strip().lower()
    if text in STATUSES:
        return text  # type: ignore[return-value]
    return "backlog"


def _priority(value: object) -> TaskPriority:
    text = str(value or "").strip().lower()
    if text in PRIORITIES:
        return text  # type: ignore[return-value]
    return "med"


def _required_status(value: object) -> TaskStatus:
    text = str(value or "").strip().lower()
    if text in STATUSES:
        return text  # type: ignore[return-value]
    raise ValueError(f"status must be one of: {', '.join(STATUSES)}")


def _required_priority(value: object) -> TaskPriority:
    text = str(value or "").strip().lower()
    if text in PRIORITIES:
        return text  # type: ignore[return-value]
    raise ValueError(f"priority must be one of: {', '.join(PRIORITIES)}")


def _scheduled_for(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as err:
        raise ValueError("scheduled_for must be an ISO datetime") from err
    return text


def _project_path(path: str | Path) -> str:
    text = str(path).strip()
    if not text:
        return ""
    return str(Path(text).expanduser().resolve(strict=False))


def _clean_optional(value: object, fallback: str) -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text else fallback


def _now() -> str:
    return datetime.now(UTC).isoformat()
