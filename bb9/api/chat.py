"""Reusable chat API service."""

from __future__ import annotations

import base64
import re
import threading
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote

from bb9.core import context_runtime, runtime_service
from bb9.core.agents import AgentNotFoundError
from bb9.core.attachments import MAX_IMAGE_BYTES, SUPPORTED_IMAGE_MIME_TYPES
from bb9.core.cli_render import archive_command_parts
from bb9.core.diffs import capture_worktree_snapshot
from bb9.core.history import VisibleHistoryStore, default_visible_history_path
from bb9.core.loop import ApprovalDecision, RunCancelled, execute_approved_action
from bb9.core.markdown import command_aliases
from bb9.core.models import Artifact, GuardianDecision, PermissionProfile, RunContext, Session, TraceEvent
from bb9.core.paths import bb9_home, default_content_dir, product_root
from bb9.core.provider_config import ProviderEntry, default_provider_config_path
from bb9.core.providers import ProviderError
from bb9.core.sessions import SessionStore, default_session_store_path
from bb9.core.settings import PROFILES, SettingsStore, default_settings_path
from bb9.core.skills import load_effective_skills
from bb9.core.tools import load_enabled_tools

MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

THEME_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
BUILTIN_THEME_IDS = {"system", "light", "dark"}
BUILTIN_THEMES = (
    {"id": "system", "label": "Système", "source": "builtin"},
    {"id": "light", "label": "Clair", "source": "builtin"},
    {"id": "dark", "label": "Sombre", "source": "builtin"},
)
NATIVE_REPL_COMMANDS = (
    ("/help", "afficher l'aide", True),
    ("/context", "afficher l'état courant", True),
    ("/history", "afficher l'historique visible", True),
    ("/new", "nouvelle session", True),
    ("/compact", "compacter le contexte court", False),
    ("/model", "choisir provider et modèle", False),
    ("/goal", "objectif autonome", False),
    ("/cron", "routines et tâches planifiées", False),
    ("/dream", "consolidation mémoire", False),
    ("/profil", "changer le niveau de permission", False),
    ("/profile", "changer le niveau de permission", False),
    ("/exit", "quitter le REPL", False),
    ("/quit", "quitter le REPL", False),
)


@dataclass
class ChatApiState:
    profile: PermissionProfile = "safe"
    provider_kind: str = "echo"
    model: str = ""
    reasoning_effort: str = ""
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
    settings_path: Path = field(default_factory=default_settings_path)
    session_store_path: Path = field(default_factory=default_session_store_path)
    visible_history_path: Path = field(default_factory=default_visible_history_path)
    show_trace: bool = False
    active_project_path: str = ""
    session: Session = field(default_factory=lambda: Session(source="web"))


