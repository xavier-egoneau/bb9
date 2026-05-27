"""Reusable chat API service."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bb9.core import context_runtime
from bb9.core.channels import intention_from_text
from bb9.core.diffs import capture_worktree_snapshot, diff_artifact_since
from bb9.core.history import VisibleHistoryStore, default_visible_history_path
from bb9.core.kernel import Kernel
from bb9.core.loop import run_once
from bb9.core.models import Artifact, PermissionProfile, Session, TraceEvent
from bb9.core.paths import default_content_dir
from bb9.core.provider_config import ProviderEntry, default_provider_config_path
from bb9.core.provider_runtime import build_provider_for_agent
from bb9.core.providers import ProviderError
from bb9.core.trace import tool_trace_artifact


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

    def history_payload(self) -> dict[str, Any]:
        with self._lock:
            messages = [
                {"role": message.role, "content": message.content, "created_at": message.time}
                for message in self.state.session.messages
                if message.role in {"user", "assistant"}
            ]
            return {"ok": True, "session_id": self.state.session.id, "messages": messages}

    def run_message(self, text: str) -> dict[str, Any]:
        message = text.strip()
        if not message:
            return {"ok": False, "error": "empty_message"}
        with self._lock:
            events: list[TraceEvent] = []
            snapshot = capture_worktree_snapshot(Path.cwd())
            try:
                context = context_runtime.build_context(self.state)
                agent = context.agent or context_runtime.load_current_agent(self.state)
                provider = build_provider_for_agent(self.state, agent)
                result = run_once(
                    Kernel(provider=provider),
                    intention_from_text(message),
                    context,
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
            }

    def _remember_turn(self, user_text: str, assistant_text: str, artifacts: tuple[Artifact, ...]) -> None:
        store = VisibleHistoryStore(self.state.visible_history_path)
        try:
            store.append_turn(
                session_id=self.state.session.id,
                user_text=user_text,
                assistant_text=assistant_text,
                source="web",
                project_path=Path.cwd(),
                artifacts=artifacts,
            )
        finally:
            store.close()


def _turn_artifacts(
    artifacts: tuple[Artifact, ...],
    trace_events: tuple[TraceEvent, ...],
    snapshot,
) -> tuple[Artifact, ...]:
    tool_trace = tool_trace_artifact(trace_events)
    if tool_trace is not None:
        artifacts = (*artifacts, tool_trace)
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
