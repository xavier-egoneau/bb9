"""Reusable chat API service."""

from __future__ import annotations

import base64
import logging
import re
import subprocess
import threading
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote

from bb9.core import context_runtime, runtime_service
from bb9.core.agents import AgentNotFoundError
from bb9.core.attachments import MAX_IMAGE_BYTES, SUPPORTED_IMAGE_MIME_TYPES
from bb9.core.cli_render import archive_command_parts, short_index_names, short_message
from bb9.core.compaction import CompactionConfig, auto_compact_session, compact_session, estimate_session_tokens
from bb9.core.diffs import capture_worktree_snapshot
from bb9.core.history import VisibleHistoryStore, default_visible_history_path
from bb9.core.kernel import Kernel
from bb9.core.loop import ApprovalDecision, RunCancelled, execute_approved_action, tool_budget_for
from bb9.core.markdown import command_aliases
from bb9.core.models import Artifact, GuardianDecision, Intention, PermissionProfile, RunContext, Session, TraceEvent
from bb9.core.paths import bb9_home, default_content_dir, product_root
from bb9.core.provider_config import (
    ModelFetchError,
    ProviderEntry,
    ProviderStore,
    default_provider_config_path,
    fetch_models,
)
from bb9.core.provider_runtime import active_model_metadata, build_provider_for_agent
from bb9.core.providers import ProviderError
from bb9.core.sessions import SessionStore, default_session_store_path
from bb9.core.settings import PROFILES, SettingsStore, default_settings_path
from bb9.core.skills import load_effective_skills
from bb9.core.tools import load_enabled_tools
from bb9.core.utils import positive_int, workspace_status_summary

from .chat_context import context_budget_lines
from .chat_git import (
    clean_git_commit_message,
    git_branches,
    git_changed_files,
    git_commit,
    git_commit_message,
    git_file_diff,
    git_text,
    valid_git_relative_path,
)

MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_logger = logging.getLogger("bb9.api")

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
    ("/compact", "compacter le contexte court", True),
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
    profile_explicit: bool = False
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
        settings = SettingsStore(self.state.settings_path).load()
        if not self.state.profile_explicit and self.state.profile == "safe":
            self.state.profile = settings.profile
        if not self.state.active_project_path:
            self.state.active_project_path = str(Path.cwd().resolve(strict=False))
        self._lock = threading.RLock()
        self._run_lock = threading.Lock()
        self._cancel_current_run = threading.Event()
        self._current_run_id = ""
        self._current_run_events: list[TraceEvent] = []
        self._approval_message = ""
        self._pending_approval: PendingApproval | None = None
        self._restore_active_session_if_needed()

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

    def models_payload(self) -> dict[str, Any]:
        with self._lock:
            config = ProviderStore(self.state.provider_config_path).load()
            active = self.state.active_provider or config.active_entry()
            providers: list[dict[str, Any]] = []
            for entry in config.entries:
                models: list[str] = []
                error = ""
                try:
                    models = fetch_models(entry, timeout=4.0)
                except ModelFetchError as exc:
                    error = str(exc)
                if entry.model and entry.model not in models:
                    models.insert(0, entry.model)
                providers.append(
                    {
                        "id": entry.id,
                        "name": entry.name,
                        "provider": entry.provider,
                        "model": entry.model,
                        "active": active is not None and entry.id == active.id,
                        "models": models,
                        "error": error,
                    }
                )
            if not providers:
                providers.append(
                    {
                        "id": "",
                        "name": self.state.provider_kind,
                        "provider": self.state.provider_kind,
                        "model": self.state.model,
                        "active": True,
                        "models": [self.state.model] if self.state.model else [],
                        "error": "",
                    }
                )
            return {
                "ok": True,
                "active_provider_id": active.id if active is not None else "",
                "model": active.model if active is not None else self.state.model,
                "providers": providers,
            }

    def git_payload(self) -> dict[str, Any]:
        with self._lock:
            root = self._active_project_path()
            git_root = git_text(root, "rev-parse", "--show-toplevel")
            if not git_root:
                return {"ok": True, "git": False, "files_changed": 0, "files": [], "branch": "", "branches": []}
            git_root_path = Path(git_root).resolve(strict=False)
            branch = git_text(git_root_path, "branch", "--show-current") or git_text(git_root_path, "rev-parse", "--short", "HEAD")
            branches = git_branches(git_root_path)
            files = git_changed_files(git_root_path)
            return {
                "ok": True,
                "git": True,
                "root": str(git_root_path),
                "branch": branch or "detached",
                "branches": branches,
                "files_changed": len(files),
                "files": files,
            }

    def git_diff_payload(self, path: str) -> dict[str, Any]:
        path = path.strip()
        if not valid_git_relative_path(path):
            return {"ok": False, "error": "invalid_path"}
        with self._lock:
            root = self._active_project_path()
            git_root = git_text(root, "rev-parse", "--show-toplevel")
            if not git_root:
                return {"ok": False, "error": "not_git_worktree"}
            git_root_path = Path(git_root).resolve(strict=False)
            files = {item["path"]: item for item in git_changed_files(git_root_path)}
            if path not in files:
                return {"ok": False, "error": "unknown_changed_file"}
            return {
                "ok": True,
                "path": path,
                "status": files[path]["status"],
                "diff": git_file_diff(git_root_path, path, str(files[path]["status"])),
            }

    def git_commit_message_payload(self) -> dict[str, Any]:
        with self._lock:
            root = self._active_project_path()
            git_root = git_text(root, "rev-parse", "--show-toplevel")
            if not git_root:
                return {"ok": False, "error": "not_git_worktree"}
            git_root_path = Path(git_root).resolve(strict=False)
            files = git_changed_files(git_root_path)
            if not files:
                return {"ok": False, "error": "clean_worktree", "message": "Aucun changement à committer."}
            return {
                "ok": True,
                "git": True,
                "root": str(git_root_path),
                "files_changed": len(files),
                "files": files,
                "message": git_commit_message(files),
            }

    def commit_git_changes(self, message: str) -> dict[str, Any]:
        message = clean_git_commit_message(message)
        if not message:
            return {"ok": False, "error": "missing_message", "message": "Message de commit requis."}
        with self._lock:
            root = self._active_project_path()
            git_root = git_text(root, "rev-parse", "--show-toplevel")
            if not git_root:
                return {"ok": False, "error": "not_git_worktree"}
            git_root_path = Path(git_root).resolve(strict=False)
            files = git_changed_files(git_root_path)
            if not files:
                return {"ok": False, "error": "clean_worktree", "message": "Aucun changement à committer."}
            paths = tuple(str(file.get("path") or "") for file in files if valid_git_relative_path(str(file.get("path") or "")))
            if not paths:
                return {"ok": False, "error": "clean_worktree", "message": "Aucun changement committable."}
            staged = subprocess.run(
                ("git", "add", "--", *paths),
                cwd=str(git_root_path),
                text=True,
                capture_output=True,
                check=False,
                timeout=8,
            )
            if staged.returncode != 0:
                return {"ok": False, "error": "git_add_failed", "message": staged.stderr.strip() or staged.stdout.strip()}
            result = git_commit(git_root_path, message)
            if result.returncode != 0:
                return {"ok": False, "error": "git_commit_failed", "message": result.stderr.strip() or result.stdout.strip()}
            payload = self.git_payload()
            payload["committed"] = True
            payload["commit"] = git_text(git_root_path, "rev-parse", "--short", "HEAD")
            payload["message"] = message
            return payload

    def switch_git_branch(self, branch: str) -> dict[str, Any]:
        branch = branch.strip()
        if not branch:
            return {"ok": False, "error": "missing_branch"}
        with self._lock:
            root = self._active_project_path()
            git_root = git_text(root, "rev-parse", "--show-toplevel")
            if not git_root:
                return {"ok": False, "error": "not_git_worktree"}
            git_root_path = Path(git_root).resolve(strict=False)
            branches = {item["name"] for item in git_branches(git_root_path)}
            if branch not in branches:
                return {"ok": False, "error": "unknown_branch"}
            dirty_files = git_changed_files(git_root_path)
            if dirty_files:
                return {
                    "ok": False,
                    "error": "dirty_worktree",
                    "message": "Commit ou stash requis avant de changer de branche.",
                    "files_changed": len(dirty_files),
                    "files": dirty_files,
                }
            result = subprocess.run(
                ("git", "switch", branch),
                cwd=str(git_root_path),
                text=True,
                capture_output=True,
                check=False,
                timeout=8,
            )
            if result.returncode != 0:
                return {"ok": False, "error": "git_switch_failed", "message": result.stderr.strip() or result.stdout.strip()}
            return self.git_payload()

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
            self._restore_active_session_if_needed()
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
            self._restore_active_session_if_needed()
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
            settings_store = SettingsStore(self.state.settings_path)
            settings = settings_store.load()
            status = _runtime_status(self.state)
            active_provider = self.state.active_provider or ProviderStore(self.state.provider_config_path).load().active_entry()
            return {
                "ok": True,
                "profiles": list(PROFILES),
                "reasoning_efforts": ["", "low", "medium", "high"],
                "profile": self.state.profile,
                "theme": settings.web_theme,
                "theme_persisted": settings_store.has_web_theme(),
                "provider_id": active_provider.id if active_provider is not None else "",
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
            theme = str(payload.get("theme") or "").strip()
            if theme:
                if not THEME_ID_RE.fullmatch(theme):
                    return {"ok": False, "error": "invalid_theme"}
                SettingsStore(self.state.settings_path).set_web_theme(theme)

            model = str(payload.get("model") or "").strip()
            provider_id = str(payload.get("provider_id") or "").strip()
            if provider_id:
                config = ProviderStore(self.state.provider_config_path).load()
                entry = next((item for item in config.entries if item.id == provider_id), None)
                if entry is None:
                    return {"ok": False, "error": "invalid_provider"}
                ProviderStore(self.state.provider_config_path).set_active(provider_id)
                self.state.active_provider = entry
                self.state.provider_kind = entry.provider
                self.state.base_url = entry.base_url
                self.state.api_key_ref = entry.api_key_ref
            update_model = "model" in payload or "provider_id" in payload
            update_reasoning = "reasoning_effort" in payload
            reasoning_effort = str(payload.get("reasoning_effort") or "").strip().lower()
            if update_reasoning and reasoning_effort not in {"", "low", "medium", "high"}:
                return {"ok": False, "error": "invalid_reasoning_effort"}
            if update_model:
                self.state.model = model
            if update_reasoning:
                self.state.reasoning_effort = reasoning_effort
            if self.state.active_provider is not None:
                metadata = dict(self.state.active_provider.metadata)
                if update_reasoning:
                    if reasoning_effort:
                        metadata["reasoning_effort"] = reasoning_effort
                    else:
                        metadata.pop("reasoning_effort", None)
                if update_model or update_reasoning:
                    self.state.active_provider = replace(
                        self.state.active_provider,
                        model=model or self.state.active_provider.model,
                        metadata=metadata,
                    )
                    ProviderStore(self.state.provider_config_path).upsert(self.state.active_provider, active=True)
            settings_store = SettingsStore(self.state.settings_path)
            settings = settings_store.load()
            status = _runtime_status(self.state)
            return {
                "ok": True,
                "profiles": list(PROFILES),
                "reasoning_efforts": ["", "low", "medium", "high"],
                "profile": self.state.profile,
                "theme": settings.web_theme,
                "theme_persisted": settings_store.has_web_theme(),
                "provider": status.provider,
                "model": status.model,
                "reasoning_effort": status.reasoning_effort,
                "provider_id": self.state.active_provider.id if self.state.active_provider is not None else "",
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
            _logger.warning("Failed to decode base64 image data")
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
            if self._pending_approval is not None:
                return {
                    "ok": False,
                    "error": "approval_pending",
                    "message": "Validation en attente. Autorise ou refuse l'action avant de lancer une nouvelle demande.",
                    "approval": _approval_payload(self._pending_approval),
                }
            if self._current_run_id:
                return {"ok": False, "error": "agent_busy", "message": "BB9 est déjà en action."}
            run_id = uuid.uuid4().hex
            self._current_run_id = run_id
            self._current_run_events = []
            self._cancel_current_run.clear()
            self._pending_approval = None
            self._approval_message = message

        events: list[TraceEvent] = []
        def record_event(event: TraceEvent) -> None:
            events.append(event)
            with self._lock:
                if self._current_run_id == run_id:
                    self._current_run_events.append(event)

        try:
            turn = runtime_service.run_message(
                self.state,
                message,
                ask_user=lambda decision, run_context: self._defer_approval(decision, run_context),
                on_event=record_event,
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
                self._approval_message = ""

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
            self._compact_current_session(force=False, context=turn.context)
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

    def run_events_payload(self) -> dict[str, Any]:
        with self._lock:
            return {
                "ok": True,
                "running": bool(self._current_run_id),
                "run_id": self._current_run_id,
                "events": [_event_payload(event) for event in self._current_run_events],
                "pending_approval": _approval_payload(self._pending_approval),
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
                self._compact_current_session(force=False, context=pending.context)
                self._persist_session()
                self._remember_turn("", answer, ())
                return {"ok": True, "answer": answer, "events": [], "artifacts": []}

            snapshot = capture_worktree_snapshot(Path.cwd())
            observation, events = execute_approved_action(pending.guardian, pending.context)
            answer = self._answer_after_approved_action(pending, observation)
            artifacts = runtime_service.artifacts_from_parts(
                observation.artifacts,
                events,
                snapshot,
                include_decision_trace=True,
            )
            self.state.session = self.state.session.with_message("assistant", answer)
            self._compact_current_session(force=False, context=pending.context)
            self._persist_session()
            self._remember_turn("", answer, artifacts)
            return {
                "ok": True,
                "answer": answer,
                "events": [_event_payload(event) for event in events],
                "artifacts": [_artifact_payload(artifact) for artifact in artifacts],
            }

    def _answer_after_approved_action(self, pending: "PendingApproval", observation) -> str:
        if not pending.message.strip() or pending.message.strip().startswith("/action "):
            return _approved_action_fallback(observation)

        tool = pending.guardian.action.name if pending.guardian.action is not None else ""
        cmd = str(pending.guardian.action.params.get("cmd", "")) if pending.guardian.action is not None else ""
        try:
            context = runtime_service.build_context(self.state)
            agent = context.agent or context_runtime.load_current_agent(self.state)
            provider = build_provider_for_agent(self.state, agent)
            decision = Kernel(provider=provider).decide(
                Intention(
                    text=(
                        pending.message
                        + "\n\n"
                        "L'action demandee a ete autorisee et executee. "
                        "Produis maintenant une reponse naturelle a l'utilisateur a partir de l'observation tool."
                    ),
                    metadata={
                        "tool_observations": (
                            {
                                "tool": tool,
                                "cmd": cmd,
                                "ok": str(observation.ok),
                                "output": observation.summary,
                            },
                        ),
                        "tool_limit_reached": True,
                    },
                ),
                replace(context, agent=agent),
            )
        except ProviderError:
            return _approved_action_fallback(observation)
        if decision.kind == "answer" and decision.summary.strip():
            return decision.summary.strip()
        return _approved_action_fallback(observation)

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

    def _compact_current_session(self, *, force: bool, context: RunContext | None = None):
        if context is None:
            try:
                context = runtime_service.build_context(self.state)
            except Exception:
                _logger.warning("Failed to build context for session compaction")
                context = None
        try:
            metadata = active_model_metadata(self.state, context.agent if context is not None else None)
            context_window = metadata.context_window_tokens
        except Exception:
            _logger.warning("Failed to get model metadata for compaction, using fallback 250k")
            context_window = 250_000
        config = CompactionConfig(
            context_window_tokens=context_window,
            soft_input_limit_tokens=0,
            trim_threshold=0.70,
        )
        result = (
            compact_session(self.state.session, force=True, config=config)
            if force
            else auto_compact_session(self.state.session, config=config)
        )
        if result.changed:
            self.state.session = result.session
        return result

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
            owners = owners_by_command.setdefault(str(entry["name"]), [])
            owner = f"{entry['source']}:{entry['owner']}"
            if owner not in owners:
                owners.append(owner)

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
            answer = self._context_answer(message)
            return {"ok": True, "session_id": self.state.session.id, "answer": answer, "events": [], "artifacts": []}
        if command == "/new":
            created = self.new_session()
            return {"ok": True, "session_id": created["session_id"], "answer": "Nouvelle session créée.", "events": [], "artifacts": []}
        if command == "/compact":
            result = self._compact_current_session(force=True)
            self._persist_session()
            return {"ok": True, "session_id": self.state.session.id, "answer": result.notice(), "events": [], "artifacts": []}
        if command == "/history":
            limit = positive_int(rest, default=20, max_value=80)
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

    def _context_answer(self, intention: str = "/context") -> str:
        context = runtime_service.build_context(self.state)
        provider = self.state.active_provider
        provider_label = provider.name if provider is not None else self.state.provider_kind
        model = (provider.model if provider is not None else self.state.model) or "-"
        reasoning = str(self.state.reasoning_effort or (provider.metadata.get("reasoning_effort") if provider else "") or "").strip()
        model_label = f"{provider_label} · {model}" + (f" · {reasoning}" if reasoning else "")
        archive_commands, collisions = self._archive_command_payloads()
        trusted = context.trusted_roots.roots if context.trusted_roots else ()
        effective_trusted = _effective_trusted_roots(context.workspace.root, self._active_project_path(), trusted)
        identity_parts = []
        if context.agent and context.agent.soul.strip():
            identity_parts.append("soul")
        if context.agent and context.agent.identity.strip():
            identity_parts.append("identity")
        try:
            metadata = active_model_metadata(self.state, context.agent)
            context_window = metadata.context_window_tokens
        except Exception:
            _logger.warning("Failed to get model metadata for context answer")
            context_window = 0
        lines = [
            "## Contexte courant",
            "",
            f"- Profil : `{context.permission_profile}`",
            f"- Modèle : `{model_label}`",
            f"- Agent : `{context.agent.name if context.agent else self.state.agent_name}`",
            f"- Session : `{context.session.id}`",
            f"- Contexte : `{(' · '.join(identity_parts) or '-')}`",
            f"- Workspace : `{context.workspace.root}`",
            f"- Projet actif : `{self._active_project_path()}`",
        ]
        workspace_summary = workspace_status_summary(context.workspace_status)
        if workspace_summary:
            lines.extend(["", "## État projet", ""])
            lines.extend(f"- {item}" for item in workspace_summary)
        lines.extend(
            [
                "",
                "## Archives actives",
                "",
                f"- Skills : `{_comma_names(skill.name for skill in context.skills)}`",
                f"- Tools : `{_comma_names(tool.name for tool in context.tools)}`",
                f"- Commandes : `{_comma_names(command['name'] for command in archive_commands)}`",
            ]
        )
        if collisions:
            lines.append(
                "- Collisions : `"
                + "; ".join(f"{item['name']} ({', '.join(item['owners'])})" for item in collisions)
                + "`"
            )
        lines.extend(
            [
                f"- Subagents : `{short_index_names(context.subagents_index) or '-'}`",
                f"- Trusted roots : `{len(effective_trusted)}` effectif(s), dont workspace/projet actif (`{len(trusted)}` persistant(s))",
                f"- Budget tools : `{tool_budget_for(context.permission_profile, context.agent.soul if context.agent else '')}` étape(s)",
                "",
                "## Mémoire courte",
                "",
                f"- Messages courts : `{len(context.session.messages)}`",
                f"- Sessions persistées : `{self._session_count()}`",
                f"- Messages visibles : `{self._visible_history_count()}`",
                f"- Compaction : `{context.session.compacted_count} message(s), ~{estimate_session_tokens(context.session)} tok"
                + (f" / {context_window}" if context_window else "")
                + "`",
                f"- Context index : `{len(context.context_index.splitlines())}` ligne(s)",
            ]
        )
        if context.session.messages:
            recent = " | ".join(short_message(message.as_prompt_line()) for message in context.session.messages[-4:])
            lines.append(f"- Récent : `{recent}`")
        lines.append("- Trace : `conversation`")
        lines.extend(["", *context_budget_lines(context, intention, context_window=context_window)])
        return "\n".join(lines)

    def _session_count(self) -> int:
        store = SessionStore(self.state.session_store_path)
        try:
            return store.count()
        finally:
            store.close()

    def _visible_history_count(self) -> int:
        store = VisibleHistoryStore(self.state.visible_history_path)
        try:
            return store.count()
        finally:
            store.close()

    def _defer_approval(self, decision: GuardianDecision, context: RunContext) -> ApprovalDecision:
        approval = PendingApproval(id=uuid.uuid4().hex, guardian=decision, context=context, message=self._approval_message)
        self._pending_approval = approval
        return ApprovalDecision(verdict="defer", summary="Validation requise.")

    def _history_messages(self) -> list[dict[str, Any]]:
        self._restore_active_session_if_needed()
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

    def _restore_active_session_if_needed(self) -> None:
        if self.state.session.source != "web":
            return
        if self.state.session.messages or self.state.session.compaction_summary.strip():
            return
        try:
            sessions = self._web_sessions_for_active_project()
        except Exception:
            _logger.warning("Failed to retrieve web sessions for active project")
            return
        if not sessions:
            return
        if any(session.id == self.state.session.id for session in sessions):
            return
        self.state.session = sessions[0].as_session()


def _runtime_status(state: ChatApiState) -> runtime_service.RuntimeStatus:
    try:
        return runtime_service.build_status(state)
    except Exception:
        _logger.warning("Failed to build runtime status, using fallback")
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


def _effective_trusted_roots(workspace: Path, active_project: Path, trusted_roots: tuple[Path, ...]) -> tuple[Path, ...]:
    roots: list[Path] = []
    for root in (workspace, active_project, *trusted_roots):
        resolved = Path(root).expanduser().resolve(strict=False)
        if resolved not in roots:
            roots.append(resolved)
    return tuple(roots)


def _comma_names(values) -> str:
    items = [str(value) for value in values if str(value)]
    return ", ".join(items) if items else "-"


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
    message: str = ""


def _approved_action_fallback(observation) -> str:
    if observation.ok:
        return f"Action exécutée. Observation : {observation.summary}"
    return f"Action exécutée mais en erreur. Observation : {observation.summary}"


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
