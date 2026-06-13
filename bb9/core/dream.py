"""Markdown dream contracts and memory consolidation helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from .archives import ArchiveNotFoundError, MarkdownArchive, discover_archives, load_archive
from .markdown import extract_section
from .memory import MemoryEdge, MemoryNode, MemoryStore
from .paths import bb9_home
from .sessions import SessionStore
from .tasks import TaskStore

DREAM_FILE = "DREAM.md"
INDEX_FILE = "INDEX.md"
PENDING_FILE = "dream-pending.json"
REPORTS_DIR = "reports"

DreamActivation = str


class DreamProvider(Protocol):
    def complete(self, prompt: str) -> str:
        ...


@dataclass(frozen=True)
class DreamSpec:
    name: str
    body: str
    summary: str = ""
    activation: DreamActivation = "paused"
    agent: str = "default"
    scope: str = "global"
    sources: str = ""
    memory_policy: str = ""
    output: str = ""
    guardrails: str = ""

    def as_index_line(self) -> str:
        summary = self.summary or "-"
        return f"- `{self.name}` ({self.activation}, {self.scope}) : {summary}"


@dataclass(frozen=True)
class DreamContribution:
    name: str
    kind: str
    body: str
    purpose: str = ""
    inputs: str = ""
    signals: str = ""
    proposed_actions: str = ""
    output_guidance: str = ""
    guardrails: str = ""

    def as_prompt_context(self) -> str:
        parts = [f"### {self.kind}:{self.name}"]
        if self.purpose.strip():
            parts.extend(["Purpose:", self.purpose.strip()])
        if self.inputs.strip():
            parts.extend(["Inputs:", self.inputs.strip()])
        if self.signals.strip():
            parts.extend(["Signals:", self.signals.strip()])
        if self.proposed_actions.strip():
            parts.extend(["Proposed Actions:", self.proposed_actions.strip()])
        if self.output_guidance.strip():
            parts.extend(["Output Guidance:", self.output_guidance.strip()])
        if self.guardrails.strip():
            parts.extend(["Guardrails:", self.guardrails.strip()])
        return "\n\n".join(parts)


@dataclass(frozen=True)
class DreamingContext:
    memories: tuple[MemoryNode, ...] = ()
    edges: tuple[MemoryEdge, ...] = ()
    contributions: tuple[DreamContribution, ...] = ()
    sessions: tuple[str, ...] = ()
    decisions_doc: str = ""
    roadmap_doc: str = ""

    @property
    def is_empty(self) -> bool:
        return not (
            self.memories
            or self.edges
            or self.contributions
            or self.sessions
            or self.decisions_doc.strip()
            or self.roadmap_doc.strip()
        )


@dataclass(frozen=True)
class DreamingResult:
    added_nodes: int = 0
    updated_nodes: int = 0
    removed_nodes: int = 0
    added_edges: int = 0
    created_tasks: int = 0
    actions: tuple[dict[str, Any], ...] = ()
    errors: int = 0
    summary: str = ""


@dataclass(frozen=True)
class DreamingPlan:
    dream: str
    operations: tuple[dict[str, Any], ...] = ()
    actions: tuple[dict[str, Any], ...] = ()
    summary: str = ""
    raw_response: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "dream": self.dream,
            "operations": list(self.operations),
            "actions": list(self.actions),
            "summary": self.summary,
            "rawResponse": self.raw_response,
        }

    @staticmethod
    def from_dict(data: dict[str, object]) -> DreamingPlan:
        operations = data.get("operations", ())
        actions = data.get("actions", ())
        return DreamingPlan(
            dream=str(data.get("dream") or ""),
            operations=tuple(item for item in operations if isinstance(item, dict))
            if isinstance(operations, list)
            else (),
            actions=tuple(item for item in actions if isinstance(item, dict))
            if isinstance(actions, list)
            else (),
            summary=str(data.get("summary") or ""),
            raw_response=str(data.get("rawResponse") or data.get("raw_response") or ""),
        )


@dataclass(frozen=True)
class DreamReport:
    id: str
    dream: str
    mode: str
    created_at: str
    project_path: str = ""
    summary: str = ""
    added_nodes: int = 0
    updated_nodes: int = 0
    removed_nodes: int = 0
    added_edges: int = 0
    created_tasks: int = 0
    errors: int = 0
    operations_count: int = 0
    actions: tuple[dict[str, Any], ...] = ()
    operations: tuple[dict[str, Any], ...] = ()
    json_path: str = ""
    markdown_path: str = ""

    @staticmethod
    def from_result(
        *,
        dream: str,
        mode: str,
        result: DreamingResult,
        operations: tuple[dict[str, Any], ...] = (),
        project_path: Path | str | None = None,
    ) -> DreamReport:
        return DreamReport(
            id=_report_id(),
            dream=dream,
            mode=mode,
            created_at=datetime.now(UTC).isoformat(),
            project_path=_project_path(project_path),
            summary=result.summary,
            added_nodes=result.added_nodes,
            updated_nodes=result.updated_nodes,
            removed_nodes=result.removed_nodes,
            added_edges=result.added_edges,
            created_tasks=result.created_tasks,
            errors=result.errors,
            operations_count=len(operations),
            actions=result.actions,
            operations=operations,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "dream": self.dream,
            "mode": self.mode,
            "createdAt": self.created_at,
            "projectPath": self.project_path,
            "summary": self.summary,
            "result": {
                "addedNodes": self.added_nodes,
                "updatedNodes": self.updated_nodes,
                "removedNodes": self.removed_nodes,
                "addedEdges": self.added_edges,
                "createdTasks": self.created_tasks,
                "errors": self.errors,
            },
            "operationsCount": self.operations_count,
            "actions": list(self.actions),
            "operations": list(self.operations),
            "jsonPath": self.json_path,
            "markdownPath": self.markdown_path,
        }

    @staticmethod
    def from_dict(data: dict[str, object]) -> DreamReport:
        result = data.get("result", {})
        if not isinstance(result, dict):
            result = {}
        actions = data.get("actions", ())
        operations = data.get("operations", ())
        return DreamReport(
            id=str(data.get("id") or ""),
            dream=str(data.get("dream") or ""),
            mode=str(data.get("mode") or ""),
            created_at=str(data.get("createdAt") or data.get("created_at") or ""),
            project_path=str(data.get("projectPath") or data.get("project_path") or ""),
            summary=str(data.get("summary") or ""),
            added_nodes=_int_object(result.get("addedNodes") or result.get("added_nodes")),
            updated_nodes=_int_object(result.get("updatedNodes") or result.get("updated_nodes")),
            removed_nodes=_int_object(result.get("removedNodes") or result.get("removed_nodes")),
            added_edges=_int_object(result.get("addedEdges") or result.get("added_edges")),
            created_tasks=_int_object(result.get("createdTasks") or result.get("created_tasks")),
            errors=_int_object(result.get("errors")),
            operations_count=_int_object(data.get("operationsCount") or data.get("operations_count")),
            actions=tuple(item for item in actions if isinstance(item, dict))
            if isinstance(actions, list)
            else (),
            operations=tuple(item for item in operations if isinstance(item, dict))
            if isinstance(operations, list)
            else (),
            json_path=str(data.get("jsonPath") or data.get("json_path") or ""),
            markdown_path=str(data.get("markdownPath") or data.get("markdown_path") or ""),
        )


def default_dreams_dir() -> Path:
    return bb9_home() / "dreams"


def default_dream_pending_path() -> Path:
    return bb9_home() / PENDING_FILE


def default_dream_reports_dir() -> Path:
    return default_dreams_dir() / REPORTS_DIR


def discover_dreams(root: Path) -> list[str]:
    return discover_archives(root, DREAM_FILE)


def load_dream(root: Path, name: str) -> DreamSpec:
    try:
        archive = load_archive(root, name, DREAM_FILE)
    except ArchiveNotFoundError as err:
        raise DreamNotFoundError(f"Dream not found: {name}") from err
    return _dream_from_archive(archive)


def load_enabled_dreams(root: Path) -> tuple[DreamSpec, ...]:
    return tuple(
        dream
        for dream in (load_dream(root, name) for name in discover_dreams(root))
        if dream.activation == "active"
    )


def build_dream_index(dreams: tuple[DreamSpec, ...]) -> str:
    lines = ["# Dream Index", ""]
    if dreams:
        lines.extend(dream.as_index_line() for dream in dreams)
    else:
        lines.append("Aucun dream configure.")
    return "\n".join(lines).strip() + "\n"


def refresh_dream_index(root: Path) -> str:
    dreams = tuple(load_dream(root, name) for name in discover_dreams(root))
    index = build_dream_index(dreams)
    root.mkdir(parents=True, exist_ok=True)
    (root / INDEX_FILE).write_text(index, encoding="utf-8")
    return index


def load_dream_contribution(root: Path, name: str, kind: str) -> DreamContribution:
    try:
        archive = load_archive(root, name, DREAM_FILE)
    except ArchiveNotFoundError as err:
        raise DreamNotFoundError(f"Dream contribution not found: {kind}:{name}") from err
    return _contribution_from_archive(archive, kind)


def load_dream_contributions(
    root: Path,
    kind: str,
    active_names: tuple[str, ...] = (),
) -> tuple[DreamContribution, ...]:
    names = active_names or tuple(discover_archives(root, DREAM_FILE))
    contributions: list[DreamContribution] = []
    for name in names:
        try:
            contributions.append(load_dream_contribution(root, name, kind))
        except DreamNotFoundError:
            continue
    return tuple(contributions)


def build_dreaming_context(
    memory_store: MemoryStore,
    *,
    project_root: Path | None = None,
    skill_contributions: tuple[DreamContribution, ...] = (),
    tool_contributions: tuple[DreamContribution, ...] = (),
    sessions: tuple[str, ...] = (),
    session_store: SessionStore | None = None,
    session_limit: int = 12,
    memory_limit: int = 2000,
) -> DreamingContext:
    memories = tuple(memory_store.list_nodes(limit=memory_limit))
    if project_root is not None:
        active = tuple(memory_store.get_active_context(project_root, limit=memory_limit))
        by_id = {memory.id: memory for memory in memories}
        by_id.update({memory.id: memory for memory in active})
        memories = tuple(by_id.values())
    edges = tuple(edge for memory in memories for edge in memory_store.edges_for(memory.id))
    stored_sessions = (
        session_store.recent_dream_context(limit=session_limit, project_path=project_root)
        if session_store is not None
        else ()
    )
    return DreamingContext(
        memories=memories,
        edges=edges,
        contributions=(*skill_contributions, *tool_contributions),
        sessions=(*sessions, *stored_sessions),
        decisions_doc=_read_optional(project_root / "DECISIONS.md") if project_root else "",
        roadmap_doc=_read_optional(project_root / "ROADMAP.md") if project_root else "",
    )


def build_dreaming_prompt(spec: DreamSpec, context: DreamingContext) -> str:
    parts = [
        "# BB9 Dreaming",
        (
            "Tu es le moteur de consolidation mémoire de BB9. "
            "Consolide les faits durables, relie les informations utiles, "
            "propose des actions sourcées, et n'exécute rien directement."
        ),
        "# Dream Contract",
        spec.body.strip(),
    ]
    if context.memories:
        parts.append(
            "# Memory Nodes\n\n"
            + "\n".join(memory.as_prompt_line() for memory in context.memories)
        )
    if context.edges:
        parts.append("# Memory Edges\n\n" + "\n".join(_edge_line(edge) for edge in context.edges))
    if context.sessions:
        parts.append("# Sessions\n\n" + "\n\n".join(context.sessions))
    if context.contributions:
        parts.append(
            "# DREAM.md Contributions\n\n"
            + "\n\n".join(contribution.as_prompt_context() for contribution in context.contributions)
        )
    if context.decisions_doc.strip():
        parts.append("# DECISIONS.md\n\n" + context.decisions_doc.strip())
    if context.roadmap_doc.strip():
        parts.append("# ROADMAP.md\n\n" + context.roadmap_doc.strip()[:4000])
    parts.append(_OUTPUT_FORMAT)
    return "\n\n".join(part for part in parts if part.strip())


def parse_dreaming_response(text: str) -> tuple[list[dict[str, object]], list[dict[str, object]], str]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        return [], [], ""
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        return [], [], ""
    operations = data.get("operations", [])
    actions = data.get("actions", [])
    summary = data.get("summary", "")
    if not isinstance(operations, list):
        operations = []
    if not isinstance(actions, list):
        actions = []
    return (
        [item for item in operations if isinstance(item, dict)],
        [item for item in actions if isinstance(item, dict)],
        str(summary or ""),
    )


def apply_dream_operations(
    operations: list[dict[str, Any]],
    memory_store: MemoryStore,
    *,
    project_root: Path | None = None,
) -> DreamingResult:
    added_nodes = updated_nodes = removed_nodes = added_edges = errors = 0
    project_path = str(project_root.expanduser().resolve(strict=False)) if project_root else None
    for operation in operations:
        op = str(operation.get("op") or "").strip()
        try:
            if op == "node.add":
                content = str(operation.get("content") or "").strip()
                if not content:
                    errors += 1
                    continue
                scope = str(operation.get("scope") or "global")
                operation_project = operation.get("project_path")
                resolved_project = str(operation_project or project_path or "")
                if scope == "project" and not resolved_project:
                    errors += 1
                    continue
                memory_store.add(
                    content,
                    scope=scope,
                    project_path=resolved_project if scope == "project" else None,
                    kind=str(operation.get("kind") or "fact"),
                    tags=str(operation.get("tags") or ""),
                    source=str(operation.get("source") or ""),
                    confidence=str(operation.get("confidence") or "medium"),
                )
                added_nodes += 1
            elif op == "node.replace":
                if memory_store.replace(
                    str(operation.get("old") or ""),
                    str(operation.get("new") or ""),
                ):
                    updated_nodes += 1
                else:
                    errors += 1
            elif op == "node.remove":
                text = str(operation.get("text") or "")
                if memory_store.remove_by_text(text):
                    removed_nodes += 1
                else:
                    errors += 1
            elif op == "edge.add":
                source_id = int(operation.get("source_id") or 0)
                target_id = int(operation.get("target_id") or 0)
                relation = str(operation.get("relation") or "")
                if source_id <= 0 or target_id <= 0 or not relation.strip():
                    errors += 1
                    continue
                memory_store.add_edge(
                    source_id,
                    target_id,
                    relation,
                    weight=float(operation.get("weight") or 1.0),
                    source=str(operation.get("source") or ""),
                )
                added_edges += 1
            else:
                errors += 1
        except Exception:
            errors += 1
    return DreamingResult(
        added_nodes=added_nodes,
        updated_nodes=updated_nodes,
        removed_nodes=removed_nodes,
        added_edges=added_edges,
        errors=errors,
    )


def plan_dreaming(
    spec: DreamSpec,
    context: DreamingContext,
    provider: DreamProvider,
) -> DreamingPlan:
    response = provider.complete(build_dreaming_prompt(spec, context))
    operations, actions, summary = parse_dreaming_response(response)
    return DreamingPlan(
        dream=spec.name,
        operations=tuple(operations),
        actions=tuple(actions),
        summary=summary,
        raw_response=response,
    )


def apply_dream_plan(
    plan: DreamingPlan,
    memory_store: MemoryStore,
    *,
    project_root: Path | None = None,
    task_store: TaskStore | None = None,
) -> DreamingResult:
    applied = apply_dream_operations(list(plan.operations), memory_store, project_root=project_root)
    actions, created_tasks, action_errors = apply_dream_actions(
        plan.actions,
        task_store=task_store,
        project_root=project_root,
    )
    return DreamingResult(
        added_nodes=applied.added_nodes,
        updated_nodes=applied.updated_nodes,
        removed_nodes=applied.removed_nodes,
        added_edges=applied.added_edges,
        created_tasks=created_tasks,
        actions=actions,
        errors=applied.errors + action_errors,
        summary=plan.summary,
    )


def run_dreaming(
    spec: DreamSpec,
    context: DreamingContext,
    memory_store: MemoryStore,
    provider: DreamProvider,
    *,
    project_root: Path | None = None,
    task_store: TaskStore | None = None,
) -> DreamingResult:
    return apply_dream_plan(
        plan_dreaming(spec, context, provider),
        memory_store,
        project_root=project_root,
        task_store=task_store,
    )


def apply_dream_actions(
    actions: tuple[dict[str, Any], ...],
    *,
    task_store: TaskStore | None = None,
    project_root: Path | None = None,
) -> tuple[tuple[dict[str, Any], ...], int, int]:
    processed: list[dict[str, object]] = []
    created_tasks = errors = 0
    for action in actions:
        kind = str(action.get("kind") or "").strip().lower()
        if kind not in {"task.create", "tasks.create"}:
            processed.append(dict(action))
            continue
        if task_store is None:
            processed.append(dict(action))
            continue
        updated = dict(action)
        try:
            title = str(action.get("title") or "").strip()
            if not title:
                raise ValueError("task.create action requires title")
            task = task_store.create(
                title=title,
                prompt=_task_prompt(action),
                priority=str(action.get("priority") or "med"),
                agent=str(action.get("agent") or "default"),
                project_path=str(action.get("project_path") or _project_path(project_root)),
                scheduled_for=str(action.get("scheduled_for") or action.get("at") or ""),
            )
            updated["status"] = "created"
            updated["task_id"] = task.id
            created_tasks += 1
        except ValueError as exc:
            updated["status"] = "error"
            updated["error"] = str(exc)
            errors += 1
        processed.append(updated)
    return tuple(processed), created_tasks, errors


def save_pending_dream_plan(plan: DreamingPlan, path: Path | None = None) -> Path:
    target = path or default_dream_pending_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def load_pending_dream_plan(path: Path | None = None) -> DreamingPlan | None:
    target = path or default_dream_pending_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return DreamingPlan.from_dict(data)


def clear_pending_dream_plan(path: Path | None = None) -> None:
    target = path or default_dream_pending_path()
    try:
        target.unlink()
    except FileNotFoundError:
        return


def save_dream_report(report: DreamReport, root: Path | None = None) -> DreamReport:
    reports_dir = root or default_dream_reports_dir()
    reports_dir.mkdir(parents=True, exist_ok=True)
    stem = _report_stem(report)
    json_path = reports_dir / f"{stem}.json"
    markdown_path = reports_dir / f"{stem}.md"
    saved = DreamReport(
        id=report.id,
        dream=report.dream,
        mode=report.mode,
        created_at=report.created_at,
        project_path=report.project_path,
        summary=report.summary,
        added_nodes=report.added_nodes,
        updated_nodes=report.updated_nodes,
        removed_nodes=report.removed_nodes,
        added_edges=report.added_edges,
        created_tasks=report.created_tasks,
        errors=report.errors,
        operations_count=report.operations_count,
        actions=report.actions,
        operations=report.operations,
        json_path=str(json_path),
        markdown_path=str(markdown_path),
    )
    json_path.write_text(json.dumps(saved.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown_path.write_text(format_dream_report(saved), encoding="utf-8")
    return saved


def list_dream_reports(root: Path | None = None, *, limit: int = 20) -> tuple[DreamReport, ...]:
    reports_dir = root or default_dream_reports_dir()
    if not reports_dir.is_dir():
        return ()
    paths = sorted(reports_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    reports: list[DreamReport] = []
    for path in paths[: max(0, limit)]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            report = DreamReport.from_dict(data)
            if not report.json_path:
                report = DreamReport(
                    id=report.id,
                    dream=report.dream,
                    mode=report.mode,
                    created_at=report.created_at,
                    project_path=report.project_path,
                    summary=report.summary,
                    added_nodes=report.added_nodes,
                    updated_nodes=report.updated_nodes,
                    removed_nodes=report.removed_nodes,
                    added_edges=report.added_edges,
                    created_tasks=report.created_tasks,
                    errors=report.errors,
                    operations_count=report.operations_count,
                    actions=report.actions,
                    operations=report.operations,
                    json_path=str(path),
                    markdown_path=str(path.with_suffix(".md")),
                )
            reports.append(report)
    return tuple(reports)


def load_dream_report(root: Path | None, report_id: str) -> DreamReport | None:
    requested = report_id.strip()
    if not requested:
        return None
    for report in list_dream_reports(root, limit=200):
        if report.id == requested or report.id.startswith(requested):
            return report
        if Path(report.json_path).stem == requested:
            return report
    return None


def format_dream_report(report: DreamReport) -> str:
    lines = [
        f"# Dream Report: {report.dream}",
        "",
        f"- Id: `{report.id}`",
        f"- Mode: `{report.mode}`",
        f"- Created: {report.created_at}",
    ]
    if report.project_path:
        lines.append(f"- Project: {report.project_path}")
    lines.extend(
        [
            "",
            "## Result",
            "",
            f"- Added nodes: {report.added_nodes}",
            f"- Updated nodes: {report.updated_nodes}",
            f"- Removed nodes: {report.removed_nodes}",
            f"- Added edges: {report.added_edges}",
            f"- Created tasks: {report.created_tasks}",
            f"- Errors: {report.errors}",
            f"- Operations parsed: {report.operations_count}",
        ]
    )
    if report.summary.strip():
        lines.extend(["", "## Summary", "", report.summary.strip()])
    if report.actions:
        lines.extend(["", "## Proposed Actions", ""])
        for action in report.actions:
            title = str(action.get("title") or action.get("kind") or "action")
            status = str(action.get("status") or "proposed")
            lines.append(f"- `{status}` {title}")
    if report.operations:
        lines.extend(["", "## Operations", ""])
        for operation in report.operations[:20]:
            lines.append(f"- `{operation.get('op', '?')}` {_operation_label(operation)}")
        if len(report.operations) > 20:
            lines.append(f"- +{len(report.operations) - 20} operation(s)")
    return "\n".join(lines).strip() + "\n"


class DreamNotFoundError(RuntimeError):
    pass


def _dream_from_archive(archive: MarkdownArchive) -> DreamSpec:
    body = archive.body
    return DreamSpec(
        name=archive.name,
        body=body,
        summary=_section(body, "Résumé", "Resume").replace("\n", " "),
        activation=_normalize_activation(_first_value(body, "Activation")),
        agent=_first_value(body, "Agent") or "default",
        scope=_first_value(body, "Scope") or "global",
        sources=_section(body, "Sources"),
        memory_policy=_section(
            body,
            "Memory Policy",
            "Politique mémoire",
            "Politique memoire",
        ),
        output=_section(body, "Output", "Sortie"),
        guardrails=_section(body, "Guardrails", "Garde-fous", "Garde fous"),
    )


def _contribution_from_archive(archive: MarkdownArchive, kind: str) -> DreamContribution:
    body = archive.body
    return DreamContribution(
        name=archive.name,
        kind=kind,
        body=body,
        purpose=_section(body, "Purpose", "But", "Intention"),
        inputs=_section(body, "Inputs", "Entrants"),
        signals=_section(body, "Signals", "Signaux"),
        proposed_actions=_section(
            body,
            "Proposed Actions",
            "Allowed Actions",
            "Actions proposées",
            "Actions proposees",
        ),
        output_guidance=_section(body, "Output Guidance", "Output", "Sortie"),
        guardrails=_section(body, "Guardrails", "Garde-fous", "Garde fous"),
    )


def _section(markdown: str, *headings: str) -> str:
    for heading in headings:
        value = extract_section(markdown, heading)
        if value:
            return value
    return ""


def _first_value(markdown: str, heading: str) -> str:
    section = _section(markdown, heading)
    for line in section.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _normalize_activation(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"active", "enabled", "on", "oui", "yes"}:
        return "active"
    return "paused"


def _edge_line(edge: MemoryEdge) -> str:
    return f"- #{edge.source_id} -[{edge.relation}]-> #{edge.target_id} (weight={edge.weight:g})"


def _read_optional(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _report_id() -> str:
    return f"dream-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"


def _report_stem(report: DreamReport) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", report.id).strip("-") or _report_id()


def _project_path(path: Path | str | None) -> str:
    if path is None:
        return ""
    text = str(path).strip()
    if not text:
        return ""
    return str(Path(text).expanduser().resolve(strict=False))


def _int_object(value: object) -> int:
    try:
        return int(value or 0)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0


def _operation_label(operation: dict[str, object]) -> str:
    for key in ("content", "new", "text", "relation"):
        value = str(operation.get(key) or "").strip()
        if value:
            return value[:120]
    return str(operation)[:120]


def _task_prompt(action: dict[str, object]) -> str:
    parts = []
    for key, label in (("content", "Contenu"), ("reason", "Raison"), ("source", "Source")):
        value = str(action.get(key) or "").strip()
        if value:
            parts.append(f"{label}: {value}")
    return "\n".join(parts)


_OUTPUT_FORMAT = """\
# Output Format