class ChatApiApp:
    def __init__(self, state: ChatApiState | None = None) -> None:
        self.state = state or ChatApiState()
        if not self.state.active_project_path:
            self.state.active_project_path = str(Path.cwd().resolve(strict=False))
        self._lock = threading.RLock()
        self._run_lock = threading.Lock()
        self._cancel_current_run = threading.Event()
        self._current_run_id = ""
        self._pending_approval: PendingApproval | None = None

    def history_payload(self) -> dict[str, Any]:
        with self._lock:
            messages = self._history_messages()
            return {
                "ok": True,
                "session_id": self.state.session.id,
                "active_project": str(self._active_project_path()),
                "messages": messages,
            }

    def commands_payload(self) -> dict[str, Any]:
        with self._lock:
            native = [
                _native_command_payload(command, description, supported)
                for command, description, supported in NATIVE_REPL_COMMANDS
            ]
            archive, collisions = self._archive_command_payloads()
            return {
                "ok": True,
                "active_project": str(self._active_project_path()),
                "workspace": str(Path.cwd().resolve(strict=False)),
                "commands": [*native, *archive],
                "collisions": collisions,
            }

    def themes_payload(self) -> dict[str, Any]:
        with self._lock:
            return {"ok": True, "themes": [*BUILTIN_THEMES, *self._custom_themes()]}

    def theme_stylesheet(self, theme_id: str) -> tuple[str, bytes] | None:
        theme_id = theme_id.strip()
        if not _valid_theme_id(theme_id):
            return None
        with self._lock:
            path = self._theme_path(theme_id)
        if path is None or not path.is_file():
            return None
        return "text/css; charset=utf-8", path.read_bytes()

    def projects_payload(self) -> dict[str, Any]:
        with self._lock:
            current = str(Path.cwd().resolve(strict=False))
            active = str(self._active_project_path())
            store = SessionStore(self.state.session_store_path)
            try:
                projects = [
                    project
                    for project in store.projects(limit=120)
                    if _project_is_visible(str(project.get("path") or ""), current=current, active=active)
                ]
            finally:
                store.close()
            if not any(project.get("path") == current for project in projects):
                projects.insert(0, {"path": current, "updated_at": "", "session_count": 0})
            projects = _dedupe_projects(projects, current=current)
            for project in projects[:50]:
                project["active"] = project.get("path") == active
                project["runtime_workspace"] = project.get("path") == current
                project["label"] = _project_label(str(project.get("path") or ""), projects)
            return {"ok": True, "active_project": active, "workspace": current, "projects": projects[:50]}

    def switch_project(self, project_path: str) -> dict[str, Any]:
        path = _normalize_project_path(project_path)
        if not path:
            return {"ok": False, "error": "missing_project_path"}
        with self._lock:
            self.state.active_project_path = path
            sessions = self._web_sessions_for_active_project()
            if sessions:
                self.state.session = sessions[0].as_session()
                messages = self._history_messages()
            else:
                self.state.session = Session(source="web")
                messages = []
            return {
                "ok": True,
                "active_project": path,
                "workspace": str(Path.cwd().resolve(strict=False)),
                "session_id": self.state.session.id,
                "messages": messages,
                "sessions": [_session_payload(session, active=session.id == self.state.session.id) for session in sessions],
            }

    def sessions_payload(self) -> dict[str, Any]:
        with self._lock:
            store = SessionStore(self.state.session_store_path)
            try:
                sessions = self._web_sessions_for_active_project(store=store)
            finally:
                store.close()
            return {
                "ok": True,
                "active_session_id": self.state.session.id,
                "active_project": str(self._active_project_path()),
                "workspace": str(Path.cwd().resolve(strict=False)),
                "sessions": [
                    _session_payload(session, active=session.id == self.state.session.id)
                    for session in sessions
                ],
            }

    def status_payload(self) -> dict[str, Any]:
        with self._lock:
            status = _runtime_status(self.state)
            return {
                "ok": True,
                "session_id": status.session_id,
                "source": status.source,
                "workspace": status.workspace,
                "active_project": str(self._active_project_path()),
                "profile": status.profile,
                "provider": status.provider,
                "model": status.model,
                "reasoning_effort": status.reasoning_effort,
                "agent": status.agent,
                "subagent": status.subagent,
                "workspace_status": status.workspace_status,
                "running": bool(self._current_run_id),
                "run_id": self._current_run_id,
                "pending_approval": _approval_payload(self._pending_approval),
            }

    def settings_payload(self) -> dict[str, Any]:
        with self._lock:
            status = _runtime_status(self.state)
            return {
                "ok": True,
                "profiles": list(PROFILES),
                "reasoning_efforts": ["", "low", "medium", "high"],
                "profile": self.state.profile,
                "provider": status.provider,
                "model": status.model,
                "reasoning_effort": status.reasoning_effort,
                "workspace": status.workspace,
                "active_project": str(self._active_project_path()),
            }

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            profile = str(payload.get("profile") or "").strip().lower()
            if profile:
                if profile not in PROFILES:
                    return {"ok": False, "error": "invalid_profile"}
                self.state.profile = profile  # type: ignore[assignment]
                SettingsStore(self.state.settings_path).set_profile(self.state.profile)

            model = str(payload.get("model") or "").strip()
            reasoning_effort = str(payload.get("reasoning_effort") or "").strip().lower()
            if reasoning_effort not in {"", "low", "medium", "high"}:
                return {"ok": False, "error": "invalid_reasoning_effort"}
            self.state.model = model
            self.state.reasoning_effort = reasoning_effort
            if self.state.active_provider is not None:
                metadata = dict(self.state.active_provider.metadata)
                if reasoning_effort:
                    metadata["reasoning_effort"] = reasoning_effort
                else:
                    metadata.pop("reasoning_effort", None)
                self.state.active_provider = replace(
                    self.state.active_provider,
                    model=model or self.state.active_provider.model,
                    metadata=metadata,
                )
            status = _runtime_status(self.state)
            return {
                "ok": True,
                "profiles": list(PROFILES),
                "reasoning_efforts": ["", "low", "medium", "high"],
                "profile": self.state.profile,
                "provider": status.provider,
                "model": status.model,
                "reasoning_effort": status.reasoning_effort,
                "workspace": status.workspace,
                "active_project": str(self._active_project_path()),
            }

    def stop_current_run(self) -> dict[str, Any]:
        with self._lock:
            run_id = self._current_run_id
            if not run_id:
                return {"ok": True, "stopped": False}
            self._cancel_current_run.set()
            return {"ok": True, "stopped": True, "run_id": run_id}

    def switch_session(self, session_id: str) -> dict[str, Any]:
        session_id = session_id.strip()
        if not session_id:
            return {"ok": False, "error": "missing_session_id"}
        with self._lock:
            store = SessionStore(self.state.session_store_path)
            try:
                stored = store.get(session_id)
            finally:
                store.close()
            if stored is None:
                return {"ok": False, "error": "session_not_found"}
            if stored.source != "web":
                return {"ok": False, "error": "session_source_not_supported"}
            project = str(self._active_project_path())
            if stored.project_path and stored.project_path != project:
                return {"ok": False, "error": "session_project_mismatch"}
            self.state.session = stored.as_session()
            return {"ok": True, "session_id": self.state.session.id, "messages": self._history_messages()}

    def new_session(self) -> dict[str, Any]:
        with self._lock:
            self._persist_session()
            self.state.session = Session(source="web")
            self._persist_session()
            return {"ok": True, "session_id": self.state.session.id, "messages": []}

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
            command = self._handle_web_command(message)
            if command is not None:
                return command
            if self._active_project_path() != Path.cwd().resolve(strict=False):
                return {
                    "ok": False,
                    "error": "project_view_only",
                    "message": "Le projet actif affiché n'est pas le workspace d'exécution de ce serveur bb9 web.",
                }
            if self._current_run_id:
                return {"ok": False, "error": "agent_busy", "message": "BB9 est déjà en action."}
            run_id = uuid.uuid4().hex
            self._current_run_id = run_id
            self._cancel_current_run.clear()
            self._pending_approval = None

        events: list[TraceEvent] = []
        try:
            turn = runtime_service.run_message(
                self.state,
                message,
                ask_user=lambda decision, run_context: self._defer_approval(decision, run_context),
                on_event=events.append,
                should_cancel=self._cancel_current_run.is_set,
            )
        except RunCancelled:
            return {"ok": False, "error": "run_cancelled", "message": "Run interrompu.", "run_id": run_id}
        except ProviderError as exc:
            return {"ok": False, "error": "provider_error", "message": str(exc), "run_id": run_id}
        except Exception as exc:
            return {"ok": False, "error": "runtime_error", "message": str(exc), "run_id": run_id}
        finally:
            with self._lock:
                if self._current_run_id == run_id:
                    self._current_run_id = ""
                    self._cancel_current_run.clear()

        with self._lock:
            answer = turn.answer
            trace_events = turn.result.trace or tuple(events)
            artifacts = runtime_service.artifacts_from_parts(
                turn.base_artifacts,
                trace_events,
                turn.snapshot,
                include_decision_trace=True,
            )
            self.state.session = self.state.session.with_message("user", message).with_message("assistant", answer)
            self._persist_session()
            self._remember_turn(message, answer, artifacts)
            return {
                "ok": True,
                "session_id": self.state.session.id,
                "run_id": run_id,
                "answer": answer,
                "events": [_event_payload(event) for event in trace_events],
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
                self._persist_session()
                self._remember_turn("", answer, ())
                return {"ok": True, "answer": answer, "events": [], "artifacts": []}

            snapshot = capture_worktree_snapshot(Path.cwd())
            observation, events = execute_approved_action(pending.guardian, pending.context)
            answer = observation.summary
            artifacts = runtime_service.artifacts_from_parts(
                observation.artifacts,
                events,
                snapshot,
                include_decision_trace=True,
            )
            self.state.session = self.state.session.with_message("assistant", answer)
            self._persist_session()
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
                    project_path=self._active_project_path(),
                    artifacts=artifacts,
                )
            else:
                store.append_message(
                    session_id=self.state.session.id,
                    role="assistant",
                    content=assistant_text,
                    source="web",
                    project_path=self._active_project_path(),
                    artifacts=artifacts,
                )
        finally:
            store.close()

    def _persist_session(self) -> None:
        store = SessionStore(self.state.session_store_path)
        try:
            store.store(self.state.session, project_path=self._active_project_path())
        finally:
            store.close()

    def _active_project_path(self) -> Path:
        return Path(self.state.active_project_path or Path.cwd()).expanduser().resolve(strict=False)

    def _web_sessions_for_active_project(self, *, store: SessionStore | None = None):
        owned_store = store is None
        store = store or SessionStore(self.state.session_store_path)
        try:
            sessions = store.recent(
                limit=30,
                project_path=self._active_project_path(),
                include_archived=False,
                include_global=False,
            )
            return tuple(session for session in sessions if session.source == "web")
        finally:
            if owned_store:
                store.close()

    def _archive_command_payloads(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        try:
            agent = context_runtime.load_current_agent(self.state)
        except AgentNotFoundError:
            return [], []

        active_project = self._active_project_path()
        local_skills_root = active_project / ".bb9" / "skills"
        entries: list[dict[str, Any]] = []
        for skill in load_effective_skills(self.state.skills_dir, local_skills_root, agent.disabled_skills):
            local = skill.root == local_skills_root
            for line in skill.commands:
                command, description = archive_command_parts(line)
                if command:
                    entries.append(
                        _archive_command_payload(
                            command,
                            description or f"skill {skill.name}",
                            owner=skill.name,
                            source="local-skill" if local else "skill",
                            local=local,
                        )
                    )
            for alias in command_aliases(skill.commands):
                if not any(entry["name"] == alias and entry["owner"] == skill.name for entry in entries):
                    entries.append(
                        _archive_command_payload(
                            alias,
                            skill.summary or f"skill {skill.name}",
                            owner=skill.name,
                            source="local-skill" if local else "skill",
                            local=local,
                        )
                    )

        for tool in load_enabled_tools(self.state.tools_dir, agent.disabled_tools):
            for line in tool.commands:
                command, description = archive_command_parts(line)
                if command:
                    entries.append(
                        _archive_command_payload(
                            command,
                            description or f"tool {tool.name}",
                            owner=tool.name,
                            source="tool",
                            local=False,
                        )
                    )

        owners_by_command: dict[str, list[str]] = {}
        native_names = {command for command, _, _ in NATIVE_REPL_COMMANDS}
        for entry in entries:
            owners_by_command.setdefault(str(entry["name"]), []).append(f"{entry['source']}:{entry['owner']}")

        collisions: list[dict[str, Any]] = []
        for command, owners in sorted(owners_by_command.items()):
            if command in native_names:
                collisions.append({"name": command, "owners": ["native", *owners]})
            elif len(owners) > 1:
                collisions.append({"name": command, "owners": owners})

        collided = {str(collision["name"]) for collision in collisions}
        commands: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in entries:
            name = str(entry["name"])
            if name in collided or name in seen:
                continue
            commands.append(entry)
            seen.add(name)
        return commands, collisions

    def _custom_themes(self) -> list[dict[str, Any]]:
        themes: list[dict[str, Any]] = []
        seen = set(BUILTIN_THEME_IDS)
        for root, source in self._theme_roots():
            if not root.is_dir():
                continue
            for path in sorted(root.glob("*.css")):
                theme_id = path.stem.strip()
                if not _valid_theme_id(theme_id) or theme_id in seen:
                    continue
                themes.append(
                    {
                        "id": theme_id,
                        "label": _theme_label(theme_id),
                        "source": source,
                        "href": f"/api/theme?name={quote(theme_id)}",
                    }
                )
                seen.add(theme_id)
        return themes

    def _theme_path(self, theme_id: str) -> Path | None:
        if theme_id in BUILTIN_THEME_IDS:
            return None
        for root, _ in self._theme_roots():
            path = root / f"{theme_id}.css"
            if path.is_file():
                return path.resolve(strict=False)
        return None

    def _theme_roots(self) -> tuple[tuple[Path, str], ...]:
        return (
            (self._active_project_path() / ".bb9" / "themes" / "web", "project"),
            (bb9_home() / "themes" / "web", "user"),
            (product_root() / "chat-web" / "themes", "builtin"),
        )

    def _handle_web_command(self, message: str) -> dict[str, Any] | None:
        command, _, rest = message.partition(" ")
        if command not in {item[0] for item in NATIVE_REPL_COMMANDS}:
            return None
        if command == "/help":
            lines = ["Commandes disponibles :"]
            for item in self.commands_payload()["commands"]:
                suffix = " (non supportée en web)" if not item.get("supported", True) else ""
                lines.append(f"- `{item['name']}` : {item.get('description') or item.get('owner')}{suffix}")
            return {"ok": True, "session_id": self.state.session.id, "answer": "\n".join(lines), "events": [], "artifacts": []}
        if command == "/context":
            status = self.status_payload()
            reasoning = f" · {status['reasoning_effort']}" if status["reasoning_effort"] else ""
            answer = (
                f"Workspace : `{status['workspace']}`\n"
                f"Projet actif : `{status['active_project']}`\n"
                f"Session : `{status['session_id']}`\n"
                f"Profil : `{status['profile']}`\n"
                f"Modèle : `{status['provider']} · {status['model'] or '-'}{reasoning}`"
            )
            return {"ok": True, "session_id": self.state.session.id, "answer": answer, "events": [], "artifacts": []}
        if command == "/new":
            created = self.new_session()
            return {"ok": True, "session_id": created["session_id"], "answer": "Nouvelle session créée.", "events": [], "artifacts": []}
        if command == "/history":
            limit = _positive_int(rest, default=20)
            messages = self._history_messages()[-limit:]
            answer = "Aucun historique visible pour cette session."
            if messages:
                answer = "\n\n".join(f"**{message['role']}**\n{message['content']}" for message in messages)
            return {"ok": True, "session_id": self.state.session.id, "answer": answer, "events": [], "artifacts": []}
        return {
            "ok": False,
            "error": "web_command_unsupported",
            "message": f"La commande `{command}` existe dans le REPL mais n'a pas encore d'équivalent web direct.",
        }

    def _defer_approval(self, decision: GuardianDecision, context: RunContext) -> ApprovalDecision:
        approval = PendingApproval(id=uuid.uuid4().hex, guardian=decision, context=context)
        self._pending_approval = approval
        return ApprovalDecision(verdict="defer", summary="Validation requise.")

    def _history_messages(self) -> list[dict[str, Any]]:
        store = VisibleHistoryStore(self.state.visible_history_path)
        try:
            visible = [
                message
                for message in store.recent(
                    limit=80,
                    session_id=self.state.session.id,
                    project_path=self._active_project_path(),
                )
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


def _runtime_status(state: ChatApiState) -> runtime_service.RuntimeStatus:
    try:
        return runtime_service.build_status(state)
    except Exception:
        provider = state.active_provider
        return runtime_service.RuntimeStatus(
            session_id=state.session.id,
            source=state.session.source,
            workspace=str(Path.cwd()),
            profile=state.profile,
            provider=(provider.name if provider is not None else state.provider_kind),
            model=(provider.model if provider is not None else state.model) or "",
            reasoning_effort=str(getattr(state, "reasoning_effort", "") or ""),
            agent=state.agent_name,
            subagent=state.subagent_name,
            workspace_status="",
        )


def _normalize_project_path(path: Path | str | None) -> str:
    if path is None:
        return ""
    text = str(path).strip()
    if not text:
        return ""
    return str(Path(text).expanduser().resolve(strict=False))


def _native_command_payload(command: str, description: str, supported: bool) -> dict[str, Any]:
    return {
        "name": command,
        "description": description,
        "source": "native",
        "owner": "bb9",
        "local": False,
        "supported": supported,
    }


def _archive_command_payload(
    command: str,
    description: str,
    *,
    owner: str,
    source: str,
    local: bool,
) -> dict[str, Any]:
    return {
        "name": command,
        "description": description,
        "source": source,
        "owner": owner,
        "local": local,
        "supported": True,
    }


def _valid_theme_id(theme_id: str) -> bool:
    return bool(theme_id) and THEME_ID_RE.fullmatch(theme_id) is not None and theme_id not in BUILTIN_THEME_IDS


def _theme_label(theme_id: str) -> str:
    return " ".join(part.capitalize() for part in theme_id.replace("_", "-").split("-") if part) or theme_id


def _positive_int(text: str, *, default: int) -> int:
    try:
        value = int(text.strip())
    except ValueError:
        return default
    return max(1, min(value, 80))


def _project_is_visible(path: str, *, current: str, active: str) -> bool:
    if path in {current, active}:
        return True
    return Path(path).is_dir()


def _dedupe_projects(projects: list[dict[str, Any]], *, current: str) -> list[dict[str, Any]]:
    by_path: dict[str, dict[str, Any]] = {}
    for project in projects:
        path = _normalize_project_path(project.get("path"))
        if not path:
            continue
        item = by_path.setdefault(path, {"path": path, "updated_at": "", "session_count": 0})
        item["session_count"] = int(item["session_count"]) + int(project.get("session_count") or 0)
        if str(project.get("updated_at") or "") > str(item.get("updated_at") or ""):
            item["updated_at"] = str(project.get("updated_at") or "")
    ordered = sorted(by_path.values(), key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    ordered.sort(key=lambda item: 0 if item["path"] == current else 1)
    return ordered


def _project_label(path: str, projects: list[dict[str, Any]]) -> str:
    target = Path(path)
    name = target.name or str(target)
    duplicate = sum(1 for project in projects if Path(str(project.get("path") or "")).name == name) > 1
    if not duplicate:
        return name
    parent = target.parent.name
    return f"{parent}/{name}" if parent else name


def _session_payload(session, *, active: bool = False) -> dict[str, Any]:
    title = ""
    for message in session.messages:
        if message.role == "user" and message.content.strip():
            title = message.content.strip().replace("\n", " ")
            break
    return {
        "id": session.id,
        "title": title[:80] or f"Session {session.id[:8]}",
        "source": session.source,
        "project_path": session.project_path or "",
        "updated_at": session.updated_at,
        "message_count": len(session.messages),
        "active": active,
    }


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
