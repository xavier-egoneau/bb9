from __future__ import annotations

import base64
import importlib
import io
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote
from urllib.request import Request, urlopen

from bb9.api.chat import ChatApiApp, ChatApiState, _plan_tasks, _skill_output_process
from bb9.api.http import chat_api_server
from bb9.cli.main import (
    Cli,
    CliState,
)
from bb9.cli.render import (
    CliActivityIndicator,
    CliTheme,
    fit_words,
    render_cli_diff_artifact,
    render_cli_markdown,
    strip_ansi,
)
from bb9.core import context_runtime, runtime_service
from bb9.core.agents import refresh_subagents_index
from bb9.core.approvals import ApprovalStore, fingerprint_action
from bb9.core.context_index import refresh_context_index
from bb9.core.gateway import execute
from bb9.core.history import VisibleHistoryStore
from bb9.core.kernel import Kernel
from bb9.core.loop import RunCancelled, run_once, tool_budget_for
from bb9.core.models import (
    Action,
    AgentProfile,
    Artifact,
    Decision,
    Intention,
    Observation,
    RunContext,
    Session,
    Skill,
    TaskResult,
    ToolSpec,
    TraceEvent,
    Workspace,
)
from bb9.core.paths import ensure_user_agents
from bb9.core.projects import resolve_project_target, workspace_safety_warning, workspace_switch_from_text
from bb9.core.sessions import AGENT_HOME_SOURCE, SessionStore, agent_home_session_id
from bb9.core.settings import SettingsStore, UserSettings
from bb9.core.tool_runtime import load_tool_module
from bb9.core.workspace_status import build_workspace_status
from bb9.providers.config import AUTH_API, ProviderConfig, ProviderEntry, ProviderStore


