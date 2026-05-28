"""Reusable chat API service."""

from __future__ import annotations

import base64
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from bb9.core.attachments import MAX_IMAGE_BYTES, SUPPORTED_IMAGE_MIME_TYPES
from bb9.core import context_runtime
from bb9.core.channels import intention_from_text
from bb9.core.diffs import capture_worktree_snapshot, diff_artifact_since
from bb9.core.history import VisibleHistoryStore, default_visible_history_path
from bb9.core.kernel import Kernel
from bb9.core.loop import ApprovalDecision, execute_approved_action, run_once
from bb9.core.models import Artifact, GuardianDecision, PermissionProfile, RunContext, Session, TraceEvent
from bb9.core.paths import default_content_dir
from bb9.core.provider_config import ProviderEntry, default_provider_config_path
from bb9.core.provider_runtime import build_provider_for_agent
from bb9.core.providers import ProviderError
from bb9.core.trace import decision_trace_artifact, tool_trace_artifact


MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


@dataclass
class ChatApiState:
    profile: PermissionProfile = "safe"
    provider_kind: str = "echo"
    model: str = ""
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    api_key_ref: str = ""
    provider_config_path: Path = field(default_factory=default_provider_config_path)
    active_provider: ProviderEntry | None = None
    agent_name: str = "default"
    subagent_name: str = ""
    agents_dir: Path = field(default_factory=lambda: default_content_dir("agents"))
    skills_dir: Path = field(default_factory=lambda: default_content_dir("skills"))
    tools_dir: Path = field(default_factory=lambda: default_content_dir("tools"))
    visible_history_path: Path = field(default_factory=default_visible_history_path)
    show_trace: bool = False
    session: Session = field(default_factory=lambda: Session(source="web"))


