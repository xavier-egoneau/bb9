"""Reusable chat API service."""

from __future__ import annotations

import contextlib
import io
import logging
import os
import re
import subprocess
import threading
import time
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote

from bb9.cli.render import archive_command_parts, short_index_names, short_message
from bb9.core import context_runtime, runtime_service
from bb9.core import delegation as delegation_core
from bb9.core.agents import AgentNotFoundError
from bb9.core.approvals import ApprovalStore, default_approval_store_path, fingerprint_action, public_action_params
from bb9.core.compaction import CompactionConfig, auto_compact_session, compact_session, estimate_session_tokens
from bb9.core.diffs import capture_worktree_snapshot
from bb9.core.history import VisibleHistoryStore, default_visible_history_path
from bb9.core.kernel import Kernel
from bb9.core.loop import (
    ApprovalDecision,
    RunCancelled,
    continue_after_approved_action,
    execute_approved_action,
    tool_budget_for,
)
from bb9.core.markdown import command_aliases
from bb9.core.model_metadata import resolve_model_metadata
from bb9.core.models import (
    Artifact,
    GuardianDecision,
    Intention,
    Observation,
    PermissionProfile,
    RunContext,
    Session,
    Task,
    TraceEvent,
)
from bb9.core.paths import bb9_home, default_content_dir, product_root
from bb9.core.sessions import SessionStore, default_session_store_path
from bb9.core.settings import PROFILES, SettingsStore, default_settings_path
from bb9.core.skills import load_effective_skills
from bb9.core.tools import load_enabled_tools
from bb9.core.trace import decision_trace_artifact
from bb9.core.trust import TrustedRoots, trusted_root_candidate
from bb9.core.utils import positive_int, workspace_status_summary
from bb9.providers.config import (
    AUTH_API,
    PROVIDER_REGISTRY,
    ModelFetchError,
    ProviderConfig,
    ProviderEntry,
    ProviderStore,
    default_provider_config_path,
    fetch_models,
    normalize_api_key_ref_input,
)
from bb9.providers.providers import ProviderError
from bb9.providers.runtime import active_model_metadata, build_provider_for_agent
from bb9.templates.skills.dev import cli as dev_skill_cli
from bb9.templates.skills.plan import cli as plan_skill_cli

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
from .chat_uploads import save_uploaded_image

LIVE_EVENT_SUMMARY_LIMIT = 2_000
LIVE_EVENT_DATA_LIMIT = 1_000
APPROVAL_TIMEOUT_SECONDS = 300
PLAN_MARKDOWN_LIMIT = 16_000
ProcessCallback = Callable[..., None]
PLAN_TASK_RE = re.compile(r"^\s*-\s*\[([ xX])\]\s+((T\d+)\s+)?(.+?)\s*$")
PLAN_FIELD_RE = re.compile(r"^\s*(status|summary|blockers|evidence)\s*:\s*(.*?)\s*$", re.IGNORECASE)
AUTO_PLAN_PHRASES = (
    "strategie d execution",
    "decoupage de tache",
    "decouper la tache",
    "corriger et tester",
    "corrige et teste",
    "fix and test",
    "tests et documentation",
    "test et documentation",
    "plusieurs fichiers",
    "multiple files",
    "multi fichiers",
    "multi-fichiers",
    "longue tache",
    "long task",
    "feature complete",
    "fonctionnalite complete",
)
AUTO_PLAN_KEYWORDS = (
    "architecture",
    "feature",
    "fonctionnalite",
    "implement",
    "migration",
    "migrate",
    "workflow",
    "refactor",
    "refactorise",
    "refactoriser",
    "implemente",
    "implementer",
)
AUTO_PLAN_DESIGN_SYSTEM_TERMS = ("design system", "designsystem")
AUTO_PLAN_COMPONENT_TERMS = ("composant", "composants", "component", "components")
AUTO_PLAN_PROPOSAL_TERMS = ("new", "nouveau", "nouveaux", "nouvelle", "nouvelles", "propose", "proposer")
AUTO_PLAN_DIRECT_PHRASES = (
    "fais un plan",
    "fait un plan",
    "faire un plan",
    "prepare un plan",
    "preparer un plan",
    "lance un plan",
    "mode plan",
)
AUTO_PLAN_CONTINUATION_PHRASES = (
    "je veux tout ca",
    "veux tout ca",
    "fais tout ca",
    "fait tout ca",
    "faire tout ca",
    "go pour tout ca",
)
AUTO_PLAN_QUESTION_STARTS = (
    "comment",
    "est",
    "how",
    "pourquoi",
    "quand",
    "quel",
    "quelle",
    "quelles",
    "quels",
    "quoi",
    "what",
    "when",
    "where",
    "why",
)
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
    approval_store_path: Path = field(default_factory=default_approval_store_path)
    session_store_path: Path = field(default_factory=default_session_store_path)
    visible_history_path: Path = field(default_factory=default_visible_history_path)
    show_trace: bool = False
    active_project_path: str = ""
    restore_web_project: bool = False
    session: Session = field(default_factory=lambda: Session(source="web"))


