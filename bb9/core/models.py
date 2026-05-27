"""Shared runtime data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4


Risk = Literal["low", "medium", "high", "forbidden"]
PermissionProfile = Literal["safe", "limited", "power"]
GuardianVerdict = Literal["allow", "ask", "block"]
DecisionKind = Literal["answer", "action", "delegate", "stop"]
TraceType = Literal["intention", "decision", "guardian", "action", "observation", "stop"]
SessionRole = Literal["user", "assistant", "observation"]
TaskStatus = Literal["done", "error"]
ArtifactKind = Literal["diff", "tool_trace", "image", "report", "file", "screenshot", "note"]
VisibleRole = Literal["user", "assistant", "notification", "system", "process"]


@dataclass(frozen=True)
class Intention:
    text: str
    source: str = "user"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Action:
    name: str
    params: dict[str, Any] = field(default_factory=dict)
    risk: Risk = "medium"


@dataclass(frozen=True)
class Decision:
    kind: DecisionKind
    summary: str
    action: Action | None = None


@dataclass(frozen=True)
class GuardianDecision:
    verdict: GuardianVerdict
    reason: str
    action: Action | None = None


@dataclass(frozen=True)
class Artifact:
    kind: ArtifactKind
    title: str = ""
    path: str = ""
    source: str = ""
    id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Observation:
    ok: bool
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    artifacts: tuple[Artifact, ...] = ()


@dataclass(frozen=True)
class SessionMessage:
    role: SessionRole
    content: str
    time: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def as_prompt_line(self) -> str:
        return f"{self.role}: {self.content.strip()}"


@dataclass(frozen=True)
class Session:
    id: str = field(default_factory=lambda: str(uuid4()))
    source: str = "cli"
    messages: tuple[SessionMessage, ...] = ()
    compaction_summary: str = ""
    compacted_count: int = 0

    def with_message(self, role: SessionRole, content: str, *, max_messages: int = 40) -> "Session":
        message = SessionMessage(role=role, content=content.strip())
        messages = (*self.messages, message)
        if len(messages) > max_messages:
            messages = messages[-max_messages:]
        return Session(
            id=self.id,
            source=self.source,
            messages=messages,
            compaction_summary=self.compaction_summary,
            compacted_count=self.compacted_count,
        )

    def with_compaction_summary(
        self,
        summary: str,
        *,
        messages: tuple[SessionMessage, ...],
        compacted_count: int,
    ) -> "Session":
        return Session(
            id=self.id,
            source=self.source,
            messages=messages,
            compaction_summary=summary.strip(),
            compacted_count=compacted_count,
        )

    def as_prompt_context(self, *, limit: int = 8) -> str:
        messages = self.messages[-limit:]
        if not messages and not self.compaction_summary.strip():
            return ""
        lines = []
        if self.compaction_summary.strip():
            lines.extend(["# Session compactee", self.compaction_summary.strip(), ""])
        if not messages:
            return "\n".join(lines).strip()
        lines.append("# Session recente")
        lines.extend(message.as_prompt_line() for message in messages if message.content.strip())
        return "\n".join(lines)


@dataclass(frozen=True)
class Workspace:
    root: Path

    @staticmethod
    def current() -> "Workspace":
        return Workspace(root=Path.cwd())


@dataclass(frozen=True)
class TraceEvent:
    event_type: TraceType
    summary: str
    session_id: str
    time: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VisibleMessage:
    id: str
    role: VisibleRole
    content: str
    session_id: str
    source: str = "cli"
    project_path: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    artifacts: tuple[Artifact, ...] = ()


@dataclass(frozen=True)
class RunContext:
    session: Session
    workspace: Workspace
    permission_profile: PermissionProfile = "safe"
    trusted_roots: TrustedRoots | None = None
    agent: AgentProfile | None = None
    skills: tuple[Skill, ...] = ()
    tools: tuple[ToolSpec, ...] = ()
    skills_index: str = ""
    tools_index: str = ""
    subagents_index: str = ""
    context_index: str = ""


@dataclass(frozen=True)
class RunResult:
    decision: Decision
    observation: Observation | None
    trace: tuple[TraceEvent, ...]


@dataclass(frozen=True)
class AgentProfile:
    name: str
    identity: str = ""
    soul: str = ""
    model: str = ""
    reasoning_effort: str = ""
    disabled_skills: tuple[str, ...] = ()
    disabled_tools: tuple[str, ...] = ()

    def as_prompt_context(self) -> str:
        parts = [f"# Agent: {self.name}"]
        if self.identity.strip():
            parts.append("## IDENTITY.md")
            parts.append(self.identity.strip())
        if self.soul.strip():
            parts.append("## SOUL.md")
            parts.append(self.soul.strip())
        if self.model.strip():
            parts.append("## MODEL.md")
            parts.append(f"Model: {self.model.strip()}")
        if self.reasoning_effort.strip():
            parts.append(f"ReasoningEffort: {self.reasoning_effort.strip()}")
        return "\n\n".join(parts)


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    goal: str
    context: str
    inputs: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    expected_output: str = ""
    done_criteria: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    parallelizable: bool = False
    suggested_worker: str = ""
    permission_profile: PermissionProfile | None = None
    max_iterations: int = 1


@dataclass(frozen=True)
class TaskResult:
    task_id: str
    status: TaskStatus
    summary: str
    changed: tuple[str, ...] = ()
    observed: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    next_suggestion: str = ""


@dataclass(frozen=True)
class Skill:
    name: str
    body: str
    summary: str = ""
    activation: str = "on-demand"
    commands: tuple[str, ...] = ()
    root: Path | None = None

    def as_prompt_context(self) -> str:
        return f"# Skill: {self.name}\n\n{self.body.strip()}"

    def as_index_line(self) -> str:
        summary = self.summary or "-"
        parts = [f"- `{self.name}` ({self.activation}) : {summary}"]
        if self.commands:
            parts.append(f"  Commandes: {' '.join(self.commands)}")
        return "\n".join(parts)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    body: str
    summary: str = ""
    usage: str = ""
    protocol: str = ""
    status: str = ""
    commands: tuple[str, ...] = ()
    root: Path | None = None

    def as_prompt_context(self) -> str:
        return f"# Tool: {self.name}\n\n{self.body.strip()}"

    def as_index_line(self) -> str:
        summary = self.summary or "-"
        parts = [f"- `{self.name}` : {summary}"]
        if self.status:
            parts.append(f"  Statut: {self.status}")
        if self.usage:
            parts.append(f"  Usage: {self.usage}")
        if self.protocol:
            parts.append(f"  Protocole: {self.protocol}")
        if self.commands:
            parts.append(f"  Commandes: {' '.join(self.commands)}")
        return "\n".join(parts)