class ChatApiApp:
    def __init__(self, state: ChatApiState | None = None) -> None:
        self.state = state or ChatApiState()
        self._lock = threading.Lock()
        self._pending_approval: PendingApproval | None = None

    def history_payload(self) -> dict[str, Any]:
        with self._lock:
            messages = self._history_messages()
            return {"ok": True, "session_id": self.state.session.id, "messages": messages}

    def status_payload(self) -> dict[str, Any]:
        with self._lock:
            provider = self.state.active_provider
            provider_label = provider.name if provider is not None else self.state.provider_kind
            model = provider.model if provider is not None else self.state.model
            return {
                "ok": True,
                "session_id": self.state.session.id,
                "source": self.state.session.source,
                "workspace": str(Path.cwd()),
                "profile": self.state.profile,
                "provider": provider_label or self.state.provider_kind,
                "model": model or "",
                "agent": self.state.agent_name,
                "subagent": self.state.subagent_name,
                "pending_approval": _approval_payload(self._pending_approval),
            }

    def upload_image(self, *, mime: str, data: str) -> dict[str, Any]:
        mime = mime.lower().strip()
        if mime not in SUPPORTED_IMAGE_MIME_TYPES or mime not in MIME_EXT:
            return {"ok": False, "error": "unsupported_image_type"}
        try:
            image_bytes = base64.b64decode(data, validate=True)
        except Exception:
            return {"ok": False, "error": "invalid_base64"}
        if not image_bytes:
            return {"ok": False, "error": "empty_image"}
        if len(image_bytes) > MAX_IMAGE_BYTES:
            return {"ok": False, "error": "image_too_large"}
        uploads_dir = Path.cwd() / ".bb9" / "uploads" / "web"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        path = uploads_dir / f"{uuid.uuid4().hex[:10]}{MIME_EXT[mime]}"
        path.write_bytes(image_bytes)
        return {
            "ok": True,
            "path": str(path),
            "reference": f"[image: {path}]",
            "url": f"/api/image?path={quote(str(path))}",
            "mime": mime,
            "size": len(image_bytes),
        }

    def run_message(self, text: str) -> dict[str, Any]:
        message = text.strip()
        if not message:
            return {"ok": False, "error": "empty_message"}
        with self._lock:
            events: list[TraceEvent] = []
            snapshot = capture_worktree_snapshot(Path.cwd())
            self._pending_approval = None
            try:
                context = context_runtime.build_context(self.state)
                agent = context.agent or context_runtime.load_current_agent(self.state)
                provider = build_provider_for_agent(self.state, agent)
                result = run_once(
                    Kernel(provider=provider),
                    intention_from_text(message),
                    context,
                    ask_user=lambda decision, run_context: self._defer_approval(decision, run_context),
                    on_event=events.append,
                )
            except ProviderError as exc:
                return {"ok": False, "error": "provider_error", "message": str(exc)}
            except Exception as exc:
                return {"ok": False, "error": "runtime_error", "message": str(exc)}

            answer = result.observation.summary if result.observation is not None else result.decision.summary
            base_artifacts = result.observation.artifacts if result.observation is not None else ()
            artifacts = _turn_artifacts(base_artifacts, result.trace or tuple(events), snapshot)
            self.state.session = self.state.session.with_message("user", message).with_message("assistant", answer)
            self._remember_turn(message, answer, artifacts)
            return {
                "ok": True,
                "session_id": self.state.session.id,
                "answer": answer,
                "events": [_event_payload(event) for event in (result.trace or tuple(events))],
                "artifacts": [_artifact_payload(artifact) for artifact in artifacts],
                "approval": _approval_payload(self._pending_approval),
            }

    def resolve_approval(self, approval_id: str, decision: str) -> dict[str, Any]:
        approval_id = approval_id.strip()
        verdict = decision.strip().lower()
        with self._lock:
            pending = self._pending_approval
            if pending is None or pending.id != approval_id:
                return {"ok": False, "error": "approval_not_found"}
            self._pending_approval = None
            if verdict not in {"allow", "deny"}:
                return {"ok": False, "error": "invalid_approval_decision"}
            if verdict == "deny":
                answer = "Action refusée."
                self.state.session = self.state.session.with_message("assistant", answer)
                self._remember_turn("", answer, ())
                return {"ok": True, "answer": answer, "events": [], "artifacts": []}

            snapshot = capture_worktree_snapshot(Path.cwd())
            observation, events = execute_approved_action(pending.guardian, pending.context)
            answer = observation.summary
            artifacts = _turn_artifacts(observation.artifacts, events, snapshot)
            self.state.session = self.state.session.with_message("assistant", answer)
            self._remember_turn("", answer, artifacts)
            return {
                "ok": True,
                "answer": answer,
                "events": [_event_payload(event) for event in events],
                "artifacts": [_artifact_payload(artifact) for artifact in artifacts],
            }

    def _remember_turn(self, user_text: str, assistant_text: str, artifacts: tuple[Artifact, ...]) -> None:
        store = VisibleHistoryStore(self.state.visible_history_path)
        try:
            if user_text:
                store.append_turn(
                    session_id=self.state.session.id,
                    user_text=user_text,
                    assistant_text=assistant_text,
                    source="web",
                    project_path=Path.cwd(),
                    artifacts=artifacts,
                )
            else:
                store.append_message(
                    session_id=self.state.session.id,
                    role="assistant",
                    content=assistant_text,
                    source="web",
                    project_path=Path.cwd(),
                    artifacts=artifacts,
                )
        finally:
            store.close()

    def _defer_approval(self, decision: GuardianDecision, context: RunContext) -> ApprovalDecision:
        approval = PendingApproval(id=uuid.uuid4().hex, guardian=decision, context=context)
        self._pending_approval = approval
        return ApprovalDecision(verdict="defer", summary="Validation requise.")

    def _history_messages(self) -> list[dict[str, Any]]:
        store = VisibleHistoryStore(self.state.visible_history_path)
        try:
            visible = [
                message
                for message in store.recent(limit=80, project_path=Path.cwd())
                if message.source == "web" and message.role in {"user", "assistant"}
            ]
        finally:
            store.close()
        if visible:
            return [
                {
                    "role": message.role,
                    "content": message.content,
                    "created_at": message.created_at,
                    "artifacts": [_artifact_payload(artifact) for artifact in message.artifacts],
                }
                for message in visible
            ]
        return [
            {"role": message.role, "content": message.content, "created_at": message.time, "artifacts": []}
            for message in self.state.session.messages
            if message.role in {"user", "assistant"}
        ]


def _turn_artifacts(
    artifacts: tuple[Artifact, ...],
    trace_events: tuple[TraceEvent, ...],
    snapshot,
) -> tuple[Artifact, ...]:
    tool_trace = tool_trace_artifact(trace_events)
    if tool_trace is not None:
        artifacts = (*artifacts, tool_trace)
    decision_trace = decision_trace_artifact(trace_events)
    if decision_trace is not None:
        artifacts = (*artifacts, decision_trace)
    diff = diff_artifact_since(snapshot)
    if diff is not None:
        artifacts = (*artifacts, diff)
    return artifacts


def _event_payload(event: TraceEvent) -> dict[str, Any]:
    return {
        "type": event.event_type,
        "summary": event.summary,
        "time": event.time,
        "data": event.data,
    }


def _artifact_payload(artifact: Artifact) -> dict[str, Any]:
    return {
        "id": artifact.id,
        "kind": artifact.kind,
        "title": artifact.title,
        "path": artifact.path,
        "source": artifact.source,
        "created_at": artifact.created_at,
        "metadata": artifact.metadata,
    }


@dataclass(frozen=True)
class PendingApproval:
    id: str
    guardian: GuardianDecision
    context: RunContext


def _approval_payload(approval: PendingApproval | None) -> dict[str, Any] | None:
    if approval is None:
        return None
    action = approval.guardian.action
    return {
        "id": approval.id,
        "reason": approval.guardian.reason,
        "tool": action.name if action is not None else "",
        "params": action.params if action is not None else {},
        "risk": action.risk if action is not None else "",
    }