class BoundaryTests(unittest.TestCase):
    def test_context_index_protects_workspace_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "README.md").write_text("# Demo\n", encoding="utf-8")
            (workspace / ".cache").mkdir()
            (workspace / ".cache" / "secret.txt").write_text("hidden\n", encoding="utf-8")

            index = refresh_context_index(workspace)

            self.assertIn("README.md", index)
            self.assertNotIn("secret.txt", index)
            self.assertTrue((workspace / ".bb9" / "context-index.md").is_file())
            self.assertEqual("*\n", (workspace / ".bb9" / ".gitignore").read_text(encoding="utf-8"))

    def test_workspace_status_reports_volatile_project_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "README.md").write_text("# Demo\n", encoding="utf-8")
            (workspace / "package.json").write_text(
                json.dumps({"scripts": {"test": "node --test", "dev": "vite"}}),
                encoding="utf-8",
            )
            (workspace / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
            context_index = refresh_context_index(workspace)

            status = build_workspace_status(workspace, context_index=context_index)

            self.assertIn("# Workspace Status", status)
            self.assertIn("Package manager: pnpm", status)
            self.assertIn("`dev`", status)
            self.assertIn("`test`", status)
            self.assertIn("Governance: `README.md`", status)
            self.assertIn("Read state:", status)

    def test_runtime_service_builds_shared_status_for_surfaces(self) -> None:
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            workspace.mkdir()
            agents.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            state = CliState(
                profile_explicit=True,
                agents_dir=root / "agents",
                skills_dir=root / "skills",
                tools_dir=root / "tools",
                provider_kind="echo",
                session=Session(source="cli"),
            )
            try:
                os.chdir(workspace)
                status = runtime_service.build_status(state)
            finally:
                os.chdir(cwd)

            self.assertEqual(str(workspace.resolve()), status.workspace)
            self.assertEqual("cli", status.source)
            self.assertIn("# Workspace Status", status.workspace_status)
            self.assertFalse((workspace / ".bb9" / "context-index.md").exists())

    def test_runtime_service_runs_message_with_shared_context(self) -> None:
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            workspace.mkdir()
            agents.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            state = CliState(
                profile_explicit=True,
                agents_dir=root / "agents",
                skills_dir=root / "skills",
                tools_dir=root / "tools",
                provider_kind="echo",
                session=Session(source="cli"),
            )
            try:
                os.chdir(workspace)
                turn = runtime_service.run_message(state, "salut")
            finally:
                os.chdir(cwd)

            self.assertEqual("salut", turn.answer)
            self.assertEqual(str(workspace.resolve()), str(turn.context.workspace.root.resolve()))
            self.assertIn("# Workspace Status", turn.context.workspace_status)
            self.assertEqual(1, turn.timings["light_context"])
            self.assertEqual("", turn.context.context_index)
            self.assertIsNone(turn.snapshot.root)

    def test_light_context_keeps_tools_and_skills_visible(self) -> None:
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            workspace.mkdir()
            agents.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            state = CliState(
                profile_explicit=True,
                agents_dir=root / "agents",
                skills_dir=root / "skills",
                tools_dir=Path(__file__).resolve().parents[1] / "bb9" / "tools",
                provider_kind="echo",
                session=Session(source="cli"),
            )
            try:
                os.chdir(workspace)
                # "agenda" question: simple chat, but the model must still see caldav.
                turn = runtime_service.run_message(state, "tu peux me dire ce que j'ai dans l'agenda ?")
            finally:
                os.chdir(cwd)

            self.assertEqual(1, turn.timings["light_context"])
            self.assertIn("caldav", [tool.name for tool in turn.context.tools])
            self.assertIn("`caldav`", turn.context.tools_index)
            # Light still skips the expensive workspace scans.
            self.assertEqual("", turn.context.context_index)
            self.assertNotIn("## Files", turn.context.workspace_status)

    def test_runtime_service_reloads_agent_home_session_from_store_before_turn(self) -> None:
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            workspace.mkdir()
            agents.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            store_path = root / "sessions.db"

            # Another surface (e.g. Telegram) persisted a turn in the shared agent home.
            store = SessionStore(store_path)
            try:
                home = store.ensure_agent_home("default").as_session()
                home = home.with_message("user", "message envoyé depuis telegram")
                home = home.with_message("assistant", "réponse envoyée depuis telegram")
                store.store(home, project_path=None)
            finally:
                store.close()

            state = CliState(
                profile_explicit=True,
                agents_dir=root / "agents",
                skills_dir=root / "skills",
                tools_dir=root / "tools",
                provider_kind="echo",
                session_store_path=store_path,
                # Stale in-memory copy of the same agent-home session, without the telegram turn.
                session=Session(id=agent_home_session_id("default"), source=AGENT_HOME_SOURCE),
            )
            try:
                os.chdir(workspace)
                turn = runtime_service.run_message(state, "salut")
            finally:
                os.chdir(cwd)

            self.assertEqual("salut", turn.answer)
            contents = [message.content for message in state.session.messages]
            self.assertIn("message envoyé depuis telegram", contents)
            self.assertIn("réponse envoyée depuis telegram", contents)
            self.assertEqual([message.content for message in turn.context.session.messages][:2], contents[:2])

    def test_project_switch_request_resolves_known_project_and_remainder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "tests"
            workspace.mkdir()
            store = SessionStore(root / "sessions.db")
            try:
                store.store(Session(id="tests-web", source="web"), project_path=workspace)
            finally:
                store.close()

            request = workspace_switch_from_text("mets-toi sur le projet tests et fais une critique")
            assert request is not None
            resolution = resolve_project_target(
                request.target,
                session_store_path=root / "sessions.db",
                settings_path=root / "settings.json",
                cwd=root,
            )

            self.assertEqual("tests", request.target)
            self.assertEqual("fais une critique", request.remainder)
            self.assertTrue(resolution.ok)
            self.assertEqual(workspace.resolve(), resolution.path)

    def test_workspace_safety_warning_flags_home_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)

            warning = workspace_safety_warning(home, home=home)

            self.assertIn("dossier utilisateur", warning)

    def test_runtime_context_uses_active_project_path_without_chdir(self) -> None:
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launcher = root / "launcher"
            workspace = root / "tests"
            agents = root / "agents" / "default"
            launcher.mkdir()
            workspace.mkdir()
            agents.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            state = ChatApiState(
                profile_explicit=True,
                agents_dir=root / "agents",
                skills_dir=root / "skills",
                tools_dir=root / "tools",
                provider_kind="echo",
                active_project_path=str(workspace),
                session=Session(source="agent_home"),
            )
            try:
                os.chdir(launcher)
                turn = runtime_service.run_message(state, "salut")
                process_cwd = Path.cwd().resolve(strict=False)
            finally:
                os.chdir(cwd)

            self.assertEqual("salut", turn.answer)
            self.assertEqual(workspace.resolve(), turn.context.workspace.root.resolve())
            self.assertEqual(launcher.resolve(), process_cwd)

    def test_web_chat_channel_runs_turn_and_keeps_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents"
            skills = root / "skills"
            tools = root / "tools"
            workspace.mkdir()
            (agents / "default").mkdir(parents=True)
            skills.mkdir()
            tools.mkdir()
            (agents / "default" / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            local_skill = workspace / ".bb9" / "skills" / "local"
            local_skill.mkdir(parents=True)
            (local_skill / "SKILL.md").write_text(
                "# Local\n\n## Résumé\n\nCommande locale.\n\n## Commandes\n\n- `/local` : commande projet.\n",
                encoding="utf-8",
            )
            local_theme = workspace / ".bb9" / "themes" / "web"
            local_theme.mkdir(parents=True)
            (local_theme / "contrast.css").write_text(
                ":root[data-theme=\"contrast\"] { --bg: #000; --text: #fff; }\n",
                encoding="utf-8",
            )
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        profile="power",
                        profile_explicit=True,
                        agents_dir=agents,
                        skills_dir=skills,
                        tools_dir=tools,
                        settings_path=root / "settings.json",
                        session_store_path=root / "sessions.db",
                        visible_history_path=root / "history.db",
                    )
                )
                payload = app.run_message("bonjour web")
            finally:
                os.chdir(cwd)

            self.assertTrue(payload["ok"])
            self.assertEqual("bonjour web", payload["answer"])
            self.assertEqual(2, len(app.state.session.messages))
            self.assertEqual("web", app.state.session.source)

            history = app.history_payload()
            self.assertEqual(["user", "assistant"], [item["role"] for item in history["messages"]])
            self.assertEqual("bonjour web", history["messages"][0]["content"])
            projects = app.projects_payload()
            self.assertTrue(projects["ok"])
            self.assertEqual(str(workspace.resolve()), projects["active_project"])
            self.assertTrue(any(project["path"] == str(workspace.resolve()) for project in projects["projects"]))
            self.assertEqual(len({project["path"] for project in projects["projects"]}), len(projects["projects"]))
            home_channel = next(project for project in projects["channels"] if project.get("kind") == "agent_home")
            self.assertEqual("agent-home:default", home_channel["channel_id"])
            self.assertEqual("Accueil · default", home_channel["label"])
            self.assertEqual(0, home_channel["message_count"])

            home = app.switch_project("agent-home:default")
            self.assertTrue(home["ok"])
            self.assertEqual(str(workspace.resolve()), home["active_project"])
            self.assertEqual(str(Path.cwd().resolve()), home["workspace"])
            self.assertEqual("agent-home:default", home["session_id"])
            self.assertEqual("agent_home", app.state.session.source)
            home_sessions = app.sessions_payload()
            self.assertEqual(["agent-home:default"], [session["id"] for session in home_sessions["sessions"]])

            plan_path = workspace / ".bb9" / "plan.md"
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_text("# BB9 Plan\n\n- [x] T1 Ancien plan\n", encoding="utf-8")

            other_project = root / "other"
            other_project.mkdir()
            store = SessionStore(root / "sessions.db")
            try:
                store.store(
                    Session(id="other-web", source="web").with_message("user", "autre projet", max_messages=10),
                    project_path=other_project,
                )
            finally:
                store.close()

            cwd_before_switch = Path.cwd()
            try:
                switched = app.switch_project(str(other_project))
                self.assertTrue(switched["ok"])
                self.assertEqual(str(other_project.resolve()), switched["active_project"])
                self.assertEqual(str(other_project.resolve()), switched["workspace"])
                self.assertEqual("other-web", switched["session_id"])
                self.assertEqual(["user"], [item["role"] for item in switched["messages"]])
                self.assertEqual("autre projet", switched["messages"][0]["content"])
                self.assertFalse(switched["plan"]["exists"])
                self.assertEqual(str(other_project.resolve()), switched["plan"]["project_path"])
                self.assertEqual(str(other_project.resolve()), str(Path.cwd().resolve()))
                other_sessions = app.sessions_payload()
                self.assertEqual(["other-web"], [session["id"] for session in other_sessions["sessions"]])
                response = app.run_message("ne pas exécuter ailleurs")
                self.assertTrue(response["ok"])
                self.assertEqual("ne pas exécuter ailleurs", response["answer"])
                self.assertTrue(plan_path.is_file())
            finally:
                os.chdir(cwd_before_switch)

    def test_web_chat_natural_project_switch_runs_remainder_in_project(self) -> None:
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launcher = root / "launcher"
            workspace = root / "tests"
            agents = root / "agents" / "default"
            launcher.mkdir()
            workspace.mkdir()
            agents.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            try:
                os.chdir(launcher)
                app = ChatApiApp(
                    ChatApiState(
                        agents_dir=root / "agents",
                        skills_dir=root / "skills",
                        tools_dir=root / "tools",
                        session_store_path=root / "sessions.db",
                        visible_history_path=root / "history.db",
                        settings_path=root / "settings.json",
                    )
                )
                payload = app.run_message("mets-toi sur le projet tests et bonjour projet")
                history = app.history_payload()
                process_cwd = Path.cwd().resolve(strict=False)
            finally:
                os.chdir(cwd)

            self.assertTrue(payload["ok"])
            self.assertIn("Workspace actif", payload["answer"])
            self.assertIn("bonjour projet", payload["answer"])
            self.assertEqual(workspace.resolve(), process_cwd)
            self.assertEqual(str(workspace.resolve()), payload.get("active_project", app.state.active_project_path))
            self.assertEqual("mets-toi sur le projet tests et bonjour projet", history["messages"][0]["content"])

    def test_web_chat_models_payload_lists_configured_providers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = ProviderEntry(
                id="p1",
                name="OpenAI",
                provider="openai",
                auth_type="web",
                model="gpt-test",
            )
            second = ProviderEntry(
                id="p2",
                name="Local",
                provider="ollama",
                auth_type=AUTH_API,
                base_url="http://127.0.0.1:9/v1",
                model="llama-test",
            )
            ProviderStore(root / "providers.json").save(ProviderConfig(active_id="p2", entries=(first, second)))
            app = ChatApiApp(ChatApiState(provider_config_path=root / "providers.json"))

            payload = app.models_payload()

            self.assertTrue(payload["ok"])
            self.assertEqual("p2", payload["active_provider_id"])
            self.assertEqual(["OpenAI", "Local"], [provider["name"] for provider in payload["providers"]])
            self.assertIn("gpt-test", payload["providers"][0]["models"])
            self.assertIn("llama-test", payload["providers"][1]["models"])

    def test_web_chat_loads_persisted_profile_and_theme_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            SettingsStore(root / "settings.json").save(UserSettings(profile="power", web_theme="fjord"))

            app = ChatApiApp(ChatApiState(settings_path=root / "settings.json"))
            payload = app.settings_payload()

            self.assertEqual("power", app.state.profile)
            self.assertEqual("power", payload["profile"])
            self.assertEqual("fjord", payload["theme"])
            self.assertTrue(payload["theme_persisted"])

    def test_settings_store_round_trips_project_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = SettingsStore(root / "settings.json")
            a = root / "alpha"
            b = root / "beta"
            a.mkdir()
            b.mkdir()
            store.set_projects((str(a), str(b), str(a)))
            loaded = store.load()
            self.assertEqual((str(a.resolve()), str(b.resolve())), loaded.projects)
            # Updating the registry preserves theme and profile.
            store.set_web_theme("fjord")
            self.assertEqual((str(a.resolve()), str(b.resolve())), store.load().projects)
            self.assertEqual("fjord", store.load().web_theme)

    def test_update_projects_add_delete_edit_registry(self) -> None:
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            alpha = root / "alpha"
            moved = root / "alpha-moved"
            workspace.mkdir()
            alpha.mkdir()
            moved.mkdir()
            agents.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        profile="power",
                        profile_explicit=True,
                        agents_dir=root / "agents",
                        skills_dir=root / "skills",
                        tools_dir=root / "tools",
                        settings_path=root / "settings.json",
                        session_store_path=root / "sessions.db",
                        visible_history_path=root / "history.db",
                    )
                )
                added = app.update_projects({"op": "add", "path": str(alpha)})
                missing = app.update_projects({"op": "add", "path": str(root / "ghost")})
                edited = app.update_projects({"op": "edit", "path": str(alpha), "new_path": str(moved)})
                registry_after_edit = SettingsStore(root / "settings.json").load().projects
                deleted = app.update_projects({"op": "delete", "path": str(moved)})
                registry_after_delete = SettingsStore(root / "settings.json").load().projects
            finally:
                os.chdir(cwd)

            self.assertTrue(added["ok"])
            self.assertTrue(any(p["path"] == str(alpha.resolve()) and p["registered"] for p in added["projects"]))
            self.assertEqual("project_not_found", missing["error"])
            self.assertTrue(edited["ok"])
            self.assertIn(str(moved.resolve()), registry_after_edit)
            self.assertNotIn(str(alpha.resolve()), registry_after_edit)
            self.assertTrue(deleted["ok"])
            self.assertEqual((), registry_after_delete)

    def test_delete_detected_project_hides_it_from_payload(self) -> None:
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            detected = root / "detected"
            workspace.mkdir()
            detected.mkdir()
            agents.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            # Seed a past session pointing at `detected` so it shows up as detected.
            store = SessionStore(root / "sessions.db")
            try:
                store.store(Session(source="web"), project_path=detected)
            finally:
                store.close()
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        profile="power",
                        profile_explicit=True,
                        agents_dir=root / "agents",
                        skills_dir=root / "skills",
                        tools_dir=root / "tools",
                        settings_path=root / "settings.json",
                        session_store_path=root / "sessions.db",
                        visible_history_path=root / "history.db",
                    )
                )
                before = app.projects_payload()
                deleted = app.update_projects({"op": "delete", "path": str(detected)})
                after = app.projects_payload()
            finally:
                os.chdir(cwd)

            self.assertTrue(any(p["path"] == str(detected.resolve()) for p in before["projects"]))
            self.assertTrue(deleted["ok"])
            self.assertFalse(any(p["path"] == str(detected.resolve()) for p in after["projects"]))
            hidden = SettingsStore(root / "settings.json").load().hidden_projects
            self.assertIn(str(detected.resolve()), hidden)

    def test_cannot_delete_active_project(self) -> None:
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            workspace.mkdir()
            agents.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        profile="power",
                        profile_explicit=True,
                        agents_dir=root / "agents",
                        skills_dir=root / "skills",
                        tools_dir=root / "tools",
                        settings_path=root / "settings.json",
                        session_store_path=root / "sessions.db",
                        visible_history_path=root / "history.db",
                    )
                )
                result = app.update_projects({"op": "delete", "path": str(workspace)})
            finally:
                os.chdir(cwd)

            self.assertEqual("project_active", result["error"])

    def test_projects_payload_marks_registered_projects(self) -> None:
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            extra = root / "extra"
            workspace.mkdir()
            extra.mkdir()
            agents.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            SettingsStore(root / "settings.json").set_projects((str(extra),))
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        profile="power",
                        profile_explicit=True,
                        agents_dir=root / "agents",
                        skills_dir=root / "skills",
                        tools_dir=root / "tools",
                        settings_path=root / "settings.json",
                        session_store_path=root / "sessions.db",
                        visible_history_path=root / "history.db",
                    )
                )
                payload = app.projects_payload()
            finally:
                os.chdir(cwd)

            by_path = {project["path"]: project for project in payload["projects"]}
            self.assertIn(str(extra.resolve()), by_path)
            self.assertTrue(by_path[str(extra.resolve())]["registered"])
            # The runtime workspace is present but not part of the durable registry.
            self.assertFalse(by_path[str(workspace.resolve())]["registered"])

    def test_notes_store_round_trips_notes_and_todos(self) -> None:
        from bb9.core import notes as notes_store

        with tempfile.TemporaryDirectory() as tmp:
            agents = Path(tmp) / "agents"
            (agents / "default").mkdir(parents=True)
            notes_store.add_todo(agents, "default", "Préparer la démo")
            notes_store.add_todo(agents, "default", "Relire le rapport")
            notes_store.set_todo_done(agents, "default", 0, True)
            todos = notes_store.read_todos(agents, "default")
            self.assertEqual([(0, True), (1, False)], [(t.index, t.done) for t in todos])

            note = notes_store.write_note(agents, "default", "Idées Projet", "- piste A\n- piste B", title="Idées")
            self.assertEqual("idees-projet", note.slug)
            self.assertEqual("Idées", note.title)
            self.assertIn("piste A", notes_store.read_note(agents, "default", "idees-projet").content)
            # Files really live under the agent folder.
            self.assertTrue((agents / "default" / "notes" / "idees-projet.md").is_file())
            self.assertTrue((agents / "default" / "TODO.md").is_file())

            context = notes_store.build_agent_notes_context(agents, "default")
            self.assertIn("Relire le rapport", context)
            self.assertIn("idees-projet", context)

            self.assertTrue(notes_store.delete_note(agents, "default", "idees-projet"))
            self.assertEqual((), notes_store.list_notes(agents, "default"))

    def test_notes_tool_uses_agent_dir_from_context(self) -> None:
        module = load_tool_module("notes", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            agents = Path(tmp) / "agents"
            (agents / "default").mkdir(parents=True)
            context = RunContext(
                session=Session(),
                workspace=Workspace(root=Path(tmp)),
                permission_profile="power",
                agents_dir=agents,
                agent=AgentProfile(name="default"),
            )
            allow = module.review(module.action_from_text("todo-add Tester"), context)
            self.assertEqual("allow", allow.verdict)
            module.execute(module.action_from_text("todo-add Tester le tool"), context)
            written = module.execute(module.action_from_text('write memo text="point clé" title=Mémo'), context)
            self.assertTrue(written.ok)
            self.assertTrue((agents / "default" / "notes" / "memo.md").is_file())
            read = module.execute(module.action_from_text("read memo"), context)
            self.assertIn("point clé", read.summary)
            invalid = module.review(module.action_from_text("frobnicate"), context)
            self.assertEqual("block", invalid.verdict)

    def test_notes_injected_into_kernel_context(self) -> None:
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            workspace.mkdir()
            agents.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            from bb9.core import notes as notes_store

            notes_store.add_todo(root / "agents", "default", "Tâche injectée")
            state = CliState(
                profile_explicit=True,
                agents_dir=root / "agents",
                skills_dir=root / "skills",
                tools_dir=Path(__file__).resolve().parents[1] / "bb9" / "tools",
                provider_kind="echo",
                session=Session(source="cli"),
            )
            try:
                os.chdir(workspace)
                context = context_runtime.build_context(state)
            finally:
                os.chdir(cwd)

            self.assertIn("Tâche injectée", context.notes_context)

    def test_web_notes_and_todos_crud(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents"
            (agents / "default").mkdir(parents=True)
            (agents / "default" / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            app = ChatApiApp(
                ChatApiState(
                    profile="limited",
                    profile_explicit=True,
                    agents_dir=agents,
                    skills_dir=root / "skills",
                    tools_dir=root / "tools",
                    settings_path=root / "settings.json",
                    session_store_path=root / "sessions.db",
                    visible_history_path=root / "history.db",
                )
            )
            empty = app.notes_payload()
            self.assertEqual([], empty["todos"])
            added = app.update_todo({"op": "add", "text": "Acheter du café"})
            self.assertEqual("Acheter du café", added["todos"][0]["text"])
            toggled = app.update_todo({"op": "toggle", "index": 0, "done": True})
            self.assertTrue(toggled["todos"][0]["done"])
            note = app.update_note({"op": "write", "slug": "memo", "content": "# Memo\n\nlignes"})
            self.assertEqual("memo", note["notes"][0]["slug"])
            self.assertIn("lignes", note["notes"][0]["content"])
            removed = app.update_note({"op": "delete", "slug": "memo"})
            self.assertEqual([], removed["notes"])
            bad = app.update_todo({"op": "toggle", "index": 9, "done": True})
            self.assertEqual("invalid_todo", bad["error"])

    def test_web_chat_exposes_live_run_events_payload(self) -> None:
        app = ChatApiApp(ChatApiState())
        event = TraceEvent(
            event_type="action",
            summary="shell",
            session_id=app.state.session.id,
            data={"tool": "shell", "cmd": "pwd"},
        )
        with app._lock:
            app._current_run_id = "run-test"
            app._current_run_events = [event]
            app._current_run_started_at = time.monotonic() - 25
            app._current_run_last_event_at = time.monotonic() - 17

        payload = app.run_events_payload()

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["running"])
        self.assertEqual("run-test", payload["run_id"])
        self.assertEqual(1, payload["next"])
        self.assertEqual(1, payload["total"])
        self.assertGreaterEqual(payload["run_age_seconds"], 20)
        self.assertGreaterEqual(payload["run_idle_seconds"], 10)
        self.assertEqual("action", payload["events"][0]["type"])
        self.assertEqual("pwd", payload["events"][0]["data"]["cmd"])

        self.assertEqual([], app.run_events_payload(after=1)["events"])

    def test_web_chat_idle_run_events_do_not_replay_previous_run_events(self) -> None:
        app = ChatApiApp(ChatApiState())
        event = TraceEvent(
            event_type="observation",
            summary="old shell ok",
            session_id=app.state.session.id,
            data={"tool": "shell", "ok": "True"},
        )
        with app._lock:
            app._current_run_id = ""
            app._current_run_events = [event]

        payload = app.run_events_payload()

        self.assertTrue(payload["ok"])
        self.assertFalse(payload["running"])
        self.assertEqual("", payload["run_id"])
        self.assertEqual([], payload["events"])
        self.assertEqual(0, payload["next"])
        self.assertEqual(0, payload["total"])

    def test_web_chat_live_run_events_are_truncated(self) -> None:
        app = ChatApiApp(ChatApiState())
        event = TraceEvent(
            event_type="observation",
            summary="x" * 3000,
            session_id=app.state.session.id,
            data={"tool": "shell", "output": "y" * 2000},
        )
        with app._lock:
            app._current_run_id = "run-test"
            app._current_run_events = [event]

        payload = app.run_events_payload()

        self.assertIn("[live truncated]", payload["events"][0]["summary"])
        self.assertIn("[live truncated]", payload["events"][0]["data"]["output"])

    def test_web_chat_updates_theme_without_resetting_runtime_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = ChatApiApp(
                ChatApiState(
                    profile="power",
                    profile_explicit=True,
                    model="gpt-demo",
                    reasoning_effort="high",
                    settings_path=root / "settings.json",
                )
            )

            payload = app.update_settings({"theme": "paper"})

            self.assertTrue(payload["ok"])
            self.assertEqual("power", payload["profile"])
            self.assertEqual("paper", payload["theme"])
            self.assertTrue(payload["theme_persisted"])
            self.assertEqual("gpt-demo", payload["model"])
            self.assertEqual("high", payload["reasoning_effort"])
            self.assertEqual("paper", SettingsStore(root / "settings.json").load().web_theme)

    def test_web_chat_restores_latest_project_session_on_start(self) -> None:
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            session = Session(source="web").with_message("user", "salut", max_messages=10).with_message("assistant", "bonjour", max_messages=10)
            store = SessionStore(root / "sessions.db")
            try:
                store.store(session, project_path=workspace)
            finally:
                store.close()
            visible = VisibleHistoryStore(root / "history.db")
            try:
                visible.append_turn(
                    session_id=session.id,
                    user_text="salut",
                    assistant_text="bonjour",
                    source="web",
                    project_path=workspace,
                )
            finally:
                visible.close()
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        session_store_path=root / "sessions.db",
                        visible_history_path=root / "history.db",
                    )
                )
                history = app.history_payload()
                sessions = app.sessions_payload()
            finally:
                os.chdir(cwd)

            self.assertEqual(session.id, history["session_id"])
            self.assertEqual(["user", "assistant"], [message["role"] for message in history["messages"]])
            self.assertEqual("salut", history["messages"][0]["content"])
            self.assertEqual(session.id, sessions["active_session_id"])
            self.assertTrue(sessions["sessions"][0]["active"])

    def test_web_chat_restores_last_selected_project_on_start(self) -> None:
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            open_ui = root / "open-ui"
            tests_project = root / "tests"
            open_ui.mkdir()
            tests_project.mkdir()
            SettingsStore(root / "settings.json").save(
                UserSettings(profile="power", web_theme="fjord", web_project_path=str(tests_project))
            )
            try:
                os.chdir(open_ui)
                app = ChatApiApp(ChatApiState(settings_path=root / "settings.json", restore_web_project=True))
                status = app.status_payload()
            finally:
                os.chdir(cwd)

            self.assertEqual(str(tests_project.resolve()), status["workspace"])
            self.assertEqual(str(tests_project.resolve()), status["active_project"])

    def test_web_chat_switch_project_persists_last_selected_project(self) -> None:
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            other = root / "other"
            workspace.mkdir()
            other.mkdir()
            try:
                os.chdir(workspace)
                app = ChatApiApp(ChatApiState(settings_path=root / "settings.json"))
                payload = app.switch_project(str(other))
            finally:
                os.chdir(cwd)

            self.assertTrue(payload["ok"])
            self.assertEqual(str(other.resolve()), SettingsStore(root / "settings.json").load().web_project_path)

    def test_web_chat_commands_payload_keeps_build_and_plan_archive_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents" / "default"
            skills = root / "skills"
            agents.mkdir(parents=True)
            (skills / "dev").mkdir(parents=True)
            (skills / "plan").mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            templates = Path(__file__).resolve().parents[1] / "bb9" / "templates" / "skills"
            (skills / "dev" / "SKILL.md").write_text(
                (templates / "dev" / "SKILL.md").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (skills / "plan" / "SKILL.md").write_text(
                (templates / "plan" / "SKILL.md").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            local_skill = root / "workspace" / ".bb9" / "skills" / "project-workflow"
            local_skill.mkdir(parents=True)
            (local_skill / "SKILL.md").write_text(
                "---\nname: project-workflow\ncommands: project-map, project-review\n---\n# Project Workflow\n\n## Résumé\n\nLocal.\n",
                encoding="utf-8",
            )
            cwd = Path.cwd()
            os.chdir(root / "workspace")
            app = ChatApiApp(ChatApiState(agents_dir=root / "agents", skills_dir=skills))

            try:
                payload = app.commands_payload()
            finally:
                os.chdir(cwd)

            names = [command["name"] for command in payload["commands"]]
            collisions = {collision["name"] for collision in payload["collisions"]}
            self.assertIn("/build", names)
            self.assertNotIn("/build delegate", names)
            self.assertIn("/plan", names)
            self.assertEqual(1, names.count("/plan"))
            self.assertNotIn("/plan ...", names)
            self.assertIn("/project-map", names)
            self.assertIn("/project-review", names)
            self.assertIn("/explore", names)
            self.assertNotIn("/build", collisions)

    def test_web_chat_skills_payload_and_toggle_use_active_agent_disabled_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            skills = root / "skills"
            workspace.mkdir()
            agents.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            (skills / "global-skill").mkdir(parents=True)
            (skills / "global-skill" / "SKILL.md").write_text(
                "# Global Skill\n\n## Résumé\n\nGlobal.\n\n## Commandes\n\n- `/global-skill` : global.\n",
                encoding="utf-8",
            )
            local = workspace / ".bb9" / "skills" / "local-skill"
            local.mkdir(parents=True)
            (local / "SKILL.md").write_text(
                "# Local Skill\n\n## Résumé\n\nLocal.\n",
                encoding="utf-8",
            )
            disabled_file = agents / "SKILLS_DISABLED.md"
            disabled_file.write_text(
                "# Skills Disabled\n\nLes skills sont actifs par défaut.\n",
                encoding="utf-8",
            )
            app = ChatApiApp(
                ChatApiState(
                    agents_dir=root / "agents",
                    skills_dir=skills,
                    active_project_path=str(workspace),
                )
            )

            payload = app.skills_payload()
            by_name = {f"{item['source']}:{item['name']}": item for item in payload["skills"]}

            self.assertTrue(payload["ok"])
            self.assertTrue(by_name["global:global-skill"]["enabled"])
            self.assertTrue(by_name["global:global-skill"]["active"])
            self.assertEqual("local", by_name["local:local-skill"]["source"])

            disabled = app.toggle_skill({"name": "global-skill", "enabled": False})

            self.assertTrue(disabled["ok"])
            disabled_text = disabled_file.read_text(encoding="utf-8")
            self.assertIn("Les skills sont actifs par défaut.", disabled_text)
            self.assertIn("- `global-skill`", disabled_text)
            global_skill = next(item for item in disabled["skills"] if item["name"] == "global-skill")
            self.assertFalse(global_skill["enabled"])
            self.assertFalse(global_skill["active"])

            enabled = app.toggle_skill({"name": "global-skill", "enabled": True})

            self.assertTrue(enabled["ok"])
            enabled_text = disabled_file.read_text(encoding="utf-8")
            self.assertIn("Les skills sont actifs par défaut.", enabled_text)
            self.assertNotIn("- `global-skill`", enabled_text)
            global_skill = next(item for item in enabled["skills"] if item["name"] == "global-skill")
            self.assertTrue(global_skill["enabled"])

    def test_web_chat_update_skill_writes_raw_skill_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            skills = root / "skills"
            skill_dir = skills / "demo"
            workspace.mkdir()
            agents.mkdir(parents=True)
            skill_dir.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            skill_path = skill_dir / "SKILL.md"
            skill_path.write_text(
                "---\nactivation: /demo\ncommands: demo\n---\n# Demo\n\n## Résumé\n\nAncien.\n",
                encoding="utf-8",
            )
            app = ChatApiApp(
                ChatApiState(
                    agents_dir=root / "agents",
                    skills_dir=skills,
                    active_project_path=str(workspace),
                )
            )

            updated_text = "---\nactivation: /demo\ncommands: demo\n---\n# Demo\n\n## Résumé\n\nNouveau.\n"
            payload = app.update_skill({"name": "demo", "source": "global", "body": updated_text})

            self.assertTrue(payload["ok"])
            self.assertEqual(updated_text, skill_path.read_text(encoding="utf-8"))
            self.assertIn("Nouveau.", (skills / "INDEX.md").read_text(encoding="utf-8"))
            demo = next(item for item in payload["skills"] if item["name"] == "demo")
            self.assertEqual(updated_text, demo["body"])
            self.assertIn("`/demo`", demo["commands"])

    def test_web_plan_command_updates_current_plan_payload(self) -> None:
        class PlanProvider:
            def complete(self, _prompt: str, *, images=()) -> str:
                return (
                    "# BB9 Plan\n\n"
                    "Objective: livrer une page\n\n"
                    "## Tasks\n\n"
                    "- [ ] T1 Créer la page\n"
                    "  worker: default\n"
                    "  parallelizable: false\n"
                    "  paths: src/page.html\n"
                    "  depends:\n"
                    "  goal: Créer la page.\n"
                    "  context: Demande utilisateur.\n"
                    "  expected: Page prête.\n"
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            workspace.mkdir()
            agents.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        agents_dir=root / "agents",
                        skills_dir=root / "skills",
                        tools_dir=root / "tools",
                        session_store_path=root / "sessions.db",
                        visible_history_path=root / "history.db",
                        settings_path=root / "settings.json",
                    )
                )
                with patch("bb9.api.chat.build_provider_for_agent", return_value=PlanProvider()):
                    payload = app.run_message("/plan livrer une page")
                plan = (workspace / ".bb9" / "plan.md").read_text(encoding="utf-8")
                history = app.history_payload()
                status = app.status_payload()
            finally:
                os.chdir(cwd)

        self.assertTrue(payload["ok"])
        self.assertEqual("Plan prêt.", payload["answer"])
        self.assertTrue(payload["plan"]["exists"])
        self.assertEqual(1, payload["plan"]["total"])
        self.assertEqual("Créer la page", payload["plan"]["tasks"][0]["title"])
        self.assertTrue(history["plan"]["exists"])
        self.assertTrue(status["plan"]["exists"])
        self.assertEqual(["user", "assistant"], [message["role"] for message in history["messages"]])
        self.assertEqual("/plan livrer une page", history["messages"][0]["content"])
        self.assertEqual("Plan prêt.", history["messages"][1]["content"])
        self.assertIn("process", [event["type"] for event in payload["events"]])
        self.assertIn("Plan prêt", [event["summary"] for event in payload["events"]])
        self.assertIn("# BB9 Plan", plan)
        self.assertIn("T1 Créer la page", plan)

    def test_web_plan_parser_normalizes_legacy_done_summary_and_dependency_blocks(self) -> None:
        tasks = _plan_tasks(
            "# BB9 Plan\n\n"
            "- [ ] T1 Auditer\n"
            "  status: error\n"
            "  summary: Status: done Analyse terminée.\n"
            "  blockers: Status: done\n\n"
            "- [ ] T2 Suite\n"
            "  depends: T1\n"
            "  status: error\n"
            "  summary: Task skipped because dependencies could not be resolved.\n"
            "  blockers: dependency:T1\n\n"
            "- [ ] T3 Sans blocker explicite\n"
            "  status: error\n"
            "  summary: Task skipped because dependencies could not be resolved.\n"
        )

        self.assertEqual("done", tasks[0]["status"])
        self.assertTrue(tasks[0]["done"])
        self.assertEqual("blocked", tasks[1]["status"])
        self.assertFalse(tasks[1]["done"])
        self.assertEqual("blocked", tasks[2]["status"])

    def test_web_auto_plan_creates_plan_for_complex_message_without_building(self) -> None:
        class PlanProvider:
            def complete(self, _prompt: str, *, images=()) -> str:
                return (
                    "# BB9 Plan\n\n"
                    "Objective: implémenter la feature\n\n"
                    "## Tasks\n\n"
                    "- [ ] T1 Cadrer la feature\n"
                    "  worker: default\n"
                    "  parallelizable: false\n"
                    "  paths: src/feature.py\n"
                    "  depends:\n"
                    "  goal: Cadrer la feature.\n"
                    "  context: Demande utilisateur complexe.\n"
                    "  expected: Plan prêt.\n"
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            workspace.mkdir()
            agents.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        agents_dir=root / "agents",
                        skills_dir=root / "skills",
                        tools_dir=root / "tools",
                        session_store_path=root / "sessions.db",
                        visible_history_path=root / "history.db",
                        settings_path=root / "settings.json",
                    )
                )
                with patch("bb9.api.chat.build_provider_for_agent", return_value=PlanProvider()), patch(
                    "bb9.templates.skills.dev.cli._run_plan",
                    side_effect=AssertionError("/build should not be called by auto-plan"),
                ):
                    payload = app.run_message("Implémente une feature complète avec tests et documentation")
                plan = (workspace / ".bb9" / "plan.md").read_text(encoding="utf-8")
                history = app.history_payload()
            finally:
                os.chdir(cwd)

        self.assertTrue(payload["ok"])
        self.assertIn("Plan prêt", payload["answer"])
        self.assertIn("/build", payload["answer"])
        self.assertTrue(payload["plan"]["exists"])
        self.assertEqual("Cadrer la feature", payload["plan"]["tasks"][0]["title"])
        self.assertEqual("Implémente une feature complète avec tests et documentation", history["messages"][0]["content"])
        self.assertIn("T1 Cadrer la feature", plan)

    def test_web_auto_plan_for_design_system_component_proposal(self) -> None:
        class PlanProvider:
            def complete(self, _prompt: str, *, images=()) -> str:
                return (
                    "# BB9 Plan\n\n"
                    "Objective: proposer de nouveaux composants design system\n\n"
                    "## Tasks\n\n"
                    "- [ ] T1 Auditer le design system\n"
                    "  worker: default\n"
                    "  parallelizable: false\n"
                    "  paths: src/components\n"
                    "  depends:\n"
                    "  goal: Identifier les manques du design system.\n"
                    "  context: Demande utilisateur de nouveaux composants.\n"
                    "  expected: Liste priorisée de composants.\n"
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            workspace.mkdir()
            agents.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        agents_dir=root / "agents",
                        skills_dir=root / "skills",
                        tools_dir=root / "tools",
                        session_store_path=root / "sessions.db",
                        visible_history_path=root / "history.db",
                        settings_path=root / "settings.json",
                    )
                )
                with patch("bb9.api.chat.build_provider_for_agent", return_value=PlanProvider()):
                    payload = app.run_message("propose moi des nouveaux composant pour le design system")
            finally:
                os.chdir(cwd)

        self.assertTrue(payload["ok"])
        self.assertIn("Plan prêt", payload["answer"])
        self.assertTrue(payload["plan"]["exists"])
        self.assertEqual("Auditer le design system", payload["plan"]["tasks"][0]["title"])

    def test_web_auto_plan_for_explicit_plan_and_continue_all_requests(self) -> None:
        class PlanProvider:
            def complete(self, _prompt: str, *, images=()) -> str:
                return (
                    "# BB9 Plan\n\n"
                    "Objective: intégrer les composants\n\n"
                    "## Tasks\n\n"
                    "- [ ] T1 Préparer l'intégration\n"
                    "  worker: default\n"
                    "  parallelizable: false\n"
                    "  paths: src/components\n"
                    "  depends:\n"
                    "  goal: Préparer l'intégration.\n"
                    "  context: Demande utilisateur.\n"
                    "  expected: Plan prêt.\n"
                )

        for message in ("du coup fais un plan", "ok c pas mal je veux tout ça"):
            with self.subTest(message=message), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                workspace = root / "workspace"
                agents = root / "agents" / "default"
                workspace.mkdir()
                agents.mkdir(parents=True)
                (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
                cwd = Path.cwd()
                try:
                    os.chdir(workspace)
                    app = ChatApiApp(
                        ChatApiState(
                            agents_dir=root / "agents",
                            skills_dir=root / "skills",
                            tools_dir=root / "tools",
                            session_store_path=root / "sessions.db",
                            visible_history_path=root / "history.db",
                            settings_path=root / "settings.json",
                        )
                    )
                    with patch("bb9.api.chat.build_provider_for_agent", return_value=PlanProvider()):
                        payload = app.run_message(message)
                finally:
                    os.chdir(cwd)

                self.assertTrue(payload["ok"])
                self.assertIn("Plan prêt", payload["answer"])
                self.assertTrue(payload["plan"]["exists"])
                self.assertEqual("Préparer l'intégration", payload["plan"]["tasks"][0]["title"])

    def test_web_simple_message_does_not_auto_plan(self) -> None:
        class SimpleProvider:
            def complete(self, _prompt: str, *, images=()) -> str:
                return "Réponse simple."

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            workspace.mkdir()
            agents.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        agents_dir=root / "agents",
                        skills_dir=root / "skills",
                        tools_dir=root / "tools",
                        session_store_path=root / "sessions.db",
                        visible_history_path=root / "history.db",
                        settings_path=root / "settings.json",
                    )
                )
                with patch("bb9.core.runtime_service.build_provider_for_agent", return_value=SimpleProvider()):
                    payload = app.run_message("bonjour web")
            finally:
                os.chdir(cwd)

        self.assertTrue(payload["ok"])
        self.assertEqual("Réponse simple.", payload["answer"])
        self.assertFalse((workspace / ".bb9" / "plan.md").exists())
        self.assertFalse(payload["plan"]["exists"])

    def test_web_architecture_question_does_not_auto_plan(self) -> None:
        class SimpleProvider:
            def complete(self, _prompt: str, *, images=()) -> str:
                return "Architecture résumée."

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            workspace.mkdir()
            agents.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        agents_dir=root / "agents",
                        skills_dir=root / "skills",
                        tools_dir=root / "tools",
                        session_store_path=root / "sessions.db",
                        visible_history_path=root / "history.db",
                        settings_path=root / "settings.json",
                    )
                )
                with patch("bb9.core.runtime_service.build_provider_for_agent", return_value=SimpleProvider()):
                    payload = app.run_message("Quelle architecture utilise ce projet ?")
            finally:
                os.chdir(cwd)

        self.assertTrue(payload["ok"])
        self.assertEqual("Architecture résumée.", payload["answer"])
        self.assertFalse((workspace / ".bb9" / "plan.md").exists())
        self.assertFalse(payload["plan"]["exists"])

    def test_web_plan_diagnostic_question_does_not_auto_plan(self) -> None:
        class SimpleProvider:
            def complete(self, _prompt: str, *, images=()) -> str:
                return "Diagnostic répondu."

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            workspace.mkdir()
            agents.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        agents_dir=root / "agents",
                        skills_dir=root / "skills",
                        tools_dir=root / "tools",
                        session_store_path=root / "sessions.db",
                        visible_history_path=root / "history.db",
                        settings_path=root / "settings.json",
                    )
                )
                with patch("bb9.core.runtime_service.build_provider_for_agent", return_value=SimpleProvider()):
                    payload = app.run_message("pourquoi tu as pas fais un plan ?")
            finally:
                os.chdir(cwd)

        self.assertTrue(payload["ok"])
        self.assertEqual("Diagnostic répondu.", payload["answer"])
        self.assertFalse((workspace / ".bb9" / "plan.md").exists())
        self.assertFalse(payload["plan"]["exists"])

    def test_web_auto_plan_does_not_replace_existing_plan(self) -> None:
        class SimpleProvider:
            def complete(self, _prompt: str, *, images=()) -> str:
                return "Je continue avec le plan courant."

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            plan_path = workspace / ".bb9" / "plan.md"
            workspace.mkdir()
            agents.mkdir(parents=True)
            plan_path.parent.mkdir(parents=True)
            plan_path.write_text("# BB9 Plan\n\n- [ ] T1 Plan existant\n", encoding="utf-8")
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        agents_dir=root / "agents",
                        skills_dir=root / "skills",
                        tools_dir=root / "tools",
                        session_store_path=root / "sessions.db",
                        visible_history_path=root / "history.db",
                        settings_path=root / "settings.json",
                    )
                )
                with patch("bb9.core.runtime_service.build_provider_for_agent", return_value=SimpleProvider()):
                    payload = app.run_message("Implémente une feature complète avec tests et documentation")
                plan = plan_path.read_text(encoding="utf-8")
            finally:
                os.chdir(cwd)

        self.assertTrue(payload["ok"])
        self.assertEqual("Je continue avec le plan courant.", payload["answer"])
        self.assertIn("T1 Plan existant", plan)
        self.assertEqual("Plan existant", payload["plan"]["tasks"][0]["title"])

    def test_web_clear_plan_removes_current_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            plan_path = workspace / ".bb9" / "plan.md"
            workspace.mkdir()
            agents.mkdir(parents=True)
            plan_path.parent.mkdir(parents=True)
            plan_path.write_text("# BB9 Plan\n\n- [ ] T1 Tester\n", encoding="utf-8")
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        agents_dir=root / "agents",
                        skills_dir=root / "skills",
                        tools_dir=root / "tools",
                        session_store_path=root / "sessions.db",
                        visible_history_path=root / "history.db",
                        settings_path=root / "settings.json",
                    )
                )
                payload = app.clear_plan()
            finally:
                os.chdir(cwd)

        self.assertTrue(payload["ok"])
        self.assertFalse(payload["plan"]["exists"])
        self.assertFalse(plan_path.exists())

    def test_web_clear_plan_after_project_switch_targets_new_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            other = root / "other"
            agents = root / "agents" / "default"
            plan_path = workspace / ".bb9" / "plan.md"
            other_plan_path = other / ".bb9" / "plan.md"
            workspace.mkdir()
            other.mkdir()
            agents.mkdir(parents=True)
            plan_path.parent.mkdir(parents=True)
            other_plan_path.parent.mkdir(parents=True)
            plan_path.write_text("# BB9 Plan\n\n- [ ] T1 Ancien\n", encoding="utf-8")
            other_plan_path.write_text("# BB9 Plan\n\n- [ ] T1 Nouveau\n", encoding="utf-8")
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        agents_dir=root / "agents",
                        skills_dir=root / "skills",
                        tools_dir=root / "tools",
                        session_store_path=root / "sessions.db",
                        visible_history_path=root / "history.db",
                        settings_path=root / "settings.json",
                    )
                )
                switched = app.switch_project(str(other))
                payload = app.clear_plan(str(other))
                old_plan_exists = plan_path.exists()
                other_plan_exists = other_plan_path.exists()
            finally:
                os.chdir(cwd)

        self.assertTrue(switched["ok"])
        self.assertEqual(str(other.resolve()), switched["workspace"])
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["plan"]["exists"])
        self.assertEqual(str(other.resolve()), payload["plan"]["project_path"])
        self.assertTrue(old_plan_exists)
        self.assertFalse(other_plan_exists)

    def test_web_build_command_requires_existing_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            workspace.mkdir()
            agents.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(ChatApiState(agents_dir=root / "agents", skills_dir=root / "skills", tools_dir=root / "tools"))
                payload = app.run_message("/build")
            finally:
                os.chdir(cwd)

        self.assertFalse(payload["ok"])
        self.assertEqual("plan_not_found", payload["error"])
        self.assertIn("/plan <demande>", payload["message"])
        self.assertNotIn(".bb9/plan.md", payload["message"])

    def test_web_native_context_command_is_persisted_to_visible_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            workspace.mkdir()
            agents.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        agents_dir=root / "agents",
                        skills_dir=root / "skills",
                        tools_dir=root / "tools",
                        session_store_path=root / "sessions.db",
                        visible_history_path=root / "history.db",
                        settings_path=root / "settings.json",
                    )
                )
                payload = app.run_message("/context")
                history = app.history_payload()
                restored = ChatApiApp(
                    ChatApiState(
                        agents_dir=root / "agents",
                        skills_dir=root / "skills",
                        tools_dir=root / "tools",
                        session_store_path=root / "sessions.db",
                        visible_history_path=root / "history.db",
                        settings_path=root / "settings.json",
                    )
                ).history_payload()
            finally:
                os.chdir(cwd)

        self.assertTrue(payload["ok"])
        self.assertEqual(["user", "assistant"], [message["role"] for message in history["messages"]])
        self.assertEqual("/context", history["messages"][0]["content"])
        self.assertEqual("/context", restored["messages"][0]["content"])

    def test_web_build_command_executes_current_plan(self) -> None:
        def fake_delegate(task, _subagent, _parent_context, _run_worker):
            return TaskResult(task_id=task.id, status="done", summary="Task done.", changed=("src/page.html",))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            subagent = agents / "subagents" / "default"
            plan_path = workspace / ".bb9" / "plan.md"
            workspace.mkdir()
            subagent.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            (subagent / "IDENTITY.md").write_text("# Worker\n", encoding="utf-8")
            plan_path.parent.mkdir(parents=True)
            plan_path.write_text(
                "# BB9 Plan\n\n"
                "- [ ] T1 Créer la page\n"
                "  worker: default\n"
                "  parallelizable: false\n"
                "  paths: src/page.html\n"
                "  depends:\n"
                "  goal: Créer la page.\n"
                "  context: Demande utilisateur.\n"
                "  expected: Page prête.\n",
                encoding="utf-8",
            )
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(ChatApiState(profile="power", agents_dir=root / "agents", skills_dir=root / "skills", tools_dir=root / "tools"))
                with patch("bb9.templates.skills.dev.cli.delegate", fake_delegate):
                    payload = app.run_message("/build")
                updated_plan = plan_path.read_text(encoding="utf-8")
                history = app.history_payload()
            finally:
                os.chdir(cwd)

        self.assertTrue(payload["ok"])
        self.assertIn("Build terminé.", payload["answer"])
        self.assertIn("Terminé : Créer la page.", payload["answer"])
        self.assertNotIn("task... Créer la page: done", payload["answer"])
        self.assertIn("process", [event["type"] for event in payload["events"]])
        self.assertIn("Subagent utilisé", [event["summary"] for event in payload["events"]])
        self.assertIn("Tâche terminée", [event["summary"] for event in payload["events"]])
        self.assertIn("Build terminé", [event["summary"] for event in payload["events"]])
        self.assertIn("Trace de décision", [artifact["title"] for artifact in payload["artifacts"]])
        self.assertIn("Sortie /build", [artifact["title"] for artifact in payload["artifacts"]])
        build_output = next(artifact for artifact in payload["artifacts"] if artifact["title"] == "Sortie /build")
        self.assertTrue(build_output["metadata"]["default_hidden"])
        self.assertIn("task... Créer la page: done", build_output["metadata"]["content"])
        assistant = history["messages"][-1]
        trace_artifact = next(artifact for artifact in assistant["artifacts"] if artifact["title"] == "Trace de décision")
        trace_summaries = [entry["summary"] for entry in trace_artifact["metadata"]["entries"]]
        self.assertIn("Subagent utilisé", trace_summaries)
        self.assertIn("`default/default` pour `Créer la page`", [entry["data"].get("detail", "") for entry in trace_artifact["metadata"]["entries"]])
        subagent_entries = [
            entry
            for entry in trace_artifact["metadata"]["entries"]
            if entry["data"].get("process_kind") == "subagent"
        ]
        self.assertEqual(["running", "done"], [entry["data"].get("subagent_status") for entry in subagent_entries])
        self.assertEqual("Créer la page", subagent_entries[0]["data"]["task_title"])
        self.assertEqual(1, payload["plan"]["completed"])
        self.assertTrue(payload["plan"]["tasks"][0]["done"])
        self.assertIn("- [x] T1 Créer la page", updated_plan)

    def test_web_build_does_not_retry_error_tasks_without_explicit_retry(self) -> None:
        def fake_delegate(*_args):
            raise AssertionError("error task should not be retried by default")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            subagent = agents / "subagents" / "default"
            plan_path = workspace / ".bb9" / "plan.md"
            workspace.mkdir()
            subagent.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            (subagent / "IDENTITY.md").write_text("# Worker\n", encoding="utf-8")
            plan_path.parent.mkdir(parents=True)
            plan_path.write_text(
                "# BB9 Plan\n\n"
                "- [ ] T1 Corriger le fichier\n"
                "  worker: default\n"
                "  goal: Corriger.\n"
                "  context: Demande utilisateur.\n"
                "  expected: Correction.\n"
                "  status: error\n"
                "  summary: Ancien échec.\n"
                "  blockers: previous failure\n",
                encoding="utf-8",
            )
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(ChatApiState(profile="power", agents_dir=root / "agents", skills_dir=root / "skills", tools_dir=root / "tools"))
                with patch("bb9.templates.skills.dev.cli.delegate", fake_delegate):
                    payload = app.run_message("/build")
                updated_plan = plan_path.read_text(encoding="utf-8")
            finally:
                os.chdir(cwd)

        self.assertTrue(payload["ok"])
        self.assertIn("Build bloqué", payload["answer"])
        self.assertIn("--retry-errors", payload["answer"])
        self.assertNotIn("Subagent utilisé", [event["summary"] for event in payload["events"]])
        self.assertIn("summary: Ancien échec.", updated_plan)

    def test_web_build_retry_errors_uses_default_plan_path(self) -> None:
        def fake_delegate(task, _subagent, _parent_context, _run_worker):
            return TaskResult(task_id=task.id, status="done", summary="Retry ok.")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            subagent = agents / "subagents" / "default"
            plan_path = workspace / ".bb9" / "plan.md"
            workspace.mkdir()
            subagent.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            (subagent / "IDENTITY.md").write_text("# Worker\n", encoding="utf-8")
            plan_path.parent.mkdir(parents=True)
            plan_path.write_text(
                "# BB9 Plan\n\n"
                "- [ ] T1 Corriger le fichier\n"
                "  worker: default\n"
                "  goal: Corriger.\n"
                "  context: Demande utilisateur.\n"
                "  expected: Correction.\n"
                "  status: error\n"
                "  summary: Ancien échec.\n"
                "  blockers: previous failure\n",
                encoding="utf-8",
            )
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(ChatApiState(profile="power", agents_dir=root / "agents", skills_dir=root / "skills", tools_dir=root / "tools"))
                with patch("bb9.templates.skills.dev.cli.delegate", fake_delegate):
                    payload = app.run_message("/build --retry-errors")
                updated_plan = plan_path.read_text(encoding="utf-8")
            finally:
                os.chdir(cwd)

        self.assertTrue(payload["ok"])
        self.assertNotIn("plan file not found: --retry-errors", payload["answer"])
        self.assertEqual(1, payload["plan"]["completed"])
        self.assertIn("- [x] T1 Corriger le fichier", updated_plan)

    def test_web_build_dependency_blocker_event_is_structured_as_blocked(self) -> None:
        event = _skill_output_process("/build", "blk... la tâche 'Auditer' n'est pas terminée")

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual("Blocage détecté", event["title"])
        self.assertEqual("bloqué", event["status"])
        self.assertEqual({"block_category": "dependency"}, event["data"])

    def test_web_build_raw_dependency_blocker_event_is_structured_as_blocked(self) -> None:
        event = _skill_output_process("/build", "blk... dependency:T2")

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual("bloqué", event["status"])
        self.assertEqual({"block_category": "dependency"}, event["data"])

    def test_web_build_direct_blocker_event_stays_error(self) -> None:
        event = _skill_output_process("/build", "blk... ProviderError")

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual("Blocage détecté", event["title"])
        self.assertEqual("erreur", event["status"])
        self.assertEqual({"block_category": "direct"}, event["data"])

    def test_web_build_runs_parallel_subagents_in_power_profile(self) -> None:
        def fake_delegate(task, _subagent, _parent_context, _run_worker):
            time.sleep(0.01)
            return TaskResult(task_id=task.id, status="done", summary=f"{task.title} done.")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            subagent = agents / "subagents" / "default"
            plan_path = workspace / ".bb9" / "plan.md"
            workspace.mkdir()
            subagent.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            (subagent / "IDENTITY.md").write_text("# Worker\n", encoding="utf-8")
            plan_path.parent.mkdir(parents=True)
            plan_path.write_text(
                "# BB9 Plan\n\n"
                "- [ ] T1 Docs\n"
                "  worker: default\n"
                "  parallelizable: true\n"
                "  paths: docs/demo.md\n"
                "  goal: Adapter docs.\n"
                "  context: Aucun conflit.\n"
                "  expected: Docs adaptées.\n\n"
                "- [ ] T2 Tests\n"
                "  worker: default\n"
                "  parallelizable: true\n"
                "  paths: tests/test_demo.py\n"
                "  goal: Adapter tests.\n"
                "  context: Aucun conflit.\n"
                "  expected: Tests adaptés.\n",
                encoding="utf-8",
            )
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        profile="power",
                        profile_explicit=True,
                        agents_dir=root / "agents",
                        skills_dir=root / "skills",
                        tools_dir=root / "tools",
                    )
                )
                with patch("bb9.templates.skills.dev.cli.delegate", fake_delegate):
                    payload = app.run_message("/build")
            finally:
                os.chdir(cwd)

        self.assertTrue(payload["ok"])
        self.assertIn("Lancer une vague parallèle", [event["summary"] for event in payload["events"]])
        subagent_events = [
            event for event in payload["events"] if event["data"].get("process_kind") == "subagent"
        ]
        self.assertEqual(4, len(subagent_events))
        self.assertEqual(2, sum(1 for event in subagent_events if event["data"].get("subagent_status") == "running"))
        self.assertEqual(2, sum(1 for event in subagent_events if event["data"].get("subagent_status") == "done"))
        self.assertEqual(2, payload["plan"]["completed"])

    def test_web_build_subagent_approval_resumes_task_after_allow(self) -> None:
        class Provider:
            def __init__(self) -> None:
                self.calls = 0

            def complete(self, _prompt: str, *, images=()) -> str:
                self.calls += 1
                if self.calls == 1:
                    return "BB9_ACTION files write path=demo.txt text=ok"
                return "Status: done\nSummary: demo.txt écrit.\nEvidence:\n- demo.txt"

        provider = Provider()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            subagent = agents / "subagents" / "default"
            plan_path = workspace / ".bb9" / "plan.md"
            workspace.mkdir()
            subagent.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            (subagent / "IDENTITY.md").write_text("# Worker\n", encoding="utf-8")
            plan_path.parent.mkdir(parents=True)
            plan_path.write_text(
                "# BB9 Plan\n\n"
                "- [ ] T1 Créer le fichier\n"
                "  worker: default\n"
                "  goal: Créer demo.txt.\n"
                "  context: Demande utilisateur.\n"
                "  expected: Fichier créé.\n",
                encoding="utf-8",
            )
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        profile="safe",
                        profile_explicit=True,
                        agents_dir=root / "agents",
                        skills_dir=root / "skills",
                        tools_dir=Path(__file__).resolve().parents[1] / "bb9" / "tools",
                        visible_history_path=root / "history.db",
                    )
                )
                with patch("bb9.api.chat.build_provider_for_agent", return_value=provider):
                    pending = app.run_message("/build")
                    plan_after_pending = plan_path.read_text(encoding="utf-8")
                    approved = app.resolve_approval(pending["approval"]["id"], "allow")
                updated_plan = plan_path.read_text(encoding="utf-8")
                created = (workspace / "demo.txt").read_text(encoding="utf-8")
            finally:
                os.chdir(cwd)

        self.assertTrue(pending["ok"])
        self.assertIn("Validation requise", pending["answer"])
        self.assertEqual("build", pending["approval"]["scope"])
        self.assertEqual("T1", pending["approval"]["task_id"])
        self.assertEqual("default/default", pending["approval"]["worker"])
        self.assertNotIn("status: error", plan_after_pending)
        self.assertTrue(approved["ok"])
        self.assertIn("Build terminé.", approved["answer"])
        self.assertIn("Terminé : Créer le fichier.", approved["answer"])
        self.assertEqual("ok", created)
        self.assertIn("- [x] T1 Créer le fichier", updated_plan)
        self.assertGreaterEqual(provider.calls, 2)

    def test_web_build_subagent_can_request_multiple_user_approvals(self) -> None:
        class Provider:
            def __init__(self) -> None:
                self.calls = 0

            def complete(self, _prompt: str, *, images=()) -> str:
                self.calls += 1
                if self.calls == 1:
                    return "BB9_ACTION files write path=first.txt text=one"
                if self.calls == 2:
                    return "BB9_ACTION files write path=second.txt text=two"
                return "Status: done\nSummary: fichiers écrits.\nEvidence:\n- first.txt\n- second.txt"

        provider = Provider()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            subagent = agents / "subagents" / "default"
            plan_path = workspace / ".bb9" / "plan.md"
            workspace.mkdir()
            subagent.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            (subagent / "IDENTITY.md").write_text("# Worker\n", encoding="utf-8")
            plan_path.parent.mkdir(parents=True)
            plan_path.write_text(
                "# BB9 Plan\n\n"
                "- [ ] T1 Écrire deux fichiers\n"
                "  worker: default\n"
                "  goal: Créer deux fichiers.\n"
                "  context: Demande utilisateur.\n"
                "  expected: Fichiers créés.\n",
                encoding="utf-8",
            )
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        profile="safe",
                        profile_explicit=True,
                        agents_dir=root / "agents",
                        skills_dir=root / "skills",
                        tools_dir=Path(__file__).resolve().parents[1] / "bb9" / "tools",
                    )
                )
                with patch("bb9.api.chat.build_provider_for_agent", return_value=provider):
                    first_pending = app.run_message("/build")
                    first_allowed = app.resolve_approval(first_pending["approval"]["id"], "allow")
                    first_text_after_first_allow = (workspace / "first.txt").read_text(encoding="utf-8")
                    second_exists_after_first_allow = (workspace / "second.txt").exists()
                    second_allowed = app.resolve_approval(first_allowed["approval"]["id"], "allow")
                    second_text_after_second_allow = (workspace / "second.txt").read_text(encoding="utf-8")
                updated_plan = plan_path.read_text(encoding="utf-8")
            finally:
                os.chdir(cwd)

        self.assertTrue(first_pending["ok"])
        self.assertEqual("build", first_pending["approval"]["scope"])
        self.assertEqual("default/default", first_pending["approval"]["worker"])
        self.assertTrue(first_allowed["ok"])
        self.assertIn("Validation requise", first_allowed["answer"])
        self.assertEqual("build", first_allowed["approval"]["scope"])
        self.assertEqual("T1", first_allowed["approval"]["task_id"])
        self.assertEqual("default/default", first_allowed["approval"]["worker"])
        self.assertNotEqual(first_pending["approval"]["id"], first_allowed["approval"]["id"])
        self.assertEqual("one", first_text_after_first_allow)
        self.assertFalse(second_exists_after_first_allow)
        self.assertTrue(second_allowed["ok"])
        self.assertIn("Build terminé.", second_allowed["answer"])
        self.assertEqual("two", second_text_after_second_allow)
        self.assertIn("- [x] T1 Écrire deux fichiers", updated_plan)
        self.assertGreaterEqual(provider.calls, 3)

    def test_web_build_subagent_denial_can_resume_with_alternative(self) -> None:
        case = self

        class Provider:
            def __init__(self) -> None:
                self.calls = 0

            def complete(self, prompt: str, *, images=()) -> str:
                self.calls += 1
                if self.calls == 1:
                    return "BB9_ACTION files write path=demo.txt text=ok"
                case.assertIn("Action refusée par l'utilisateur", prompt)
                return "Status: done\nSummary: action refusée, alternative sans écriture.\nEvidence:\n- refus intégré"

        provider = Provider()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            subagent = agents / "subagents" / "default"
            plan_path = workspace / ".bb9" / "plan.md"
            workspace.mkdir()
            subagent.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            (subagent / "IDENTITY.md").write_text("# Worker\n", encoding="utf-8")
            plan_path.parent.mkdir(parents=True)
            plan_path.write_text(
                "# BB9 Plan\n\n"
                "- [ ] T1 Trouver une voie\n"
                "  worker: default\n"
                "  goal: Produire un résultat sans bloquer.\n"
                "  context: Demande utilisateur.\n"
                "  expected: Alternative ou blocage expliqué.\n",
                encoding="utf-8",
            )
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        profile="safe",
                        profile_explicit=True,
                        agents_dir=root / "agents",
                        skills_dir=root / "skills",
                        tools_dir=Path(__file__).resolve().parents[1] / "bb9" / "tools",
                    )
                )
                with patch("bb9.api.chat.build_provider_for_agent", return_value=provider):
                    pending = app.run_message("/build")
                    denied = app.resolve_approval(pending["approval"]["id"], "deny")
                updated_plan = plan_path.read_text(encoding="utf-8")
            finally:
                os.chdir(cwd)

        self.assertTrue(denied["ok"])
        self.assertIn("Build terminé.", denied["answer"])
        self.assertFalse((workspace / "demo.txt").exists())
        self.assertIn("- [x] T1 Trouver une voie", updated_plan)
        self.assertGreaterEqual(provider.calls, 2)

    def test_web_build_command_exposes_running_state_without_blocking_status(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def slow_delegate(task, _subagent, _parent_context, _run_worker):
            started.set()
            release.wait(timeout=5)
            return TaskResult(task_id=task.id, status="done", summary="Task done.")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            subagent = agents / "subagents" / "default"
            plan_path = workspace / ".bb9" / "plan.md"
            workspace.mkdir()
            subagent.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            (subagent / "IDENTITY.md").write_text("# Worker\n", encoding="utf-8")
            plan_path.parent.mkdir(parents=True)
            plan_path.write_text(
                "# BB9 Plan\n\n"
                "- [ ] T1 Créer la page\n"
                "  worker: default\n"
                "  goal: Créer la page.\n"
                "  context: Demande utilisateur.\n"
                "  expected: Page prête.\n",
                encoding="utf-8",
            )
            cwd = Path.cwd()
            result: dict[str, object] = {}
            try:
                os.chdir(workspace)
                app = ChatApiApp(ChatApiState(profile="power", agents_dir=root / "agents", skills_dir=root / "skills", tools_dir=root / "tools"))

                def run_build():
                    with patch("bb9.templates.skills.dev.cli.delegate", slow_delegate):
                        result.update(app.run_message("/build"))

                thread = threading.Thread(target=run_build)
                thread.start()
                self.assertTrue(started.wait(timeout=2))
                status = app.status_payload()
                events = app.run_events_payload()
                release.set()
                thread.join(timeout=5)
            finally:
                os.chdir(cwd)

        self.assertTrue(status["running"])
        self.assertTrue(events["running"])
        self.assertGreater(events["total"], 0)
        self.assertIn("Subagent utilisé", [event["summary"] for event in events["events"]])
        self.assertTrue(result["ok"])

    def test_web_plan_payload_includes_task_error_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            plan_path = workspace / ".bb9" / "plan.md"
            workspace.mkdir()
            agents.mkdir(parents=True)
            plan_path.parent.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            plan_path.write_text(
                "# BB9 Plan\n\n"
                "- [ ] T1 Valider\n"
                "  goal: Valider.\n"
                "  context: Contexte.\n"
                "  expected: Tests.\n"
                "  status: error\n"
                "  summary: Delegation failed: ChatGPT web request timed out\n"
                "  blockers: ProviderError\n",
                encoding="utf-8",
            )
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        agents_dir=root / "agents",
                        skills_dir=root / "skills",
                        tools_dir=root / "tools",
                        settings_path=root / "settings.json",
                    )
                )
                payload = app.status_payload()
            finally:
                os.chdir(cwd)

        task = payload["plan"]["tasks"][0]
        self.assertEqual("error", task["status"])
        self.assertEqual("Delegation failed: ChatGPT web request timed out", task["summary"])
        self.assertEqual("ProviderError", task["blockers"])

    def test_web_chat_compact_command_compacts_short_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents" / "default"
            workspace.mkdir()
            agents.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            session = Session(source="web")
            for index in range(12):
                session = session.with_message("user", f"message {index}", max_messages=20)
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(ChatApiState(agents_dir=root / "agents", session=session))
                payload = app.run_message("/compact")
            finally:
                os.chdir(cwd)

            self.assertTrue(payload["ok"])
            self.assertIn("Contexte compacte", payload["answer"])
            self.assertGreater(app.state.session.compacted_count, 0)

    def test_web_chat_git_payload_reports_dirty_project(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            subprocess.run(["git", "init"], cwd=workspace, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["git", "config", "user.email", "test@example.local"], cwd=workspace, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=workspace, check=True)
            (workspace / "base.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "base.txt"], cwd=workspace, check=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=workspace, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["git", "branch", "feature"], cwd=workspace, check=True)
            (workspace / "tracked.txt").write_text("hello\n", encoding="utf-8")
            (workspace / ".bb9" / "uploads").mkdir(parents=True)
            (workspace / ".bb9" / "uploads" / "ignored.txt").write_text("local\n", encoding="utf-8")
            app = ChatApiApp(ChatApiState(active_project_path=str(workspace)))

            payload = app.git_payload()
            diff = app.git_diff_payload("tracked.txt")

            self.assertTrue(payload["ok"])
            self.assertTrue(payload["git"])
            self.assertEqual(str(workspace.resolve()), payload["root"])
            self.assertEqual(1, payload["files_changed"])
            self.assertEqual("tracked.txt", payload["files"][0]["path"])
            self.assertEqual("??", payload["files"][0]["status"])
            self.assertEqual(1, payload["files"][0]["insertions"])
            self.assertTrue(diff["ok"])
            self.assertIn("diff --git a/tracked.txt b/tracked.txt", diff["diff"])
            blocked = app.switch_git_branch("feature")
            self.assertFalse(blocked["ok"])
            self.assertEqual("dirty_worktree", blocked["error"])
            self.assertEqual(1, blocked["files_changed"])
            commit_message = app.git_commit_message_payload()
            self.assertTrue(commit_message["ok"])
            self.assertIn("Add tracked.txt", commit_message["message"])
            committed = app.commit_git_changes(commit_message["message"])
            self.assertTrue(committed["ok"])
            self.assertTrue(committed["committed"])
            self.assertEqual(0, committed["files_changed"])
            self.assertTrue(committed["commit"])
            tracked = subprocess.run(["git", "ls-files"], cwd=workspace, text=True, capture_output=True, check=True)
            self.assertIn("tracked.txt", tracked.stdout)
            self.assertNotIn(".bb9/uploads/ignored.txt", tracked.stdout)

    def test_web_chat_http_api_returns_answer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents"
            skills = root / "skills"
            tools = root / "tools"
            workspace.mkdir()
            (agents / "default").mkdir(parents=True)
            skills.mkdir()
            tools.mkdir()
            (agents / "default" / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            local_skill = workspace / ".bb9" / "skills" / "local"
            local_skill.mkdir(parents=True)
            (local_skill / "SKILL.md").write_text(
                "# Local\n\n## Résumé\n\nCommande locale.\n\n## Commandes\n\n- `/local` : commande projet.\n",
                encoding="utf-8",
            )
            local_theme = workspace / ".bb9" / "themes" / "web"
            local_theme.mkdir(parents=True)
            (local_theme / "contrast.css").write_text(
                ":root[data-theme=\"contrast\"] { --bg: #000; --text: #fff; }\n",
                encoding="utf-8",
            )
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        agents_dir=agents,
                        skills_dir=skills,
                        tools_dir=tools,
                        settings_path=root / "settings.json",
                        visible_history_path=root / "history.db",
                    )
                )
                server = chat_api_server(app, 0)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/chat",
                    data=json.dumps({"message": "salut"}).encode("utf-8"),
                    headers={"content-type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/history", timeout=5) as response:
                    history = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/status", timeout=5) as response:
                    status = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/settings", timeout=5) as response:
                    settings = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/projects", timeout=5) as response:
                    projects = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/commands", timeout=5) as response:
                    commands = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/themes", timeout=5) as response:
                    themes = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/theme?name=contrast", timeout=5) as response:
                    theme_css = response.read().decode("utf-8")
                    theme_type = response.headers.get("content-type")
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/theme?name=graphite", timeout=5) as response:
                    generated_theme_css = response.read().decode("utf-8")
                help_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/chat",
                    data=json.dumps({"message": "/help"}).encode("utf-8"),
                    headers={"content-type": "application/json"},
                    method="POST",
                )
                with urlopen(help_request, timeout=5) as response:
                    help_payload = json.loads(response.read().decode("utf-8"))
                context_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/chat",
                    data=json.dumps({"message": "/context"}).encode("utf-8"),
                    headers={"content-type": "application/json"},
                    method="POST",
                )
                with urlopen(context_request, timeout=5) as response:
                    context_payload = json.loads(response.read().decode("utf-8"))
                stop_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/stop",
                    data=json.dumps({}).encode("utf-8"),
                    headers={"content-type": "application/json"},
                    method="POST",
                )
                with urlopen(stop_request, timeout=5) as response:
                    stop_payload = json.loads(response.read().decode("utf-8"))
                project_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/project",
                    data=json.dumps({"path": str(workspace)}).encode("utf-8"),
                    headers={"content-type": "application/json"},
                    method="POST",
                )
                with urlopen(project_request, timeout=5) as response:
                    selected_project = json.loads(response.read().decode("utf-8"))
                settings_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/settings",
                    data=json.dumps({"profile": "safe", "model": "demo-model", "reasoning_effort": "medium"}).encode("utf-8"),
                    headers={"content-type": "application/json"},
                    method="POST",
                )
                with urlopen(settings_request, timeout=5) as response:
                    updated_settings = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/sessions", timeout=5) as response:
                    sessions = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}/health", timeout=5) as response:
                    health = json.loads(response.read().decode("utf-8"))
                upload_request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/upload",
                    data=json.dumps({"mime": "image/png", "data": base64.b64encode(b"png").decode("ascii")}).encode("utf-8"),
                    headers={"content-type": "application/json"},
                    method="POST",
                )
                with urlopen(upload_request, timeout=5) as response:
                    upload = json.loads(response.read().decode("utf-8"))
                with urlopen(f"http://127.0.0.1:{server.server_port}{upload['url']}", timeout=5) as response:
                    image_body = response.read()
                    image_type = response.headers.get("content-type")
            finally:
                if "server" in locals():
                    server.shutdown()
                    server.server_close()
                os.chdir(cwd)

            self.assertTrue(payload["ok"])
            self.assertEqual("salut", payload["answer"])
            self.assertTrue(history["ok"])
            self.assertEqual(["user", "assistant"], [item["role"] for item in history["messages"]])
            self.assertTrue(status["ok"])
            self.assertEqual(str(workspace.resolve()), status["workspace"])
            self.assertEqual("web", status["source"])
            self.assertEqual(str(workspace.resolve()), status["active_project"])
            self.assertIn("# Workspace Status", status["workspace_status"])
            self.assertIn("power", settings["profiles"])
            self.assertIn("medium", settings["reasoning_efforts"])
            self.assertTrue(projects["ok"])
            self.assertEqual(str(workspace.resolve()), projects["active_project"])
            self.assertTrue(commands["ok"])
            self.assertIn("/help", [command["name"] for command in commands["commands"]])
            self.assertIn("/local", [command["name"] for command in commands["commands"]])
            self.assertTrue(themes["ok"])
            theme_ids = [theme["id"] for theme in themes["themes"]]
            self.assertIn("contrast", theme_ids)
            self.assertIn("graphite", theme_ids)
            self.assertIn("fjord", theme_ids)
            self.assertIn("paper", theme_ids)
            self.assertIn("--bg: #000", theme_css)
            self.assertEqual("text/css; charset=utf-8", theme_type)
            self.assertIn("Generated by scripts/generate-theme.mjs", generated_theme_css)
            self.assertIn(':root[data-theme="graphite"]', generated_theme_css)
            self.assertIn("--control-bg:", generated_theme_css)
            self.assertIn("--trace-neutral:", generated_theme_css)
            self.assertTrue(help_payload["ok"])
            self.assertIn("/local", help_payload["answer"])
            self.assertTrue(context_payload["ok"])
            self.assertIn("## Contexte courant", context_payload["answer"])
            self.assertIn("## Budget contexte", context_payload["answer"])
            self.assertIn("Fenêtre utilisée, session incluse", context_payload["answer"])
            self.assertIn("Avant session courte", context_payload["answer"])
            self.assertIn("## Archives actives", context_payload["answer"])
            self.assertIn("Skills : `local`", context_payload["answer"])
            self.assertIn("Tools : `-`", context_payload["answer"])
            self.assertIn("Budget tools", context_payload["answer"])
            self.assertIn("Context index", context_payload["answer"])
            self.assertIn("## Coût contexte estimé", context_payload["answer"])
            self.assertIn("Total prompt avant réponse", context_payload["answer"])
            self.assertIn("Corps de skills on-demand non inclus", context_payload["answer"])
            self.assertTrue(stop_payload["ok"])
            self.assertFalse(stop_payload["stopped"])
            self.assertTrue(selected_project["ok"])
            self.assertEqual(str(workspace.resolve()), selected_project["active_project"])
            self.assertTrue(updated_settings["ok"])
            self.assertEqual("safe", updated_settings["profile"])
            self.assertEqual("demo-model", updated_settings["model"])
            self.assertEqual("medium", updated_settings["reasoning_effort"])
            self.assertTrue(sessions["ok"])
            self.assertEqual(payload["session_id"], sessions["active_session_id"])
            self.assertTrue(sessions["sessions"])
            self.assertIn("image-api", health["features"])
            self.assertIn("web-ui-v1", health["features"])
            self.assertIn("commands-api", health["features"])
            self.assertIn("themes-api", health["features"])
            self.assertIn("git-api", health["features"])
            self.assertIn("git-diff-api", health["features"])
            self.assertIn("git-commit-api", health["features"])
            self.assertIn("file-preview-api", health["features"])
            self.assertTrue(upload["ok"])
            self.assertTrue(Path(upload["path"]).is_file())
            self.assertIn("[image:", upload["reference"])
            self.assertIn("/api/image?path=", upload["url"])
            self.assertEqual(b"png", image_body)
            self.assertEqual("image/png", image_type)

    def test_web_chat_image_api_serves_bb9_screenshot_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            server_cwd = root / "server"
            screenshot = workspace / ".bb9" / "artifacts" / "screenshots" / "screen.png"
            screenshot.parent.mkdir(parents=True)
            screenshot.write_bytes(b"shot")
            server_cwd.mkdir()
            cwd = Path.cwd()
            try:
                os.chdir(server_cwd)
                app = ChatApiApp(ChatApiState(visible_history_path=root / "history.db"))
                server = chat_api_server(app, 0)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                url = f"http://127.0.0.1:{server.server_port}/api/image?path={quote(str(screenshot))}"
                with urlopen(url, timeout=5) as response:
                    image_body = response.read()
                    image_type = response.headers.get("content-type")
            finally:
                if "server" in locals():
                    server.shutdown()
                    server.server_close()
                os.chdir(cwd)

        self.assertEqual(b"shot", image_body)
        self.assertEqual("image/png", image_type)

    def test_web_chat_file_preview_api_serves_workspace_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            sketch = workspace / "dev" / "sketches" / "demo" / "index.html"
            sketch.parent.mkdir(parents=True)
            sketch.write_text("<!doctype html><title>Demo</title>", encoding="utf-8")
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(ChatApiState(visible_history_path=workspace / ".bb9" / "history.db"))
                server = chat_api_server(app, 0)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                with urlopen(
                    f"http://127.0.0.1:{server.server_port}/api/file/dev/sketches/demo/index.html",
                    timeout=5,
                ) as response:
                    body = response.read().decode("utf-8")
                    content_type = response.headers.get("content-type")
            finally:
                if "server" in locals():
                    server.shutdown()
                    server.server_close()
                os.chdir(cwd)

        self.assertIn("<title>Demo</title>", body)
        self.assertIn("text/html", content_type)

    def test_web_chat_approval_flow_allows_pending_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents"
            skills = root / "skills"
            tools = Path(__file__).resolve().parents[1] / "bb9" / "tools"
            workspace.mkdir()
            target = workspace / "delete-me.txt"
            target.write_text("bye", encoding="utf-8")
            (agents / "default").mkdir(parents=True)
            skills.mkdir()
            (agents / "default" / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        profile="limited",
                        profile_explicit=True,
                        agents_dir=agents,
                        skills_dir=skills,
                        tools_dir=tools,
                        visible_history_path=root / "history.db",
                    )
                )
                pending = app.run_message("/action shell rm delete-me.txt")
                approved = app.resolve_approval(pending["approval"]["id"], "allow")
                target_exists = target.exists()
            finally:
                os.chdir(cwd)

        self.assertTrue(pending["ok"])
        self.assertEqual("Validation requise.", pending["answer"])
        self.assertEqual("shell", pending["approval"]["tool"])
        self.assertTrue(approved["ok"])
        self.assertFalse(target_exists)

    def test_switch_agent_home_activates_that_agent_and_its_model(self) -> None:
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents"
            workspace = root / "workspace"
            providers_path = root / "providers.json"
            (agents / "default").mkdir(parents=True)
            (agents / "local").mkdir(parents=True)
            workspace.mkdir()
            (agents / "default" / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            (agents / "local" / "IDENTITY.md").write_text("# Local\n", encoding="utf-8")
            (agents / "local" / "MODEL.md").write_text("ProviderId: local\nModel: qwen3:14b\n", encoding="utf-8")
            local_provider = ProviderEntry(
                id="local",
                name="ollama local",
                provider="ollama",
                auth_type=AUTH_API,
                base_url="http://127.0.0.1:11434/v1",
                model="qwen3:14b",
            )
            ProviderStore(providers_path).save(ProviderConfig(active_id="local", entries=(local_provider,)))
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        profile="power",
                        profile_explicit=True,
                        agents_dir=agents,
                        skills_dir=root / "skills",
                        tools_dir=root / "tools",
                        provider_config_path=providers_path,
                        active_provider=local_provider,
                        settings_path=root / "settings.json",
                        session_store_path=root / "sessions.db",
                        visible_history_path=root / "history.db",
                    )
                )
                self.assertEqual("default", app.state.agent_name)
                result = app.switch_project("agent-home:local")
                status = runtime_service.build_status(app.state)
            finally:
                os.chdir(cwd)

            self.assertTrue(result["ok"])
            # Entering an agent home activates that agent and its MODEL.md.
            self.assertEqual("local", app.state.agent_name)
            self.assertEqual("", app.state.subagent_name)
            self.assertEqual("local", status.agent)
            self.assertEqual("ollama local", status.provider)
            self.assertEqual("qwen3:14b", status.model)

    def test_web_chat_does_not_create_extra_agent_home_session(self) -> None:
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents"
            workspace = root / "workspace"
            (agents / "default").mkdir(parents=True)
            workspace.mkdir()
            (agents / "default" / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        profile="power",
                        profile_explicit=True,
                        agents_dir=agents,
                        skills_dir=root / "skills",
                        tools_dir=root / "tools",
                        session_store_path=root / "sessions.db",
                        visible_history_path=root / "history.db",
                    )
                )
                home = app.switch_project("agent-home:default")
                created = app.new_session()
                sessions = app.sessions_payload()
            finally:
                os.chdir(cwd)

        self.assertTrue(home["ok"])
        self.assertFalse(created["ok"])
        self.assertEqual("agent_home_singleton", created["error"])
        self.assertEqual("agent-home:default", created["session_id"])
        self.assertEqual(["agent-home:default"], [session["id"] for session in sessions["sessions"]])

    def test_web_settings_exposes_agent_provider_and_model_as_effective_values(self) -> None:
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents"
            workspace = root / "workspace"
            providers_path = root / "providers.json"
            (agents / "default").mkdir(parents=True)
            (agents / "local").mkdir(parents=True)
            workspace.mkdir()
            (agents / "default" / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            (agents / "local" / "IDENTITY.md").write_text("# Local\n", encoding="utf-8")
            (agents / "local" / "MODEL.md").write_text("ProviderId: local\nModel: qwen3:14b\n", encoding="utf-8")
            cloud_provider = ProviderEntry(
                id="cloud",
                name="ollama cloud",
                provider="ollama-cloud",
                auth_type=AUTH_API,
                base_url="https://ollama.com",
                api_key_ref="env:OLLAMA_API_KEY",
                model="minimax-m3",
            )
            local_provider = ProviderEntry(
                id="local",
                name="ollama local",
                provider="ollama",
                auth_type=AUTH_API,
                base_url="http://127.0.0.1:11434/v1",
                model="qwen3:14b",
            )
            ProviderStore(providers_path).save(ProviderConfig(active_id="cloud", entries=(cloud_provider, local_provider)))
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        profile="power",
                        profile_explicit=True,
                        provider_kind=cloud_provider.provider,
                        model=cloud_provider.model,
                        base_url=cloud_provider.base_url,
                        api_key_ref=cloud_provider.api_key_ref,
                        provider_config_path=providers_path,
                        active_provider=cloud_provider,
                        agents_dir=agents,
                        skills_dir=root / "skills",
                        tools_dir=root / "tools",
                        settings_path=root / "settings.json",
                        session_store_path=root / "sessions.db",
                        visible_history_path=root / "history.db",
                    )
                )
                app.switch_project("agent-home:local")
                settings = app.settings_payload()
                context_answer = app._context_answer()
            finally:
                os.chdir(cwd)

        self.assertEqual("local", settings["provider_id"])
        self.assertEqual("ollama local", settings["provider"])
        self.assertEqual("qwen3:14b", settings["provider_model"])
        self.assertEqual("", settings["model_override"])
        self.assertEqual("", settings["model_override_source"])
        self.assertEqual("qwen3:14b", settings["effective_model"])
        self.assertEqual("qwen3:14b", settings["model"])
        self.assertIn("- Provider actif : `ollama local · qwen3:14b`", context_answer)
        self.assertNotIn("Override agent", context_answer)
        self.assertIn("- Modèle effectif : `qwen3:14b`", context_answer)

    def test_message_feedback_is_only_available_for_local_ollama(self) -> None:
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents"
            providers_path = root / "providers.json"
            history_path = root / "history.db"
            workspace.mkdir()
            (agents / "default").mkdir(parents=True)
            (agents / "default" / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            cloud_provider = ProviderEntry(
                id="cloud",
                name="ollama cloud",
                provider="ollama-cloud",
                auth_type=AUTH_API,
                base_url="https://ollama.com",
                api_key_ref="env:OLLAMA_API_KEY",
                model="minimax-m3",
            )
            local_provider = ProviderEntry(
                id="local",
                name="ollama local",
                provider="ollama",
                auth_type=AUTH_API,
                base_url="http://127.0.0.1:11434/v1",
                model="qwen3:14b",
            )
            ProviderStore(providers_path).save(ProviderConfig(active_id="cloud", entries=(cloud_provider, local_provider)))
            try:
                os.chdir(workspace)
                cloud_app = ChatApiApp(
                    ChatApiState(
                        provider_kind=cloud_provider.provider,
                        model=cloud_provider.model,
                        base_url=cloud_provider.base_url,
                        api_key_ref=cloud_provider.api_key_ref,
                        provider_config_path=providers_path,
                        active_provider=cloud_provider,
                        agents_dir=agents,
                        skills_dir=root / "skills",
                        tools_dir=root / "tools",
                        visible_history_path=history_path,
                    )
                )
                local_app = ChatApiApp(
                    ChatApiState(
                        provider_kind=local_provider.provider,
                        model=local_provider.model,
                        base_url=local_provider.base_url,
                        provider_config_path=providers_path,
                        active_provider=local_provider,
                        agents_dir=agents,
                        skills_dir=root / "skills",
                        tools_dir=root / "tools",
                        visible_history_path=history_path,
                    )
                )
                store = VisibleHistoryStore(history_path)
                try:
                    message = store.append_message(
                        session_id=local_app.state.session.id,
                        role="assistant",
                        content="Réponse locale",
                        source="web",
                        project_path=workspace,
                    )
                finally:
                    store.close()
                cloud_result = cloud_app.store_message_feedback({"message_id": message.id, "rating": "up"})
                local_result = local_app.store_message_feedback({"message_id": message.id, "rating": "down"})
                history = local_app.history_payload()
            finally:
                os.chdir(cwd)

        self.assertFalse(cloud_result["ok"])
        self.assertEqual("feedback_unavailable", cloud_result["error"])
        self.assertTrue(local_result["ok"])
        self.assertTrue(history["reinforcement_enabled"])
        self.assertEqual(message.id, history["messages"][0]["id"])
        self.assertEqual("down", history["messages"][0]["feedback"]["rating"])
        self.assertEqual("qwen3:14b", history["messages"][0]["feedback"]["model"])

    def test_switch_agent_home_ignores_unknown_agent(self) -> None:
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents"
            workspace = root / "workspace"
            (agents / "default").mkdir(parents=True)
            workspace.mkdir()
            (agents / "default" / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        profile="power",
                        profile_explicit=True,
                        agents_dir=agents,
                        skills_dir=root / "skills",
                        tools_dir=root / "tools",
                        settings_path=root / "settings.json",
                        session_store_path=root / "sessions.db",
                        visible_history_path=root / "history.db",
                    )
                )
                app.switch_project("agent-home:ghost")
            finally:
                os.chdir(cwd)

            # A home for an agent that no longer exists must not break the active agent.
            self.assertEqual("default", app.state.agent_name)

    def test_web_chat_remembered_approval_skips_second_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents"
            skills = root / "skills"
            tools = Path(__file__).resolve().parents[1] / "bb9" / "tools"
            workspace.mkdir()
            target = workspace / "delete-me.txt"
            target.write_text("bye", encoding="utf-8")
            (agents / "default").mkdir(parents=True)
            skills.mkdir()
            (agents / "default" / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        profile="limited",
                        profile_explicit=True,
                        agents_dir=agents,
                        skills_dir=skills,
                        tools_dir=tools,
                        approval_store_path=root / "approvals.json",
                        visible_history_path=root / "history.db",
                    )
                )
                pending = app.run_message("/action shell rm delete-me.txt")
                approved = app.resolve_approval(pending["approval"]["id"], "allow", remember=True)
                target.write_text("again", encoding="utf-8")
                second = app.run_message("/action shell rm delete-me.txt")
                target_exists = target.exists()
            finally:
                os.chdir(cwd)

        self.assertTrue(approved["ok"])
        self.assertTrue(second["ok"])
        self.assertIsNone(second["approval"])
        self.assertFalse(target_exists)

    def test_web_chat_remembered_approval_is_workspace_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_workspace = root / "one"
            second_workspace = root / "two"
            agents = root / "agents"
            skills = root / "skills"
            tools = Path(__file__).resolve().parents[1] / "bb9" / "tools"
            first_workspace.mkdir()
            second_workspace.mkdir()
            (first_workspace / "delete-me.txt").write_text("bye", encoding="utf-8")
            second_target = second_workspace / "delete-me.txt"
            second_target.write_text("stay", encoding="utf-8")
            (agents / "default").mkdir(parents=True)
            skills.mkdir()
            (agents / "default" / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            cwd = Path.cwd()
            try:
                os.chdir(first_workspace)
                first_app = ChatApiApp(
                    ChatApiState(
                        profile="limited",
                        profile_explicit=True,
                        agents_dir=agents,
                        skills_dir=skills,
                        tools_dir=tools,
                        approval_store_path=root / "approvals.json",
                        visible_history_path=root / "history-one.db",
                    )
                )
                pending = first_app.run_message("/action shell rm delete-me.txt")
                first_app.resolve_approval(pending["approval"]["id"], "allow", remember=True)

                os.chdir(second_workspace)
                second_app = ChatApiApp(
                    ChatApiState(
                        profile="limited",
                        profile_explicit=True,
                        agents_dir=agents,
                        skills_dir=skills,
                        tools_dir=tools,
                        approval_store_path=root / "approvals.json",
                        visible_history_path=root / "history-two.db",
                    )
                )
                second = second_app.run_message("/action shell rm delete-me.txt")
                target_exists = second_target.exists()
            finally:
                os.chdir(cwd)

        self.assertTrue(second["ok"])
        self.assertEqual("Validation requise.", second["answer"])
        self.assertIsNotNone(second["approval"])
        self.assertTrue(target_exists)

    def test_web_chat_can_add_trusted_root_from_pending_approval(self) -> None:
        old_home = os.environ.get("BB9_HOME")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                os.environ["BB9_HOME"] = str(root / "bb9-home")
                workspace = root / "workspace"
                outside = root / "outside"
                agents = root / "agents"
                skills = root / "skills"
                tools = Path(__file__).resolve().parents[1] / "bb9" / "tools"
                workspace.mkdir()
                outside.mkdir()
                (agents / "default").mkdir(parents=True)
                skills.mkdir()
                (agents / "default" / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
                cwd = Path.cwd()
                try:
                    os.chdir(workspace)
                    app = ChatApiApp(
                        ChatApiState(
                            profile="power",
                            profile_explicit=True,
                            agents_dir=agents,
                            skills_dir=skills,
                            tools_dir=tools,
                            visible_history_path=root / "history.db",
                        )
                    )
                    pending = app.run_message(f"/action files write path={outside / 'note.txt'} text=ok")
                    approved = app.resolve_approval(pending["approval"]["id"], "allow", trust_root=True)
                    second = app.run_message(f"/action files write path={outside / 'next.txt'} text=ok")
                    trusted_roots = (Path(os.environ["BB9_HOME"]) / "trusted-roots.md").read_text(encoding="utf-8")
                finally:
                    os.chdir(cwd)

                self.assertEqual(str(outside.resolve()), pending["approval"]["trusted_root_candidate"])
                self.assertTrue(approved["ok"])
                self.assertEqual("ok", (outside / "note.txt").read_text(encoding="utf-8"))
                self.assertTrue(second["ok"])
                self.assertIsNone(second["approval"])
                self.assertEqual("ok", (outside / "next.txt").read_text(encoding="utf-8"))
                self.assertIn(str(outside.resolve()), trusted_roots)
        finally:
            if old_home is None:
                os.environ.pop("BB9_HOME", None)
            else:
                os.environ["BB9_HOME"] = old_home

    def test_web_chat_approval_flow_finalizes_with_provider_answer(self) -> None:
        class ApprovalProvider:
            def complete(self, prompt: str, *, images=()) -> str:
                if "# Observations tools" in prompt:
                    return "Le fichier a bien été supprimé."
                return "BB9_ACTION shell rm delete-me.txt"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents"
            skills = root / "skills"
            tools = Path(__file__).resolve().parents[1] / "bb9" / "tools"
            workspace.mkdir()
            target = workspace / "delete-me.txt"
            target.write_text("bye", encoding="utf-8")
            (agents / "default").mkdir(parents=True)
            skills.mkdir()
            (agents / "default" / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        profile="limited",
                        profile_explicit=True,
                        agents_dir=agents,
                        skills_dir=skills,
                        tools_dir=tools,
                        visible_history_path=root / "history.db",
                    )
                )
                with (
                    patch("bb9.core.runtime_service.build_provider_for_agent", return_value=ApprovalProvider()),
                    patch("bb9.api.chat.build_provider_for_agent", return_value=ApprovalProvider()),
                ):
                    pending = app.run_message("supprime le fichier")
                    approved = app.resolve_approval(pending["approval"]["id"], "allow")
                target_exists = target.exists()
            finally:
                os.chdir(cwd)

        self.assertTrue(pending["ok"])
        self.assertEqual("Validation requise.", pending["answer"])
        self.assertTrue(approved["ok"])
        self.assertEqual("Le fichier a bien été supprimé.", approved["answer"])
        self.assertFalse(target_exists)

    def test_web_chat_approval_flow_continues_after_approved_action(self) -> None:
        class MultiStepProvider:
            def __init__(self) -> None:
                self.calls = 0

            def complete(self, prompt: str, *, images=()) -> str:
                self.calls += 1
                if self.calls == 1:
                    return "BB9_ACTION shell rm delete-me.txt"
                if self.calls == 2:
                    return "BB9_ACTION files write path=created.txt text=ok"
                return "Workflow terminé."

        provider = MultiStepProvider()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents"
            skills = root / "skills"
            tools = Path(__file__).resolve().parents[1] / "bb9" / "tools"
            workspace.mkdir()
            target = workspace / "delete-me.txt"
            target.write_text("bye", encoding="utf-8")
            created = workspace / "created.txt"
            (agents / "default").mkdir(parents=True)
            skills.mkdir()
            (agents / "default" / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        profile="limited",
                        profile_explicit=True,
                        agents_dir=agents,
                        skills_dir=skills,
                        tools_dir=tools,
                        visible_history_path=root / "history.db",
                    )
                )
                with (
                    patch("bb9.core.runtime_service.build_provider_for_agent", return_value=provider),
                    patch("bb9.api.chat.build_provider_for_agent", return_value=provider),
                ):
                    pending = app.run_message("supprime puis cree un fichier")
                    approved = app.resolve_approval(pending["approval"]["id"], "allow")
                target_exists = target.exists()
                created_text = created.read_text(encoding="utf-8") if created.exists() else ""
            finally:
                os.chdir(cwd)

        self.assertTrue(pending["ok"])
        self.assertEqual("Validation requise.", pending["answer"])
        self.assertTrue(approved["ok"])
        self.assertEqual("Workflow terminé.", approved["answer"])
        self.assertFalse(target_exists)
        self.assertEqual("ok", created_text)
        self.assertGreaterEqual(provider.calls, 3)

    def test_web_chat_approval_uses_latest_persisted_profile_for_continuation(self) -> None:
        class MultiFileProvider:
            def __init__(self) -> None:
                self.calls = 0

            def complete(self, prompt: str, *, images=()) -> str:
                self.calls += 1
                if self.calls == 1:
                    return "BB9_ACTION files write path=index.html text=html"
                if self.calls == 2:
                    return "BB9_ACTION files write path=style.css text=css"
                return "Livraison complète."

        provider = MultiFileProvider()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents"
            skills = root / "skills"
            tools = Path(__file__).resolve().parents[1] / "bb9" / "tools"
            settings_path = root / "settings.json"
            workspace.mkdir()
            (agents / "default").mkdir(parents=True)
            skills.mkdir()
            (agents / "default" / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            SettingsStore(settings_path).save(UserSettings(profile="safe"))
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        profile="safe",
                        agents_dir=agents,
                        skills_dir=skills,
                        tools_dir=tools,
                        settings_path=settings_path,
                        visible_history_path=root / "history.db",
                    )
                )
                with (
                    patch("bb9.core.runtime_service.build_provider_for_agent", return_value=provider),
                    patch("bb9.api.chat.build_provider_for_agent", return_value=provider),
                ):
                    pending = app.run_message("cree deux fichiers")
                    SettingsStore(settings_path).set_profile("power")
                    approved = app.resolve_approval(pending["approval"]["id"], "allow")
                html = (workspace / "index.html").read_text(encoding="utf-8")
                css = (workspace / "style.css").read_text(encoding="utf-8")
            finally:
                os.chdir(cwd)

        self.assertEqual("Validation requise.", pending["answer"])
        self.assertTrue(approved["ok"])
        self.assertNotIn("approval", approved)
        self.assertEqual("Livraison complète.", approved["answer"])
        self.assertEqual("html", html)
        self.assertEqual("css", css)
        self.assertGreaterEqual(provider.calls, 3)

    def test_web_chat_approval_flow_denies_pending_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents"
            skills = root / "skills"
            tools = Path(__file__).resolve().parents[1] / "bb9" / "tools"
            workspace.mkdir()
            target = workspace / "keep-me.txt"
            target.write_text("stay", encoding="utf-8")
            (agents / "default").mkdir(parents=True)
            skills.mkdir()
            (agents / "default" / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        profile="limited",
                        profile_explicit=True,
                        agents_dir=agents,
                        skills_dir=skills,
                        tools_dir=tools,
                        visible_history_path=root / "history.db",
                    )
                )
                pending = app.run_message("/action shell rm keep-me.txt")
                denied = app.resolve_approval(pending["approval"]["id"], "deny")
                target_exists = target.exists()
            finally:
                os.chdir(cwd)

        self.assertTrue(denied["ok"])
        self.assertEqual("Action refusée.", denied["answer"])
        self.assertTrue(target_exists)

    def test_web_chat_invalid_approval_decision_keeps_pending_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents"
            skills = root / "skills"
            tools = Path(__file__).resolve().parents[1] / "bb9" / "tools"
            workspace.mkdir()
            (workspace / "keep-me.txt").write_text("stay", encoding="utf-8")
            (agents / "default").mkdir(parents=True)
            skills.mkdir()
            (agents / "default" / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(ChatApiState(profile="limited", profile_explicit=True, agents_dir=agents, skills_dir=skills, tools_dir=tools))
                pending = app.run_message("/action shell rm keep-me.txt")
                invalid = app.resolve_approval(pending["approval"]["id"], "maybe")
                status = app.status_payload()
            finally:
                os.chdir(cwd)

        self.assertFalse(invalid["ok"])
        self.assertEqual("invalid_approval_decision", invalid["error"])
        self.assertEqual(pending["approval"]["id"], status["pending_approval"]["id"])

    def test_web_chat_approved_action_exposes_running_state_without_blocking_status(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def slow_execute(_decision, _context, on_event=None):
            started.set()
            release.wait(timeout=5)
            return Observation(ok=True, summary="approved ok"), ()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents"
            skills = root / "skills"
            tools = Path(__file__).resolve().parents[1] / "bb9" / "tools"
            workspace.mkdir()
            (workspace / "delete-me.txt").write_text("bye", encoding="utf-8")
            (agents / "default").mkdir(parents=True)
            skills.mkdir()
            (agents / "default" / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            cwd = Path.cwd()
            result: dict[str, object] = {}
            try:
                os.chdir(workspace)
                app = ChatApiApp(ChatApiState(profile="limited", profile_explicit=True, agents_dir=agents, skills_dir=skills, tools_dir=tools))
                pending = app.run_message("/action shell rm delete-me.txt")

                def approve():
                    with patch("bb9.api.chat.execute_approved_action", slow_execute):
                        result.update(app.resolve_approval(pending["approval"]["id"], "allow"))

                thread = threading.Thread(target=approve)
                thread.start()
                self.assertTrue(started.wait(timeout=2))
                status = app.status_payload()
                busy = app.run_message("autre demande")
                release.set()
                thread.join(timeout=5)
            finally:
                os.chdir(cwd)

        self.assertTrue(status["running"])
        self.assertFalse(busy["ok"])
        self.assertEqual("agent_busy", busy["error"])
        self.assertTrue(result["ok"])

    def test_web_chat_blocks_new_message_while_approval_is_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents"
            skills = root / "skills"
            tools = Path(__file__).resolve().parents[1] / "bb9" / "tools"
            workspace.mkdir()
            target = workspace / "delete-me-later.txt"
            target.write_text("bye", encoding="utf-8")
            (agents / "default").mkdir(parents=True)
            skills.mkdir()
            (agents / "default" / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        profile="limited",
                        profile_explicit=True,
                        agents_dir=agents,
                        skills_dir=skills,
                        tools_dir=tools,
                        visible_history_path=root / "history.db",
                    )
                )
                pending = app.run_message("/action shell rm delete-me-later.txt")
                blocked = app.run_message("deuxième demande")
                approved = app.resolve_approval(pending["approval"]["id"], "allow")
            finally:
                os.chdir(cwd)

        self.assertTrue(pending["ok"])
        self.assertFalse(blocked["ok"])
        self.assertEqual("approval_pending", blocked["error"])
        self.assertEqual(pending["approval"]["id"], blocked["approval"]["id"])
        self.assertTrue(approved["ok"])

    def test_web_chat_status_prunes_expired_pending_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents"
            skills = root / "skills"
            tools = Path(__file__).resolve().parents[1] / "bb9" / "tools"
            workspace.mkdir()
            (workspace / "delete-me.txt").write_text("bye", encoding="utf-8")
            (agents / "default").mkdir(parents=True)
            skills.mkdir()
            (agents / "default" / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(ChatApiState(profile="limited", profile_explicit=True, agents_dir=agents, skills_dir=skills, tools_dir=tools))
                pending = app.run_message("/action shell rm delete-me.txt")
                approval = app._pending_approval
                app._pending_approval = approval.__class__(
                    id=approval.id,
                    guardian=approval.guardian,
                    context=approval.context,
                    created_at=time.time() - 360,
                    session_id=approval.session_id,
                    project_path=approval.project_path,
                    message=approval.message,
                )
                status = app.status_payload()
                resolved = app.resolve_approval(pending["approval"]["id"], "allow")
            finally:
                os.chdir(cwd)

        self.assertIsNone(status["pending_approval"])
        self.assertFalse(resolved["ok"])
        self.assertEqual("approval_not_found", resolved["error"])

    def test_web_chat_new_session_clears_pending_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents"
            skills = root / "skills"
            tools = Path(__file__).resolve().parents[1] / "bb9" / "tools"
            workspace.mkdir()
            (workspace / "delete-me.txt").write_text("bye", encoding="utf-8")
            (agents / "default").mkdir(parents=True)
            skills.mkdir()
            (agents / "default" / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(ChatApiState(profile="limited", profile_explicit=True, agents_dir=agents, skills_dir=skills, tools_dir=tools))
                pending = app.run_message("/action shell rm delete-me.txt")
                created = app.new_session()
                resolved = app.resolve_approval(pending["approval"]["id"], "allow")
            finally:
                os.chdir(cwd)

        self.assertIsNone(created["pending_approval"])
        self.assertFalse(resolved["ok"])
        self.assertEqual("approval_not_found", resolved["error"])

    def test_web_chat_command_matches_prioritize_supported_and_archive_commands(self) -> None:
        if shutil.which("node") is None:
            self.skipTest("node unavailable")
        script = """
import {commandMatches} from './bb9/chat-web/chat-ui.js';
const commands = [
  {name: '/help', source: 'native', supported: true},
  {name: '/context', source: 'native', supported: true},
  {name: '/history', source: 'native', supported: true},
  {name: '/new', source: 'native', supported: true},
  {name: '/compact', source: 'native', supported: true},
  {name: '/model', source: 'native', supported: false},
  {name: '/goal', source: 'native', supported: false},
  {name: '/cron', source: 'native', supported: false},
  {name: '/dream', source: 'native', supported: false},
  {name: '/build', source: 'skill', supported: true},
  {name: '/build delegate', source: 'skill', supported: true},
  {name: '/plan ...', source: 'skill', supported: true},
  {name: '/plan', source: 'skill', supported: true},
  {name: '/secrets', source: 'tool', supported: true},
  {name: '/project-map', source: 'local-skill', supported: true},
  {name: '/project-impact', source: 'local-skill', supported: true},
  {name: '/project-modify', source: 'local-skill', supported: true},
  {name: '/project-create-component', source: 'local-skill', supported: true},
  {name: '/project-sketch', source: 'local-skill', supported: true},
  {name: '/project-check', source: 'local-skill', supported: true},
  {name: '/project-review', source: 'local-skill', supported: true},
  {name: '/project-a11y-check', source: 'local-skill', supported: true},
  {name: '/project-cleanup', source: 'local-skill', supported: true},
  {name: '/project-docs', source: 'local-skill', supported: true},
];
console.log(JSON.stringify(commandMatches('/', commands).map((command) => command.name)));
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )

        names = json.loads(result.stdout)
        self.assertEqual(["/plan", "/build"], names[:2])
        self.assertLess(names.index("/compact"), names.index("/secrets"))
        self.assertLess(names.index("/secrets"), names.index("/project-check"))
        self.assertLess(names.index("/plan"), names.index("/project-check"))
        self.assertIn("/project-docs", names)
        self.assertEqual(1, names.count("/build"))
        self.assertEqual(1, names.count("/plan"))

    def test_web_live_trace_marks_previous_process_steps_past(self) -> None:
        if shutil.which("node") is None:
            self.skipTest("node unavailable")
        script = """
import {liveTraceDisplayGroups} from './bb9/chat-web/chat-ui.js';
const groups = liveTraceDisplayGroups([
  {kind: 'process', title: 'Comprendre la demande', status: 'en cours'},
  {kind: 'process', title: 'Choisir la prochaine étape', status: 'en cours'},
]);
console.log(JSON.stringify(groups.map((group) => group.status)));
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(["passé", "en cours"], json.loads(result.stdout))

    def test_web_live_trace_keeps_running_subagents_visible(self) -> None:
        if shutil.which("node") is None:
            self.skipTest("node unavailable")
        script = """
import {liveTraceVisibleGroups} from './bb9/chat-web/chat-ui.js';
const groups = liveTraceVisibleGroups([
  {kind: 'process', title: 'Lire le plan', status: 'en cours'},
  {kind: 'subagent', title: 'default/default', summary: 'Docs', status: 'en cours', subagentStatus: 'running'},
  {kind: 'subagent', title: 'default/default', summary: 'Tests', status: 'en cours', subagentStatus: 'running'},
  {kind: 'process', title: 'Étape 1', status: 'en cours'},
  {kind: 'process', title: 'Étape 2', status: 'en cours'},
  {kind: 'process', title: 'Étape 3', status: 'en cours'},
  {kind: 'process', title: 'Étape 4', status: 'en cours'},
  {kind: 'process', title: 'Étape 5', status: 'en cours'},
  {kind: 'process', title: 'Étape 6', status: 'en cours'},
], 4);
console.log(JSON.stringify(groups.map((group) => [group.kind, group.summary || group.title, group.status, group.subagentStatus || ''])));
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(
            [
                ["subagent", "Docs", "en cours", "running"],
                ["subagent", "Tests", "en cours", "running"],
                ["process", "Étape 3", "passé", ""],
                ["process", "Étape 4", "passé", ""],
                ["process", "Étape 5", "passé", ""],
                ["process", "Étape 6", "en cours", ""],
            ],
            json.loads(result.stdout),
        )

    def test_web_plan_retry_button_only_targets_direct_errors(self) -> None:
        if shutil.which("node") is None:
            self.skipTest("node unavailable")
        script = """
import {planHasRetryableErrors} from './bb9/chat-web/chat-ui.js';
const cases = [
  [{status: 'error', blockers: 'ProviderError'}],
  [{status: 'error', blockers: 'dependency:T1'}],
  [{status: 'error', summary: 'Task skipped because dependencies could not be resolved.'}],
  [{status: 'blocked'}],
  [{done: true, status: 'done'}],
];
console.log(JSON.stringify(cases.map((tasks) => planHasRetryableErrors(tasks))));
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual([True, False, False, False, False], json.loads(result.stdout))

    def test_web_idle_trace_label_does_not_blame_provider(self) -> None:
        if shutil.which("node") is None:
            self.skipTest("node unavailable")
        script = """
import {idleTraceLabel} from './bb9/chat-web/chat-ui.js';
const labels = [
  idleTraceLabel([
    {type: 'process', summary: 'Subagent utilisé', data: {process_kind: 'subagent', subagent_status: 'running', status: 'en cours', worker: 'default/default', task_title: 'API'}},
  ], 278),
  idleTraceLabel([
    {type: 'process', summary: 'Lire le plan', data: {status: 'en cours', detail: 'plan...'}},
  ], 42),
  idleTraceLabel([], 15),
];
console.log(JSON.stringify(labels));
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )

        labels = json.loads(result.stdout)
        self.assertEqual("Subagent en cours · 278s sans nouvelle trace", labels[0])
        self.assertEqual("Toujours en cours · 42s sans nouvelle trace", labels[1])
        self.assertEqual("Aucune nouvelle trace · 15s sans nouvelle trace", labels[2])
        self.assertNotIn("provider", " ".join(labels).lower())

    def test_web_trace_groups_rebuild_process_from_decision_trace_artifact(self) -> None:
        if shutil.which("node") is None:
            self.skipTest("node unavailable")
        script = """
import {traceGroupsFromArtifacts} from './bb9/chat-web/renderers.js';
const groups = traceGroupsFromArtifacts([{kind: 'report', title: 'Trace de décision', metadata: {entries: [
  {type: 'process', summary: 'Subagent utilisé', data: {status: 'en cours', detail: '`default` pour `Créer la page`'}},
  {type: 'process', summary: 'Tâche terminée', data: {status: 'terminé', detail: 'Créer la page'}},
]}}]);
console.log(JSON.stringify(groups.map((group) => [group.title, group.summary, group.status])));
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(
            [
                ["Subagent utilisé", "`default` pour `Créer la page`", "en cours"],
                ["Tâche terminée", "Créer la page", "terminé"],
            ],
            json.loads(result.stdout),
        )

    def test_web_trace_groups_merge_structured_subagent_events(self) -> None:
        if shutil.which("node") is None:
            self.skipTest("node unavailable")
        script = """
import {workflowGroups} from './bb9/chat-web/renderers.js';
const groups = workflowGroups([
  {type: 'process', summary: 'Subagent utilisé', data: {process_kind: 'subagent', subagent_status: 'running', status: 'en cours', worker: 'default/default', task_title: 'Créer la page'}},
  {type: 'process', summary: 'Tâche terminée', data: {process_kind: 'subagent', subagent_status: 'done', status: 'terminé', task_title: 'Créer la page'}},
]);
console.log(JSON.stringify(groups.map((group) => [group.kind, group.title, group.summary, group.status, group.subagentStatus])));
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(
            [["subagent", "default/default", "Créer la page", "terminé", "done"]],
            json.loads(result.stdout),
        )

    def test_web_trace_groups_attach_blocker_to_failed_task(self) -> None:
        if shutil.which("node") is None:
            self.skipTest("node unavailable")
        script = """
import {workflowGroups} from './bb9/chat-web/renderers.js';
const groups = workflowGroups([
  {type: 'process', summary: 'Tâche en erreur', data: {process_kind: 'subagent', subagent_status: 'error', status: 'erreur', task_title: 'Vérifier le rendu réel de la démo'}},
  {type: 'process', summary: 'Blocage détecté', data: {status: 'erreur', detail: 'Evidence:'}},
  {type: 'process', summary: 'Blocage détecté', data: {status: 'erreur', detail: "la tâche 'Durcir la validation des liens et attributs' n'est pas terminée"}},
  {type: 'process', summary: 'Subagent utilisé', data: {process_kind: 'subagent', subagent_status: 'running', status: 'en cours', worker: 'default/default', task_title: 'Ajouter une vérification API minimale'}},
]);
console.log(JSON.stringify(groups.map((group) => [group.kind, group.title, group.summary, group.status, group.subagentStatus, group.blockers || []])));
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(
            [
                [
                    "subagent",
                    "Tâche bloquée",
                    "Vérifier le rendu réel de la démo",
                    "bloqué",
                    "blocked",
                    ["la tâche 'Durcir la validation des liens et attributs' n'est pas terminée"],
                ],
                ["subagent", "default/default", "Ajouter une vérification API minimale", "en cours", "running", []],
            ],
            json.loads(result.stdout),
        )

    def test_web_trace_groups_uses_structured_dependency_block_category(self) -> None:
        if shutil.which("node") is None:
            self.skipTest("node unavailable")
        script = """
import {workflowGroups} from './bb9/chat-web/renderers.js';
const groups = workflowGroups([
  {type: 'process', summary: 'Tâche en erreur', data: {process_kind: 'subagent', subagent_status: 'error', status: 'erreur', task_title: 'Vérifier'}},
  {type: 'process', summary: 'Blocage détecté', data: {status: 'bloqué', detail: 'dependency:T2', block_category: 'dependency'}},
]);
console.log(JSON.stringify(groups.map((group) => [group.status, group.subagentStatus, group.blockers || []])));
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual([["bloqué", "blocked", ["dependency:T2"]]], json.loads(result.stdout))

    def test_web_latest_validation_message_matches_build_approval_text(self) -> None:
        if shutil.which("node") is None:
            self.skipTest("node unavailable")
        script = """
import {latestValidationMessageIndex} from './bb9/chat-web/chat-ui.js';
const cases = [
  [{role: 'assistant', content: 'Validation requise.'}],
  [{role: 'assistant', content: 'Validation requise pour `Ajouter une vérification API minimale`.\\nRaison : compound shell command requires confirmation'}],
  [{role: 'assistant', content: 'Validation requise pour `Autre tâche`.\\nRaison : x'}],
  [
    {role: 'assistant', content: 'Validation requise.'},
    {role: 'assistant', content: 'Build terminé.'},
  ],
];
const approval = {task_title: 'Ajouter une vérification API minimale'};
console.log(JSON.stringify(cases.map((messages) => latestValidationMessageIndex(messages, approval))));
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual([0, 0, 0, -1], json.loads(result.stdout))

    def test_web_chat_server_serves_static_app_over_same_api(self) -> None:
        app = ChatApiApp(ChatApiState())
        server = chat_api_server(app, 0, static_root=resources.files("bb9").joinpath("chat-web"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=5) as response:
                html = response.read().decode("utf-8")
                cache_control = response.headers.get("cache-control")
            with urlopen(f"http://127.0.0.1:{server.server_port}/app.css", timeout=5) as response:
                css = response.read().decode("utf-8")
            with urlopen(f"http://127.0.0.1:{server.server_port}/app.js", timeout=5) as response:
                app_js = response.read().decode("utf-8")
            with urlopen(f"http://127.0.0.1:{server.server_port}/bb9-client.js", timeout=5) as response:
                client_js = response.read().decode("utf-8")
            with urlopen(f"http://127.0.0.1:{server.server_port}/chat-ui.js", timeout=5) as response:
                chat_ui_js = response.read().decode("utf-8")
            with urlopen(f"http://127.0.0.1:{server.server_port}/renderers.js", timeout=5) as response:
                renderers_js = response.read().decode("utf-8")
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual("no-store", cache_control)
        self.assertIn("<title>BB9 Web Chat</title>", html)
        self.assertIn('<link rel="stylesheet" href="./app.css?v=workspace-switch-1">', html)
        self.assertIn('<script type="module" src="./app.js?v=workspace-switch-1"></script>', html)
        self.assertIn('id="plan-panel"', html)
        self.assertIn('data-panel="skills"', html)
        self.assertIn('id="skills-modal"', html)
        self.assertIn('id="skills-list"', html)
        self.assertIn('id="skill-body"', html)
        self.assertIn(".message-images", css)
        self.assertIn(".plan-panel", css)
        self.assertIn(".skills-modal-box", css)
        self.assertIn(".skills-modal-body", css)
        self.assertIn(".skills-list", css)
        self.assertIn(".skill-toggle", css)
        self.assertIn(".skill-body", css)
        self.assertIn(".plan-heading-title", css)
        self.assertIn(".plan-clear", css)
        self.assertIn(".plan-retry", css)
        self.assertIn("border-right: 2px solid currentColor", css)
        self.assertIn("transform: rotate(225deg)", css)
        self.assertIn("transform: rotate(45deg)", css)
        self.assertIn(".plan-task-box", css)
        self.assertIn(".plan-task.blocked", css)
        self.assertIn(".plan-task.blocked .plan-task-box", css)
        self.assertIn(".copy-message", css)
        self.assertIn(".copy-message svg", css)
        self.assertIn(".message-feedback", css)
        self.assertIn(".feedback-button", css)
        self.assertIn(":root[data-theme=\"dark\"]", css)
        self.assertIn("--composer: #3c3f35", css)
        self.assertIn("--trace-panel: #20211f", css)
        self.assertIn("--warning-bg: #2f2816", css)
        self.assertIn("--success:", css)
        self.assertIn("--danger:", css)
        self.assertIn("--control-bg:", css)
        self.assertIn("--control-hover:", css)
        self.assertIn("--control-fg:", css)
        self.assertIn("--badge-bg:", css)
        self.assertIn("--badge-text:", css)
        self.assertIn("--trace-neutral:", css)
        self.assertIn("background: transparent", css)
        self.assertIn("header::after", css)
        self.assertIn("mask-image: linear-gradient(to bottom, #000, transparent)", css)
        self.assertIn("--composer-space: 148px", css)
        self.assertIn("--scrollbar-width: 18px", css)
        self.assertIn("grid-template-rows: auto 1fr", css)
        self.assertIn("scrollbar-gutter: stable", css)
        self.assertIn("right: var(--scrollbar-width)", css)
        self.assertIn("padding: 22px 22px calc(var(--composer-space) + 22px)", css)
        self.assertIn("position: fixed", css)
        self.assertIn("bottom: 0", css)
        self.assertIn("pointer-events: none", css)
        self.assertIn("form > *", css)
        self.assertIn("form::before", css)
        self.assertIn("linear-gradient(to top, #000 0%, #000 58%, transparent 100%)", css)
        self.assertIn("background: var(--composer)", css)
        self.assertIn("box-shadow: 0 18px 48px rgba(0, 0, 0, 0.18)", css)
        self.assertIn("border-radius: 28px", css)
        self.assertIn("min-height: 30px", css)
        self.assertIn("resize: none", css)
        self.assertIn(".composer-settings", css)
        self.assertIn(".model-effective", css)
        self.assertIn(".composer-run-actions", css)
        self.assertIn(".attach", css)
        self.assertIn("color: color-mix(in srgb, var(--text) 42%, transparent)", css)
        self.assertIn(".send-icon", css)
        self.assertIn("background: var(--control-bg)", css)
        self.assertIn("color: var(--control-fg)", css)
        self.assertIn(".stop-run", css)
        self.assertIn(".message.working", css)
        self.assertIn(".working-trace", css)
        self.assertIn("width: min(720px", css)
        self.assertIn(".working-trace .trace-step.active .trace-dot", css)
        self.assertIn(".trace-step.blocked .trace-dot", css)
        self.assertIn(".trace-step.past .trace-dot", css)
        self.assertIn(".trace-step.done .trace-dot", css)
        self.assertIn(".trace-step.subagent", css)
        self.assertIn("@keyframes trace-active-pulse", css)
        self.assertNotIn(".pixel-loader", css)
        self.assertNotIn("working-label::after", css)
        self.assertNotIn("@keyframes cursor-blink", css)
        self.assertIn("prefers-reduced-motion: reduce", css)
        self.assertIn(".icon-select svg", css)
        self.assertIn("justify-self: end", css)
        self.assertIn(".command-menu", css)
        self.assertIn("position: absolute", css)
        self.assertIn("bottom: calc(100% - 8px)", css)
        self.assertIn("transform: translateX(-50%)", css)
        self.assertIn("box-shadow: none", css)
        self.assertIn("box-shadow: inset 3px 0 0 var(--accent)", css)
        self.assertIn(".draft-queue", css)
        self.assertIn(".draft-queue-title", css)
        self.assertNotIn(".stop-icon", css)
        self.assertIn(".git-count", css)
        self.assertIn(".git-panel-head", css)
        self.assertIn(".git-panel.fullscreen", css)
        self.assertIn(".git-panel-actions", css)
        self.assertIn(".git-panel-action svg", css)
        self.assertIn(".git-commit", css)
        self.assertIn(".git-commit-preview", css)
        self.assertIn(".git-commit-actions", css)
        self.assertIn(".git-files", css)
        self.assertIn(".git-file-status", css)
        self.assertIn(".git-file-main", css)
        self.assertIn(".git-branch-note", css)
        self.assertIn(".branch-select:disabled", css)
        self.assertIn(".git-diff-detail", css)
        self.assertIn(".git-diff-line-number", css)
        self.assertIn(".git-diff-old", css)
        self.assertIn(".git-diff-new", css)
        self.assertIn("grid-template-columns: 34px 34px", css)
        self.assertIn(".git-diff-detail.additions-only", css)
        self.assertIn("grid-template-columns: 34px minmax", css)
        self.assertIn(".git-diff-add", css)
        self.assertIn(".git-diff-remove", css)
        self.assertNotIn(".file-artifact", css)
        self.assertNotIn(".file-preview-html", css)
        self.assertIn(".markdown a", css)
        self.assertIn("field-sizing: content", css)
        self.assertIn("createBb9Chat", app_js)
        self.assertIn("httpBb9Client({apiBase: '/api'})", app_js)
        self.assertIn("fetch(url", client_js)
        self.assertIn("updateSettings(settings)", client_js)
        self.assertIn("switchSession(id)", client_js)
        self.assertIn("clearPlan(projectPath", client_js)
        self.assertIn("/plan/clear", client_js)
        self.assertIn("imageUrl(path)", client_js)
        self.assertNotIn("fileUrl(path)", client_js)
        self.assertNotIn("encodePath(path)", client_js)
        self.assertIn("commands()", client_js)
        self.assertIn("stop()", client_js)
        self.assertIn("feedback(messageId, rating", client_js)
        self.assertIn("/feedback", client_js)
        self.assertIn("themes()", client_js)
        self.assertIn("models()", client_js)
        self.assertIn('id="model-effective"', html)
        self.assertIn("git()", client_js)
        self.assertIn("gitDiff(path)", client_js)
        self.assertIn("skills()", client_js)
        self.assertIn("toggleSkill(name, enabled)", client_js)
        self.assertIn("updateSkill(data)", client_js)
        self.assertIn("/skills/toggle", client_js)
        self.assertIn("/skills/update", client_js)
        self.assertIn("gitCommitMessage()", client_js)
        self.assertIn("commitGit(message)", client_js)
        self.assertIn("switchGitBranch(branch)", client_js)
        self.assertIn("runEvents(after = 0)", client_js)
        self.assertIn("after=${encodeURIComponent(after)}", client_js)
        self.assertIn("/run/events", client_js)
        self.assertIn("createBb9Chat", chat_ui_js)
        self.assertIn(
            "renderMessageContent(content, client, {markdown: role === 'assistant' || role === 'notification'})",
            chat_ui_js,
        )
        self.assertIn("capabilities", chat_ui_js)
        self.assertIn("event.key === 'Enter' && !event.shiftKey", chat_ui_js)
        self.assertIn("localStorage", chat_ui_js)
        self.assertIn("Serveur BB9 web ancien ou incomplet", chat_ui_js)
        self.assertIn("Historique indisponible", chat_ui_js)
        self.assertIn("loadProjects", chat_ui_js)
        self.assertIn("updateModelEffective", chat_ui_js)
        self.assertIn("Accueil d'agent canonique", chat_ui_js)
        self.assertIn("bb9.chat.channel.seen.v2", chat_ui_js)
        self.assertIn("reconcileChannelSeen", chat_ui_js)
        self.assertIn("avec nouvelle activité", chat_ui_js)
        self.assertIn("loadCommands", chat_ui_js)
        self.assertIn("openSkillsModal", chat_ui_js)
        self.assertIn("loadSkillsModal", chat_ui_js)
        self.assertIn("openNotesModal", chat_ui_js)
        self.assertIn("panel === 'notes'", chat_ui_js)
        self.assertIn("Notes &amp; todos", html)
        self.assertIn("toggleSkill(skill)", chat_ui_js)
        self.assertIn("client.updateSkill", chat_ui_js)
        self.assertIn("panel === 'skills'", chat_ui_js)
        self.assertIn("agentTelegramPayload", chat_ui_js)
        self.assertIn("agent-telegram-token", html)
        self.assertIn("agent-telegram-chat-ids", html)
        self.assertIn("agent-telegram-chat-id-input", html)
        self.assertIn("agent-telegram-chat-id-chips", html)
        self.assertIn("addAgentTelegramChatId", chat_ui_js)
        self.assertIn("array-chip", css)
        self.assertIn("loadThemes", chat_ui_js)
        self.assertIn("handleCommandKey", chat_ui_js)
        self.assertIn("copyButton(content)", chat_ui_js)
        self.assertIn("navigator.clipboard.writeText", chat_ui_js)
        self.assertIn("reinforcementEnabled", chat_ui_js)
        self.assertIn("renderFeedbackControls(meta)", chat_ui_js)
        self.assertIn("submitFeedback(button, messageId, rating)", chat_ui_js)
        self.assertIn("Boolean(payload.reinforcement_enabled)", chat_ui_js)
        self.assertIn("workflowCommandRank", chat_ui_js)
        self.assertIn("name === '/build'", chat_ui_js)
        self.assertIn("dependencyOnlyBlockers", chat_ui_js)
        self.assertIn("dependencySkipSummary", chat_ui_js)
        self.assertIn("planTaskStatus(task) === 'blocked'", chat_ui_js)
        self.assertIn("planHasRetryableErrors", chat_ui_js)
        self.assertIn("Relancer les tâches en erreur du plan", chat_ui_js)
        self.assertIn("/build --retry-errors", chat_ui_js)
        self.assertIn("retryPlanErrors(event)", chat_ui_js)
        self.assertIn("showActivityIndicator", chat_ui_js)
        self.assertIn("finalizeActivityMessage", chat_ui_js)
        self.assertIn("node.className = 'message assistant'", chat_ui_js)
        self.assertIn("trace.className = 'trace working-live-trace'", chat_ui_js)
        self.assertIn("trace.open = true", chat_ui_js)
        self.assertIn("removeActivityIndicator", chat_ui_js)
        self.assertIn("renderLiveTrace", chat_ui_js)
        self.assertIn("finalizeActivityMessage(payload.answer", chat_ui_js)
        self.assertIn("shouldStickToBottom", chat_ui_js)
        self.assertIn("scrollToThreadBottom", chat_ui_js)
        self.assertIn("distance <= threshold", chat_ui_js)
        self.assertNotIn("scrollIntoView", chat_ui_js)
        self.assertIn("startLiveTracePolling", chat_ui_js)
        self.assertIn("client.runEvents", chat_ui_js)
        self.assertIn("restoreActivityAfterRender", chat_ui_js)
        self.assertIn("showActivityIndicator({preserveTrace: true})", chat_ui_js)
        self.assertIn("recoverCompletedRunFromHistory()", chat_ui_js)
        self.assertIn("liveTraceCursor", chat_ui_js)
        self.assertIn("liveTraceInFlight", chat_ui_js)
        self.assertIn("liveTraceGeneration", chat_ui_js)
        self.assertIn("generation !== liveTraceGeneration", chat_ui_js)
        self.assertIn("liveTraceRunId", chat_ui_js)
        self.assertIn("!payload.running || !runId", chat_ui_js)
        self.assertIn("statusInFlight", chat_ui_js)
        self.assertIn("updateRunWaitLabel", chat_ui_js)
        self.assertIn("idleTraceLabel", chat_ui_js)
        self.assertIn("Subagent en cours", chat_ui_js)
        self.assertIn("sans nouvelle trace", chat_ui_js)
        self.assertIn("Aucune nouvelle trace", chat_ui_js)
        self.assertIn("En attente provider", chat_ui_js)
        self.assertIn("run_idle_seconds", chat_ui_js)
        self.assertIn("projectReloadInFlight", chat_ui_js)
        self.assertIn("reloadProjectViewAfterExternalSwitch", chat_ui_js)
        self.assertIn("const projectChanged = syncCurrentProject(payload)", chat_ui_js)
        self.assertIn("slice(-50)", chat_ui_js)
        self.assertIn("Promise.allSettled", chat_ui_js)
        self.assertIn("payload.next", chat_ui_js)
        self.assertIn("window.setInterval(poll, 900)", chat_ui_js)
        self.assertIn("approvalSummary", renderers_js)
        self.assertIn("approval-detail", renderers_js)
        self.assertIn("Commande", renderers_js)
        self.assertIn(".approval-detail code", css)
        self.assertIn("renderInactiveApprovalNotice", chat_ui_js)
        self.assertIn("approval_not_found", chat_ui_js)
        self.assertIn("latestValidationMessageIndex", chat_ui_js)
        self.assertIn("payload.pending_approval", chat_ui_js)
        self.assertIn("Validation inactive", chat_ui_js)
        self.assertIn(".approval.inactive", css)
        self.assertIn("working-trace timeline", chat_ui_js)
        self.assertIn("Traitement en cours", chat_ui_js)
        self.assertIn("workflowGroups", chat_ui_js)
        self.assertIn("Préparer le travail", chat_ui_js)
        self.assertNotIn("tool: 'Réflexion'", chat_ui_js)
        self.assertNotIn("pixel-loader", chat_ui_js)
        self.assertIn("recoverAfterChatNetworkError", chat_ui_js)
        self.assertIn("Connexion interrompue; résultat récupéré", chat_ui_js)
        self.assertIn("renderedMessageCount()", chat_ui_js)
        self.assertIn("15 * 60 * 1000", chat_ui_js)
        self.assertIn("reconcileRuntimeStatus(payload)", chat_ui_js)
        self.assertIn("Date.now() - runningSince > 1800", chat_ui_js)
        self.assertIn("startStatusPolling", chat_ui_js)
        self.assertIn("window.setInterval", chat_ui_js)
        self.assertIn("stopRun", chat_ui_js)
        self.assertIn("prepareGitCommit", chat_ui_js)
        self.assertIn("commitGitChanges", chat_ui_js)
        self.assertIn("addCommitTrace", chat_ui_js)
        self.assertIn("data: {tool: 'git', cmd: 'git commit'}", chat_ui_js)
        self.assertNotIn("elements.banner.textContent = payload.commit ? `Commit créé", chat_ui_js)
        self.assertIn("draftQueue", chat_ui_js)
        self.assertIn("pendingApproval", chat_ui_js)
        self.assertIn("Validation en attente", chat_ui_js)
        self.assertIn("elements.stop.addEventListener('click', stopRun)", chat_ui_js)
        self.assertIn("Ajouter à la queue", chat_ui_js)
        self.assertIn("demande(s) en attente", chat_ui_js)
        self.assertIn("resizeComposer", chat_ui_js)
        self.assertIn("ResizeObserver", chat_ui_js)
        self.assertIn("syncComposerSpace", chat_ui_js)
        self.assertIn("clearPlan(event)", chat_ui_js)
        self.assertIn("Vider le plan courant", chat_ui_js)
        self.assertNotIn("chevron.textContent = '⌄'", chat_ui_js)
        self.assertIn("--scrollbar-width", chat_ui_js)
        self.assertIn("measuredScrollbarWidth", chat_ui_js)
        self.assertIn("Math.max(18, measuredScrollbarWidth)", chat_ui_js)
        self.assertIn("elements.main.offsetWidth - elements.main.clientWidth", chat_ui_js)
        self.assertIn("Math.min(elements.input.scrollHeight, 180)", chat_ui_js)
        self.assertIn("runNextDraft", chat_ui_js)
        self.assertIn("loadGit", chat_ui_js)
        self.assertIn("renderGitPanel", chat_ui_js)
        self.assertIn("gitStatusLabel", chat_ui_js)
        self.assertIn("Nouveau", chat_ui_js)
        self.assertIn("Modifié", chat_ui_js)
        self.assertIn("loadGitFileDiff", chat_ui_js)
        self.assertIn("renderColoredDiff", chat_ui_js)
        self.assertIn("diffHasAdditionsOnly", chat_ui_js)
        self.assertIn("diffLineClass", chat_ui_js)
        self.assertIn("diffLineNumbers", chat_ui_js)
        self.assertIn("parseHunkHeader", chat_ui_js)
        self.assertIn("toggleGitPanelFullscreen", chat_ui_js)
        self.assertIn("Commit ou stash requis avant de changer de branche.", chat_ui_js)
        self.assertIn("switchGitBranch", chat_ui_js)
        self.assertIn("elements.profile.addEventListener('change', saveSettings)", chat_ui_js)
        self.assertIn("renderModelOptions", chat_ui_js)
        self.assertIn("selectedModelProviderId", chat_ui_js)
        self.assertIn("switchProject", chat_ui_js)
        self.assertIn("projectLabel", chat_ui_js)
        self.assertIn("className = 'trace'", renderers_js)
        self.assertIn("className = 'timeline'", renderers_js)
        self.assertIn("title.textContent = 'Processus'", renderers_js)
        self.assertIn("workflowGroups(events)", renderers_js)
        self.assertIn("event.type === 'process'", renderers_js)
        self.assertIn("process_kind", renderers_js)
        self.assertIn("subagentStatusLabel", renderers_js)
        self.assertIn("dependencyBlockerDetail", renderers_js)
        self.assertIn("Trace de décision", renderers_js)
        self.assertIn("decisionEntries.map", renderers_js)
        self.assertIn("renderTraceStep", renderers_js)
        self.assertIn("'en cours'", renderers_js)
        self.assertIn("renderMarkdownFragment", renderers_js)
        self.assertNotIn("renderFileArtifact", renderers_js)
        self.assertNotIn("client.fileUrl", renderers_js)
        self.assertNotIn("file-preview-html", renderers_js)
        self.assertIn("appendInlineMarkdown", renderers_js)
        self.assertIn("safeHref", renderers_js)
        self.assertIn("document.createElement('a')", renderers_js)
        self.assertIn("compact-markdown", renderers_js)
        self.assertIn("options.markdown", renderers_js)
        self.assertIn("document.createElement('ol')", renderers_js)
        self.assertIn("traceGroupsFromArtifacts", renderers_js)
        self.assertIn("artifact.kind !== 'tool_trace'", renderers_js)
        self.assertIn("className = 'message-images'", renderers_js)
        self.assertIn("markdownPattern", renderers_js)
        self.assertIn(".markdown h1", css)
        self.assertIn(".message-text.markdown", css)
        self.assertIn(".message-text:not(.markdown)", css)
        self.assertIn(".markdown ol", css)
        self.assertIn(".compact-markdown", css)
        self.assertIn("cleanImagePath", renderers_js)
        self.assertIn("className = 'thumb-remove'", chat_ui_js)
        self.assertIn("removeAttachment(index)", chat_ui_js)
        self.assertIn("REQUIRED_FEATURES", client_js)
        self.assertIn("git-diff-api", client_js)
        self.assertIn("parseJsonResponse", client_js)
        self.assertIn("invalid_json_response", client_js)
        self.assertIn("projects()", client_js)
        self.assertIn("switchProject(path)", client_js)
        self.assertNotIn("id=\"meta\"", html)
        self.assertIn("elements.status.title", chat_ui_js)
        self.assertIn("id=\"banner\"", html)
        self.assertIn("id=\"status\" class=\"status\" hidden", html)
        self.assertIn("id=\"queued\"", html)
        self.assertIn("id=\"draft-queue\"", html)
        self.assertIn("id=\"command-menu\"", html)
        self.assertIn("id=\"profile\"", html)
        self.assertIn("<select id=\"model\"", html)
        self.assertNotIn("id=\"model\" type=\"text\"", html)
        self.assertNotIn("id=\"apply-settings\"", html)
        self.assertNotIn(">Appliquer<", html)
        self.assertIn("id=\"project\"", html)
        self.assertIn("id=\"git-diff\"", html)
        self.assertIn("id=\"git-count\"", html)
        self.assertIn("id=\"git-panel\"", html)
        self.assertIn("id=\"git-files\"", html)
        self.assertIn("id=\"git-panel-fullscreen\"", html)
        self.assertIn("id=\"git-panel-close\"", html)
        self.assertIn("id=\"git-branch\"", html)
        self.assertIn("id=\"git-branch-note\"", html)
        self.assertIn("id=\"git-commit\" class=\"git-panel-action git-commit-button\"", html)
        self.assertIn("id=\"git-commit-message\"", html)
        self.assertIn("id=\"git-commit-confirm\"", html)
        self.assertIn("M5 3h12l2 2v16H5z", html)
        self.assertIn("M4 6.5A3.5", html)
        self.assertIn("id=\"channel-badge\"", html)
        self.assertIn("id=\"session\"", html)
        self.assertIn("aria-label=\"Envoyer\"", html)
        self.assertIn("rows=\"1\"", html)
        self.assertIn("id=\"stop\"", html)
        self.assertIn("class=\"composer-run-actions\"", html)

    def test_web_chat_command_reuses_existing_healthy_server(self) -> None:
        import bb9.__main__ as main_module

        app = ChatApiApp(ChatApiState())
        server = chat_api_server(app, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        output = io.StringIO()
        try:
            with redirect_stdout(output):
                main_module.serve_chat_web(ChatApiState(), port=server.server_port, open_browser=False)
        finally:
            server.shutdown()
            server.server_close()

        self.assertIn("already running", output.getvalue())
        self.assertIn(f"127.0.0.1:{server.server_port}", output.getvalue())

    def test_web_chat_command_opens_browser_quietly(self) -> None:
        import bb9.__main__ as main_module

        app = ChatApiApp(ChatApiState())
        server = chat_api_server(app, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        opened: list[str] = []
        try:
            with patch.object(main_module, "_open_browser_quietly", lambda url: opened.append(url) or True):
                main_module.serve_chat_web(ChatApiState(), port=server.server_port, open_browser=True)
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual([f"http://127.0.0.1:{server.server_port}"], opened)

    def test_web_chat_command_tries_next_port_when_requested_port_is_taken(self) -> None:
        import bb9.__main__ as main_module

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
        try:
            server = main_module._open_chat_server(ChatApiApp(ChatApiState()), port)
        finally:
            sock.close()
        try:
            self.assertIsNotNone(server)
            self.assertEqual(port + 1, server.server_port)
        finally:
            server.server_close()

    def test_web_chat_command_tries_next_port_when_existing_server_has_other_workspace(self) -> None:
        import bb9.__main__ as main_module

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_workspace = root / "old"
            new_workspace = root / "new"
            old_workspace.mkdir()
            new_workspace.mkdir()

            class OtherWorkspaceHandler(BaseHTTPRequestHandler):
                def do_GET(self):  # noqa: N802
                    if self.path == "/health":
                        self._json({"ok": True, "features": ["chat-api", "image-api"]})
                        return
                    if self.path == "/api/status":
                        self._json({"ok": True, "workspace": str(old_workspace.resolve())})
                        return
                    self.send_error(404)

                def log_message(self, *_args):
                    return

                def _json(self, payload: dict[str, object]) -> None:
                    body = json.dumps(payload).encode("utf-8")
                    self.send_response(200)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            existing = ThreadingHTTPServer(("127.0.0.1", 0), OtherWorkspaceHandler)
            thread = threading.Thread(target=existing.serve_forever, daemon=True)
            thread.start()
            cwd = Path.cwd()
            try:
                os.chdir(new_workspace)
                server = main_module._open_chat_server(ChatApiApp(ChatApiState()), int(existing.server_port))
            finally:
                os.chdir(cwd)
                existing.shutdown()
                existing.server_close()
            try:
                self.assertIsNotNone(server)
                self.assertEqual(int(existing.server_port) + 1, int(server.server_port))
            finally:
                server.server_close()

    def test_web_chat_command_switches_existing_server_to_requested_workspace(self) -> None:
        import bb9.__main__ as main_module

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_workspace = root / "open-ui"
            new_workspace = root / "tests"
            old_workspace.mkdir()
            new_workspace.mkdir()
            state = {"workspace": str(old_workspace.resolve()), "requested": ""}

            class SwitchableWorkspaceHandler(BaseHTTPRequestHandler):
                def do_GET(self):  # noqa: N802
                    if self.path == "/health":
                        self._json({"ok": True, "features": ["chat-api", "image-api"]})
                        return
                    if self.path == "/api/status":
                        self._json({"ok": True, "workspace": state["workspace"], "active_project": state["workspace"]})
                        return
                    self.send_error(404)

                def do_POST(self):  # noqa: N802
                    if self.path != "/api/project":
                        self.send_error(404)
                        return
                    length = int(self.headers.get("content-length", "0"))
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    state["requested"] = str(payload.get("path") or "")
                    state["workspace"] = state["requested"]
                    self._json({"ok": True, "workspace": state["workspace"], "active_project": state["workspace"]})

                def log_message(self, *_args):
                    return

                def _json(self, payload: dict[str, object]) -> None:
                    body = json.dumps(payload).encode("utf-8")
                    self.send_response(200)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            existing = ThreadingHTTPServer(("127.0.0.1", 0), SwitchableWorkspaceHandler)
            thread = threading.Thread(target=existing.serve_forever, daemon=True)
            thread.start()
            cwd = Path.cwd()
            try:
                os.chdir(new_workspace)
                server = main_module._open_chat_server(ChatApiApp(ChatApiState()), int(existing.server_port))
            finally:
                os.chdir(cwd)
                existing.shutdown()
                existing.server_close()

            self.assertIsNone(server)
            self.assertEqual(str(new_workspace.resolve()), state["requested"])
            self.assertEqual(str(new_workspace.resolve()), state["workspace"])

    def test_http_post_endpoint_errors_return_json(self) -> None:
        class FailingApp:
            def resolve_approval(self, **_kwargs):
                raise RuntimeError("boom")

        server = chat_api_server(FailingApp(), 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_port}/api/approval",
                data=json.dumps({"id": "x", "decision": "allow"}).encode("utf-8"),
                headers={"content-type": "application/json"},
                method="POST",
            )
            with patch("bb9.api.http._logger.exception"):
                try:
                    urlopen(request, timeout=5)
                    self.fail("expected HTTP error")
                except Exception as exc:
                    response = exc.read().decode("utf-8")
        finally:
            server.shutdown()
            server.server_close()

        payload = json.loads(response)
        self.assertFalse(payload["ok"])
        self.assertEqual("internal_error", payload["error"])

    def test_cli_web_command_starts_web_chat_channel(self) -> None:
        import bb9.__main__ as main_module

        calls: list[tuple[object, int, bool]] = []

        def fake_serve(state, *, port, open_browser):
            calls.append((state, port, open_browser))

        with patch.object(main_module, "serve_chat_web", fake_serve), patch(
            "sys.argv",
            ["bb9", "web", "--provider", "echo", "--web-port", "8899", "--no-open"],
        ):
            code = main_module.main()

        self.assertEqual(0, code)
        self.assertEqual(1, len(calls))
        state, port, open_browser = calls[0]
        self.assertEqual("web", state.session.source)
        self.assertEqual("echo", state.provider_kind)
        self.assertEqual(8899, port)
        self.assertFalse(open_browser)

    def test_cli_web_command_defaults_to_configured_provider(self) -> None:
        import bb9.__main__ as main_module

        calls: list[object] = []

        def fake_entry(provider, args, store, *, require_model):
            self.assertEqual("configured", provider)
            self.assertFalse(require_model)
            return ProviderEntry(
                id="active",
                name="Active",
                provider="openai-compatible",
                auth_type=AUTH_API,
                base_url="https://example.test/v1",
                api_key_ref="env:EXAMPLE_KEY",
                model="demo",
            )

        def fake_serve(state, *, port, open_browser):
            calls.append(state)

        with (
            patch.object(main_module, "_entry_for_provider_arg", fake_entry),
            patch.object(main_module, "serve_chat_web", fake_serve),
            patch("sys.argv", ["bb9", "web", "--no-open"]),
        ):
            code = main_module.main()

        self.assertEqual(0, code)
        self.assertEqual(1, len(calls))
        self.assertEqual("configured", calls[0].provider_kind)
        self.assertEqual("Active", calls[0].active_provider.name)

    def test_cli_web_command_allows_configured_provider_without_model(self) -> None:
        import bb9.__main__ as main_module

        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "providers.json"
            provider = ProviderEntry(
                id="deepseek",
                name="DeepSeek",
                provider="openai-compatible",
                auth_type=AUTH_API,
                base_url="https://api.deepseek.com",
                api_key_ref="secret:DEEPSEEK_API_KEY",
            )
            ProviderStore(config_path).save(ProviderConfig(active_id="deepseek", entries=(provider,)))
            calls: list[object] = []

            def fake_serve(state, *, port, open_browser):
                calls.append(state)

            with (
                patch.object(main_module, "serve_chat_web", fake_serve),
                patch(
                    "sys.argv",
                    [
                        "bb9",
                        "web",
                        "--profile",
                        "limited",
                        "--provider-config-path",
                        str(config_path),
                        "--no-open",
                    ],
                ),
            ):
                code = main_module.main()

        self.assertEqual(0, code)
        self.assertEqual(1, len(calls))
        self.assertEqual("configured", calls[0].provider_kind)
        self.assertEqual("DeepSeek", calls[0].active_provider.name)
        self.assertEqual("", calls[0].active_provider.model)

    def test_cli_web_chat_flag_defaults_to_configured_provider(self) -> None:
        import bb9.__main__ as main_module

        calls: list[object] = []

        def fake_entry(provider, args, store, *, require_model):
            self.assertEqual("configured", provider)
            self.assertFalse(require_model)
            return ProviderEntry(
                id="active",
                name="Active",
                provider="openai-compatible",
                auth_type=AUTH_API,
                base_url="https://example.test/v1",
                api_key_ref="env:EXAMPLE_KEY",
                model="demo",
            )

        def fake_serve(state, *, port, open_browser):
            calls.append(state)

        with (
            patch.object(main_module, "_entry_for_provider_arg", fake_entry),
            patch.object(main_module, "serve_chat_web", fake_serve),
            patch("sys.argv", ["bb9", "--web-chat", "--no-open"]),
        ):
            code = main_module.main()

        self.assertEqual(0, code)
        self.assertEqual(1, len(calls))
        self.assertEqual("configured", calls[0].provider_kind)
        self.assertEqual("Active", calls[0].active_provider.name)

    def test_trusted_roots_live_in_user_folder(self) -> None:
        old_home = os.environ.get("BB9_HOME")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["BB9_HOME"] = str(Path(tmp) / "user")
                import bb9.core.trust as trust

                trust = importlib.reload(trust)
                trusted = Path(tmp) / "workspace"
                trusted.mkdir()

                trust.TrustedRoots.add(trusted)

                self.assertEqual(Path(tmp) / "user" / "trusted-roots.md", trust.TRUSTED_ROOTS_FILE)
                self.assertTrue(trust.TRUSTED_ROOTS_FILE.is_file())
                self.assertIn(trusted.resolve(), trust.TrustedRoots.load().roots)
        finally:
            if old_home is None:
                os.environ.pop("BB9_HOME", None)
            else:
                os.environ["BB9_HOME"] = old_home
            import bb9.core.trust as trust

            importlib.reload(trust)

    def test_approval_store_sanitizes_and_remembers_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            path = root / "approvals.json"
            params = {
                "cmd": "curl https://example.test/?api_key=supersecret1234567890",
                "__bb9_archive_root": "/internal/path",
            }
            fingerprint = fingerprint_action("shell", params, workspace)
            store = ApprovalStore(path)

            store.record(
                fingerprint=fingerprint,
                tool_name="shell",
                params=params,
                workspace=workspace,
                reason="test",
                risk="high",
                approved=True,
                remembered=True,
            )
            raw = path.read_text(encoding="utf-8")
            lookup = store.lookup(fingerprint)

        self.assertTrue(lookup)
        self.assertIn("<secret-redacted>", raw)
        self.assertNotIn("supersecret1234567890", raw)
        self.assertNotIn("__bb9_archive_root", raw)

    def test_approval_fingerprint_is_workspace_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            params = {"cmd": "rm delete-me.txt"}
            first_fingerprint = fingerprint_action("shell", params, first)
            second_fingerprint = fingerprint_action("shell", params, second)
            store = ApprovalStore(root / "approvals.json")
            store.record(
                fingerprint=first_fingerprint,
                tool_name="shell",
                params=params,
                workspace=first,
                reason="test",
                risk="high",
                approved=True,
                remembered=True,
            )
            first_lookup = store.lookup(first_fingerprint)
            second_lookup = store.lookup(second_fingerprint)

        self.assertNotEqual(first_fingerprint, second_fingerprint)
        self.assertTrue(first_lookup)
        self.assertIsNone(second_lookup)

    def test_tool_runtime_loads_local_backend_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = root / "demo"
            tool.mkdir()
            (tool / "helper.py").write_text("VALUE = 'ok'\n", encoding="utf-8")
            (tool / "runtime.py").write_text("from .helper import VALUE\n", encoding="utf-8")

            module = load_tool_module("demo", "runtime", root)

            self.assertIsNotNone(module)
            self.assertEqual("ok", module.VALUE)

    def test_tool_runtime_reloads_backend_when_source_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = root / "demo_reload"
            tool.mkdir()
            runtime = tool / "runtime.py"
            runtime.write_text("VALUE = 'one'\n", encoding="utf-8")

            first = load_tool_module("demo_reload", "runtime", root)
            self.assertIsNotNone(first)
            self.assertEqual("one", first.VALUE)

            runtime.write_text("VALUE = 'two'\n", encoding="utf-8")
            next_mtime = runtime.stat().st_mtime_ns + 1_000_000_000
            os.utime(runtime, ns=(next_mtime, next_mtime))

            second = load_tool_module("demo_reload", "runtime", root)

            self.assertIsNotNone(second)
            self.assertEqual("two", second.VALUE)

    def test_tool_runtime_reloads_backend_when_helper_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = root / "demo_reload_helper"
            tool.mkdir()
            helper = tool / "helper.py"
            helper.write_text("VALUE = 'one'\n", encoding="utf-8")
            (tool / "runtime.py").write_text("from .helper import VALUE\n", encoding="utf-8")

            first = load_tool_module("demo_reload_helper", "runtime", root)
            self.assertIsNotNone(first)
            self.assertEqual("one", first.VALUE)

            helper.write_text("VALUE = 'two'\n", encoding="utf-8")
            next_mtime = helper.stat().st_mtime_ns + 1_000_000_000
            os.utime(helper, ns=(next_mtime, next_mtime))

            second = load_tool_module("demo_reload_helper", "runtime", root)

            self.assertIsNotNone(second)
            self.assertEqual("two", second.VALUE)

    def test_browser_session_is_recreated_when_workspace_changes(self) -> None:
        module = load_tool_module("browser", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "one"
            second = Path(tmp) / "two"
            first.mkdir()
            second.mkdir()
            session_one = module._session(first)
            session_two = module._session(second)
            try:
                self.assertIsNot(session_one, session_two)
                self.assertEqual(second.resolve(), session_two.workspace.resolve())
            finally:
                module._close_session()

    def test_archive_core_file_can_be_loaded_as_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = root / "demo"
            tool.mkdir()
            (tool / "core.py").write_text("VALUE = 'core-ok'\n", encoding="utf-8")

            core_module = load_tool_module("demo", "core", root)

            self.assertIsNotNone(core_module)
            self.assertEqual("core-ok", core_module.VALUE)

    def test_archive_core_directory_supports_local_imports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            core_dir = root / "demo" / "core"
            core_dir.mkdir(parents=True)
            (core_dir / "helper.py").write_text("VALUE = 'nested-ok'\n", encoding="utf-8")
            (core_dir / "core.py").write_text("from .helper import VALUE\n", encoding="utf-8")

            module = load_tool_module("demo", "core", root)

            self.assertIsNotNone(module)
            self.assertEqual("nested-ok", module.VALUE)

    def test_active_skill_runtime_can_import_core_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills = root / "skills"
            skill = skills / "demo"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# Demo\n", encoding="utf-8")
            (skill / "core.py").write_text("PREFIX = 'skill:'\n", encoding="utf-8")
            (skill / "runtime.py").write_text(
                "from bb9.core.models import Action, Observation\n\n"
                "from .core import PREFIX\n\n"
                "def action_from_text(text):\n"
                "    return Action(name='demo', params={'text': text}, risk='low')\n\n"
                "def execute(action):\n"
                "    return Observation(ok=True, summary=PREFIX + action.params['text'])\n",
                encoding="utf-8",
            )
            context = RunContext(
                session=Session(),
                workspace=Workspace(root=root),
                skills=(Skill(name="demo", body="# Demo", root=skills),),
            )

            decision = Kernel().decide(Intention("/action demo ping"), context)
            observation = execute(decision.action)

            self.assertEqual("action", decision.kind)
            self.assertEqual("demo", decision.action.name)
            self.assertTrue(observation.ok)
            self.assertEqual("skill:ping", observation.summary)

    def test_create_skill_generates_requested_python_files(self) -> None:
        module = load_tool_module("create_skill", "runtime")
        self.assertIsNotNone(module)
        with tempfile.TemporaryDirectory() as tmp:
            old_skills_dir = module.USER_SKILLS_DIR
            module.USER_SKILLS_DIR = Path(tmp) / "skills"
            try:
                action = module.action_from_text("draft demo cli runtime core")
                observation = module.execute(action)
            finally:
                module.USER_SKILLS_DIR = old_skills_dir

            skill_dir = Path(tmp) / "skills" / "demo"
            self.assertTrue(observation.ok)
            self.assertEqual("global", observation.data["scope"])
            self.assertTrue((skill_dir / "SKILL.md").is_file())
            self.assertTrue((skill_dir / "cli.py").is_file())
            self.assertTrue((skill_dir / "runtime.py").is_file())
            self.assertTrue((skill_dir / "core.py").is_file())
            skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            cli_text = (skill_dir / "cli.py").read_text(encoding="utf-8")
            self.assertIn("workspace peut le surcharger", skill_text)
            self.assertIn("commande utilisateur explicite via `cli.py`", skill_text)
            self.assertIn("/demo-<action>", skill_text)
            self.assertIn("observations techniques en réponse naturelle", skill_text)
            self.assertIn('cli.add_command("/demo"', cli_text)
            self.assertIn("Commande demo terminée.", cli_text)

    def test_create_skill_can_generate_local_workspace_skill(self) -> None:
        module = load_tool_module("create_skill", "runtime")
        self.assertIsNotNone(module)
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            try:
                os.chdir(workspace)
                action = module.action_from_text("draft demo local cli")
                observation = module.execute(action)
            finally:
                os.chdir(cwd)

            skill_dir = workspace / ".bb9" / "skills" / "demo"
            self.assertTrue(observation.ok)
            self.assertEqual("local", observation.data["scope"])
            self.assertEqual(str((workspace / ".bb9" / "skills").resolve()), str(Path(observation.data["root"]).resolve()))
            self.assertTrue((skill_dir / "SKILL.md").is_file())
            self.assertTrue((skill_dir / "cli.py").is_file())
            skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("Skill local au workspace", skill_text)
            self.assertIn("commande utilisateur explicite via `cli.py`", skill_text)

    def test_shell_tool_returns_observation_for_missing_command(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        observation = module.execute(module.action_from_text("...`"))

        self.assertFalse(observation.ok)
        self.assertIn("command not found", observation.summary)

    def test_shell_create_file_in_workspace_is_allowed_in_power_profile(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(
                session=Session(),
                workspace=Workspace(root=workspace),
                permission_profile="power",
            )
            decision = module.review(module.action_from_text("touch test.html"), context)

        self.assertEqual("allow", decision.verdict)
        self.assertIn("workspace write command allowed", decision.reason)

    def test_shell_create_file_outside_workspace_asks_for_confirmation(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            outside = root / "outside.html"
            context = RunContext(
                session=Session(),
                workspace=Workspace(root=workspace),
                permission_profile="power",
            )
            decision = module.review(module.action_from_text(f"touch {outside}"), context)

        self.assertEqual("ask", decision.verdict)
        self.assertIn("outside workspace", decision.reason)

    def test_files_replace_is_allowed_in_power_profile(self) -> None:
        module = load_tool_module("files", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            target = workspace / "index.html"
            target.write_text("<head>\n</head>\n", encoding="utf-8")
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            action = module.action_from_text('insert_before path=index.html marker="</head>" text="<link rel=\\"stylesheet\\" href=\\"https://cdn.example/fa.css\\">"')
            decision = module.review(action, context)

        self.assertEqual("allow", decision.verdict)
        self.assertIn("workspace file edit allowed", decision.reason)

    def test_files_insert_before_updates_workspace_file(self) -> None:
        module = load_tool_module("files", "runtime")
        self.assertIsNotNone(module)
        cwd = Path.cwd()

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            target = workspace / "index.html"
            target.write_text("<head>\n</head>\n", encoding="utf-8")
            try:
                os.chdir(workspace)
                observation = module.execute(
                    module.action_from_text(
                        'insert_before path=index.html marker="</head>" text="<link rel=\\"stylesheet\\" href=\\"https://cdn.example/fa.css\\">"'
                    )
                )
            finally:
                os.chdir(cwd)

            updated = target.read_text(encoding="utf-8")

        self.assertTrue(observation.ok)
        self.assertIn("File updated: index.html", observation.summary)
        self.assertIn('href="https://cdn.example/fa.css"', updated)

    def test_files_write_accepts_content_alias_and_relaxed_text(self) -> None:
        module = load_tool_module("files", "runtime")
        self.assertIsNotNone(module)
        cwd = Path.cwd()

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            try:
                os.chdir(workspace)
                observation = module.execute(
                    module.action_from_text('write path=demo.html content="""<h1 class="hero">Demo</h1>"""')
                )
            finally:
                os.chdir(cwd)

            written = (workspace / "demo.html").read_text(encoding="utf-8")

        self.assertTrue(observation.ok)
        self.assertEqual('<h1 class="hero">Demo</h1>', written)
        self.assertEqual((), observation.artifacts)

    def test_files_write_accepts_positional_path_and_heredoc(self) -> None:
        module = load_tool_module("files", "runtime")
        self.assertIsNotNone(module)

        action = module.action_from_text(
            "write .bb9/skills/veille-rss.md <<'EOF'\n"
            "# Veille RSS Skill\n\n"
            "Contenu lisible.\n"
            "EOF\n"
            "texte final ignoré"
        )

        self.assertEqual("medium", action.risk)
        self.assertEqual("write", action.params["op"])
        self.assertEqual(".bb9/skills/veille-rss.md", action.params["path"])
        self.assertEqual("# Veille RSS Skill\n\nContenu lisible.", action.params["text"])

    def test_files_write_heredoc_outside_workspace_asks_for_confirmation(self) -> None:
        module = load_tool_module("files", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            outside = root / "outside" / "SKILL.md"
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            action = module.action_from_text(f"write {outside} <<'EOF'\n# Skill\nEOF")
            decision = module.review(action, context)

        self.assertEqual("ask", decision.verdict)
        self.assertIn("outside workspace", decision.reason)

    def test_files_json_without_op_infers_write(self) -> None:
        module = load_tool_module("files", "runtime")
        self.assertIsNotNone(module)

        action = module.action_from_text(json.dumps({"path": "note.md", "content": "# Note"}))

        self.assertEqual("write", action.params["op"])
        self.assertEqual("note.md", action.params["path"])
        self.assertEqual("# Note", action.params["text"])

    def test_files_write_many_writes_multiple_workspace_files(self) -> None:
        module = load_tool_module("files", "runtime")
        self.assertIsNotNone(module)
        cwd = Path.cwd()

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            action = module.action_from_text(
                'write_many [{"path":"public/drafts/demo/index.html","content":"<h1>Demo</h1>"},'
                '{"path":"public/drafts/demo/style.css","content":":root { color-scheme: light; }"}]'
            )
            decision = module.review(action, context)
            try:
                os.chdir(workspace)
                observation = module.execute(action)
            finally:
                os.chdir(cwd)

            html = (workspace / "public" / "drafts" / "demo" / "index.html").read_text(encoding="utf-8")
            css = (workspace / "public" / "drafts" / "demo" / "style.css").read_text(encoding="utf-8")

        self.assertEqual("allow", decision.verdict)
        self.assertTrue(observation.ok)
        self.assertIn("Files written:", observation.summary)
        self.assertEqual("<h1>Demo</h1>", html)
        self.assertIn("color-scheme", css)

    def test_files_write_many_accepts_files_alias(self) -> None:
        module = load_tool_module("files", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            action = module.action_from_text(
                'write_many files=[{"path":"public/drafts/demo/index.html","content":"<h1>Demo</h1>"}]'
            )
            decision = module.review(action, context)

        self.assertEqual("write_many", action.params["op"])
        self.assertEqual("allow", decision.verdict)

    def test_files_accepts_json_ops_as_write_many(self) -> None:
        module = load_tool_module("files", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            payload = {
                "ops": [
                    {"op": "write", "path": "public/drafts/demo/index.html", "content": "<h1>Demo</h1>"},
                    {"op": "write", "path": "public/drafts/demo/style.css", "content": "body { color: #111; }"},
                ]
            }
            action = module.action_from_text(json.dumps(payload) + "\nJ'ai prepare les fichiers.")
            decision = module.review(action, context)
            observation = module.execute(action, context)

            html = (workspace / "public" / "drafts" / "demo" / "index.html").read_text(encoding="utf-8")
            css = (workspace / "public" / "drafts" / "demo" / "style.css").read_text(encoding="utf-8")

        self.assertEqual("write_many", action.params["op"])
        self.assertEqual("allow", decision.verdict)
        self.assertTrue(observation.ok)
        self.assertEqual("<h1>Demo</h1>", html)
        self.assertIn("color", css)

    def test_kernel_accepts_files_json_ops_action(self) -> None:
        payload = {
            "ops": [
                {"op": "write", "path": "public/drafts/demo/index.html", "content": "<h1>Demo</h1>"},
            ]
        }

        class JsonOpsProvider:
            def complete(self, prompt: str, **_: object) -> str:
                return "BB9_ACTION files " + json.dumps(payload)

        context = RunContext(
            session=Session(),
            workspace=Workspace(root=Path.cwd()),
            tools=(ToolSpec(name="files", body=""),),
        )

        decision = Kernel(provider=JsonOpsProvider()).decide(Intention("cree une page"), context)

        self.assertEqual("action", decision.kind)
        self.assertEqual("files", decision.action.name)
        self.assertEqual("write_many", decision.action.params["op"])
        self.assertEqual("public/drafts/demo/index.html", decision.action.params["items"][0]["path"])

    def test_files_write_quoted_text_ignores_trailing_provider_prose(self) -> None:
        module = load_tool_module("files", "runtime")
        self.assertIsNotNone(module)

        action = module.action_from_text(
            'write path=demo.html text="<h1 class=\\"hero\\">Demo</h1>"\n'
            "J'ai préparé le fichier demandé."
        )

        self.assertEqual("write", action.params["op"])
        self.assertEqual("demo.html", action.params["path"])
        self.assertEqual('<h1 class="hero">Demo</h1>', action.params["text"])

    def test_files_write_quoted_text_preserves_unescaped_html_attributes(self) -> None:
        module = load_tool_module("files", "runtime")
        self.assertIsNotNone(module)

        action = module.action_from_text(
            'write path=demo.html text="<div class="hero" id="main">Demo</div>"\n'
            "Fichier prêt."
        )

        self.assertEqual('<div class="hero" id="main">Demo</div>', action.params["text"])

    def test_files_review_blocks_provider_status_text_leaked_into_content(self) -> None:
        module = load_tool_module("files", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            action = module.action_from_text(
                'replace path=src/app.js old="function run() {}" '
                'new="function run() {Status:\\n  return true;\\n}"'
            )
            decision = module.review(action, context)

        self.assertEqual("block", decision.verdict)
        self.assertIn("provider status text", decision.reason)

    def test_files_relaxed_text_trims_trailing_task_result_contract(self) -> None:
        module = load_tool_module("files", "runtime")
        self.assertIsNotNone(module)

        action = module.action_from_text(
            "write path=demo.js text=function run() {\n"
            "  return true;\n"
            "}\n"
            "Status: done\n"
            "Evidence:\n"
            "- file updated\n"
        )

        self.assertEqual("function run() {\n  return true;\n}", action.params["text"])

    def test_files_execute_uses_context_workspace_not_process_cwd(self) -> None:
        module = load_tool_module("files", "runtime")
        self.assertIsNotNone(module)
        cwd = Path.cwd()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            other = root / "other"
            workspace.mkdir()
            other.mkdir()
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            action = module.action_from_text("write path=demo.txt text=ok")
            try:
                os.chdir(other)
                observation = module.execute(action, context)
            finally:
                os.chdir(cwd)
            written = (workspace / "demo.txt").read_text(encoding="utf-8")
            other_exists = (other / "demo.txt").exists()

        self.assertTrue(observation.ok)
        self.assertEqual("ok", written)
        self.assertFalse(other_exists)

    def test_kernel_accepts_multiline_files_write_action(self) -> None:
        class MultilineProvider:
            def complete(self, prompt: str, **_: object) -> str:
                return (
                    "Je prepare le fichier.\n"
                    "BB9_ACTION files write path=demo.html text=\"\"\"<!doctype html>\n"
                    "<html>\n"
                    "<body>Demo</body>\n"
                    "</html>\n"
                    "\"\"\""
                )

        context = RunContext(
            session=Session(),
            workspace=Workspace(root=Path.cwd()),
            tools=(ToolSpec(name="files", body=""),),
        )

        decision = Kernel(provider=MultilineProvider()).decide(Intention("cree une page"), context)

        self.assertEqual("action", decision.kind)
        self.assertEqual("files", decision.action.name)
        self.assertEqual("write", decision.action.params["op"])
        self.assertIn("<body>Demo</body>", decision.action.params["text"])

    def test_kernel_accepts_files_write_heredoc_action(self) -> None:
        class HeredocProvider:
            def complete(self, prompt: str, **_: object) -> str:
                return (
                    "BB9_ACTION files write .bb9/skills/veille-rss.md <<'EOF'\n"
                    "# Veille RSS Skill\n\n"
                    "Contenu lisible.\n"
                    "EOF"
                )

        context = RunContext(
            session=Session(),
            workspace=Workspace(root=Path.cwd()),
            tools=(ToolSpec(name="files", body=""),),
        )

        decision = Kernel(provider=HeredocProvider()).decide(Intention("modifie le skill"), context)

        self.assertEqual("action", decision.kind)
        self.assertEqual("files", decision.action.name)
        self.assertEqual("write", decision.action.params["op"])
        self.assertEqual(".bb9/skills/veille-rss.md", decision.action.params["path"])
        self.assertIn("Contenu lisible.", decision.action.params["text"])

    def test_files_replace_requires_existing_text(self) -> None:
        module = load_tool_module("files", "runtime")
        self.assertIsNotNone(module)
        cwd = Path.cwd()

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "index.html").write_text("<p>hello</p>\n", encoding="utf-8")
            try:
                os.chdir(workspace)
                observation = module.execute(module.action_from_text('replace path=index.html old="missing" new="ok"'))
            finally:
                os.chdir(cwd)

        self.assertFalse(observation.ok)
        self.assertIn("replace text not found", observation.summary)

    def test_shell_read_grep_is_allowed_without_confirmation(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "demo.js").write_text("document.execCommand('bold')\n", encoding="utf-8")
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            decision = module.review(module.action_from_text('grep -n "execCommand\\|destroy" demo.js'), context)

        self.assertEqual("allow", decision.verdict)

    def test_shell_simple_read_pipeline_is_rewritten_without_confirmation(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "demo.js").write_text("one\ntwo\n", encoding="utf-8")
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            decision = module.review(module.action_from_text("cat demo.js | head -1"), context)

        self.assertEqual("allow", decision.verdict)
        self.assertEqual("head -1 demo.js", decision.action.params["cmd"])

    def test_shell_unsupported_pipeline_is_blocked_before_confirmation(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "demo.js").write_text("one\ntwo\n", encoding="utf-8")
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            decision = module.review(module.action_from_text("cat demo.js | uniq"), context)

        self.assertEqual("block", decision.verdict)
        self.assertIn("unsupported compound", decision.reason)
        self.assertIn("shell=True is disabled", decision.reason)

    def test_shell_simple_output_redirection_is_allowed_in_power_profile(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            decision = module.review(module.action_from_text("echo hello > out.txt"), context)
            observation = module.execute(decision.action, context)
            written = (workspace / "out.txt").read_text(encoding="utf-8")

        self.assertEqual("allow", decision.verdict)
        self.assertIn("shell output file write allowed", decision.reason)
        self.assertTrue(observation.ok)
        self.assertEqual("hello\n", written)

    def test_shell_output_redirection_outside_workspace_asks_for_confirmation(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            outside = root / "outside.txt"
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            decision = module.review(module.action_from_text(f"echo hello > {outside}"), context)

        self.assertEqual("ask", decision.verdict)
        self.assertIn("outside workspace", decision.reason)

    def test_shell_stderr_redirection_is_blocked_before_confirmation(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            decision = module.review(module.action_from_text("npm test 2> out.txt"), context)

        self.assertEqual("block", decision.verdict)
        self.assertIn("unsupported compound", decision.reason)
        self.assertIn("shell=True is disabled", decision.reason)

    def test_loop_does_not_ask_user_for_safe_shell_redirection(self) -> None:
        class RedirectProvider:
            calls = 0

            def complete(self, _: str, **___: object) -> str:
                self.calls += 1
                if self.calls == 1:
                    return "BB9_ACTION shell echo hello > out.txt"
                return "Fichier écrit."

        approvals: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")

            result = run_once(
                Kernel(provider=RedirectProvider()),
                Intention("écris un fichier"),
                context,
                ask_user=lambda *_args: approvals.append("ask") or "defer",
            )
            written = (workspace / "out.txt").read_text(encoding="utf-8")

        self.assertEqual([], approvals)
        self.assertTrue(result.observation.ok)
        self.assertEqual("Fichier écrit.", result.observation.summary)
        self.assertEqual("hello\n", written)

    def test_shell_verification_chain_is_allowed_without_confirmation(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            decision = module.review(module.action_from_text("npm test && npm run lint"), context)

        self.assertEqual("allow", decision.verdict)
        self.assertIn("verification shell chain", decision.reason)

    def test_shell_workspace_write_chain_is_allowed_in_power_profile(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            decision = module.review(module.action_from_text("mkdir -p public && touch public/index.html"), context)

        self.assertEqual("allow", decision.verdict)
        self.assertIn("workspace write shell chain", decision.reason)

    def test_shell_destructive_chain_asks_instead_of_blocking(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="limited")
            decision = module.review(module.action_from_text("rm test.txt && echo done"), context)

        self.assertEqual("ask", decision.verdict)
        self.assertIn("destructive shell chain", decision.reason)

    def test_shell_destructive_chain_in_workspace_is_allowed_in_power_profile(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            decision = module.review(module.action_from_text("rm test.txt && echo done"), context)

        self.assertEqual("allow", decision.verdict)
        self.assertIn("power profile", decision.reason)

    def test_shell_hard_confirm_command_asks_even_in_power_profile(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            decision = module.review(module.action_from_text("sudo systemctl restart nginx"), context)

        self.assertEqual("ask", decision.verdict)
        self.assertIn("destructive", decision.reason)

    def test_shell_unknown_chain_asks_instead_of_blocking(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="limited")
            decision = module.review(module.action_from_text("custom-tool scan && custom-tool report"), context)

        self.assertEqual("ask", decision.verdict)
        self.assertIn("unknown shell chain", decision.reason)

    def test_shell_unknown_chain_is_allowed_in_power_profile(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            decision = module.review(module.action_from_text("custom-tool scan && custom-tool report"), context)

        self.assertEqual("allow", decision.verdict)
        self.assertIn("power profile", decision.reason)

    def test_shell_unknown_command_is_allowed_in_power_profile(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            power = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            limited = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="limited")
            allowed = module.review(module.action_from_text("custom-tool scan"), power)
            asked = module.review(module.action_from_text("custom-tool scan"), limited)

        self.assertEqual("allow", allowed.verdict)
        self.assertEqual("ask", asked.verdict)

    def test_shell_cd_prefix_is_rewritten_to_cwd_param(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            subdir = workspace / "demo"
            subdir.mkdir()
            (subdir / "page.txt").write_text("hello-cd", encoding="utf-8")
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            decision = module.review(module.action_from_text(f"cd {subdir} && cat page.txt"), context)
            self.assertEqual("allow", decision.verdict)
            self.assertEqual("cat page.txt", decision.action.params["cmd"])
            self.assertEqual(str(subdir), decision.action.params["cwd"])
            observation = module.execute(decision.action, context)

        self.assertTrue(observation.ok)
        self.assertIn("hello-cd", observation.summary)

    def test_shell_cd_prefix_outside_workspace_asks_for_confirmation(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            outside = root / "elsewhere"
            workspace.mkdir()
            outside.mkdir()
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            decision = module.review(module.action_from_text(f"cd {outside} && ls"), context)

        self.assertEqual("ask", decision.verdict)
        self.assertIn("path outside workspace/trusted roots", decision.reason)
        self.assertEqual("ls", decision.action.params["cmd"])
        self.assertEqual(str(outside), decision.action.params["cwd"])

    def test_shell_bare_cd_returns_guidance_instead_of_failing(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            decision = module.review(module.action_from_text(f"cd {workspace}"), context)
            self.assertEqual("allow", decision.verdict)
            observation = module.execute(decision.action, context)

        self.assertFalse(observation.ok)
        self.assertIn("cd <dossier> && <commande>", observation.summary)

    def test_shell_git_range_ellipsis_is_not_a_placeholder(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            decision = module.review(module.action_from_text("git log main...develop"), context)

        self.assertEqual("allow", decision.verdict)

    def test_shell_single_error_marker_is_not_treated_as_provider_prose(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            decision = module.review(module.action_from_text("grep -n error: app.log"), context)

        self.assertEqual("allow", decision.verdict)

    def test_shell_semicolon_chain_still_blocks_without_shell_true(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            decision = module.review(module.action_from_text("pwd; ls"), context)

        self.assertEqual("block", decision.verdict)
        self.assertIn("unsupported compound", decision.reason)

    def test_shell_find_sort_pipeline_is_executed_without_shell_true(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            sketches = workspace / "dev" / "sketches"
            sketches.mkdir(parents=True)
            (sketches / "b.md").write_text("b\n", encoding="utf-8")
            (sketches / "a.md").write_text("a\n", encoding="utf-8")
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                action = module.action_from_text("find dev/sketches -maxdepth 2 -type f | sort")
                decision = module.review(action, context)
                observation = module.execute(decision.action)
            finally:
                os.chdir(cwd)

        self.assertEqual("allow", decision.verdict)
        self.assertIn("read-only shell pipeline", decision.reason)
        self.assertTrue(observation.ok)
        self.assertEqual("dev/sketches/a.md\ndev/sketches/b.md", observation.summary)

    def test_shell_find_grep_head_pipeline_is_executed_without_shell_true(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            public = workspace / "public"
            public.mkdir()
            (public / "listing.html").write_text("ok\n", encoding="utf-8")
            (public / "ignore.txt").write_text("ok\n", encoding="utf-8")
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                action = module.action_from_text("find public -maxdepth 1 -type f | grep -E 'listing|patients' | head -20")
                decision = module.review(action, context)
                observation = module.execute(decision.action)
            finally:
                os.chdir(cwd)

        self.assertEqual("allow", decision.verdict)
        self.assertIn("read-only shell pipeline", decision.reason)
        self.assertTrue(observation.ok)
        self.assertEqual("public/listing.html", observation.summary)

    def test_shell_rg_head_pipeline_is_executed_without_shell_true(self) -> None:
        if shutil.which("rg") is None:
            self.skipTest("rg unavailable")
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "demo.js").write_text("alpha\nbeta\n", encoding="utf-8")
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                action = module.action_from_text("rg -n alpha . | head -5")
                decision = module.review(action, context)
                observation = module.execute(decision.action)
            finally:
                os.chdir(cwd)

        self.assertEqual("allow", decision.verdict)
        self.assertTrue(observation.ok)
        self.assertIn("demo.js:1:alpha", observation.summary)

    def test_shell_executes_with_context_workspace_as_cwd(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            other = root / "other"
            workspace.mkdir()
            other.mkdir()
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            cwd = Path.cwd()
            try:
                os.chdir(other)
                observation = module.execute(module.action_from_text("pwd"), context)
            finally:
                os.chdir(cwd)

        self.assertTrue(observation.ok)
        self.assertEqual(workspace.resolve(), Path(observation.summary).resolve())

    def test_shell_blocks_mutating_options_disguised_as_read_commands(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            sed = module.action_from_text("sed -i s/a/b/ demo.txt")
            find = module.action_from_text("find . -delete")
            sort = module.action_from_text("sort demo.txt -o demo.txt")

            sed_decision = module.review(sed, context)
            find_decision = module.review(find, context)
            sort_decision = module.review(sort, context)
            sed_observation = module.execute(sed, context)

        self.assertEqual("block", sed_decision.verdict)
        self.assertEqual("block", find_decision.verdict)
        self.assertEqual("block", sort_decision.verdict)
        self.assertFalse(sed_observation.ok)
        self.assertEqual("block_exact", sed_observation.retry_policy)

    def test_shell_blocks_provider_prose_leaked_into_read_command(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            action = module.action_from_text(
                "sed -n '1,240p' test.htmlerror — Lecture impossible. "
                "Blocker: fichier non lu. Next suggestion: relancer la lecture."
            )
            decision = module.review(action, context)

        self.assertEqual("block", decision.verdict)
        self.assertIn("provider prose leaked", decision.reason)

    def test_shell_blocks_provider_status_appended_to_filename(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            # Model appended "Status:" directly to the filename without a space
            action = module.action_from_text("cat test.htmlStatus:")
            decision = module.review(action, context)
            # Also test evidence and blocker variants
            action2 = module.action_from_text("cat src/app.jsblocker")
            decision2 = module.review(action2, context)

        self.assertEqual("block", decision.verdict)
        self.assertIn("provider prose leaked", decision.reason)
        self.assertEqual("block", decision2.verdict)
        self.assertIn("provider prose leaked", decision2.reason)

    def test_shell_quoted_angle_search_is_not_treated_as_placeholder(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            decision = module.review(module.action_from_text('rg "</div>" .'), context)

        self.assertEqual("allow", decision.verdict)

    def test_shell_read_chain_is_allowed_without_confirmation(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "a.txt").write_text("a\n", encoding="utf-8")
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                action = module.action_from_text("pwd && ls")
                decision = module.review(action, context)
                observation = module.execute(decision.action)
            finally:
                os.chdir(cwd)

        self.assertEqual("allow", decision.verdict)
        self.assertIn("read-only shell chain", decision.reason)
        self.assertTrue(observation.ok)
        self.assertIn("a.txt", observation.summary)

    def test_shell_read_chain_with_or_true_tolerates_missing_optional_file(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "MEMORY.md").write_text("memo\n", encoding="utf-8")
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                action = module.action_from_text("sed -n '1,220p' MEMORY.md && sed -n '1,180p' docs/sketches.md || true")
                decision = module.review(action, context)
                observation = module.execute(decision.action)
            finally:
                os.chdir(cwd)

        self.assertEqual("allow", decision.verdict)
        self.assertIn("read-only shell chain", decision.reason)
        self.assertTrue(observation.ok)
        self.assertIn("memo", observation.summary)
        self.assertIn("docs/sketches.md", observation.summary)
        self.assertEqual(0, observation.data["returncode"])

    def test_shell_read_only_git_chain_is_allowed_without_confirmation(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git unavailable")
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            subprocess.run(["git", "init"], cwd=workspace, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                action = module.action_from_text("git status --short && git branch --show-current")
                decision = module.review(action, context)
                observation = module.execute(decision.action)
            finally:
                os.chdir(cwd)

        self.assertEqual("allow", decision.verdict)
        self.assertIn("read-only shell chain", decision.reason)
        self.assertTrue(observation.ok)

    def test_shell_python_heredoc_is_allowed_without_confirmation_in_power_profile(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            action = module.action_from_text("python3 - <<'PY'\nprint('ok')\nPY")
            decision = module.review(action, context)

        self.assertEqual("allow", decision.verdict)
        self.assertIn("local interpreter heredoc allowed", decision.reason)

    def test_shell_python_heredoc_executes_without_shell_true(self) -> None:
        if shutil.which("python3") is None:
            self.skipTest("python3 unavailable")
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            action = module.action_from_text("python3 - <<'PY'\nfrom pathlib import Path\nprint(Path.cwd().name)\nPY")
            observation = module.execute(action, context)

        self.assertTrue(observation.ok)
        self.assertEqual(workspace.name, observation.summary)

    def test_kernel_keeps_shell_heredoc_and_drops_following_prose(self) -> None:
        class HeredocProvider:
            def complete(self, _: str, **___: object) -> str:
                return (
                    "BB9_ACTION shell python3 - <<'PY'\n"
                    "print('ok')\n"
                    "PY\n\n"
                    "Je vais analyser les resultats ensuite."
                )

        context = RunContext(
            session=Session(),
            workspace=Workspace(root=Path.cwd()),
            tools=(ToolSpec(name="shell", body=""),),
        )

        decision = Kernel(provider=HeredocProvider()).decide(Intention("execute le diagnostic"), context)

        self.assertEqual("action", decision.kind)
        self.assertEqual("shell", decision.action.name)
        self.assertEqual("python3 - <<'PY'\nprint('ok')\nPY", decision.action.params["cmd"])

    def test_shell_executes_simple_read_pipeline_without_shell_true(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "demo.txt"
            target.write_text("one\ntwo\n", encoding="utf-8")
            observation = module.execute(module.action_from_text(f"cat {target} | head -1"))

        self.assertTrue(observation.ok)
        self.assertEqual("one", observation.summary)

    def test_shell_grep_no_match_is_not_a_tool_error(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "demo.txt"
            target.write_text("one\ntwo\n", encoding="utf-8")
            observation = module.execute(module.action_from_text(f"grep -n absent {target}"))

        self.assertTrue(observation.ok)
        self.assertEqual("no matches", observation.summary)
        self.assertEqual(1, observation.data["returncode"])

    def test_shell_http_server_is_allowed_in_power_profile(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            decision = module.review(module.action_from_text("python3 -m http.server 8000"), context)

        self.assertEqual("allow", decision.verdict)
        self.assertIn("local http server allowed", decision.reason)

    def test_shell_http_server_invalid_port_is_blocked_before_execution(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            action = module.action_from_text("python3 -m http.server 8000كي")
            decision = module.review(action, context)
            with patch("bb9.tools.shell.runtime.subprocess.run") as run:
                observation = module.execute(action)

        self.assertEqual("block", decision.verdict)
        self.assertIn("port must be numeric", decision.reason)
        self.assertFalse(observation.ok)
        self.assertEqual("recoverable", observation.retry_policy)
        self.assertIn("port must be numeric", observation.summary)
        run.assert_not_called()

    def test_shell_http_server_prose_after_port_is_blocked_not_asked(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            action = module.action_from_text(
                "python3 -m http.server 4173J’ai repris la commande comme une vraie exécution `/workspace-sketch`."
            )
            decision = module.review(action, context)

        self.assertEqual("block", decision.verdict)
        self.assertIn("port must be numeric", decision.reason)

    def test_shell_http_server_starts_in_background_on_localhost(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        class FakeProcess:
            pid = 4242
            returncode = None

            def __init__(self) -> None:
                self.stdout = io.StringIO()
                self.stderr = io.StringIO()

            def poll(self):
                return None

        fake = FakeProcess()
        with (
            patch.object(module.subprocess, "Popen", return_value=fake) as popen,
            patch.object(module, "_wait_for_http_server", return_value=True),
        ):
            observation = module.execute(module.action_from_text("python3 -m http.server 8000"))

        self.assertTrue(observation.ok)
        self.assertEqual("HTTP server started: http://127.0.0.1:8000", observation.summary)
        argv = popen.call_args.args[0]
        self.assertEqual(["python3", "-m", "http.server", "--bind", "127.0.0.1", "8000"], argv)
        self.assertEqual(module.subprocess.DEVNULL, popen.call_args.kwargs["stdout"])
        self.assertEqual(module.subprocess.DEVNULL, popen.call_args.kwargs["stderr"])
        self.assertEqual(4242, observation.data["pid"])

    def test_shell_http_server_reports_when_started_process_does_not_answer(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        class FakeProcess:
            pid = 4242
            returncode = None
            terminated = False

            def poll(self):
                return None

            def terminate(self):
                self.terminated = True

        fake = FakeProcess()
        with (
            patch.object(module.subprocess, "Popen", return_value=fake),
            patch.object(module, "_wait_for_http_server", return_value=False),
        ):
            observation = module.execute(module.action_from_text("python3 -m http.server 8000"))

        self.assertFalse(observation.ok)
        self.assertIn("no HTTP response", observation.summary)
        self.assertTrue(fake.terminated)

    def test_shell_http_server_reuses_existing_responsive_server(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        class ExitedProcess:
            returncode = 1

            def poll(self):
                return self.returncode

        with (
            patch.object(module.subprocess, "Popen", return_value=ExitedProcess()),
            patch.object(module, "_wait_for_http_server", return_value=True),
        ):
            observation = module.execute(module.action_from_text("python3 -m http.server 8000"))

        self.assertTrue(observation.ok)
        self.assertEqual("HTTP server already available: http://127.0.0.1:8000", observation.summary)
        self.assertTrue(observation.data["reused"])

    def test_shell_http_server_tries_next_port_when_requested_port_is_unavailable(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        class ExitedProcess:
            returncode = 1

            def poll(self):
                return self.returncode

        class RunningProcess:
            pid = 4243
            returncode = None

            def poll(self):
                return None

        with (
            patch.object(module.subprocess, "Popen", side_effect=[ExitedProcess(), RunningProcess()]) as popen,
            patch.object(module, "_wait_for_http_server", side_effect=[False, True]),
        ):
            observation = module.execute(module.action_from_text("python3 -m http.server 8000"))

        self.assertTrue(observation.ok)
        self.assertEqual("HTTP server started: http://127.0.0.1:8001 (port 8000 unavailable)", observation.summary)
        self.assertEqual("http://127.0.0.1:8001", observation.data["url"])
        self.assertEqual(8000, observation.data["requested_port"])
        self.assertEqual(["python3", "-m", "http.server", "--bind", "127.0.0.1", "8000"], popen.call_args_list[0].args[0])
        self.assertEqual(["python3", "-m", "http.server", "--bind", "127.0.0.1", "8001"], popen.call_args_list[1].args[0])

    def test_shell_delete_in_workspace_asks_for_confirmation(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace))
            decision = module.review(module.action_from_text("rm test.html"), context)

        self.assertEqual("ask", decision.verdict)
        self.assertIn("destructive", decision.reason)

    def test_shell_delete_protected_path_is_blocked(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace))
            decision = module.review(module.action_from_text("rm /etc/passwd"), context)

        self.assertEqual("block", decision.verdict)
        self.assertIn("protected path", decision.reason)

    def test_kernel_ignores_placeholder_provider_actions(self) -> None:
        class PlaceholderProvider:
            def complete(self, _: str, **___: object) -> str:
                return "BB9_ACTION shell <commande>`"

        context = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()))

        decision = Kernel(provider=PlaceholderProvider()).decide(Intention("analyse ce projet"), context)

        self.assertEqual("answer", decision.kind)
        self.assertIsNone(decision.action)
        self.assertIn("placeholder", decision.summary)

    def test_kernel_shell_action_ignores_following_provider_prose(self) -> None:
        class ChattyProvider:
            def complete(self, _: str, **___: object) -> str:
                return "BB9_ACTION shell rg -n alpha .\n\nJe vais analyser les résultats ensuite."

        context = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()))

        decision = Kernel(provider=ChattyProvider()).decide(Intention("cherche alpha"), context)

        self.assertEqual("action", decision.kind)
        self.assertEqual("shell", decision.action.name)
        self.assertEqual("rg -n alpha .", decision.action.params["cmd"])

    def test_kernel_blocks_nested_provider_action_prefix_before_guardian_ask(self) -> None:
        class NestedActionProvider:
            def complete(self, _: str, **___: object) -> str:
                return (
                    "BB9_ACTION shell find public/drafts/demo -maxdepth 1 -type f -print"
                    "BB9_ACTION browser check url=http://127.0.0.1:4173/public/drafts/demo/index.html screenshot=true"
                )

        context = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()), permission_profile="power")

        decision = Kernel(provider=NestedActionProvider()).decide(Intention("verifie la maquette"), context)

        self.assertEqual("action", decision.kind)
        self.assertEqual("invalid-provider-action", decision.action.name)
        self.assertIn("Invalid provider action request", decision.summary)

    def test_kernel_keeps_first_concatenated_files_read_action(self) -> None:
        class RepeatedReadProvider:
            def complete(self, _: str, **___: object) -> str:
                return (
                    "BB9_ACTION files read path=README.md"
                    "BB9_ACTION files read path=src/mini-wysiwyg.js"
                    "BB9_ACTION files read path=README.md"
                )

        context = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()), permission_profile="power")

        decision = Kernel(provider=RepeatedReadProvider()).decide(Intention("lis les fichiers"), context)

        self.assertEqual("action", decision.kind)
        self.assertEqual("files", decision.action.name)
        self.assertEqual("read", decision.action.params["op"])
        self.assertEqual("README.md", decision.action.params["path"])

    def test_loop_does_not_ask_user_for_nested_provider_action_prefix(self) -> None:
        class NestedActionProvider:
            calls = 0

            def complete(self, _: str, **___: object) -> str:
                self.calls += 1
                if self.calls == 1:
                    return (
                        "BB9_ACTION shell find public/drafts/demo -maxdepth 1 -type f -print"
                        "BB9_ACTION browser check url=http://127.0.0.1:4173/public/drafts/demo/index.html screenshot=true"
                    )
                return "Action malformee corrigee sans validation utilisateur."

        approvals: list[str] = []
        context = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()), permission_profile="power")

        result = run_once(
            Kernel(provider=NestedActionProvider()),
            Intention("verifie la maquette"),
            context,
            ask_user=lambda *_args: approvals.append("ask") or "defer",
        )

        self.assertEqual([], approvals)
        self.assertTrue(result.observation.ok)
        self.assertEqual("Action malformee corrigee sans validation utilisateur.", result.observation.summary)

    def test_kernel_allows_write_action_when_content_mentions_action_prefix_inline(self) -> None:
        """files write body containing BB9_ACTION in prose (not at line start) must not be rejected."""

        class WriteWithActionMentionProvider:
            def complete(self, _: str, **___: object) -> str:
                return (
                    "BB9_ACTION files write path=README.md text=\"\"\"\n"
                    "# API\n\n"
                    "Use BB9_ACTION files read to read a file.\n"
                    "\"\"\""
                )

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            context = RunContext(
                session=Session(),
                workspace=Workspace(root=Path(tmp)),
                permission_profile="power",
                tools=(ToolSpec(name="files", body=""),),
            )
            decision = Kernel(provider=WriteWithActionMentionProvider()).decide(
                Intention("document the API"), context
            )

        self.assertEqual("action", decision.kind)
        self.assertEqual("files", decision.action.name)
        self.assertNotEqual("invalid-provider-action", decision.action.name)

    def test_loop_emits_public_process_events_without_private_thinking(self) -> None:
        class ProcessProvider:
            calls = 0

            def complete(self, _: str, **___: object) -> str:
                self.calls += 1
                if self.calls == 1:
                    return "BB9_ACTION shell pwd"
                return "Chemin vérifié."

        events: list[TraceEvent] = []
        context = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()), permission_profile="power")

        result = run_once(Kernel(provider=ProcessProvider()), Intention("où suis-je"), context, on_event=events.append)
        process_events = [event for event in events if event.event_type == "process"]
        public_text = "\n".join(event.summary + "\n" + str(event.data) for event in process_events).lower()

        self.assertTrue(result.observation.ok)
        self.assertGreaterEqual(len(process_events), 5)
        self.assertIn("Comprendre la demande", [event.summary for event in process_events])
        self.assertIn("Exécuter une commande locale", [event.summary for event in process_events])
        self.assertIn("Intégrer l'observation", [event.summary for event in process_events])
        self.assertNotIn("chain-of-thought", public_text)
        self.assertNotIn("private thinking", public_text)
        self.assertNotIn("prompt interne", public_text)

    def test_kernel_does_not_execute_inline_action_examples(self) -> None:
        class ExampleProvider:
            def complete(self, _: str, **___: object) -> str:
                return "Exemple: `BB9_ACTION shell rm demo.txt` ne doit pas être exécuté."

        context = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()))

        decision = Kernel(provider=ExampleProvider()).decide(Intention("explique"), context)

        self.assertEqual("answer", decision.kind)
        self.assertIsNone(decision.action)

    def test_kernel_accepts_colon_after_tool_name(self) -> None:
        class ColonProvider:
            def complete(self, _: str, **___: object) -> str:
                return "BB9_ACTION shell: ls"

        context = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()))

        decision = Kernel(provider=ColonProvider()).decide(Intention("liste"), context)

        self.assertEqual("action", decision.kind)
        self.assertEqual("shell", decision.action.name)
        self.assertEqual("ls", decision.action.params["cmd"])

    def test_kernel_accepts_files_action_with_html_text(self) -> None:
        class HtmlFilesProvider:
            def complete(self, _: str, **___: object) -> str:
                return 'BB9_ACTION files insert_before path=index.html marker="</head>" text="<link rel=\\"stylesheet\\" href=\\"https://cdn.example/fa.css\\">"'

        context = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()))

        decision = Kernel(provider=HtmlFilesProvider()).decide(Intention("ajoute le CDN"), context)

        self.assertEqual("action", decision.kind)
        self.assertEqual("files", decision.action.name)
        self.assertEqual("insert_before", decision.action.params["op"])
        self.assertEqual("</head>", decision.action.params["marker"])
        self.assertIn("<link", decision.action.params["text"])

    def test_kernel_answers_context_inventory_without_provider(self) -> None:
        class FailingProvider:
            def complete(self, _: str, **___: object) -> str:
                raise AssertionError("provider should not be called")

        context = RunContext(
            session=Session(),
            workspace=Workspace(root=Path("/tmp/demo")),
            permission_profile="power",
            agent=AgentProfile(
                name="bb9",
                identity="Nom : bb9\nStyle : Critique chill\nLangue : Francais",
                soul="Sois debrouillard avant de demander.\nGagne la confiance par la competence.",
            ),
            tools=(ToolSpec(name="shell", body="", summary="lecture locale"),),
            skills=(Skill(name="project-onboarding", body="", summary="orientation"),),
            workspace_status=(
                "# Workspace Status\n\n"
                "- Git: branch `main`, clean\n"
                "- Package manager: pnpm\n"
                "- Scripts: `dev`, `test`\n"
                "- Read state: aucun fichier source n'est considere comme lu durablement par cet inventaire.\n"
            ),
            context_index=(
                "# Context Index\n\n"
                "## Governance\n\n"
                "- `AGENTS.md`\n"
                "## Files\n\n"
                "- `index.html`\n"
                "- `js/core.js`\n"
            ),
            subagents_index="# Subagents Index\n\n- `default` : worker generique\n",
        )

        decision = Kernel(provider=FailingProvider()).decide(Intention("tu as quoi en context?"), context)

        self.assertEqual("answer", decision.kind)
        self.assertIn("`SOUL.md` actif", decision.summary)
        self.assertIn("`/tmp/demo`", decision.summary)
        self.assertIn("Etat technique courant", decision.summary)
        self.assertIn("Package manager: pnpm", decision.summary)
        self.assertIn("Carte locale indexee", decision.summary)
        self.assertIn("`default`", decision.summary)
        self.assertIn("`shell`", decision.summary)
        self.assertIn("actions controlees", decision.summary)
        self.assertNotIn("pas encore lu", decision.summary)
        self.assertNotIn("si tu veux", decision.summary)

    def test_kernel_answers_context_inventory_for_ton_context_wording(self) -> None:
        class FailingProvider:
            def complete(self, _: str, **___: object) -> str:
                raise AssertionError("provider should not be called")

        context = RunContext(session=Session(), workspace=Workspace(root=Path("/tmp/demo")))

        decision = Kernel(provider=FailingProvider()).decide(Intention("quel est ton context"), context)

        self.assertEqual("answer", decision.kind)
        self.assertIn("`/tmp/demo`", decision.summary)

    def test_kernel_prompt_includes_power_autonomy(self) -> None:
        class CapturingProvider:
            prompt = ""

            def complete(self, prompt: str, **_: object) -> str:
                self.prompt = prompt
                return "ok"

        provider = CapturingProvider()
        context = RunContext(
            session=Session(),
            workspace=Workspace(root=Path.cwd()),
            permission_profile="power",
            workspace_status="# Workspace Status\n\n- Git: branch `main`, clean\n",
        )

        Kernel(provider=provider).decide(Intention("analyse ce projet"), context)

        self.assertIn("# Workspace Status", provider.prompt)
        self.assertIn("Profil actif: power", provider.prompt)
        self.assertIn("marque un tool comme `unavailable`", provider.prompt)
        self.assertIn("utilise le tool `files`", provider.prompt)
        self.assertIn("demande directement", provider.prompt)
        self.assertIn("Evite les fins timides", provider.prompt)
        self.assertIn("Ne termine pas par une limite passive", provider.prompt)
        self.assertIn("rg, grep, head, tail ou sed -n", provider.prompt)
        self.assertIn("Ne repete pas la meme commande de lecture", provider.prompt)
        self.assertIn("python3 -m http.server", provider.prompt)
        self.assertIn("Si l'utilisateur a deja dit go", provider.prompt)

    def test_kernel_uses_compact_prompt_for_4k_context_models(self) -> None:
        class CapturingProvider:
            prompt = ""

            def complete(self, prompt: str, **_: object) -> str:
                self.prompt = prompt
                return "ok"

        provider = CapturingProvider()
        session = Session()
        for index in range(6):
            session = session.with_message("user", f"message historique {index}")
        context = RunContext(
            session=session,
            workspace=Workspace(root=Path.cwd()),
            permission_profile="power",
            tools=(ToolSpec(name="files", body=""),),
            tools_index="# Tools Index\n\n" + ("gros index tools " * 400),
            skills=(Skill(name="dev", body=""),),
            skills_index="# Skills Index\n\n" + ("gros index skills " * 400),
            context_window_tokens=4096,
        )

        Kernel(provider=provider).decide(Intention("hey"), context)

        self.assertIn("# BB9 runtime context compact", provider.prompt)
        self.assertIn("Tools: `files`", provider.prompt)
        self.assertIn("Skills: `dev`", provider.prompt)
        self.assertIn("message historique 5", provider.prompt)
        self.assertNotIn("message historique 0", provider.prompt)
        self.assertNotIn("gros index tools", provider.prompt)
        self.assertNotIn("gros index skills", provider.prompt)
        self.assertLess(len(provider.prompt), 4000)

    def test_kernel_prompt_sends_explicit_destructive_requests_to_guardian(self) -> None:
        class CapturingProvider:
            prompt = ""

            def complete(self, prompt: str, **_: object) -> str:
                self.prompt = prompt
                return "ok"

        provider = CapturingProvider()
        context = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()))

        Kernel(provider=provider).decide(Intention("supprime test.html"), context)

        self.assertIn("commande destructive n'est pas interdite par principe", provider.prompt)
        self.assertIn("laisse le guardian demander validation ou bloquer", provider.prompt)
        self.assertIn("Ne propose pas a l'utilisateur d'executer lui-meme", provider.prompt)

    def test_kernel_prompt_guides_repo_analysis_without_file_inventory(self) -> None:
        class CapturingProvider:
            prompt = ""

            def complete(self, prompt: str, **_: object) -> str:
                self.prompt = prompt
                return "ok"

        provider = CapturingProvider()
        context = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()))

        Kernel(provider=provider).decide(Intention("analyse le repo"), context)

        self.assertIn("ne transforme pas la reponse en inventaire", provider.prompt)
        self.assertIn("decrivent tes moyens de travail, pas le projet analyse", provider.prompt)
        self.assertIn("Ne presente pas les tools, skills, subagents", provider.prompt)
        self.assertIn("verdict global", provider.prompt)
        self.assertIn("priorites d'amelioration", provider.prompt)
        self.assertIn("sauf si l'utilisateur demande explicitement la structure", provider.prompt)

    def test_plan_prompt_keeps_agent_capabilities_out_of_project_assessment(self) -> None:
        from bb9.templates.skills.plan.cli import _plan_prompt

        prompt = _plan_prompt("fais moi un bilan utile du projet")

        self.assertIn("le sujet est le workspace/repo courant", prompt)
        self.assertIn("N'utilise pas les sections Tools Index, Skills Index, Subagents Index", prompt)
        self.assertIn("tes moyens de travail", prompt)
        self.assertIn("bilan de BB9", prompt)
        self.assertIn("Le plan est le livrable de cadrage", prompt)
        self.assertIn("Ne produis pas un plan dont les tâches sont seulement", prompt)
        self.assertIn("propose directement des évolutions concrètes", prompt)
        self.assertIn("elle ne doit pas simplement préparer un futur plan", prompt)
        self.assertIn("Le champ `worker:` doit contenir `default` ou un nom présent dans Subagents Index", prompt)
        self.assertIn("N'utilise jamais un nom de tool ou de skill comme worker", prompt)

    def test_kernel_prompt_includes_called_skill_body(self) -> None:
        class CapturingProvider:
            prompt = ""

            def complete(self, prompt: str, **_: object) -> str:
                self.prompt = prompt
                return "ok"

        provider = CapturingProvider()
        context = RunContext(
            session=Session(),
            workspace=Workspace(root=Path.cwd()),
            skills=(
                Skill(
                    name="plan",
                    body="# Plan\n\n## Regles\n\n- Decouper en taches standalone.",
                    summary="planification",
                    commands=("`/p` : planifier vite.",),
                ),
            ),
            skills_index="# Skills Index\n\n- `plan` (on-demand) : planification\n",
        )

        Kernel(provider=provider).decide(Intention("/p refactoriser le module"), context)

        self.assertIn("# Skill: plan", provider.prompt)
        self.assertIn("taches standalone", provider.prompt)

    def test_loop_does_not_repeat_structurally_unavailable_browser_tool(self) -> None:
        class RepeatingBrowserKernel:
            calls = 0

            def decide(self, intention: Intention, context: RunContext) -> Decision:
                self.calls += 1
                if intention.metadata.get("tool_limit_reached"):
                    return Decision(kind="answer", summary="Browser indisponible, bilan avec les infos disponibles.")
                return Decision(
                    kind="action",
                    summary="browser check",
                    action=Action(
                        name="browser",
                        params={"op": "check", "url": "https://example.org", "text": "Hello"},
                        risk="low",
                    ),
                )

        kernel = RepeatingBrowserKernel()
        context = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()), permission_profile="power")
        executed: list[Action] = []
        events: list[TraceEvent] = []

        def fake_execute(action: Action, context=None) -> Observation:
            executed.append(action)
            return Observation(ok=False, summary="Playwright missing. Install with: python3 -m pip install playwright", retry_policy="block_tool")

        with patch("bb9.core.loop.execute", fake_execute):
            result = run_once(kernel, Intention("teste la page"), context, on_event=events.append)

        self.assertEqual(1, len(executed))
        self.assertTrue(result.observation.ok)
        self.assertIn("Browser indisponible", result.observation.summary)
        browser_actions = [event for event in events if event.event_type == "action" and event.data.get("tool") == "browser"]
        self.assertEqual(1, len(browser_actions))

    def test_loop_can_be_cancelled_between_steps(self) -> None:
        class AnswerKernel:
            def decide(self, intention: Intention, context: RunContext) -> Decision:
                return Decision(kind="answer", summary="ne devrait pas répondre")

        context = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()))

        with self.assertRaises(RunCancelled):
            run_once(AnswerKernel(), Intention("stop"), context, should_cancel=lambda: True)

    def test_loop_forces_workspace_artifact_command_to_attempt_files_before_answer(self) -> None:
        class SketchKernel:
            def decide(self, intention: Intention, context: RunContext) -> Decision:
                observations = tuple(intention.metadata.get("tool_observations") or ())
                if len(observations) == 0:
                    return Decision(kind="answer", summary="Voici 4 directions de maquette en texte.")
                if not any(item.get("tool") == "files" and item.get("ok") == "True" for item in observations):
                    return Decision(
                        kind="action",
                        summary="creer la maquette",
                        action=Action(
                            name="files",
                            params={
                                "op": "write",
                                "path": "public/drafts/demo/index.html",
                                "text": "<!doctype html><html><body>Demo</body></html>",
                            },
                            risk="low",
                        ),
                    )
                if not any(item.get("tool") == "browser" for item in observations):
                    return Decision(
                        kind="action",
                        summary="verifier la maquette",
                        action=Action(
                            name="browser",
                            params={"op": "check", "url": "http://127.0.0.1:4173/public/drafts/demo/index.html", "screenshot": "true"},
                            risk="low",
                        ),
                    )
                return Decision(kind="answer", summary="Maquette créée : /api/file/public/drafts/demo/index.html")

        context = RunContext(
            session=Session(),
            workspace=Workspace(root=Path.cwd()),
            permission_profile="power",
            skills=(
                Skill(
                    name="artifact-delivery",
                    body=(
                        "# Artifact Delivery\n\n"
                        "## Contrat de livraison\n\n"
                        "type: workspace-artifact\n"
                        "commands: /workspace-sketch\n"
                        "path: public/drafts/\n"
                        "link: /api/file/public/drafts/\n"
                        "preview: browser\n"
                    ),
                    commands=("`/workspace-sketch` : produire une maquette.",),
                ),
            ),
        )
        executed: list[Action] = []
        events: list[TraceEvent] = []

        def fake_execute(action: Action, context=None) -> Observation:
            executed.append(action)
            return Observation(
                ok=True,
                summary=f"Wrote {action.params.get('path')}",
                artifacts=(
                    Artifact(
                        kind="screenshot",
                        title="preview",
                        path=".bb9/artifacts/screenshots/demo.png",
                        source="browser",
                    ),
                ),
            )

        with patch("bb9.core.loop.execute", fake_execute):
            result = run_once(
                SketchKernel(),
                Intention("/workspace-sketch fais une maquette sante"),
                context,
                on_event=events.append,
            )

        self.assertEqual("Maquette créée : /api/file/public/drafts/demo/index.html", result.observation.summary)
        self.assertEqual("screenshot", result.observation.artifacts[0].kind)
        self.assertEqual(".bb9/artifacts/screenshots/demo.png", result.observation.artifacts[0].path)
        self.assertEqual(["files", "browser"], [action.name for action in executed])
        self.assertTrue(
            any(
                event.event_type == "observation"
                and event.data.get("tool") == "runtime"
                and "pas une proposition textuelle seule" in event.summary
                for event in events
            )
        )

    def test_loop_allows_workspace_artifact_clarifying_question_before_files(self) -> None:
        class QuestionKernel:
            def decide(self, intention: Intention, context: RunContext) -> Decision:
                return Decision(kind="answer", summary="Quel type de pro vise-t-on ?")

        context = RunContext(
            session=Session(),
            workspace=Workspace(root=Path.cwd()),
            permission_profile="power",
            skills=(
                Skill(
                    name="artifact-delivery",
                    body=(
                        "# Artifact Delivery\n\n"
                        "## Contrat de livraison\n\n"
                        "type: workspace-artifact\n"
                        "commands: /workspace-sketch\n"
                        "path: public/drafts/\n"
                        "link: /api/file/public/drafts/\n"
                        "preview: browser\n"
                    ),
                    commands=("`/workspace-sketch` : produire une maquette.",),
                ),
            ),
        )

        result = run_once(QuestionKernel(), Intention("/workspace-sketch maquette trop vague"), context)

        self.assertEqual("Quel type de pro vise-t-on ?", result.observation.summary)

    def test_loop_recovers_from_invalid_actions_then_forces_final_answer(self) -> None:
        class BlockedKernel:
            calls = 0

            def decide(self, intention: Intention, context: RunContext) -> Decision:
                if intention.metadata.get("tool_limit_reached"):
                    return Decision(kind="answer", summary="Je suis bloque par le protocole d'action.")
                self.calls += 1
                return Decision(
                    kind="action",
                    summary="tenter une ecriture invalide",
                    action=Action(
                        name="files",
                        params={"op": "invalid", "path": f"demo-{self.calls}.html"},
                        risk="forbidden",
                    ),
                )

        events: list[TraceEvent] = []
        context = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()), permission_profile="power")

        kernel = BlockedKernel()
        result = run_once(kernel, Intention("cree une maquette"), context, on_event=events.append)

        # Formulation errors are corrective observations, not fatal guardian blocks:
        # the model gets several chances, then is forced to answer itself.
        self.assertEqual("Je suis bloque par le protocole d'action.", result.observation.summary)
        self.assertNotIn("Action bloquée par le guardian", result.observation.summary)
        self.assertEqual(3, kernel.calls)
        corrective = [
            event
            for event in events
            if event.event_type == "observation" and "reformule l'action" in event.summary
        ]
        self.assertEqual(3, len(corrective))
        self.assertIn("invalid files action", corrective[0].summary)

    def test_loop_gives_usage_hint_for_invalid_browser_action_without_asking_user(self) -> None:
        browser_module = load_tool_module("browser", "runtime")
        self.assertIsNotNone(browser_module)

        class InvalidBrowserKernel:
            calls = 0

            def decide(self, intention: Intention, context: RunContext) -> Decision:
                self.calls += 1
                if self.calls == 1:
                    return Decision(
                        kind="action",
                        summary="capture",
                        action=browser_module.action_from_text("screenshot du projet"),
                    )
                return Decision(kind="answer", summary="Action corrigée au tour suivant.")

        approvals: list[str] = []
        events: list[TraceEvent] = []
        context = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()), permission_profile="power")

        result = run_once(
            InvalidBrowserKernel(),
            Intention("fais une capture du projet"),
            context,
            ask_user=lambda *_args: approvals.append("ask") or "defer",
            on_event=events.append,
        )

        self.assertEqual([], approvals)
        self.assertTrue(result.observation.ok)
        self.assertEqual("Action corrigée au tour suivant.", result.observation.summary)
        hint = next(
            event
            for event in events
            if event.event_type == "observation" and "invalid browser action" in event.summary
        )
        self.assertIn("Usage attendu", hint.summary)
        self.assertIn("browser <op>", hint.summary)

    def test_loop_fallback_does_not_expose_tool_budget_when_model_keeps_requesting_tools(self) -> None:
        class StubbornBrowserKernel:
            def decide(self, intention: Intention, context: RunContext) -> Decision:
                return Decision(
                    kind="action",
                    summary="browser check",
                    action=Action(
                        name="browser",
                        params={"op": "check", "url": "https://example.org", "text": "Hello"},
                        risk="low",
                    ),
                )

        context = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()), permission_profile="power")
        executed: list[Action] = []

        def fake_execute(action: Action, context=None) -> Observation:
            executed.append(action)
            return Observation(ok=False, summary="Playwright missing. Install with: python3 -m pip install playwright", retry_policy="block_tool")

        with patch("bb9.core.loop.execute", fake_execute):
            result = run_once(StubbornBrowserKernel(), Intention("teste la page"), context)

        self.assertEqual(1, len(executed))
        self.assertTrue(result.observation.ok)
        self.assertIn("Je m'arrête ici", result.observation.summary)
        self.assertIn("Playwright missing", result.observation.summary)
        self.assertNotIn("Tool budget reached", result.observation.summary)

    def test_loop_allows_browser_screenshot_retry_after_opening_page(self) -> None:
        class BrowserScreenshotKernel:
            def decide(self, intention: Intention, context: RunContext) -> Decision:
                observations = tuple(intention.metadata.get("tool_observations") or ())
                if len(observations) == 0:
                    return Decision(
                        kind="action",
                        summary="screenshot",
                        action=Action(name="browser", params={"op": "screenshot"}, risk="low"),
                    )
                if len(observations) == 1:
                    return Decision(
                        kind="action",
                        summary="open",
                        action=Action(
                            name="browser",
                            params={"op": "open", "url": "http://127.0.0.1:8002/index.html"},
                            risk="low",
                        ),
                    )
                if len(observations) == 2:
                    return Decision(
                        kind="action",
                        summary="screenshot retry",
                        action=Action(name="browser", params={"op": "screenshot"}, risk="low"),
                    )
                return Decision(kind="answer", summary="Capture faite.")

        executed: list[Action] = []

        def fake_execute(action: Action, context=None) -> Observation:
            executed.append(action)
            op = str(action.params.get("op"))
            if len(executed) == 1 and op == "screenshot":
                return Observation(ok=False, summary="No page open. Use browser open or browser check with url.")
            if op == "open":
                return Observation(ok=True, summary="Opened http://127.0.0.1:8002/index.html")
            if op == "screenshot":
                return Observation(ok=True, summary="Screenshot saved: /tmp/screen.png")
            return Observation(ok=False, summary="unexpected")

        context = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()), permission_profile="power")
        with patch("bb9.core.loop.execute", fake_execute):
            result = run_once(BrowserScreenshotKernel(), Intention("prends une capture"), context)

        self.assertTrue(result.observation.ok)
        self.assertEqual("Capture faite.", result.observation.summary)
        self.assertEqual(["screenshot", "open", "screenshot"], [str(action.params.get("op")) for action in executed])

    def test_loop_allows_recovery_after_local_browser_navigation_retry(self) -> None:
        class RecoveringBrowserKernel:
            def decide(self, intention: Intention, context: RunContext) -> Decision:
                observations = tuple(intention.metadata.get("tool_observations") or ())
                if len(observations) == 0:
                    return Decision(
                        kind="action",
                        summary="open",
                        action=Action(
                            name="browser",
                            params={"op": "open", "url": "http://127.0.0.1:3000/"},
                            risk="low",
                        ),
                    )
                if len(observations) == 1:
                    return Decision(
                        kind="action",
                        summary="open retry",
                        action=Action(
                            name="browser",
                            params={"op": "open", "url": "http://127.0.0.1:3000/"},
                            risk="low",
                        ),
                    )
                if len(observations) == 2:
                    return Decision(
                        kind="action",
                        summary="start server",
                        action=Action(name="shell", params={"cmd": "python3 -m http.server 3000"}, risk="medium"),
                    )
                return Decision(kind="answer", summary="Serveur relance, nouvelle URL a utiliser.")

        executed: list[Action] = []

        def fake_execute(action: Action, context=None) -> Observation:
            executed.append(action)
            if action.name == "browser":
                return Observation(
                    ok=False,
                    summary="browser navigation failed: Page.goto: net::ERR_EMPTY_RESPONSE at http://127.0.0.1:3000/",
                    data={"url": "http://127.0.0.1:3000/"},
                    retry_policy="recoverable",
                )
            return Observation(ok=True, summary="HTTP server started: http://127.0.0.1:3001", data={"url": "http://127.0.0.1:3001"})

        context = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()), permission_profile="power")
        with patch("bb9.core.loop.execute", fake_execute):
            result = run_once(RecoveringBrowserKernel(), Intention("teste index"), context)

        self.assertTrue(result.observation.ok)
        self.assertEqual("Serveur relance, nouvelle URL a utiliser.", result.observation.summary)
        self.assertEqual(["browser", "shell"], [action.name for action in executed])

    def test_cli_routes_unknown_slash_command_to_matching_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = root / "agents" / "default"
            skill = root / "skills" / "plan"
            agent.mkdir(parents=True)
            skill.mkdir(parents=True)
            (agent / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            (skill / "SKILL.md").write_text(
                "# Plan\n\n## Résumé\n\nPlanifier.\n\n## Commandes\n\n- `/p` : planifier vite.\n",
                encoding="utf-8",
            )
            state = CliState(
                profile_explicit=True,
                agents_dir=root / "agents",
                skills_dir=root / "skills",
                tools_dir=root / "tools",
            )
            cli = Cli(state)
            seen: list[str] = []
            cli.run_intention = seen.append

            self.assertTrue(cli.handle_command("/plan livrer la feature"))
            self.assertTrue(cli.handle_command("/p livrer la feature"))
            self.assertEqual(["/plan livrer la feature", "/p livrer la feature"], seen)

    def test_cli_renders_live_tool_markers(self) -> None:
        cli = Cli(CliState(profile_explicit=True))
        output = io.StringIO()

        with redirect_stdout(output):
            cli.render_live_event(
                TraceEvent(event_type="action", summary="shell", session_id="s", data={"tool": "shell", "cmd": "pwd"})
            )
            cli.render_live_event(
                TraceEvent(
                    event_type="observation",
                    summary="commande terminée",
                    session_id="s",
                    data={"tool": "shell", "ok": True},
                )
            )

        rendered = output.getvalue()
        self.assertIn("tool... shell en cours", rendered)
        self.assertIn("pwd", rendered)
        self.assertIn("tool... shell ok - commande terminée", rendered)

    def test_cli_live_shell_summary_hides_raw_html(self) -> None:
        cli = Cli(CliState(profile_explicit=True))
        output = io.StringIO()

        with redirect_stdout(output):
            cli.render_live_event(
                TraceEvent(
                    event_type="observation",
                    summary="<!doctype html><html><head><title>Demo</title></head><body>ok</body></html>",
                    session_id="s",
                    data={"tool": "shell", "ok": True},
                )
            )

        rendered = strip_ansi(output.getvalue())
        self.assertIn("tool... shell ok - sortie HTML recue", rendered)
        self.assertNotIn("<!doctype", rendered)

    def test_cli_live_tool_markers_update_activity_text(self) -> None:
        class FakeActivity:
            def __init__(self) -> None:
                self.texts: list[str] = []

            def interrupt(self, writer) -> None:
                writer()

            def set_text(self, text: str) -> None:
                self.texts.append(text)

        cli = Cli(CliState(profile_explicit=True))
        activity = FakeActivity()
        cli.activity = activity
        output = io.StringIO()

        with redirect_stdout(output):
            cli.render_live_event(
                TraceEvent(event_type="action", summary="shell", session_id="s", data={"tool": "shell", "cmd": "pwd"})
            )
            cli.render_live_event(
                TraceEvent(
                    event_type="observation",
                    summary="commande terminée",
                    session_id="s",
                    data={"tool": "shell", "ok": True},
                )
            )

        self.assertIn("tool... shell en cours", strip_ansi(output.getvalue()))
        self.assertEqual(["shell en cours", "BB9 prepare une reponse"], activity.texts)

    def test_cli_activity_indicator_keeps_non_interactive_output_clean(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output):
            indicator = CliActivityIndicator(CliTheme(enabled=True), "BB9 prepare une reponse")
            indicator.start()
            indicator.set_text("shell en cours")
            indicator.interrupt(lambda: print("tool... shell en cours"))
            with indicator.paused():
                print("Validation requise")
            indicator.stop()

        self.assertEqual("tool... shell en cours\nValidation requise\n", output.getvalue())

    def test_cli_diff_artifact_renderer_summarizes_changed_files(self) -> None:
        artifact = Artifact(
            kind="diff",
            title="2 fichiers modifiés (+14/-3)",
            path="/tmp/bb9.diff",
            metadata={
                "files": [
                    {"path": "README.md", "status": "M", "insertions": 10, "deletions": 1},
                    {"path": "bb9/core/cli.py", "status": "M", "insertions": 4, "deletions": 2},
                ]
            },
        )

        rendered = strip_ansi(render_cli_diff_artifact(artifact, CliTheme(enabled=True)))

        self.assertIn("diff... 2 fichiers modifiés (+14/-3)", rendered)
        self.assertIn("README.md (M) +10/-1", rendered)
        self.assertIn("bb9/core/cli.py (M) +4/-2", rendered)
        self.assertIn("patch... /tmp/bb9.diff", rendered)

    def test_cli_prints_diff_artifacts_after_turn(self) -> None:
        cli = Cli(CliState(profile_explicit=True))
        artifact = Artifact(
            kind="diff",
            title="1 fichier modifié (+2/-0)",
            metadata={"files": [{"path": "README.md", "status": "M", "insertions": 2, "deletions": 0}]},
        )
        output = io.StringIO()

        with redirect_stdout(output):
            cli.print_turn_artifacts((artifact,))

        rendered = strip_ansi(output.getvalue())
        self.assertIn("diff... 1 fichier modifié (+2/-0)", rendered)
        self.assertIn("README.md (M) +2/-0", rendered)

    def test_cli_markdown_renderer_keeps_plain_output_when_disabled(self) -> None:
        markdown = "# Titre\n\n- item\n\n```bash\necho ok\n```"

        rendered = render_cli_markdown(markdown, CliTheme(enabled=False))

        self.assertEqual(markdown, rendered)

    def test_cli_markdown_renderer_formats_common_blocks(self) -> None:
        markdown = "# Titre\n\n- item `code`\n\n> note\n\n```bash\necho ok\n```"

        rendered = render_cli_markdown(markdown, CliTheme(enabled=True))
        plain = strip_ansi(rendered)

        self.assertIn("━ Titre", plain)
        self.assertIn("• item code", plain)
        self.assertIn("│ note", plain)
        self.assertIn("╭─ code bash", plain)
        self.assertIn("│ echo ok", plain)
        self.assertIn("╰─", plain)

    def test_cli_markdown_renderer_highlights_code_blocks(self) -> None:
        markdown = "```js\nconst answer = 42;\nreturn \"ok\";\n```"

        rendered = render_cli_markdown(markdown, CliTheme(enabled=True))
        plain = strip_ansi(rendered)

        self.assertIn("│ const answer = 42;", plain)
        self.assertIn("│ return \"ok\";", plain)
        self.assertIn("\033[", rendered)

    def test_cli_markdown_renderer_keeps_code_uncolored_when_disabled(self) -> None:
        markdown = "```js\nconst answer = 42;\n```"

        rendered = render_cli_markdown(markdown, CliTheme(enabled=False))

        self.assertEqual(markdown, rendered)

    def test_cli_banner_status_uses_readable_labels(self) -> None:
        cli = Cli(CliState(profile_explicit=True))
        plain = [strip_ansi(line) for line in cli.status_lines()]

        self.assertIn("Profil: safe", plain)
        self.assertTrue(any(line.startswith("Modele:") for line in plain))
        self.assertTrue(any(line.startswith("Agent:") for line in plain))
        self.assertFalse(any("..." in line.split(":", 1)[0] for line in plain))

    def test_cli_banner_recent_activity_is_aligned(self) -> None:
        cli = Cli(CliState(profile_explicit=True))
        output = io.StringIO()

        with redirect_stdout(output):
            cli.print_banner()

        plain_lines = [strip_ansi(line) for line in output.getvalue().splitlines()]
        title = next(line for line in plain_lines if "Activite recente" in line)
        empty = next(line for line in plain_lines if "Aucune activite recente" in line)

        self.assertEqual(title.index("Activite recente"), empty.index("Aucune activite recente"))

    def test_fit_words_truncates_at_word_boundary(self) -> None:
        fitted = fit_words("afficher l'historique visible", 18)

        self.assertEqual("afficher…", fitted)
        self.assertNotIn("histori…", fitted)

    def test_cli_help_lists_archive_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = root / "agents" / "default"
            skill = root / "skills" / "plan"
            tool = root / "tools" / "web"
            agent.mkdir(parents=True)
            skill.mkdir(parents=True)
            tool.mkdir(parents=True)
            (agent / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            (skill / "SKILL.md").write_text(
                "# Plan\n\n## Résumé\n\nPlanifier.\n\n## Commandes\n\n- `/p` : planifier vite.\n",
                encoding="utf-8",
            )
            (tool / "TOOL.md").write_text(
                "# Web\n\n## Résumé\n\nWeb.\n\n## Commandes\n\n- `/web` : ouvrir web.\n",
                encoding="utf-8",
            )
            cli = Cli(
                CliState(
                    profile_explicit=True,
                    agents_dir=root / "agents",
                    skills_dir=root / "skills",
                    tools_dir=root / "tools",
                )
            )

            output = io.StringIO()
            with redirect_stdout(output):
                cli.cmd_help("")

            self.assertIn("/p", output.getvalue())
            self.assertIn("planifier vite", output.getvalue())
            self.assertIn("/web", output.getvalue())

    def test_cli_reports_archive_command_collisions_without_routing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = root / "agents" / "default"
            skill = root / "skills" / "plan"
            tool = root / "tools" / "web"
            agent.mkdir(parents=True)
            skill.mkdir(parents=True)
            tool.mkdir(parents=True)
            (agent / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            (skill / "SKILL.md").write_text(
                "# Plan\n\n## Résumé\n\nPlanifier.\n\n## Commandes\n\n- `/go` : côté skill.\n",
                encoding="utf-8",
            )
            (tool / "TOOL.md").write_text(
                "# Web\n\n## Résumé\n\nWeb.\n\n## Commandes\n\n- `/go` : côté tool.\n- `/help` : conflit natif.\n",
                encoding="utf-8",
            )
            cli = Cli(
                CliState(
                    profile_explicit=True,
                    agents_dir=root / "agents",
                    skills_dir=root / "skills",
                    tools_dir=root / "tools",
                )
            )
            seen: list[str] = []
            cli.run_intention = seen.append

            output = io.StringIO()
            with redirect_stdout(output):
                self.assertTrue(cli.handle_command("/go faire"))
                self.assertTrue(cli.cmd_context(""))

            self.assertEqual([], seen)
            self.assertIn("Commande d'archive ambiguë: /go", output.getvalue())
            self.assertIn("cmd!...", output.getvalue())
            self.assertIn("/go", output.getvalue())
            self.assertIn("/help", output.getvalue())
            self.assertIn("native", output.getvalue())

    def test_cli_skill_command_prefers_local_skill(self) -> None:
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agent = root / "agents" / "default"
            global_skill = root / "skills" / "plan"
            local_skill = workspace / ".bb9" / "skills" / "plan"
            agent.mkdir(parents=True)
            global_skill.mkdir(parents=True)
            local_skill.mkdir(parents=True)
            (agent / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            (global_skill / "SKILL.md").write_text("# Plan\n\n## Résumé\n\nGlobal.\n", encoding="utf-8")
            (local_skill / "SKILL.md").write_text("# Plan\n\n## Résumé\n\nLocal.\n", encoding="utf-8")
            state = CliState(
                profile_explicit=True,
                agents_dir=root / "agents",
                skills_dir=root / "skills",
                tools_dir=root / "tools",
            )
            try:
                os.chdir(workspace)
                context = context_runtime.build_context(state)
            finally:
                os.chdir(cwd)

            self.assertEqual(("plan",), tuple(skill.name for skill in context.skills))
            self.assertEqual(local_skill.parent.resolve(), context.skills[0].root.resolve())
            self.assertIn("Local.", context.skills[0].summary)

    def test_skill_summary_uses_frontmatter_description_when_no_resume_section(self) -> None:
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agent = root / "agents" / "default"
            skill = workspace / ".bb9" / "skills" / "project-workflow"
            agent.mkdir(parents=True)
            skill.mkdir(parents=True)
            (agent / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            (skill / "SKILL.md").write_text(
                "---\n"
                "name: project-workflow\n"
                "description: Skill projet exemple.\n"
                "commands: project-map, project-map\n"
                "---\n"
                "# Project Workflow\n",
                encoding="utf-8",
            )
            state = CliState(
                profile_explicit=True,
                agents_dir=root / "agents",
                skills_dir=root / "skills",
                tools_dir=root / "tools",
            )
            try:
                os.chdir(workspace)
                context = context_runtime.build_context(state)
            finally:
                os.chdir(cwd)

            self.assertEqual("Skill projet exemple.", context.skills[0].summary)
            self.assertEqual(("`/project-map`",), context.skills[0].commands)
            self.assertIn("Skill projet exemple.", context.skills_index)

    def test_skill_activation_can_match_project_command_without_declaring_collision(self) -> None:
        class CapturingProvider:
            prompt = ""

            def complete(self, prompt: str, **_: object) -> str:
                self.prompt = prompt
                return "ok"

        context = RunContext(
            session=Session(),
            workspace=Workspace(root=Path.cwd()),
            skills=(
                Skill(name="project-workflow", body="# Project Workflow", commands=("`/project-sketch` : sketch.",)),
                Skill(
                    name="visual-sketching",
                    body="# Visual Sketching",
                    activation="/project-sketch, maquette libre",
                ),
            ),
        )
        provider = CapturingProvider()

        Kernel(provider=provider).decide(Intention("/project-sketch propose 3 directions"), context)

        self.assertIn("# Skill: project-workflow", provider.prompt)
        self.assertIn("# Skill: visual-sketching", provider.prompt)

    def test_provider_prompt_marks_current_intention_as_turn_authority(self) -> None:
        class CapturingProvider:
            prompt = ""

            def complete(self, prompt: str, **_: object) -> str:
                self.prompt = prompt
                return "ok"

        provider = CapturingProvider()
        context = RunContext(
            session=Session().with_message("user", "check le avec vision").with_message("assistant", "Voilà Planty."),
            workspace=Workspace(root=Path.cwd()),
        )

        Kernel(provider=provider).decide(Intention("/project-sketch fait moi 3 maquettes"), context)

        self.assertIn("# Frontiere de tour", provider.prompt)
        self.assertIn("L'intention courante ci-dessous est l'autorite de ce tour", provider.prompt)
        self.assertLess(provider.prompt.index("# Frontiere de tour"), provider.prompt.index("# Intention courante"))
        self.assertIn("/project-sketch fait moi 3 maquettes", provider.prompt)

    def test_provider_prompt_requires_single_clean_action(self) -> None:
        class CapturingProvider:
            prompt = ""

            def complete(self, prompt: str, **_: object) -> str:
                self.prompt = prompt
                return "ok"

        provider = CapturingProvider()
        context = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()), permission_profile="power")

        Kernel(provider=provider).decide(Intention("cree une maquette"), context)

        self.assertIn("# Protocole BB9_ACTION", provider.prompt)
        self.assertIn("une seule action `BB9_ACTION`", provider.prompt)
        self.assertIn("sans phrase naturelle ajoutee", provider.prompt)
        self.assertIn("plusieurs actions imbriquees sera bloquee", provider.prompt)

    def test_agent_soul_is_promoted_to_behavior_contract(self) -> None:
        class CapturingProvider:
            prompt = ""

            def complete(self, prompt: str, **_: object) -> str:
                self.prompt = prompt
                return "ok"

        provider = CapturingProvider()
        context = RunContext(
            session=Session(),
            workspace=Workspace(root=Path.cwd()),
            agent=AgentProfile(
                name="bb9",
                identity="Nom : bb9",
                soul="Sois debrouillard avant de demander. Sois audacieux dans le workspace.",
            ),
        )

        Kernel(provider=provider).decide(Intention("analyse ce projet"), context)

        self.assertIn("# Contrat comportemental actif", provider.prompt)
        self.assertIn("modifient tes decisions", provider.prompt)
        self.assertIn("BB9_ACTION precise", provider.prompt)
        self.assertIn("explore, verifie et synthetise", provider.prompt)

    def test_agent_soul_can_increase_tool_budget(self) -> None:
        soul = "Sois debrouillard avant de demander. Explore et verifie le workspace."

        self.assertGreater(tool_budget_for("safe", soul), tool_budget_for("safe"))
        self.assertGreater(tool_budget_for("limited", soul), tool_budget_for("limited"))
        self.assertEqual(tool_budget_for("power", soul), tool_budget_for("power"))

    def test_kernel_prompt_marks_soul_as_active_identity_context(self) -> None:
        class CapturingProvider:
            prompt = ""

            def complete(self, prompt: str, **_: object) -> str:
                self.prompt = prompt
                return "ok"

        provider = CapturingProvider()
        context = RunContext(
            session=Session(),
            workspace=Workspace(root=Path.cwd()),
            agent=AgentProfile(
                name="test",
                identity="# Identity\nRole test",
                soul="# SOUL.md\nSois audacieux.",
            ),
        )

        Kernel(provider=provider).decide(Intention("presente toi"), context)

        self.assertIn("contexte d'identite actif", provider.prompt)
        self.assertIn("pas decoratifs", provider.prompt)
        self.assertIn("## SOUL.md", provider.prompt)

    def test_secret_placeholder_is_forbidden_before_ask(self) -> None:
        module = load_tool_module("secret", "runtime")
        self.assertIsNotNone(module)

        action = module.action_from_text("add <NOM_DE_VARIABLE>")

        self.assertEqual("forbidden", action.risk)
        self.assertEqual("invalid", action.params["op"])

    def test_default_agents_are_seeded_in_user_folder(self) -> None:
        old_home = os.environ.get("BB9_HOME")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["BB9_HOME"] = str(Path(tmp) / "user")
                import bb9.core.paths as paths

                paths = importlib.reload(paths)
                agents = paths.default_agents_dir()

                self.assertEqual(Path(tmp) / "user" / "agents", agents)
                self.assertTrue((agents / "default" / "IDENTITY.md").is_file())
                self.assertTrue((agents / "default" / "SOUL.md").is_file())
                self.assertTrue((agents / "default" / "MODEL.md").is_file())
                self.assertTrue((agents / "default" / "subagents" / "default" / "IDENTITY.md").is_file())
        finally:
            if old_home is None:
                os.environ.pop("BB9_HOME", None)
            else:
                os.environ["BB9_HOME"] = old_home
            import bb9.core.paths as paths

            importlib.reload(paths)

    def test_user_agent_templates_are_merged_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agents = Path(tmp) / "agents"
            existing = agents / "default"
            existing.mkdir(parents=True)
            (existing / "IDENTITY.md").write_text("custom identity\n", encoding="utf-8")

            ensure_user_agents(agents)

            self.assertEqual("custom identity\n", (existing / "IDENTITY.md").read_text(encoding="utf-8"))
            self.assertTrue((existing / "subagents" / "default" / "IDENTITY.md").is_file())

    def test_subagents_index_is_generated_from_subagent_identities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agents = ensure_user_agents(Path(tmp) / "agents")

            index = refresh_subagents_index(agents, "default")

            self.assertIn("`default`", index)
            self.assertIn("`research`", index)
            self.assertIn("`worker`", index)
            self.assertIn("implementation locale", index)
            self.assertIn("recherche documentaire", index)
            self.assertTrue((agents / "default" / "subagents" / "INDEX.md").is_file())

    def test_goal_worker_uses_dev_ephemeral_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = ensure_user_agents(root / "agents")
            state = CliState(
                agents_dir=agents,
                skills_dir=root / "skills",
                tools_dir=root / "tools",
            )

            worker = context_runtime.load_goal_worker_agent(state)

            self.assertEqual("default/dev", worker.name)
            self.assertIn("delegate", worker.disabled_tools)

    def test_repl_profile_command_updates_context_profile(self) -> None:
        old_home = os.environ.get("BB9_HOME")
        with tempfile.TemporaryDirectory() as tmp:
            try:
                root = Path(tmp)
                os.environ["BB9_HOME"] = str(root / "home")
                cli = Cli(
                    CliState(
                        agents_dir=ensure_user_agents(root / "agents"),
                        skills_dir=root / "skills",
                        tools_dir=root / "tools",
                    )
                )

                with redirect_stdout(io.StringIO()):
                    self.assertTrue(cli.cmd_profile("power"))

                self.assertEqual("power", cli.state.profile)
                self.assertEqual("power", cli.build_context().permission_profile)
                self.assertEqual("power", context_runtime.build_context(cli.state).permission_profile)
                self.assertEqual("power", SettingsStore().load().profile)
            finally:
                if old_home is None:
                    os.environ.pop("BB9_HOME", None)
                else:
                    os.environ["BB9_HOME"] = old_home

    def test_goal_dev_worker_model_overrides_active_provider_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = ensure_user_agents(root / "agents")
            dev = agents / "default" / "subagents" / "dev"
            dev.mkdir(parents=True)
            (dev / "MODEL.md").write_text(
                "# Model\n\nModel : light-model\nReasoningEffort : low\n",
                encoding="utf-8",
            )
            state = CliState(
                agents_dir=agents,
                skills_dir=root / "skills",
                tools_dir=root / "tools",
                active_provider=ProviderEntry(
                    id="p1",
                    name="local",
                    provider="openai-compatible",
                    auth_type=AUTH_API,
                    base_url="http://localhost:1234/v1",
                    api_key_ref="env:TEST",
                    model="heavy-model",
                ),
            )

            provider = Cli(state).build_goal_provider()

            self.assertEqual("light-model", provider.model)
            self.assertEqual("low", provider.reasoning_effort)

    def test_subagent_model_inherits_reasoning_effort_from_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = ensure_user_agents(root / "agents")
            (agents / "default" / "MODEL.md").write_text(
                "# Model\n\nModel : gpt-5.5\nReasoningEffort : high\n",
                encoding="utf-8",
            )
            (agents / "default" / "subagents" / "research" / "MODEL.md").write_text(
                "# Model\n\nModel :\nReasoningEffort :\n",
                encoding="utf-8",
            )

            worker = __import__("bb9.core.agents", fromlist=["load_subagent"]).load_subagent(
                agents,
                "default",
                "research",
            )

            self.assertEqual("gpt-5.5", worker.model)
            self.assertEqual("high", worker.reasoning_effort)

    def test_agents_payload_exposes_declared_tool_params(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents"
            agents.joinpath("default").mkdir(parents=True)
            (agents / "default" / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            app = ChatApiApp(
                ChatApiState(
                    profile="limited",
                    profile_explicit=True,
                    agents_dir=agents,
                    skills_dir=root / "skills",
                    tools_dir=Path(__file__).resolve().parents[1] / "bb9" / "tools",
                    secret_store_root=root / "secrets",
                    visible_history_path=root / "history.db",
                )
            )
            payload = app.agents_payload()

        tools = {tool["name"]: tool for tool in payload["tools"]}
        self.assertEqual(
            [
                {"name": "CALDAV_URL", "set": False},
                {"name": "CALDAV_USERNAME", "set": False},
                {"name": "CALDAV_PASSWORD", "set": False},
            ],
            tools["caldav"]["params"],
        )
        # `secret:NOM` mentions outside a `Secrets requis` section must not become params.
        self.assertEqual([], tools["secret"]["params"])
        self.assertEqual([], tools["browser"]["params"])

    def test_set_tool_secret_accepts_only_declared_params(self) -> None:
        from bb9.tools.secret.store import SecretStore

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents"
            agents.joinpath("default").mkdir(parents=True)
            (agents / "default" / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            app = ChatApiApp(
                ChatApiState(
                    profile="limited",
                    profile_explicit=True,
                    agents_dir=agents,
                    skills_dir=root / "skills",
                    tools_dir=Path(__file__).resolve().parents[1] / "bb9" / "tools",
                    secret_store_root=root / "secrets",
                    visible_history_path=root / "history.db",
                )
            )
            saved = app.set_tool_secret({"tool": "caldav", "name": "CALDAV_URL", "value": "https://cal.example.test"})
            undeclared = app.set_tool_secret({"tool": "caldav", "name": "OPENAI_API_KEY", "value": "x" * 12})
            empty = app.set_tool_secret({"tool": "caldav", "name": "CALDAV_USERNAME", "value": "  "})
            unknown_tool = app.set_tool_secret({"tool": "nope", "name": "CALDAV_URL", "value": "x"})
            stored_value = SecretStore(root / "secrets").get("CALDAV_URL")

        self.assertTrue(saved["ok"])
        tools = {tool["name"]: tool for tool in saved["tools"]}
        params = {param["name"]: param["set"] for param in tools["caldav"]["params"]}
        self.assertTrue(params["CALDAV_URL"])
        self.assertFalse(params["CALDAV_PASSWORD"])
        self.assertEqual("https://cal.example.test", stored_value)
        self.assertEqual({"ok": False, "error": "param_not_declared"}, undeclared)
        self.assertEqual({"ok": False, "error": "empty_value"}, empty)
        self.assertEqual({"ok": False, "error": "tool_not_found"}, unknown_tool)

    def test_create_skill_names_cannot_escape_skills_root(self) -> None:
        module = load_tool_module("create_skill", "runtime")
        self.assertIsNotNone(module)

        self.assertEqual("evil", module.normalize_skill_name("../evil"))
        self.assertEqual("etc-passwd", module.normalize_skill_name("/etc/passwd"))
        action = module.action_from_text("draft ../../outside")
        self.assertEqual("draft", action.params["op"])
        self.assertNotIn("/", action.params["name"])
        self.assertNotIn("..", action.params["name"])

    def test_agents_api_renames_agent_and_appends_missing_model_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents"
            skills = root / "skills"
            tools = root / "tools"
            agent = agents / "draft"
            agent.mkdir(parents=True)
            skills.mkdir()
            tools.mkdir()
            agent.joinpath("IDENTITY.md").write_text("Nom : draft\n", encoding="utf-8")
            agent.joinpath("SOUL.md").write_text("# Soul\n", encoding="utf-8")
            agent.joinpath("MODEL.md").write_text("# Model\n", encoding="utf-8")
            app = ChatApiApp(ChatApiState(agents_dir=agents, skills_dir=skills, tools_dir=tools))

            payload = app.update_agent(
                {
                    "name": "draft",
                    "new_name": "renamed",
                    "provider_id": "local",
                    "model": "gpt-5-mini",
                    "reasoning_effort": "low",
                }
            )

            self.assertTrue(payload["ok"])
            self.assertFalse(agent.exists())
            model_text = (agents / "renamed" / "MODEL.md").read_text(encoding="utf-8")
            self.assertIn("ProviderId : local", model_text)
            self.assertIn("Model : gpt-5-mini", model_text)
            self.assertIn("ReasoningEffort : low", model_text)
            self.assertIn("renamed", [item["name"] for item in payload["agents"]])

    def test_agents_api_stores_telegram_config_with_secret_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents"
            skills = root / "skills"
            tools = root / "tools"
            agent = agents / "default"
            agent.mkdir(parents=True)
            skills.mkdir()
            tools.mkdir()
            agent.joinpath("IDENTITY.md").write_text("Nom : default\n", encoding="utf-8")
            agent.joinpath("SOUL.md").write_text("# Soul\n", encoding="utf-8")
            app = ChatApiApp(ChatApiState(agents_dir=agents, skills_dir=skills, tools_dir=tools))

            with patch("bb9.core.agent_telegram.normalize_api_key_ref_input", return_value=("secret:TELEGRAM_DEFAULT_BOT_TOKEN", "")):
                payload = app.update_agent(
                    {
                        "name": "default",
                        "telegram": {
                            "enabled": True,
                            "token": "123456:raw-token",
                            "allowed_chat_ids": "[123, -456]",
                        },
                    }
                )

            self.assertTrue(payload["ok"])
            telegram_text = agent.joinpath("TELEGRAM.md").read_text(encoding="utf-8")
            self.assertIn("active", telegram_text)
            self.assertIn("secret:TELEGRAM_DEFAULT_BOT_TOKEN", telegram_text)
            self.assertNotIn("raw-token", telegram_text)
            self.assertIn("[123, -456]", telegram_text)
            default = next(item for item in payload["agents"] if item["name"] == "default")
            self.assertTrue(default["telegram"]["enabled"])
            self.assertEqual("secret:TELEGRAM_DEFAULT_BOT_TOKEN", default["telegram"]["token_ref"])
            self.assertEqual([123, -456], default["telegram"]["allowed_chat_ids"])

    def test_telegram_channel_parses_update_and_chunks_messages(self) -> None:
        from bb9.channels.telegram import (
            looks_like_telegram_token,
            telegram_callback_from_update,
            telegram_chunks,
            telegram_message_from_update,
        )

        update = {
            "update_id": 42,
            "message": {
                "message_id": 7,
                "text": "bonjour",
                "chat": {"id": 123},
                "from": {"username": "egza"},
            },
        }

        message = telegram_message_from_update(update)

        self.assertIsNotNone(message)
        assert message is not None
        self.assertEqual(42, message.update_id)
        self.assertEqual(123, message.chat_id)
        self.assertEqual("bonjour", message.text)
        self.assertEqual(7, message.message_id)
        self.assertEqual("egza", message.user_label)
        self.assertEqual(["abc"], list(telegram_chunks("abc", limit=10)))
        self.assertEqual(["abc", "def"], list(telegram_chunks("abc\ndef", limit=5)))
        self.assertTrue(looks_like_telegram_token("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abc"))
        self.assertFalse(looks_like_telegram_token("123456789"))
        callback = telegram_callback_from_update(
            {
                "update_id": 43,
                "callback_query": {
                    "id": "cb-1",
                    "data": "bb9:a:token:allow",
                    "from": {"id": 999},
                    "message": {"message_id": 8, "chat": {"id": 123}},
                },
            }
        )
        self.assertIsNotNone(callback)
        assert callback is not None
        self.assertEqual("cb-1", callback.callback_id)
        self.assertEqual(123, callback.chat_id)
        self.assertEqual("bb9:a:token:allow", callback.data)

    def test_web_agent_home_history_includes_telegram_turns(self) -> None:
        from bb9.core.sessions import AGENT_HOME_SOURCE, agent_home_session_id

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents"
            agents.joinpath("default").mkdir(parents=True)
            (agents / "default" / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            home_id = agent_home_session_id("default")

            history = VisibleHistoryStore(root / "history.db")
            try:
                history.append_turn(
                    session_id=home_id,
                    user_text="question depuis telegram",
                    assistant_text="réponse vue sur telegram",
                    source="telegram",
                    project_path=None,
                    artifacts=(),
                )
                history.append_turn(
                    session_id=home_id,
                    user_text="question depuis le web",
                    assistant_text="réponse vue sur le web",
                    source="web",
                    project_path=None,
                    artifacts=(),
                )
            finally:
                history.close()

            app = ChatApiApp(
                ChatApiState(
                    profile="limited",
                    profile_explicit=True,
                    agents_dir=agents,
                    skills_dir=root / "skills",
                    tools_dir=root / "tools",
                    session_store_path=root / "sessions.db",
                    visible_history_path=root / "history.db",
                    session=Session(id=home_id, source=AGENT_HOME_SOURCE),
                )
            )
            payload = app.switch_agent_home(home_id)

        self.assertTrue(payload["ok"])
        contents = [str(message.get("content") or "") for message in payload["messages"]]
        self.assertIn("question depuis telegram", contents)
        self.assertIn("réponse vue sur telegram", contents)
        self.assertIn("réponse vue sur le web", contents)

    def test_telegram_channel_handles_start_without_provider(self) -> None:
        from bb9.channels.telegram import TelegramHost, TelegramMessage
        from bb9.core.agent_telegram import AgentTelegramConfig
        from bb9.core.sessions import AGENT_HOME_SOURCE, agent_home_session_id

        class FakeTelegramClient:
            def __init__(self) -> None:
                self.sent: list[tuple[int | str, str, int]] = []

            def send_message(self, chat_id, text, *, reply_to_message_id=0):
                self.sent.append((chat_id, text, reply_to_message_id))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents"
            skills = root / "skills"
            tools = root / "tools"
            agents.joinpath("default").mkdir(parents=True)
            skills.mkdir()
            tools.mkdir()
            state = ChatApiState(
                agents_dir=agents,
                skills_dir=skills,
                tools_dir=tools,
                session_store_path=root / "sessions.db",
                visible_history_path=root / "visible-history.db",
                session=Session(id=agent_home_session_id("default"), source=AGENT_HOME_SOURCE),
            )
            client = FakeTelegramClient()
            host = TelegramHost(
                state,
                AgentTelegramConfig(enabled=True, token_ref="secret:BOT", allowed_chat_ids=(123,)),
                client,  # type: ignore[arg-type]
            )

            answer = host.handle_message(TelegramMessage(update_id=1, chat_id=123, text="/start", message_id=9))

        self.assertIn("BB9 est connecté", answer)
        self.assertEqual([(123, answer, 9)], client.sent)

    def test_telegram_context_command_lists_subagents(self) -> None:
        from bb9.channels.telegram import TelegramHost, TelegramMessage
        from bb9.core.agent_telegram import AgentTelegramConfig

        class FakeTelegramClient:
            def __init__(self) -> None:
                self.sent: list[tuple[int | str, str, int]] = []

            def send_message(self, chat_id, text, *, reply_to_message_id=0):
                self.sent.append((chat_id, text, reply_to_message_id))

        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents"
            (agents / "default" / "subagents" / "dev").mkdir(parents=True)
            (agents / "default" / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            (agents / "default" / "subagents" / "dev" / "IDENTITY.md").write_text("# Dev\n", encoding="utf-8")
            workspace.mkdir()
            state = ChatApiState(
                agents_dir=agents,
                skills_dir=root / "skills",
                tools_dir=root / "tools",
                provider_kind="echo",
                session_store_path=root / "sessions.db",
                visible_history_path=root / "history.db",
            )
            client = FakeTelegramClient()
            host = TelegramHost(
                state,
                AgentTelegramConfig(enabled=True, token_ref="secret:BOT", allowed_chat_ids=(123,)),
                client,  # type: ignore[arg-type]
            )
            try:
                os.chdir(workspace)
                answer = host.handle_message(TelegramMessage(update_id=1, chat_id=123, text="/context", message_id=4))
            finally:
                os.chdir(cwd)

        self.assertIn("## Contexte courant", answer)
        self.assertIn("- Subagents : `dev`", answer)

    def test_telegram_project_switch_updates_host_workspace_without_process_chdir(self) -> None:
        from bb9.channels.telegram import TelegramHost
        from bb9.core.agent_telegram import AgentTelegramConfig

        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launcher = root / "launcher"
            workspace = root / "tests"
            agents = root / "agents" / "default"
            launcher.mkdir()
            workspace.mkdir()
            agents.mkdir(parents=True)
            (agents / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            state = ChatApiState(
                agents_dir=root / "agents",
                skills_dir=root / "skills",
                tools_dir=root / "tools",
                active_project_path=str(launcher),
                session_store_path=root / "sessions.db",
                visible_history_path=root / "history.db",
                settings_path=root / "settings.json",
            )
            host = TelegramHost(state, AgentTelegramConfig(enabled=True, allowed_chat_ids=(1,)), object())  # type: ignore[arg-type]
            try:
                os.chdir(launcher)
                text, notice, answer = host._prepare_workspace_text("mets-toi sur le projet tests et critique")
                process_cwd = Path.cwd().resolve(strict=False)
            finally:
                os.chdir(cwd)

            self.assertEqual("critique", text)
            self.assertEqual("", answer)
            self.assertIn("Workspace actif", notice)
            self.assertEqual(str(workspace.resolve()), state.active_project_path)
            self.assertEqual(launcher.resolve(), process_cwd)

    def test_telegram_channel_sends_typing_action_for_agent_turn(self) -> None:
        from bb9.channels.telegram import TelegramClient, TelegramHost, TelegramMessage
        from bb9.core.agent_telegram import AgentTelegramConfig
        from bb9.core.sessions import AGENT_HOME_SOURCE, agent_home_session_id

        class RecordingTelegramClient(TelegramClient):
            def __init__(self) -> None:
                super().__init__("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abc")
                self.calls: list[tuple[str, dict[str, object] | None]] = []

            def call(self, method: str, data: dict[str, object] | None = None, *, timeout: int = 35) -> dict[str, object]:
                self.calls.append((method, data))
                return {"ok": True, "result": True}

        class FakeTelegramClient:
            def __init__(self) -> None:
                self.sent: list[tuple[int | str, str, int]] = []
                self.actions: list[tuple[int | str, str]] = []

            def send_message(self, chat_id, text, *, reply_to_message_id=0):
                self.sent.append((chat_id, text, reply_to_message_id))

            def send_chat_action(self, chat_id, action="typing"):
                self.actions.append((chat_id, action))

        class FakeTurn:
            answer = "réponse"

        recording = RecordingTelegramClient()
        recording.send_chat_action(123)
        self.assertEqual([("sendChatAction", {"chat_id": 123, "action": "typing"})], recording.calls)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents"
            skills = root / "skills"
            tools = root / "tools"
            agents.joinpath("default").mkdir(parents=True)
            skills.mkdir()
            tools.mkdir()
            state = ChatApiState(
                agents_dir=agents,
                skills_dir=skills,
                tools_dir=tools,
                session_store_path=root / "sessions.db",
                visible_history_path=root / "visible-history.db",
                session=Session(id=agent_home_session_id("default"), source=AGENT_HOME_SOURCE),
            )
            client = FakeTelegramClient()
            host = TelegramHost(
                state,
                AgentTelegramConfig(enabled=True, token_ref="secret:BOT", allowed_chat_ids=(123,)),
                client,  # type: ignore[arg-type]
            )

            with (
                patch("bb9.channels.telegram.runtime_service.run_message", return_value=FakeTurn()),
                patch("bb9.channels.telegram.runtime_service.turn_artifacts", return_value=()),
            ):
                answer = host.handle_message(TelegramMessage(update_id=1, chat_id=123, text="bonjour", message_id=9))

        self.assertEqual("réponse", answer)
        self.assertEqual([(123, "typing")], client.actions)
        self.assertEqual([(123, "réponse", 9)], client.sent)

    def test_telegram_channel_alerts_user_when_auto_compaction_runs(self) -> None:
        from bb9.channels.telegram import TelegramHost, TelegramMessage
        from bb9.core.agent_telegram import AgentTelegramConfig
        from bb9.core.sessions import AGENT_HOME_SOURCE, agent_home_session_id

        class FakeTelegramClient:
            def __init__(self) -> None:
                self.sent: list[tuple[int | str, str, int]] = []

            def send_message(self, chat_id, text, *, reply_to_message_id=0):
                self.sent.append((chat_id, text, reply_to_message_id))

            def send_chat_action(self, chat_id, action="typing"):
                return None

        class FakeTurn:
            answer = "réponse"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents"
            agents.joinpath("default").mkdir(parents=True)
            home_id = agent_home_session_id("default")

            # A long-lived agent-home conversation, above the auto-compaction threshold.
            store = SessionStore(root / "sessions.db")
            try:
                home = store.ensure_agent_home("default").as_session()
                for index in range(20):
                    home = home.with_message("user" if index % 2 == 0 else "assistant", f"échange {index}")
                store.store(home, project_path=None)
            finally:
                store.close()

            state = ChatApiState(
                agents_dir=agents,
                skills_dir=root / "skills",
                tools_dir=root / "tools",
                session_store_path=root / "sessions.db",
                visible_history_path=root / "visible-history.db",
                session=Session(id=home_id, source=AGENT_HOME_SOURCE),
            )
            client = FakeTelegramClient()
            host = TelegramHost(
                state,
                AgentTelegramConfig(enabled=True, token_ref="secret:BOT", allowed_chat_ids=(123,)),
                client,  # type: ignore[arg-type]
            )

            with (
                patch("bb9.channels.telegram.runtime_service.run_message", return_value=FakeTurn()),
                patch("bb9.channels.telegram.runtime_service.turn_artifacts", return_value=()),
            ):
                answer = host.handle_message(TelegramMessage(update_id=1, chat_id=123, text="bonjour", message_id=9))

            history = VisibleHistoryStore(root / "visible-history.db")
            try:
                notifications = [
                    message
                    for message in history.recent(limit=20, session_id=home_id, project_path=None)
                    if message.role == "notification"
                ]
            finally:
                history.close()

            store = SessionStore(root / "sessions.db")
            try:
                persisted = store.get(home_id)
            finally:
                store.close()

        self.assertIn("Auto-compaction du contexte court", answer)
        self.assertIn("réponse", answer)
        self.assertEqual(1, len(client.sent))
        self.assertIn("Auto-compaction", client.sent[0][1])
        self.assertEqual(1, len(notifications))
        self.assertEqual("telegram", notifications[0].source)
        # The persisted session is the compacted one, with a summary of older turns.
        self.assertIsNotNone(persisted)
        self.assertLess(len(persisted.messages), 22)
        self.assertTrue(persisted.compaction_summary.strip())

    def test_telegram_channel_uploads_screenshot_artifacts(self) -> None:
        from bb9.channels.telegram import TelegramHost, TelegramMessage
        from bb9.core.agent_telegram import AgentTelegramConfig
        from bb9.core.sessions import AGENT_HOME_SOURCE, agent_home_session_id

        class FakeTelegramClient:
            def __init__(self) -> None:
                self.sent: list[tuple[int | str, str, int]] = []
                self.actions: list[tuple[int | str, str]] = []
                self.photos: list[tuple[int | str, Path, str, int]] = []

            def send_message(self, chat_id, text, *, reply_to_message_id=0):
                self.sent.append((chat_id, text, reply_to_message_id))

            def send_chat_action(self, chat_id, action="typing"):
                self.actions.append((chat_id, action))

            def send_photo(self, chat_id, path, *, caption="", reply_to_message_id=0):
                self.photos.append((chat_id, Path(path), caption, reply_to_message_id))

        class FakeTurn:
            answer = "![aperçu](.bb9/artifacts/screenshots/screen.png)\n\nOK"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents"
            skills = root / "skills"
            tools = root / "tools"
            screenshot = workspace / ".bb9" / "artifacts" / "screenshots" / "screen.png"
            screenshot.parent.mkdir(parents=True)
            screenshot.write_bytes(b"png")
            agents.joinpath("default").mkdir(parents=True)
            skills.mkdir()
            tools.mkdir()
            state = ChatApiState(
                agents_dir=agents,
                skills_dir=skills,
                tools_dir=tools,
                active_project_path=str(workspace),
                session_store_path=root / "sessions.db",
                visible_history_path=root / "visible-history.db",
                session=Session(id=agent_home_session_id("default"), source=AGENT_HOME_SOURCE),
            )
            client = FakeTelegramClient()
            host = TelegramHost(
                state,
                AgentTelegramConfig(enabled=True, token_ref="secret:BOT", allowed_chat_ids=(123,)),
                client,  # type: ignore[arg-type]
            )

            artifact = Artifact(kind="screenshot", title="preview", path=".bb9/artifacts/screenshots/screen.png")
            with (
                patch("bb9.channels.telegram.runtime_service.run_message", return_value=FakeTurn()),
                patch("bb9.channels.telegram.runtime_service.turn_artifacts", return_value=(artifact,)),
            ):
                answer = host.handle_message(TelegramMessage(update_id=1, chat_id=123, text="montre moi", message_id=9))

        self.assertIn("aperçu", answer)
        self.assertEqual([(123, answer, 9)], client.sent)
        self.assertEqual([(123, screenshot.resolve(), "preview", 9)], client.photos)

    def test_telegram_channel_approval_uses_inline_callback(self) -> None:
        from bb9.channels.telegram import TelegramHost
        from bb9.core.agent_telegram import AgentTelegramConfig
        from bb9.core.models import GuardianDecision
        from bb9.core.sessions import AGENT_HOME_SOURCE, agent_home_session_id

        class FakeTelegramClient:
            def __init__(self) -> None:
                self.sent: list[tuple[int | str, str, dict[str, object] | None]] = []
                self.answered: list[tuple[str, str]] = []
                self.edited: list[tuple[int | str, int]] = []

            def send_message(self, chat_id, text, *, reply_to_message_id=0, reply_markup=None):
                self.sent.append((chat_id, text, reply_markup))

            def get_updates(self, *, offset=0, timeout=25, limit=20):
                markup = self.sent[0][2]
                assert markup is not None
                data = markup["inline_keyboard"][0][0]["callback_data"]  # type: ignore[index]
                return [
                    {
                        "update_id": 12,
                        "callback_query": {
                            "id": "callback-1",
                            "data": data,
                            "from": {"id": 999},
                            "message": {"message_id": 77, "chat": {"id": 123}},
                        },
                    }
                ]

            def answer_callback_query(self, callback_query_id, *, text="", show_alert=False):
                self.answered.append((callback_query_id, text))

            def edit_message_reply_markup(self, chat_id, message_id, *, reply_markup=None):
                self.edited.append((chat_id, message_id))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents"
            skills = root / "skills"
            tools = root / "tools"
            agents.joinpath("default").mkdir(parents=True)
            skills.mkdir()
            tools.mkdir()
            session = Session(id=agent_home_session_id("default"), source=AGENT_HOME_SOURCE)
            state = ChatApiState(
                agents_dir=agents,
                skills_dir=skills,
                tools_dir=tools,
                session_store_path=root / "sessions.db",
                visible_history_path=root / "visible-history.db",
                session=session,
            )
            client = FakeTelegramClient()
            host = TelegramHost(
                state,
                AgentTelegramConfig(enabled=True, token_ref="secret:BOT", allowed_chat_ids=(123,)),
                client,  # type: ignore[arg-type]
            )

            approval = host._ask_approval(
                chat_id=123,
                after_update_id=10,
                decision=GuardianDecision(verdict="ask", reason="confirmation required", action=Action("shell", {"cmd": "touch x"}, "high")),
                context=RunContext(session=session, workspace=Workspace(root=root)),
            )

        self.assertEqual("allow", approval.verdict)
        self.assertIn("Validation requise", client.sent[0][1])
        self.assertEqual([("callback-1", "Action validée.")], client.answered)
        self.assertEqual([(123, 77)], client.edited)

    def test_telegram_channel_configures_repl_commands(self) -> None:
        from bb9.channels.telegram import TelegramHost, telegram_menu_command_name
        from bb9.core.agent_telegram import AgentTelegramConfig

        class FakeTelegramClient:
            def __init__(self) -> None:
                self.commands = ()

            def set_my_commands(self, commands):
                self.commands = commands

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents"
            skills = root / "skills"
            tools = root / "tools"
            agents.joinpath("default").mkdir(parents=True)
            skills.mkdir()
            tools.mkdir()
            state = ChatApiState(
                agents_dir=agents,
                skills_dir=skills,
                tools_dir=tools,
                session_store_path=root / "sessions.db",
                visible_history_path=root / "visible-history.db",
            )
            client = FakeTelegramClient()
            host = TelegramHost(
                state,
                AgentTelegramConfig(enabled=True, token_ref="secret:BOT", allowed_chat_ids=(123,)),
                client,  # type: ignore[arg-type]
            )
            web_commands = ChatApiApp(state).commands_payload()["commands"]

            host.configure_bot_commands()
            help_answer = host._handle_command("/help")
            telegram_commands = host.telegram_commands_payload()["commands"]

        self.assertEqual([item["name"] for item in web_commands], [item["name"] for item in telegram_commands])
        self.assertIn(("context", "afficher l'état courant"), client.commands)
        self.assertNotIn(("model-context", "définir la taille de la fenêtre de contexte du modèle actif"), client.commands)
        self.assertEqual("", telegram_menu_command_name("/model-context"))
        self.assertIsNotNone(help_answer)
        assert help_answer is not None
        self.assertIn("/context", help_answer)
        self.assertIn("/model-context", help_answer)

    def test_telegram_channel_routes_veille_to_direct_runner(self) -> None:
        from bb9.channels.telegram import TelegramHost, TelegramMessage
        from bb9.core.agent_telegram import AgentTelegramConfig
        from bb9.core.sessions import AGENT_HOME_SOURCE, agent_home_session_id
        from bb9.core.veille_rss import veille_command_from_text

        class FakeTelegramClient:
            def __init__(self) -> None:
                self.sent: list[tuple[int | str, str, int]] = []
                self.actions: list[tuple[int | str, str]] = []

            def send_message(self, chat_id, text, *, reply_to_message_id=0, reply_markup=None):
                self.sent.append((chat_id, text, reply_to_message_id))

            def send_chat_action(self, chat_id, action="typing"):
                self.actions.append((chat_id, action))

        self.assertEqual("/veille IA", veille_command_from_text("tu peux me faire une veille IA ?"))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents"
            skills = root / "skills"
            tools = root / "tools"
            agents.joinpath("default").mkdir(parents=True)
            skills.mkdir()
            tools.mkdir()
            state = ChatApiState(
                agents_dir=agents,
                skills_dir=skills,
                tools_dir=tools,
                session_store_path=root / "sessions.db",
                visible_history_path=root / "visible-history.db",
                session=Session(id=agent_home_session_id("default"), source=AGENT_HOME_SOURCE),
            )
            client = FakeTelegramClient()
            host = TelegramHost(
                state,
                AgentTelegramConfig(enabled=True, token_ref="secret:BOT", allowed_chat_ids=(123,)),
                client,  # type: ignore[arg-type]
            )

            with patch("bb9.channels.telegram.run_veille_rss_command", return_value="# Veille RSS — IA") as runner:
                answer = host.handle_message(TelegramMessage(update_id=1, chat_id=123, text="tu peux me faire une veille IA ?", message_id=9))

        self.assertEqual("# Veille RSS — IA", answer)
        runner.assert_called_once_with(skills, "/veille IA")
        self.assertEqual([(123, "# Veille RSS — IA", 9)], client.sent)

    def test_stop_command_detects_bb9_process_commands(self) -> None:
        from bb9.__main__ import _is_bb9_process_command

        self.assertTrue(_is_bb9_process_command("python3.11 -m bb9 web --web-port 8781"))
        self.assertTrue(_is_bb9_process_command("/home/egza/.local/bin/bb9 telegram"))
        self.assertTrue(_is_bb9_process_command("bb9 web"))
        self.assertFalse(_is_bb9_process_command("python3.11 -m unittest tests.test_boundaries"))
        self.assertFalse(_is_bb9_process_command("rg bb9"))

    def test_web_app_starts_telegram_channel_for_active_agent(self) -> None:
        from bb9.core.sessions import AGENT_HOME_SOURCE, agent_home_session_id

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents"
            skills = root / "skills"
            tools = root / "tools"
            agent = agents / "default"
            agent.mkdir(parents=True)
            skills.mkdir()
            tools.mkdir()
            agent.joinpath("IDENTITY.md").write_text("Nom : default\n", encoding="utf-8")
            agent.joinpath("SOUL.md").write_text("# Soul\n", encoding="utf-8")
            agent.joinpath("TELEGRAM.md").write_text(
                "# Telegram\n\n## Activation\n\nactive\n\n## Token\n\nsecret:BOT\n\n## AllowedChatIds\n\n[123]\n",
                encoding="utf-8",
            )
            state = ChatApiState(
                agents_dir=agents,
                skills_dir=skills,
                tools_dir=tools,
                session_store_path=root / "sessions.db",
                visible_history_path=root / "visible-history.db",
                session=Session(id=agent_home_session_id("default"), source=AGENT_HOME_SOURCE),
            )
            app = ChatApiApp(state)

            with (
                patch("bb9.core.agent_telegram.resolve_secret_ref", return_value="123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abc"),
                patch.object(ChatApiApp, "_telegram_channel_loop") as loop,
            ):
                app.start_telegram_channel()
                for _ in range(50):
                    if loop.called:
                        break
                    time.sleep(0.01)

            try:
                self.assertEqual("default", app._telegram_agent_name)
                self.assertEqual("secret:BOT", app._telegram_token_ref)
                self.assertIsNotNone(app._telegram_thread)
                self.assertTrue(loop.called)
            finally:
                app.stop_telegram_channel()

    def test_web_app_syncs_telegram_channel_after_agent_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents"
            skills = root / "skills"
            tools = root / "tools"
            agent = agents / "default"
            agent.mkdir(parents=True)
            skills.mkdir()
            tools.mkdir()
            agent.joinpath("IDENTITY.md").write_text("Nom : default\n", encoding="utf-8")
            agent.joinpath("SOUL.md").write_text("# Soul\n", encoding="utf-8")
            app = ChatApiApp(
                ChatApiState(
                    agents_dir=agents,
                    skills_dir=skills,
                    tools_dir=tools,
                    session_store_path=root / "sessions.db",
                    visible_history_path=root / "visible-history.db",
                )
            )

            with (
                patch("bb9.core.agent_telegram.normalize_api_key_ref_input", return_value=("secret:BOT", "")),
                patch("bb9.core.agent_telegram.resolve_secret_ref", return_value="123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abc"),
                patch.object(ChatApiApp, "_telegram_channel_loop") as loop,
            ):
                payload = app.update_agent(
                    {
                        "name": "default",
                        "telegram": {
                            "enabled": True,
                            "token": "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abc",
                            "allowed_chat_ids": "[123]",
                        },
                    }
                )
                for _ in range(50):
                    if loop.called:
                        break
                    time.sleep(0.01)

            try:
                self.assertTrue(payload["ok"])
                self.assertTrue(loop.called)
                self.assertEqual("default", app._telegram_agent_name)
            finally:
                app.stop_telegram_channel()

    def test_skills_api_creates_global_and_local_skill_archives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            agents = root / "agents"
            skills = root / "skills"
            tools = root / "tools"
            workspace.mkdir()
            agents.mkdir()
            skills.mkdir()
            tools.mkdir()
            app = ChatApiApp(
                ChatApiState(
                    agents_dir=agents,
                    skills_dir=skills,
                    tools_dir=tools,
                    active_project_path=str(workspace),
                )
            )

            global_payload = app.add_skill({"name": "demo-skill", "source": "global"})
            local_payload = app.add_skill(
                {
                    "name": "project-skill",
                    "source": "local",
                    "body": "# Project Skill\n\n## Résumé\n\nProjet.\n",
                }
            )

            self.assertTrue(global_payload["ok"])
            self.assertTrue(local_payload["ok"])
            self.assertTrue((skills / "demo-skill" / "SKILL.md").is_file())
            self.assertTrue((skills / "INDEX.md").is_file())
            self.assertTrue((workspace / ".bb9" / "skills" / "project-skill" / "SKILL.md").is_file())
            self.assertTrue((workspace / ".bb9" / "skills" / "INDEX.md").is_file())
            self.assertIn("demo-skill", [item["name"] for item in global_payload["skills"]])
            local = next(item for item in local_payload["skills"] if item["name"] == "project-skill")
            self.assertEqual("local", local["source"])
            self.assertEqual("Projet.", local["summary"])

    def test_routines_api_creates_and_updates_cron_archives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crons = root / "cron"
            state_path = root / "cron-state.json"
            app = ChatApiApp(ChatApiState(crons_dir=crons, cron_state_path=state_path))

            created = app.add_routine({"name": "morning-briefing"})
            updated = app.update_routine(
                {
                    "name": "morning-briefing",
                    "body": (
                        "# CRON.md\n\n"
                        "## Résumé\n\nBriefing du matin.\n\n"
                        "## Activation\n\nactive\n\n"
                        "## Mode\n\nrecurring\n\n"
                        "## Schedule\n\nTime: 08:30\nDays: weekdays\nTimezone: Europe/Paris\n"
                    ),
                }
            )

            self.assertTrue(created["ok"])
            self.assertTrue(updated["ok"])
            routine_file = crons / "morning-briefing" / "CRON.md"
            self.assertTrue(routine_file.is_file())
            self.assertTrue((crons / "INDEX.md").is_file())
            routine = next(item for item in updated["routines"] if item["name"] == "morning-briefing")
            self.assertEqual("Briefing du matin.", routine["summary"])
            self.assertEqual("active", routine["activation"])
            self.assertEqual("recurring", routine["mode"])
            self.assertEqual("08:30 weekly monday,tuesday,wednesday,thursday,friday", routine["schedule"])
            self.assertEqual("", routine["state"]["last_run"])
            self.assertIn("Briefing du matin.", routine_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