class ChatApiApp:
    def __init__(self, state: ChatApiState | None = None) -> None:
        self.state = state or ChatApiState()
        settings = SettingsStore(self.state.settings_path).load()
        if not self.state.profile_explicit and self.state.profile == "safe":
            self.state.profile = settings.profile
        if not self.state.active_project_path and self.state.restore_web_project:
            startup_project = _startup_project_path(settings.web_project_path)
            if startup_project != Path.cwd().resolve(strict=False):
                try:
                    os.chdir(startup_project)
                except OSError:
                    startup_project = Path.cwd().resolve(strict=False)
            self.state.active_project_path = str(startup_project)
        if not self.state.active_project_path:
            self.state.active_project_path = str(Path.cwd().resolve(strict=False))
        self._lock = threading.RLock()
        self._run_lock = threading.Lock()
        self._cancel_current_run = threading.Event()
        self._current_run_id = ""
        self._current_run_events: list[TraceEvent] = []
        self._current_run_started_at = 0.0
        self._current_run_last_event_at = 0.0
        self._approval_message = ""
        self._pending_approval: PendingApproval | None = None
        self._status_cache: dict[str, Any] = {}
        self._restore_active_session_if_needed()

    def history_payload(self) -> dict[str, Any]:
        with self._lock:
            self._prune_pending_approval()
            messages = self._history_messages()
            return {
                "ok": True,
                "session_id": self.state.session.id,
                "active_project": str(self._active_project_path()),
                "messages": messages,
                "pending_approval": _approval_payload(self._pending_approval),
                "plan": self._current_plan_payload(),
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

    def providers_payload(self) -> dict[str, Any]:
        with self._lock:
            config = ProviderStore(self.state.provider_config_path).load()
            entries = [
                {
                    "id": entry.id,
                    "name": entry.name,
                    "provider": entry.provider,
                    "auth_type": entry.auth_type,
                    "base_url": entry.base_url,
                    "model": entry.model,
                    "active": config.active_id == entry.id,
                    "added_at": entry.added_at,
                }
                for entry in config.entries
            ]
            return {"ok": True, "providers": entries, "active_id": config.active_id}

    def add_provider(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            provider_kind = str(payload.get("provider") or "").strip()
            auth_type = str(payload.get("auth_type") or AUTH_API).strip()
            name = str(payload.get("name") or "").strip()
            base_url = str(payload.get("base_url") or "").strip()
            api_key = str(payload.get("api_key") or "").strip()
            if not provider_kind or provider_kind not in PROVIDER_REGISTRY:
                return {"ok": False, "error": "unknown_provider"}
            reg = PROVIDER_REGISTRY[provider_kind]
            if auth_type not in reg.supported_auth_types:
                return {"ok": False, "error": "unsupported_auth_type"}
            if not name:
                name = reg.label
            if not base_url:
                base_url = reg.default_base_url
            api_key_ref = ""
            if auth_type == AUTH_API and api_key:
                entry_id = ProviderEntry.new_id()
                api_key_ref, _ = normalize_api_key_ref_input(
                    api_key,
                    secret_name=f"provider_{entry_id}_key",
                )
                entry = ProviderEntry(
                    id=entry_id,
                    name=name,
                    provider=provider_kind,
                    auth_type=auth_type,
                    base_url=base_url,
                    api_key_ref=api_key_ref,
                )
            else:
                entry = ProviderEntry(
                    id=ProviderEntry.new_id(),
                    name=name,
                    provider=provider_kind,
                    auth_type=auth_type,
                    base_url=base_url,
                    api_key_ref=api_key_ref,
                )
            store = ProviderStore(self.state.provider_config_path)
            store.upsert(entry, active=True)
            self.state.active_provider = entry
            self.state.provider_kind = entry.provider
            self.state.base_url = entry.base_url
            self.state.api_key_ref = entry.api_key_ref
            return {"ok": True, "id": entry.id}

    def update_provider(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            provider_id = str(payload.get("id") or "").strip()
            if not provider_id:
                return {"ok": False, "error": "id_required"}
            name = str(payload.get("name") or "").strip()
            base_url = str(payload.get("base_url") or "").strip()
            api_key = str(payload.get("api_key") or "").strip()
            store = ProviderStore(self.state.provider_config_path)
            config = store.load()
            existing = next((e for e in config.entries if e.id == provider_id), None)
            if existing is None:
                return {"ok": False, "error": "not_found"}
            api_key_ref = existing.api_key_ref
            if api_key:
                api_key_ref, _ = normalize_api_key_ref_input(
                    api_key,
                    secret_name=f"provider_{provider_id}_key",
                )
            updated = ProviderEntry(
                id=existing.id,
                name=name or existing.name,
                provider=existing.provider,
                auth_type=existing.auth_type,
                base_url=base_url or existing.base_url,
                api_key_ref=api_key_ref,
                model=existing.model,
                metadata=existing.metadata,
                added_at=existing.added_at,
            )
            store.upsert(updated, active=config.active_id == existing.id)
            if self.state.active_provider is not None and self.state.active_provider.id == existing.id:
                self.state.active_provider = updated
                self.state.base_url = updated.base_url
                self.state.api_key_ref = updated.api_key_ref
            return {"ok": True, "id": updated.id}

    def delete_provider(self, provider_id: str) -> dict[str, Any]:
        with self._lock:
            if not provider_id:
                return {"ok": False, "error": "id_required"}
            store = ProviderStore(self.state.provider_config_path)
            config = store.load()
            entries = [e for e in config.entries if e.id != provider_id]
            if len(entries) == len(config.entries):
                return {"ok": False, "error": "not_found"}
            new_active = config.active_id
            if config.active_id == provider_id:
                new_active = entries[0].id if entries else ""
            store.save(ProviderConfig(active_id=new_active, entries=tuple(entries)))
            if self.state.active_provider is not None and self.state.active_provider.id == provider_id:
                self.state.active_provider = next((e for e in entries if e.id == new_active), None)
            return {"ok": True}

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
                    for project in store.projects(limit=120, filter_existing=True)
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
        target = Path(path)
        if not target.is_dir():
            return {"ok": False, "error": "project_not_found", "message": "Projet introuvable."}
        with self._lock:
            if self._current_run_id:
                return {"ok": False, "error": "agent_busy", "message": "BB9 est déjà en action."}
            self._prune_pending_approval()
            try:
                os.chdir(target)
            except OSError as exc:
                return {"ok": False, "error": "project_switch_failed", "message": str(exc)}
            self.state.active_project_path = str(target.resolve(strict=False))
            SettingsStore(self.state.settings_path).set_web_project_path(self.state.active_project_path)
            self._pending_approval = None
            self._status_cache = {}
            sessions = self._web_sessions_for_active_project()
            if sessions:
                self.state.session = sessions[0].as_session()
                messages = self._history_messages()
            else:
                self.state.session = Session(source="web")
                messages = []
            return {
                "ok": True,
                "active_project": str(self._active_project_path()),
                "workspace": str(Path.cwd().resolve(strict=False)),
                "session_id": self.state.session.id,
                "messages": messages,
                "sessions": [_session_payload(session, active=session.id == self.state.session.id) for session in sessions],
                "pending_approval": None,
                "plan": self._current_plan_payload(),
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
            self._refresh_profile_from_settings()
            self._prune_pending_approval()
            if self._current_run_id and self._status_cache:
                payload = dict(self._status_cache)
                payload.update(
                    {
                        "profile": self.state.profile,
                        "running": True,
                        "run_id": self._current_run_id,
                        **self._run_timing_payload(),
                        "pending_approval": _approval_payload(self._pending_approval),
                        "plan": self._current_plan_payload(),
                    }
                )
                return payload
            status = _runtime_status(self.state)
            payload = {
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
                **self._run_timing_payload(),
                "pending_approval": _approval_payload(self._pending_approval),
                "plan": self._current_plan_payload(),
            }
            self._status_cache = dict(payload)
            return payload

    def settings_payload(self) -> dict[str, Any]:
        with self._lock:
            self._refresh_profile_from_settings()
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
                "web_project_path": settings.web_project_path,
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
            model_metadata = None
            if update_model:
                selected_model = (
                    self.state.active_provider.model
                    if self.state.active_provider is not None
                    else self.state.model
                )
                if selected_model:
                    model_metadata = resolve_model_metadata(selected_model)
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
                "web_project_path": settings.web_project_path,
                "provider": status.provider,
                "model": status.model,
                "reasoning_effort": status.reasoning_effort,
                "provider_id": self.state.active_provider.id if self.state.active_provider is not None else "",
                "workspace": status.workspace,
                "active_project": str(self._active_project_path()),
                "context_window_tokens": model_metadata.context_window_tokens if model_metadata is not None else 0,
                "model_metadata_source": model_metadata.source if model_metadata is not None else "",
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
            self._prune_pending_approval()
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
            self._pending_approval = None
            return {"ok": True, "session_id": self.state.session.id, "messages": self._history_messages(), "pending_approval": None, "plan": self._current_plan_payload()}

    def new_session(self) -> dict[str, Any]:
        with self._lock:
            self._persist_session()
            self.state.session = Session(source="web")
            self._pending_approval = None
            self._persist_session()
            return {"ok": True, "session_id": self.state.session.id, "messages": [], "pending_approval": None, "plan": self._current_plan_payload()}

    def upload_image(self, *, mime: str, data: str) -> dict[str, Any]:
        return save_uploaded_image(mime=mime, data=data, workspace=Path.cwd())

    def _prepare_run_start(self, message: str) -> dict[str, Any] | None:
        if self._active_project_path() != Path.cwd().resolve(strict=False):
            return {
                "ok": False,
                "error": "project_view_only",
                "message": "Le projet actif affiché n'est pas le workspace d'exécution de ce serveur bb9 web.",
            }
        if self._pending_approval is not None:
            if self._prune_pending_approval():
                return {
                    "ok": True,
                    "session_id": self.state.session.id,
                    "answer": "Validation expirée (5 min), action refusée automatiquement.",
                    "events": [],
                    "artifacts": [],
                }
            return {
                "ok": False,
                "error": "approval_pending",
                "message": "Validation en attente. Autorise ou refuse l'action avant de lancer une nouvelle demande.",
                "approval": _approval_payload(self._pending_approval),
            }
        if self._current_run_id:
            return {"ok": False, "error": "agent_busy", "message": "BB9 est déjà en action."}
        self._current_run_id = uuid.uuid4().hex
        self._current_run_events = []
        now = time.monotonic()
        self._current_run_started_at = now
        self._current_run_last_event_at = now
        self._cancel_current_run.clear()
        self._pending_approval = None
        self._approval_message = message
        return None

    def _run_timing_payload(self) -> dict[str, int]:
        if not self._current_run_id or self._current_run_started_at <= 0:
            return {"run_age_seconds": 0, "run_idle_seconds": 0}
        now = time.monotonic()
        last_event = self._current_run_last_event_at or self._current_run_started_at
        return {
            "run_age_seconds": max(0, int(now - self._current_run_started_at)),
            "run_idle_seconds": max(0, int(now - last_event)),
        }

    def _clear_current_run(self) -> None:
        self._current_run_id = ""
        self._current_run_started_at = 0.0
        self._current_run_last_event_at = 0.0

    def run_message(self, text: str) -> dict[str, Any]:
        message = text.strip()
        if not message:
            return {"ok": False, "error": "empty_message"}
        long_command = _long_web_command(message)
        auto_plan = False
        with self._lock:
            self._refresh_profile_from_settings()
            auto_plan = (
                not long_command
                and _should_auto_plan_message(message)
                and not self._current_plan_payload().get("exists", False)
            )
            if long_command or auto_plan:
                blocked = self._prepare_run_start(message)
                if blocked is not None:
                    return blocked
                run_id = self._current_run_id
            else:
                run_id = ""
                command = self._handle_web_command(message)
                if command is not None:
                    return command
            if not long_command and not auto_plan:
                blocked = self._prepare_run_start(message)
                if blocked is not None:
                    return blocked
                run_id = self._current_run_id

        if long_command or auto_plan:
            try:
                def emit_process(title: str, detail: str = "", status: str = "en cours", **data: object) -> None:
                    self._append_run_process(run_id or "", title, detail=detail, status=status, **data)

                if auto_plan:
                    payload = self._run_plan_command(message, message=message, auto=True, on_process=emit_process)
                else:
                    payload = self._handle_web_command(message, on_process=emit_process) or {
                        "ok": False,
                        "error": "web_command_unsupported",
                        "message": f"Commande web non supportée: {message.split(maxsplit=1)[0]}",
                    }
                payload.setdefault("run_id", run_id)
                return payload
            except ProviderError as exc:
                return {"ok": False, "error": "provider_error", "message": str(exc), "run_id": run_id}
            except Exception as exc:
                return {"ok": False, "error": "runtime_error", "message": str(exc), "run_id": run_id}
            finally:
                with self._lock:
                    if self._current_run_id == run_id:
                        self._clear_current_run()
                        self._cancel_current_run.clear()
                    self._approval_message = ""

        events: list[TraceEvent] = []
        def record_event(event: TraceEvent) -> None:
            events.append(event)
            with self._lock:
                if self._current_run_id == run_id:
                    self._current_run_events.append(event)
                    self._current_run_last_event_at = time.monotonic()

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
                    self._clear_current_run()
                    self._cancel_current_run.clear()
                self._approval_message = ""

        with self._lock:
            answer = turn.answer
            trace_events = turn.result.trace or tuple(events)
            timings = dict(turn.timings)
            started = time.perf_counter()
            artifacts = runtime_service.artifacts_from_parts(
                turn.base_artifacts,
                trace_events,
                turn.snapshot,
                include_decision_trace=True,
            )
            timings["artifacts_ms"] = _elapsed_ms(started)
            self.state.session = self.state.session.with_message("user", message).with_message("assistant", answer)
            started = time.perf_counter()
            compaction_notice = self._auto_compact_with_notice(context=turn.context)
            self._persist_session()
            self._remember_turn(message, answer, artifacts)
            if compaction_notice:
                self._remember_notification(compaction_notice)
            timings["persist_ms"] = _elapsed_ms(started)
            return {
                "ok": True,
                "session_id": self.state.session.id,
                "run_id": run_id,
                "answer": answer,
                "notice": compaction_notice,
                "events": [_event_payload(event) for event in trace_events],
                "artifacts": [_artifact_payload(artifact) for artifact in artifacts],
                "timings": timings,
                "approval": _approval_payload(self._pending_approval),
                "plan": self._current_plan_payload(),
            }

    def run_events_payload(self, *, after: int = 0) -> dict[str, Any]:
        with self._lock:
            self._prune_pending_approval()
            if not self._current_run_id:
                return {
                    "ok": True,
                    "running": False,
                    "run_id": "",
                    "events": [],
                    "next": 0,
                    "total": 0,
                    "pending_approval": _approval_payload(self._pending_approval),
                }
            total = len(self._current_run_events)
            start = min(max(0, after), total)
            return {
                "ok": True,
                "running": bool(self._current_run_id),
                "run_id": self._current_run_id,
                **self._run_timing_payload(),
                "events": [_event_payload(event, live=True) for event in self._current_run_events[start:]],
                "next": total,
                "total": total,
                "pending_approval": _approval_payload(self._pending_approval),
            }

    def _append_run_process(
        self,
        run_id: str,
        title: str,
        *,
        detail: str = "",
        status: str = "en cours",
        **data: object,
    ) -> None:
        if not run_id or not title.strip():
            return
        payload = {"detail": detail.strip(), "status": status.strip() or "en cours"}
        for key, value in data.items():
            if value == "" or value is None:
                continue
            payload[key] = value
        event = TraceEvent(
            event_type="process",
            summary=title.strip(),
            session_id=self.state.session.id,
            data=payload,
        )
        with self._lock:
            if self._current_run_id == run_id:
                self._current_run_events.append(event)
                self._current_run_last_event_at = time.monotonic()

    def _current_run_event_payloads(self) -> list[dict[str, Any]]:
        with self._lock:
            return [_event_payload(event) for event in self._current_run_events]

    def _current_run_events_snapshot(self) -> tuple[TraceEvent, ...]:
        with self._lock:
            return tuple(self._current_run_events)

    def resolve_approval(
        self,
        approval_id: str,
        decision: str,
        *,
        remember: bool = False,
        trust_root: bool = False,
    ) -> dict[str, Any]:
        approval_id = approval_id.strip()
        verdict = decision.strip().lower()
        with self._lock:
            self._refresh_profile_from_settings()
            self._prune_pending_approval()
            pending = self._pending_approval
            if pending is None or pending.id != approval_id:
                return {"ok": False, "error": "approval_not_found"}
            if verdict not in {"allow", "deny"}:
                return {"ok": False, "error": "invalid_approval_decision"}
            if self._current_run_id:
                return {"ok": False, "error": "agent_busy", "message": "BB9 est déjà en action."}
            if trust_root and verdict == "allow":
                if not pending.trusted_root:
                    return {"ok": False, "error": "trusted_root_not_available"}
                try:
                    TrustedRoots.add(Path(pending.trusted_root))
                except ValueError as exc:
                    return {"ok": False, "error": "trusted_root_refused", "message": str(exc)}
            if remember and verdict == "allow" and pending.fingerprint and pending.guardian.action is not None:
                action = pending.guardian.action
                ApprovalStore(self.state.approval_store_path).record(
                    fingerprint=pending.fingerprint,
                    tool_name=action.name,
                    params=action.params,
                    workspace=pending.context.workspace.root,
                    reason=pending.guardian.reason,
                    risk=action.risk,
                    approved=verdict == "allow",
                    remembered=True,
                )
            self._pending_approval = None
            direct_denial = verdict == "deny" and (
                not pending.message.strip() or pending.message.strip().startswith("/action ")
            )
            if direct_denial:
                answer = "Action refusée."
                self.state.session = self.state.session.with_message("assistant", answer)
                compaction_notice = self._auto_compact_with_notice(context=pending.context)
                self._persist_session()
                self._remember_turn("", answer, ())
                if compaction_notice:
                    self._remember_notification(compaction_notice)
                return {"ok": True, "answer": answer, "notice": compaction_notice, "events": [], "artifacts": []}
            run_id = uuid.uuid4().hex
            self._current_run_id = run_id
            self._current_run_events = []
            now = time.monotonic()
            self._current_run_started_at = now
            self._current_run_last_event_at = now
            self._cancel_current_run.clear()

        events: list[TraceEvent] = []

        def record_event(event: TraceEvent) -> None:
            events.append(event)
            with self._lock:
                if self._current_run_id == run_id:
                    self._current_run_events.append(event)
                    self._current_run_last_event_at = time.monotonic()

        try:
            snapshot = capture_worktree_snapshot(Path.cwd())
            if verdict == "allow":
                observation, approved_events = execute_approved_action(pending.guardian, pending.context, on_event=record_event)
                events = list(approved_events or tuple(events))
            else:
                observation = _denied_approval_observation(pending)
                record_event(
                    TraceEvent(
                        event_type="guardian",
                        summary="user approval: deny",
                        session_id=self.state.session.id,
                        data={"verdict": "deny"},
                    )
                )
            if pending.build_resume is not None:
                build_result, build_output = self._resume_build_after_guardian_decision(
                    pending,
                    observation,
                    tuple(events),
                    on_event=record_event,
                )
                answer = dev_skill_cli.build_summary(build_result)
                events = list(self._current_run_events_snapshot())
                artifacts = _command_trace_artifacts(tuple(events))
                if build_output:
                    artifacts = (*artifacts, _build_output_artifact(build_output, build_result))
            else:
                if (
                    pending.guardian.action is not None
                    and pending.message.strip()
                    and not pending.message.strip().startswith("/action ")
                ):
                    result = self._continue_after_approved_action(pending, observation, tuple(events), on_event=record_event)
                    observation = result.observation or observation
                    events = list(result.trace)
                    answer = observation.summary if result.observation is not None else result.decision.summary
                else:
                    answer = self._answer_after_approved_action(pending, observation)
                artifacts = runtime_service.artifacts_from_parts(
                    observation.artifacts,
                    tuple(events),
                    snapshot,
                    include_decision_trace=True,
                )
        except ProviderError as exc:
            return {"ok": False, "error": "provider_error", "message": str(exc), "run_id": run_id}
        except Exception as exc:
            return {"ok": False, "error": "runtime_error", "message": str(exc), "run_id": run_id}
        finally:
            with self._lock:
                if self._current_run_id == run_id:
                    self._clear_current_run()
                    self._cancel_current_run.clear()

        with self._lock:
            self.state.session = self.state.session.with_message("assistant", answer)
            compaction_notice = self._auto_compact_with_notice(context=pending.context)
            self._persist_session()
            self._remember_turn("", answer, artifacts)
            if compaction_notice:
                self._remember_notification(compaction_notice)
            payload = {
                "ok": True,
                "run_id": run_id,
                "answer": answer,
                "notice": compaction_notice,
                "events": [_event_payload(event) for event in events],
                "artifacts": [_artifact_payload(artifact) for artifact in artifacts],
                "plan": self._current_plan_payload(),
            }
            approval_payload = _approval_payload(self._pending_approval)
            if approval_payload is not None:
                payload["approval"] = approval_payload
            return payload

    def _continue_after_approved_action(self, pending: PendingApproval, observation, events, on_event=None) -> Any:
        action = pending.guardian.action
        assert action is not None
        context = runtime_service.build_context(self.state)
        agent = context.agent or context_runtime.load_current_agent(self.state)
        provider = build_provider_for_agent(self.state, agent)
        self._approval_message = pending.message
        try:
            return continue_after_approved_action(
                Kernel(provider=provider),
                Intention(pending.message),
                replace(context, agent=agent, provider_for_agent=lambda worker: build_provider_for_agent(self.state, worker)),
                action,
                observation,
                initial_trace=events,
                ask_user=lambda decision, run_context: self._defer_approval(decision, run_context),
                on_event=on_event,
            )
        finally:
            self._approval_message = ""

    def _resume_build_after_guardian_decision(
        self,
        pending: PendingApproval,
        observation: Observation,
        events: tuple[TraceEvent, ...],
        on_event=None,
    ):
        action = pending.guardian.action
        resume = pending.build_resume
        assert action is not None
        assert resume is not None
        task = resume.task
        plan_path = resume.plan_path
        output_lines: list[str] = []

        def emit_build_line(line: str) -> None:
            text = str(line).strip()
            if not text:
                return
            output_lines.append(text)
            process = _skill_output_process("/build", text)
            if process is None or on_event is None:
                return
            on_event(
                TraceEvent(
                    event_type="process",
                    summary=str(process["title"]),
                    session_id=self.state.session.id,
                    data={
                        "detail": str(process.get("detail") or ""),
                        "status": str(process.get("status") or "en cours"),
                        **dict(process.get("data") or {}),
                    },
                )
            )

        def total_tasks() -> int:
            try:
                return len(_plan_tasks(plan_path.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                return 0

        agent = pending.context.agent or context_runtime.load_current_agent(self.state)
        provider = build_provider_for_agent(self.state, agent)
        context = replace(
            pending.context,
            agent=agent,
            provider_for_agent=lambda worker: build_provider_for_agent(self.state, worker),
        )
        self._approval_message = resume.message
        try:
            run_result = continue_after_approved_action(
                Kernel(provider=provider),
                Intention(delegation_core.task_prompt(task)),
                context,
                action,
                observation,
                initial_trace=events,
                ask_user=lambda decision, run_context: self._defer_approval(
                    decision,
                    run_context,
                    build_resume=resume,
                ),
                on_event=on_event,
                should_cancel=self._cancel_current_run.is_set,
            )
            task_result = delegation_core.task_result_from_run(task, run_result)
            report = dev_skill_cli.BuildTaskReport(
                task=task,
                result=task_result,
                title=task.title,
                worker=agent.name,
                trace=tuple(run_result.trace),
            )
            dev_skill_cli._print_result(task_result, task.title, {task.id: task.title}, emit=emit_build_line)
            if dev_skill_cli._report_needs_approval(report):
                emit_build_line(f"ask... validation guardian requise pour {task.title}")
                return (
                    dev_skill_cli.BuildResult(
                        plan_path=plan_path,
                        total=total_tasks(),
                        reports=(report,),
                        approval_pending=True,
                    ),
                    "\n".join(output_lines).strip(),
                )
            if not plan_path.is_file():
                return (
                    dev_skill_cli.BuildResult(
                        plan_path=plan_path,
                        total=0,
                        reports=(report,),
                        error="plan introuvable pendant la reprise",
                    ),
                    "\n".join(output_lines).strip(),
                )
            if task_result.status == "done":
                dev_skill_cli.mark_task_done(plan_path, task.id)
            dev_skill_cli.write_task_state(plan_path, task.id, task_result)
            if task_result.status != "done":
                return (
                    dev_skill_cli.BuildResult(
                        plan_path=plan_path,
                        total=total_tasks(),
                        reports=(report,),
                    ),
                    "\n".join(output_lines).strip(),
                )

            skill_cli = _WebSkillCli(self, build_message=resume.message, build_rest=resume.rest)
            tail_result = dev_skill_cli.build_plan(skill_cli, resume.rest, emit=emit_build_line)
            return (
                replace(tail_result, reports=(report, *tail_result.reports)),
                "\n".join(output_lines).strip(),
            )
        finally:
            self._approval_message = ""

    def _answer_after_approved_action(self, pending: PendingApproval, observation) -> str:
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

    def _remember_notification(self, content: str) -> None:
        if not content.strip():
            return
        store = VisibleHistoryStore(self.state.visible_history_path)
        try:
            store.append_message(
                session_id=self.state.session.id,
                role="notification",
                content=content,
                source="web",
                project_path=self._active_project_path(),
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

    def _auto_compact_with_notice(self, *, context: RunContext | None = None) -> str:
        result = self._compact_current_session(force=False, context=context)
        if not result.changed:
            return ""
        return _auto_compaction_notice(result)

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

    def _handle_web_command(
        self,
        message: str,
        *,
        on_process: ProcessCallback | None = None,
    ) -> dict[str, Any] | None:
        command, _, rest = message.partition(" ")
        if command == "/plan":
            return self._run_plan_command(rest, message=message, on_process=on_process)
        if command == "/build":
            return self._run_build_command(rest, message=message, on_process=on_process)
        if command not in {item[0] for item in NATIVE_REPL_COMMANDS}:
            return None
        if command == "/help":
            lines = ["Commandes disponibles :"]
            for item in self.commands_payload()["commands"]:
                suffix = " (non supportée en web)" if not item.get("supported", True) else ""
                lines.append(f"- `{item['name']}` : {item.get('description') or item.get('owner')}{suffix}")
            answer = "\n".join(lines)
            self._remember_command_turn(message, answer)
            return {"ok": True, "session_id": self.state.session.id, "answer": answer, "events": [], "artifacts": []}
        if command == "/context":
            answer = self._context_answer(message)
            self._remember_command_turn(message, answer)
            return {"ok": True, "session_id": self.state.session.id, "answer": answer, "events": [], "artifacts": []}
        if command == "/new":
            created = self.new_session()
            return {"ok": True, "session_id": created["session_id"], "answer": "Nouvelle session créée.", "events": [], "artifacts": [], "plan": created.get("plan")}
        if command == "/compact":
            result = self._compact_current_session(force=True)
            answer = result.notice()
            self._persist_session()
            self._remember_command_turn(message, answer)
            return {"ok": True, "session_id": self.state.session.id, "answer": answer, "events": [], "artifacts": []}
        if command == "/history":
            limit = positive_int(rest, default=20, max_value=80)
            messages = self._history_messages()[-limit:]
            answer = "Aucun historique visible pour cette session."
            if messages:
                answer = "\n\n".join(f"**{message['role']}**\n{message['content']}" for message in messages)
            self._remember_command_turn(message, answer)
            return {"ok": True, "session_id": self.state.session.id, "answer": answer, "events": [], "artifacts": []}
        return {
            "ok": False,
            "error": "web_command_unsupported",
            "message": f"La commande `{command}` existe dans le REPL mais n'a pas encore d'équivalent web direct.",
        }

    def _run_plan_command(
        self,
        rest: str,
        *,
        message: str = "/plan",
        auto: bool = False,
        on_process: ProcessCallback | None = None,
    ) -> dict[str, Any]:
        if self._active_project_path() != Path.cwd().resolve(strict=False):
            return {
                "ok": False,
                "error": "project_view_only",
                "message": "Le projet actif affiché n'est pas le workspace d'exécution de ce serveur bb9 web.",
            }
        if on_process is not None:
            on_process("Préparer le plan", "Transformation de la demande en plan courant.", "en cours")
        output = _capture_skill_output(
            lambda: plan_skill_cli._run(_WebSkillCli(self), rest),
            on_line=lambda line: _emit_skill_output_process(on_process, "/plan", line),
        )
        plan_path = Path.cwd() / ".bb9" / "plan.md"
        if not plan_path.is_file():
            if on_process is not None:
                on_process("Plan bloqué", output or "Plan non généré.", "erreur")
            return {
                "ok": False,
                "error": "plan_failed",
                "message": output or "Plan non généré.",
                "events": self._current_run_event_payloads(),
            }
        answer = "Plan prêt. Lance `/build` pour l'exécuter." if auto else "Plan prêt."
        if on_process is not None:
            on_process("Plan prêt", ".bb9/plan.md est à jour.", "terminé")
        trace_events = self._current_run_events_snapshot()
        artifacts = _command_trace_artifacts(trace_events)
        self._remember_command_turn(message, answer, artifacts=artifacts)
        return {
            "ok": True,
            "session_id": self.state.session.id,
            "answer": answer,
            "events": [_event_payload(event) for event in trace_events],
            "artifacts": [_artifact_payload(artifact) for artifact in artifacts],
            "plan": self._current_plan_payload(),
        }

    def _run_build_command(
        self,
        rest: str,
        *,
        message: str = "/build",
        on_process: ProcessCallback | None = None,
    ) -> dict[str, Any]:
        if self._active_project_path() != Path.cwd().resolve(strict=False):
            return {
                "ok": False,
                "error": "project_view_only",
                "message": "Le projet actif affiché n'est pas le workspace d'exécution de ce serveur bb9 web.",
            }
        plan_path = Path.cwd() / ".bb9" / "plan.md"
        if not plan_path.is_file():
            if on_process is not None:
                on_process("Plan introuvable", "Aucun plan courant à exécuter.", "erreur")
            return {
                "ok": False,
                "error": "plan_not_found",
                "message": "Aucun plan courant. Lance d'abord `/plan <demande>`.",
                "events": self._current_run_event_payloads(),
            }
        stripped = rest.strip()
        if on_process is not None:
            on_process("Lire le plan", "Exécution des prochaines tâches prêtes du plan courant.", "en cours")
        lines: list[str] = []
        line_lock = threading.Lock()

        def emit_build_line(line: str) -> None:
            text = str(line).strip()
            if not text:
                return
            with line_lock:
                lines.append(text)
            _emit_skill_output_process(on_process, "/build", text)

        skill_cli = _WebSkillCli(self, build_message=message, build_rest=stripped)
        if stripped.startswith("delegate"):
            output = _capture_skill_output(
                lambda: dev_skill_cli._run(skill_cli, stripped),
                on_line=lambda line: _emit_skill_output_process(on_process, "/build", line),
            )
            build_result = None
            answer = output.strip() or "Build terminé."
        else:
            build_result = dev_skill_cli.build_plan(skill_cli, stripped, emit=emit_build_line)
            output = "\n".join(lines).strip()
            answer = dev_skill_cli.build_summary(build_result)
        if on_process is not None:
            if build_result is not None and build_result.approval_pending:
                status = "en attente"
                title = "Validation requise"
            else:
                status = (
                    "erreur"
                    if (build_result.has_errors if build_result is not None else _skill_output_has_error(output))
                    else "terminé"
                )
                title = "Build bloqué" if status == "erreur" else "Build terminé"
            on_process(title, "La sortie finale résume les tâches traitées.", status)
        trace_events = self._current_run_events_snapshot()
        artifacts = _command_trace_artifacts(trace_events)
        if output:
            artifacts = (*artifacts, _build_output_artifact(output, build_result))
        self._remember_command_turn(message, answer, artifacts=artifacts)
        return {
            "ok": True,
            "session_id": self.state.session.id,
            "answer": answer,
            "events": [_event_payload(event) for event in trace_events],
            "artifacts": [_artifact_payload(artifact) for artifact in artifacts],
            "approval": _approval_payload(self._pending_approval),
            "plan": self._current_plan_payload(),
        }

    def clear_plan(self, project_path: str = "") -> dict[str, Any]:
        target = self._clear_plan_target(project_path)
        if target is None:
            return {"ok": False, "error": "invalid_project", "message": "Projet de plan invalide."}
        plan_path = target / ".bb9" / "plan.md"
        try:
            if plan_path.is_file():
                plan_path.unlink()
        except OSError as exc:
            return {"ok": False, "error": "plan_clear_failed", "message": str(exc)}
        return {"ok": True, "plan": self._current_plan_payload()}

    def _clear_plan_target(self, project_path: str = "") -> Path | None:
        raw = project_path.strip()
        target = Path(_normalize_project_path(raw) if raw else self._active_project_path()).resolve(strict=False)
        active = self._active_project_path()
        current = Path.cwd().resolve(strict=False)
        if target in {active, current}:
            return target
        store = SessionStore(self.state.session_store_path)
        try:
            visible = {
                str(project.get("path") or "")
                for project in store.projects(limit=120, filter_existing=True)
                if _project_is_visible(str(project.get("path") or ""), current=str(current), active=str(active))
            }
        finally:
            store.close()
        if str(target) in visible:
            return target
        return None

    def _remember_command_turn(self, message: str, answer: str, *, artifacts: tuple[Artifact, ...] = ()) -> None:
        with self._lock:
            self.state.session = self.state.session.with_message("user", message).with_message("assistant", answer)
            self._persist_session()
            self._remember_turn(message, answer, artifacts)

    def _current_plan_payload(self) -> dict[str, Any]:
        path = self._active_project_path() / ".bb9" / "plan.md"
        if not path.is_file():
            return {
                "exists": False,
                "project_path": str(self._active_project_path()),
                "markdown": "",
                "tasks": [],
                "completed": 0,
                "total": 0,
            }
        markdown = path.read_text(encoding="utf-8", errors="replace")
        tasks = _plan_tasks(markdown)
        completed = sum(1 for task in tasks if task["done"])
        return {
            "exists": True,
            "project_path": str(self._active_project_path()),
            "markdown": _clip_text(markdown, PLAN_MARKDOWN_LIMIT),
            "tasks": tasks,
            "completed": completed,
            "total": len(tasks),
            "updated_at": path.stat().st_mtime,
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

    def _defer_approval(
        self,
        decision: GuardianDecision,
        context: RunContext,
        *,
        build_resume: BuildApprovalResume | None = None,
    ) -> ApprovalDecision:
        action = decision.action
        fingerprint = ""
        trusted_root = ""
        if action is not None:
            fingerprint = fingerprint_action(action.name, action.params, context.workspace.root)
            remembered = ApprovalStore(self.state.approval_store_path).lookup(fingerprint)
            if remembered is not None:
                verdict = "allow" if remembered else "deny"
                return ApprovalDecision(verdict=verdict, summary="Validation mémorisée.", action=action)
        candidate = trusted_root_candidate(decision.reason)
        if candidate is not None:
            trusted_root = str(candidate)
        with self._lock:
            approval = PendingApproval(
                id=uuid.uuid4().hex,
                guardian=decision,
                context=context,
                created_at=time.time(),
                session_id=self.state.session.id,
                project_path=str(self._active_project_path()),
                message=self._approval_message,
                fingerprint=fingerprint,
                trusted_root=trusted_root,
                build_resume=build_resume,
            )
            self._pending_approval = approval
        return ApprovalDecision(verdict="defer", summary="Validation requise.")

    def _prune_pending_approval(self) -> bool:
        pending = self._pending_approval
        if pending is None:
            return False
        expired = time.time() - pending.created_at > APPROVAL_TIMEOUT_SECONDS
        out_of_scope = pending.session_id != self.state.session.id or pending.project_path != str(self._active_project_path())
        if expired or out_of_scope:
            self._pending_approval = None
            return expired
        return False

    def _refresh_profile_from_settings(self) -> None:
        if self.state.profile_explicit:
            return
        self.state.profile = SettingsStore(self.state.settings_path).load().profile

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
                if message.source == "web" and message.role in {"user", "assistant", "notification"}
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


def _auto_compaction_notice(result: Any) -> str:
    return (
        "Auto-compaction du contexte court : "
        f"{result.compacted_messages} ancien(s) message(s) résumés, "
        f"{len(result.session.messages)} message(s) récent(s) conservés."
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


def _startup_project_path(project_path: str) -> Path:
    path = Path(project_path).expanduser().resolve(strict=False) if project_path.strip() else Path.cwd().resolve(strict=False)
    if path.is_dir():
        return path
    return Path.cwd().resolve(strict=False)


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


def _long_web_command(message: str) -> bool:
    command = message.strip().split(maxsplit=1)[0] if message.strip() else ""
    return command in {"/plan", "/build"}


def _should_auto_plan_message(message: str) -> bool:
    if message.lstrip().startswith("/"):
        return False
    normalized = _normalize_for_auto_plan(message)
    if len(normalized) < 12:
        return False
    first = normalized.split(maxsplit=1)[0] if normalized else ""
    if first in AUTO_PLAN_QUESTION_STARTS:
        return False
    if _is_direct_plan_request(normalized) or _is_continuation_plan_request(normalized):
        return True
    if any(phrase in normalized for phrase in AUTO_PLAN_PHRASES):
        return True
    if _is_design_system_component_plan_request(normalized):
        return True
    return any(keyword in normalized.split() for keyword in AUTO_PLAN_KEYWORDS)


def _is_direct_plan_request(normalized: str) -> bool:
    return any(phrase in normalized for phrase in AUTO_PLAN_DIRECT_PHRASES)


def _is_continuation_plan_request(normalized: str) -> bool:
    return any(phrase in normalized for phrase in AUTO_PLAN_CONTINUATION_PHRASES)


def _is_design_system_component_plan_request(normalized: str) -> bool:
    tokens = set(normalized.split())
    return (
        any(term in normalized for term in AUTO_PLAN_DESIGN_SYSTEM_TERMS)
        and any(term in tokens for term in AUTO_PLAN_COMPONENT_TERMS)
        and any(term in tokens for term in AUTO_PLAN_PROPOSAL_TERMS)
    )


def _normalize_for_auto_plan(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_text).split())


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


def _event_payload(event: TraceEvent, *, live: bool = False) -> dict[str, Any]:
    return {
        "type": event.event_type,
        "summary": _clip_text(event.summary, LIVE_EVENT_SUMMARY_LIMIT) if live else event.summary,
        "time": event.time,
        "data": _clip_event_data(event.data) if live else event.data,
    }


def _clip_event_data(data: dict[str, Any]) -> dict[str, Any]:
    clipped: dict[str, Any] = {}
    for key, value in data.items():
        clipped[key] = _clip_text(value, LIVE_EVENT_DATA_LIMIT) if isinstance(value, str) else value
    return clipped


def _clip_text(value: str, limit: int) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 24)].rstrip() + "\n...[live truncated]"


def _plan_tasks(markdown: str) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in str(markdown or "").splitlines():
        match = PLAN_TASK_RE.match(line)
        if match:
            _normalize_plan_task(current)
            done = match.group(1).lower() == "x"
            task_id = match.group(3) or ""
            title = match.group(4).strip()
            current = {"id": task_id, "title": title, "done": done, "status": "done" if done else ""}
            tasks.append(current)
            continue
        if current is None:
            continue
        field_match = PLAN_FIELD_RE.match(line)
        if not field_match:
            continue
        key = field_match.group(1).lower()
        value = field_match.group(2).strip()
        if key == "status":
            current[key] = value.lower()
        else:
            current[key] = value
    _normalize_plan_task(current)
    return tasks


def _normalize_plan_task(task: dict[str, Any] | None) -> None:
    if task is None:
        return
    status = str(task.get("status") or "").strip().lower()
    summary_status = delegation_core.explicit_status_from_summary(str(task.get("summary") or ""))
    if status == "done" or summary_status == "done":
        task["status"] = "done"
        task["done"] = True
        return
    if status == "error" and _plan_task_dependency_only(task):
        task["status"] = "blocked"


def _plan_task_dependency_only(task: dict[str, Any]) -> bool:
    if _dependency_skip_summary(str(task.get("summary") or "")):
        return True
    blockers = [
        item.strip()
        for item in re.split(r"[;,]\s*", str(task.get("blockers") or ""))
        if item.strip()
    ]
    return bool(blockers) and all(blocker.startswith("dependency:") for blocker in blockers)


def _dependency_skip_summary(value: str) -> bool:
    summary = " ".join(str(value or "").lower().split())
    return "dependencies are not done" in summary or "dependencies could not be resolved" in summary


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


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
class BuildApprovalResume:
    message: str
    rest: str
    plan_path: Path
    task: Task
    worker_name: str = ""


@dataclass(frozen=True)
class PendingApproval:
    id: str
    guardian: GuardianDecision
    context: RunContext
    created_at: float
    session_id: str
    project_path: str
    message: str = ""
    fingerprint: str = ""
    trusted_root: str = ""
    build_resume: BuildApprovalResume | None = None


def _approved_action_fallback(observation) -> str:
    if observation.ok:
        return f"Action exécutée. Observation : {observation.summary}"
    return f"Action exécutée mais en erreur. Observation : {observation.summary}"


def _denied_approval_observation(pending: PendingApproval) -> Observation:
    action = pending.guardian.action
    tool = action.name if action is not None else "action"
    return Observation(
        ok=False,
        summary=(
            f"Action refusée par l'utilisateur : `{tool}`. "
            f"Raison guardian : {pending.guardian.reason}. "
            "Cherche une autre approche sans cette action ; si aucune alternative n'est suffisante, explique le blocage."
        ),
        data={
            "guardian_verdict": "deny",
            "guardian_reason": pending.guardian.reason,
            "blockers": ("user_denied_approval", pending.guardian.reason),
            "next_suggestion": "Chercher une autre action autorisée ou expliquer le blocage.",
        },
    )


def _capture_skill_output(fn, *, on_line: Callable[[str], None] | None = None) -> str:
    buffer = _LiveOutputCapture(on_line=on_line)
    with contextlib.redirect_stdout(buffer):
        fn()
        buffer.flush()
    return buffer.getvalue().strip()


class _LiveOutputCapture(io.StringIO):
    def __init__(self, *, on_line: Callable[[str], None] | None = None) -> None:
        super().__init__()
        self._on_line = on_line
        self._pending = ""
        self._line_lock = threading.Lock()

    def write(self, value: str) -> int:
        text = str(value)
        with self._line_lock:
            written = super().write(text)
            self._pending += text
            self._emit_complete_lines()
            return written

    def flush(self) -> None:
        with self._line_lock:
            super().flush()
            if self._pending.strip():
                self._safe_emit(self._pending)
            self._pending = ""

    def _emit_complete_lines(self) -> None:
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            self._safe_emit(line)

    def _safe_emit(self, line: str) -> None:
        if self._on_line is None:
            return
        text = " ".join(str(line).split())
        if not text:
            return
        try:
            self._on_line(text)
        except Exception:
            _logger.warning("Failed to emit live skill output", exc_info=True)


def _emit_skill_output_process(
    on_process: ProcessCallback | None,
    command: str,
    line: str,
) -> None:
    if on_process is None:
        return
    event = _skill_output_process(command, line)
    if event is None:
        return
    on_process(
        str(event["title"]),
        str(event.get("detail") or ""),
        str(event.get("status") or "en cours"),
        **dict(event.get("data") or {}),
    )


def _skill_output_process(command: str, line: str) -> dict[str, object] | None:
    text = " ".join(str(line).split())
    if not text:
        return None
    if text.startswith("plan..."):
        if text == "plan... écrit":
            return _skill_process("Plan écrit", text, "terminé")
        if text == "plan... error":
            return _skill_process("Plan bloqué", text, "erreur")
        if command == "/build":
            return _skill_process("Lire le plan", text, "en cours")
        return _skill_process("Écrire le plan", text, "en cours")
    if text.startswith("parallel..."):
        detail = text.removeprefix("parallel...").strip()
        return _skill_process(
            "Lancer une vague parallèle",
            detail,
            "en cours",
            process_kind="subagents_batch",
            task_titles=[item.strip() for item in detail.split(" et ") if item.strip()],
        )
    if text.startswith("task..."):
        detail = text.removeprefix("task...").strip()
        label, separator, state = detail.rpartition(":")
        if separator:
            task_title = label.strip()
            state = state.strip()
            if state.startswith("start"):
                worker = state.removeprefix("start").strip()
                if worker:
                    return _skill_process(
                        "Subagent utilisé",
                        f"`{worker}` pour `{task_title}`",
                        "en cours",
                        process_kind="subagent",
                        subagent_status="running",
                        worker=worker,
                        task_title=task_title,
                    )
                return _skill_process(
                    "Tâche lancée",
                    task_title,
                    "en cours",
                    process_kind="subagent",
                    subagent_status="running",
                    task_title=task_title,
                )
            if state == "done":
                return _skill_process(
                    "Tâche terminée",
                    task_title,
                    "terminé",
                    process_kind="subagent",
                    subagent_status="done",
                    task_title=task_title,
                )
            if state == "error":
                return _skill_process(
                    "Tâche en erreur",
                    task_title,
                    "erreur",
                    process_kind="subagent",
                    subagent_status="error",
                    task_title=task_title,
                )
        return _skill_process("Tâche en cours", detail, "en cours")
    if text.startswith(("blocker...", "blk...")):
        _, _, detail = text.partition("...")
        detail = detail.strip()
        if not _meaningful_blocker_detail(detail):
            return None
        category = _blocker_category(detail)
        status = "bloqué" if category == "dependency" else "erreur"
        return _skill_process("Blocage détecté", detail, status, block_category=category)
    return None


def _meaningful_blocker_detail(detail: str) -> bool:
    value = re.sub(r"[.\s]+$", "", str(detail).strip().lower())
    return bool(value) and value not in {
        "evidence",
        "evidence:",
        "status",
        "status:",
        "summary",
        "summary:",
        "blockers",
        "blockers:",
    }


def _blocker_category(detail: str) -> str:
    value = str(detail or "").strip().lower()
    if value.startswith("dependency:") or re.search(r"la tâche ['\"].+['\"] n['’']est pas terminée", value):
        return "dependency"
    return "direct"


def _skill_process(title: str, detail: str, status: str, **data: object) -> dict[str, object]:
    return {"title": title, "detail": detail, "status": status, "data": data}


def _skill_output_has_error(output: str) -> bool:
    normalized = "\n" + "\n".join(line.strip() for line in output.splitlines()) + "\n"
    return any(
        marker in normalized
        for marker in ("\nplan... error\n", ": error\n", "\nblocker...", "\nblk...")
    )


def _command_trace_artifacts(events: tuple[TraceEvent, ...]) -> tuple[Artifact, ...]:
    trace = decision_trace_artifact(events)
    if trace is None:
        return ()
    return (trace,)


def _build_output_artifact(output: str, build_result: Any | None = None) -> Artifact:
    metadata: dict[str, Any] = {
        "content": output,
        "default_hidden": True,
        "note": "Sortie technique brute de /build, conservée pour diagnostic.",
    }
    if build_result is not None and hasattr(dev_skill_cli, "build_output_metadata"):
        metadata.update(dev_skill_cli.build_output_metadata(build_result))
    return Artifact(
        kind="report",
        title="Sortie /build",
        source="skill:dev",
        metadata=metadata,
    )


class _WebSkillCli:
    def __init__(self, app: ChatApiApp, *, build_message: str = "", build_rest: str = "") -> None:
        self.app = app
        self.state = app.state
        self.build_message = build_message
        self.build_rest = build_rest
        self._build_scope = threading.local()

    @property
    def serial_build_for_approvals(self) -> bool:
        return self.state.profile == "safe"

    def build_context(self) -> RunContext:
        return runtime_service.build_context(self.state)

    def build_provider(self):
        context = self.build_context()
        agent = context.agent or context_runtime.load_current_agent(self.state)
        return build_provider_for_agent(self.state, agent)

    def build_provider_for_agent(self, agent):
        return build_provider_for_agent(self.state, agent)

    def ask_guardian(self, decision, context) -> ApprovalDecision:
        return self.app._defer_approval(decision, context, build_resume=self._current_build_resume())

    def begin_build_task(self, task: Task, plan_path: Path | None, worker_name: str = "") -> None:
        self._build_scope.task = task
        self._build_scope.plan_path = plan_path
        self._build_scope.worker_name = worker_name

    def end_build_task(self, task: Task) -> None:
        if getattr(self._build_scope, "task", None) is task:
            self._build_scope.task = None
            self._build_scope.plan_path = None
            self._build_scope.worker_name = None

    def _current_build_resume(self) -> BuildApprovalResume | None:
        task = getattr(self._build_scope, "task", None)
        plan_path = getattr(self._build_scope, "plan_path", None)
        worker_name = getattr(self._build_scope, "worker_name", "") or ""
        if task is None or plan_path is None:
            return None
        return BuildApprovalResume(
            message=self.build_message or "/build",
            rest=self.build_rest,
            plan_path=Path(plan_path),
            task=task,
            worker_name=str(worker_name),
        )

    def run_intention(self, text: str) -> None:
        turn = runtime_service.run_message(self.state, text, ask_user=self.ask_guardian)
        print(turn.answer)


def _approval_payload(approval: PendingApproval | None) -> dict[str, Any] | None:
    if approval is None:
        return None
    action = approval.guardian.action
    payload = {
        "id": approval.id,
        "reason": approval.guardian.reason,
        "tool": action.name if action is not None else "",
        "params": public_action_params(action.params) if action is not None else {},
        "risk": action.risk if action is not None else "",
        "rememberable": bool(approval.fingerprint),
        "trusted_root_candidate": approval.trusted_root,
    }
    if approval.build_resume is not None:
        payload["scope"] = "build"
        payload["task_id"] = approval.build_resume.task.id
        payload["task_title"] = approval.build_resume.task.title
        if approval.build_resume.worker_name:
            payload["worker"] = approval.build_resume.worker_name
    return payload