Réponds uniquement avec un JSON valide :

```json
{
  "operations": [
    {"op": "node.add", "content": "...", "scope": "global|project", "kind": "fact", "tags": "...", "source": "...", "confidence": "low|medium|high"},
    {"op": "node.replace", "old": "<extrait exact>", "new": "..."},
    {"op": "node.remove", "text": "<extrait exact>"},
    {"op": "edge.add", "source_id": 1, "target_id": 2, "relation": "supports", "weight": 1.0, "source": "..."}
  ],
  "actions": [
    {"kind": "skill.action", "title": "...", "content": "...", "source": "...", "confidence": "medium", "status": "proposed", "reason": "..."},
    {"kind": "task.create", "title": "...", "content": "...", "priority": "high|med|low", "agent": "default", "scheduled_for": "YYYY-MM-DDTHH:MM:SS+02:00", "source": "...", "status": "proposed", "reason": "..."}
  ],
  "summary": "Bilan court."
}
```

Règles :
- Les opérations modifient seulement la mémoire SQL graph.
- Les actions métier restent proposées et ne sont jamais exécutées par le dreaming.
- `task.create` peut seulement être matérialisé en tâche durable lors d'un `/dream run` ou `/dream apply` explicite.
- Les sources doivent être explicites et non secrètes.
- N'invente pas de source absente du contexte.
- Si rien à faire : `{"operations": [], "actions": [], "summary": "Mémoire à jour."}`.
"""
