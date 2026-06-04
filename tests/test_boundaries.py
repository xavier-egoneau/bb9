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
from importlib import resources
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote
from urllib.request import Request, urlopen

from bb9.api.chat import ChatApiApp, ChatApiState
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
from bb9.core.sessions import SessionStore
from bb9.core.settings import SettingsStore, UserSettings
from bb9.core.tool_runtime import load_tool_module
from bb9.core.workspace_status import build_workspace_status
from bb9.providers.config import AUTH_API, ProviderConfig, ProviderEntry, ProviderStore


class BoundaryTests(unittest.TestCase):
    def test_context_index_protects_workspace_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "README.md").write_text("# Demo\n", encoding="utf-8")

            index = refresh_context_index(workspace)

            self.assertIn("README.md", index)
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

            switched = app.switch_project(str(other_project))
            self.assertTrue(switched["ok"])
            self.assertEqual("other-web", switched["session_id"])
            self.assertEqual(["user"], [item["role"] for item in switched["messages"]])
            self.assertEqual("autre projet", switched["messages"][0]["content"])
            other_sessions = app.sessions_payload()
            self.assertEqual(["other-web"], [session["id"] for session in other_sessions["sessions"]])
            blocked = app.run_message("ne pas exécuter ailleurs")
            self.assertFalse(blocked["ok"])
            self.assertEqual("project_view_only", blocked["error"])

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

        payload = app.run_events_payload()

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["running"])
        self.assertEqual("run-test", payload["run_id"])
        self.assertEqual(1, payload["next"])
        self.assertEqual(1, payload["total"])
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
            local_skill = root / "workspace" / ".bb9" / "skills" / "open-ui"
            local_skill.mkdir(parents=True)
            (local_skill / "SKILL.md").write_text(
                "---\nname: open-ui\ncommands: open-ui-map, open-ui-review\n---\n# Open UI\n\n## Résumé\n\nLocal.\n",
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
            self.assertIn("/open-ui-map", names)
            self.assertIn("/open-ui-review", names)
            self.assertIn("/explore", names)
            self.assertNotIn("/build", collisions)

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
        self.assertIn("# BB9 Plan", plan)
        self.assertIn("T1 Créer la page", plan)

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
            finally:
                os.chdir(cwd)

        self.assertTrue(payload["ok"])
        self.assertIn("task... Créer la page: done", payload["answer"])
        self.assertEqual(1, payload["plan"]["completed"])
        self.assertTrue(payload["plan"]["tasks"][0]["done"])
        self.assertIn("- [x] T1 Créer la page", updated_plan)

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
                        profile="power",
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
                        profile="power",
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
                        profile="power",
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
                        profile="power",
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
                app = ChatApiApp(ChatApiState(profile="power", agents_dir=agents, skills_dir=skills, tools_dir=tools))
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
                app = ChatApiApp(ChatApiState(profile="power", agents_dir=agents, skills_dir=skills, tools_dir=tools))
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
                        profile="power",
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
                app = ChatApiApp(ChatApiState(profile="power", agents_dir=agents, skills_dir=skills, tools_dir=tools))
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
                app = ChatApiApp(ChatApiState(profile="power", agents_dir=agents, skills_dir=skills, tools_dir=tools))
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
  {name: '/open-ui-map', source: 'local-skill', supported: true},
  {name: '/open-ui-impact', source: 'local-skill', supported: true},
  {name: '/open-ui-modify', source: 'local-skill', supported: true},
  {name: '/open-ui-create-component', source: 'local-skill', supported: true},
  {name: '/open-ui-sketch', source: 'local-skill', supported: true},
  {name: '/open-ui-check', source: 'local-skill', supported: true},
  {name: '/open-ui-review', source: 'local-skill', supported: true},
  {name: '/open-ui-rgaa-check', source: 'local-skill', supported: true},
  {name: '/open-ui-cleanup', source: 'local-skill', supported: true},
  {name: '/open-ui-docs', source: 'local-skill', supported: true},
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
        self.assertLess(names.index("/secrets"), names.index("/open-ui-check"))
        self.assertLess(names.index("/plan"), names.index("/open-ui-check"))
        self.assertIn("/open-ui-docs", names)
        self.assertEqual(1, names.count("/build"))
        self.assertEqual(1, names.count("/plan"))

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
        self.assertIn('<link rel="stylesheet" href="./app.css">', html)
        self.assertIn('<script type="module" src="./app.js"></script>', html)
        self.assertIn('id="plan-panel"', html)
        self.assertIn(".message-images", css)
        self.assertIn(".plan-panel", css)
        self.assertIn(".plan-task-box", css)
        self.assertIn(".copy-message", css)
        self.assertIn(".copy-message svg", css)
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
        self.assertIn(".composer-run-actions", css)
        self.assertIn(".attach", css)
        self.assertIn("color: color-mix(in srgb, var(--text) 42%, transparent)", css)
        self.assertIn(".send-icon", css)
        self.assertIn("background: var(--control-bg)", css)
        self.assertIn("color: var(--control-fg)", css)
        self.assertIn(".stop-run", css)
        self.assertIn(".message.working", css)
        self.assertIn(".pixel-loader", css)
        self.assertIn(".working-trace", css)
        self.assertIn("width: min(440px", css)
        self.assertIn("grid-template-columns: repeat(7, 5px)", css)
        self.assertIn("@keyframes pixel-load", css)
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
        self.assertIn("imageUrl(path)", client_js)
        self.assertNotIn("fileUrl(path)", client_js)
        self.assertNotIn("encodePath(path)", client_js)
        self.assertIn("commands()", client_js)
        self.assertIn("stop()", client_js)
        self.assertIn("themes()", client_js)
        self.assertIn("models()", client_js)
        self.assertIn("git()", client_js)
        self.assertIn("gitDiff(path)", client_js)
        self.assertIn("gitCommitMessage()", client_js)
        self.assertIn("commitGit(message)", client_js)
        self.assertIn("switchGitBranch(branch)", client_js)
        self.assertIn("runEvents(after = 0)", client_js)
        self.assertIn("after=${encodeURIComponent(after)}", client_js)
        self.assertIn("/run/events", client_js)
        self.assertIn("createBb9Chat", chat_ui_js)
        self.assertIn("renderMessageContent(content, client, {markdown: role === 'assistant'})", chat_ui_js)
        self.assertIn("capabilities", chat_ui_js)
        self.assertIn("event.key === 'Enter' && !event.shiftKey", chat_ui_js)
        self.assertIn("localStorage", chat_ui_js)
        self.assertIn("Serveur BB9 web ancien ou incomplet", chat_ui_js)
        self.assertIn("Historique indisponible", chat_ui_js)
        self.assertIn("loadProjects", chat_ui_js)
        self.assertIn("loadCommands", chat_ui_js)
        self.assertIn("loadThemes", chat_ui_js)
        self.assertIn("handleCommandKey", chat_ui_js)
        self.assertIn("copyButton(content)", chat_ui_js)
        self.assertIn("navigator.clipboard.writeText", chat_ui_js)
        self.assertIn("workflowCommandRank", chat_ui_js)
        self.assertIn("name === '/build'", chat_ui_js)
        self.assertIn("showActivityIndicator", chat_ui_js)
        self.assertIn("removeActivityIndicator", chat_ui_js)
        self.assertIn("renderLiveTrace", chat_ui_js)
        self.assertIn("shouldStickToBottom", chat_ui_js)
        self.assertIn("scrollToThreadBottom", chat_ui_js)
        self.assertIn("distance <= threshold", chat_ui_js)
        self.assertNotIn("scrollIntoView", chat_ui_js)
        self.assertIn("startLiveTracePolling", chat_ui_js)
        self.assertIn("client.runEvents", chat_ui_js)
        self.assertIn("liveTraceCursor", chat_ui_js)
        self.assertIn("liveTraceInFlight", chat_ui_js)
        self.assertIn("liveTraceGeneration", chat_ui_js)
        self.assertIn("generation !== liveTraceGeneration", chat_ui_js)
        self.assertIn("liveTraceRunId", chat_ui_js)
        self.assertIn("!payload.running || !runId", chat_ui_js)
        self.assertIn("statusInFlight", chat_ui_js)
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
        self.assertIn("M3 6.5A2.5 2.5", html)
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
            self.assertTrue(require_model)
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

    def test_cli_web_chat_flag_defaults_to_configured_provider(self) -> None:
        import bb9.__main__ as main_module

        calls: list[object] = []

        def fake_entry(provider, args, store, *, require_model):
            self.assertEqual("configured", provider)
            self.assertTrue(require_model)
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

    def test_files_write_many_writes_multiple_workspace_files(self) -> None:
        module = load_tool_module("files", "runtime")
        self.assertIsNotNone(module)
        cwd = Path.cwd()

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            action = module.action_from_text(
                'write_many [{"path":"public/sketches/demo/index.html","content":"<h1>Demo</h1>"},'
                '{"path":"public/sketches/demo/style.css","content":":root { color-scheme: light; }"}]'
            )
            decision = module.review(action, context)
            try:
                os.chdir(workspace)
                observation = module.execute(action)
            finally:
                os.chdir(cwd)

            html = (workspace / "public" / "sketches" / "demo" / "index.html").read_text(encoding="utf-8")
            css = (workspace / "public" / "sketches" / "demo" / "style.css").read_text(encoding="utf-8")

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
                'write_many files=[{"path":"public/sketches/demo/index.html","content":"<h1>Demo</h1>"}]'
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
                    {"op": "write", "path": "public/sketches/demo/index.html", "content": "<h1>Demo</h1>"},
                    {"op": "write", "path": "public/sketches/demo/style.css", "content": "body { color: #111; }"},
                ]
            }
            action = module.action_from_text(json.dumps(payload) + "\nJ'ai prepare les fichiers.")
            decision = module.review(action, context)
            observation = module.execute(action, context)

            html = (workspace / "public" / "sketches" / "demo" / "index.html").read_text(encoding="utf-8")
            css = (workspace / "public" / "sketches" / "demo" / "style.css").read_text(encoding="utf-8")

        self.assertEqual("write_many", action.params["op"])
        self.assertEqual("allow", decision.verdict)
        self.assertTrue(observation.ok)
        self.assertEqual("<h1>Demo</h1>", html)
        self.assertIn("color", css)

    def test_kernel_accepts_files_json_ops_action(self) -> None:
        payload = {
            "ops": [
                {"op": "write", "path": "public/sketches/demo/index.html", "content": "<h1>Demo</h1>"},
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
        self.assertEqual("public/sketches/demo/index.html", decision.action.params["items"][0]["path"])

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
                "python3 -m http.server 4173J’ai repris la commande comme une vraie exécution `/open-ui-sketch`."
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
                    "BB9_ACTION shell find public/sketches/demo -maxdepth 1 -type f -print"
                    "BB9_ACTION browser check url=http://127.0.0.1:4173/public/sketches/demo/index.html screenshot=true"
                )

        context = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()), permission_profile="power")

        decision = Kernel(provider=NestedActionProvider()).decide(Intention("verifie la maquette"), context)

        self.assertEqual("action", decision.kind)
        self.assertEqual("invalid-provider-action", decision.action.name)
        self.assertIn("Invalid provider action request", decision.summary)

    def test_loop_does_not_ask_user_for_nested_provider_action_prefix(self) -> None:
        class NestedActionProvider:
            calls = 0

            def complete(self, _: str, **___: object) -> str:
                self.calls += 1
                if self.calls == 1:
                    return (
                        "BB9_ACTION shell find public/sketches/demo -maxdepth 1 -type f -print"
                        "BB9_ACTION browser check url=http://127.0.0.1:4173/public/sketches/demo/index.html screenshot=true"
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
        self.assertIn("verdict global", provider.prompt)
        self.assertIn("priorites d'amelioration", provider.prompt)
        self.assertIn("sauf si l'utilisateur demande explicitement la structure", provider.prompt)

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

    def test_loop_forces_open_ui_sketch_to_attempt_files_before_answer(self) -> None:
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
                                "path": "public/sketches/demo/index.html",
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
                            params={"op": "check", "url": "http://127.0.0.1:4173/public/sketches/demo/index.html", "screenshot": "true"},
                            risk="low",
                        ),
                    )
                return Decision(kind="answer", summary="Maquette créée : /api/file/public/sketches/demo/index.html")

        context = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()), permission_profile="power")
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
                Intention("/open-ui-sketch fais une maquette sante"),
                context,
                on_event=events.append,
            )

        self.assertEqual("Maquette créée : /api/file/public/sketches/demo/index.html", result.observation.summary)
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

    def test_loop_allows_open_ui_sketch_clarifying_question_before_files(self) -> None:
        class QuestionKernel:
            def decide(self, intention: Intention, context: RunContext) -> Decision:
                return Decision(kind="answer", summary="Quel type de pro vise-t-on ?")

        context = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()), permission_profile="power")

        result = run_once(QuestionKernel(), Intention("/open-ui-sketch maquette trop vague"), context)

        self.assertEqual("Quel type de pro vise-t-on ?", result.observation.summary)

    def test_loop_returns_to_user_after_repeated_guardian_blocks(self) -> None:
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

        result = run_once(BlockedKernel(), Intention("cree une maquette"), context, on_event=events.append)

        self.assertEqual("Je suis bloque par le protocole d'action.", result.observation.summary)
        self.assertEqual(
            2,
            len(
                [
                    event
                    for event in events
                    if event.event_type == "guardian" and event.data.get("verdict") == "block"
                ]
            ),
        )

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
            skill = workspace / ".bb9" / "skills" / "open-ui"
            agent.mkdir(parents=True)
            skill.mkdir(parents=True)
            (agent / "IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            (skill / "SKILL.md").write_text(
                "---\n"
                "name: open-ui\n"
                "description: Skill projet Open UI.\n"
                "commands: open-ui-map, open-ui-map\n"
                "---\n"
                "# Open UI\n",
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

            self.assertEqual("Skill projet Open UI.", context.skills[0].summary)
            self.assertEqual(("`/open-ui-map`",), context.skills[0].commands)
            self.assertIn("Skill projet Open UI.", context.skills_index)

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
                Skill(name="open-ui", body="# Open UI", commands=("`/open-ui-sketch` : sketch.",)),
                Skill(
                    name="design-sketching",
                    body="# Design Sketching",
                    activation="/open-ui-sketch, maquette libre",
                ),
            ),
        )
        provider = CapturingProvider()

        Kernel(provider=provider).decide(Intention("/open-ui-sketch propose 3 directions"), context)

        self.assertIn("# Skill: open-ui", provider.prompt)
        self.assertIn("# Skill: design-sketching", provider.prompt)

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

        Kernel(provider=provider).decide(Intention("/open-ui-sketch fait moi 3 maquettes"), context)

        self.assertIn("# Frontiere de tour", provider.prompt)
        self.assertIn("L'intention courante ci-dessous est l'autorite de ce tour", provider.prompt)
        self.assertLess(provider.prompt.index("# Frontiere de tour"), provider.prompt.index("# Intention courante"))
        self.assertIn("/open-ui-sketch fait moi 3 maquettes", provider.prompt)

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
                self.assertTrue((agents / "default" / "subagents" / "goal" / "IDENTITY.md").is_file())
                self.assertTrue((agents / "default" / "subagents" / "goal" / "MODEL.md").is_file())
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
            self.assertTrue((existing / "subagents" / "goal" / "IDENTITY.md").is_file())
            self.assertTrue((existing / "subagents" / "goal" / "MODEL.md").is_file())

    def test_subagents_index_is_generated_from_subagent_identities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agents = ensure_user_agents(Path(tmp) / "agents")

            index = refresh_subagents_index(agents, "default")

            self.assertIn("`default`", index)
            self.assertIn("`goal`", index)
            self.assertIn("`research`", index)
            self.assertIn("implementation locale", index)
            self.assertIn("objectif long", index)
            self.assertTrue((agents / "default" / "subagents" / "INDEX.md").is_file())

    def test_goal_worker_prefers_goal_subagent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = ensure_user_agents(root / "agents")
            state = CliState(
                agents_dir=agents,
                skills_dir=root / "skills",
                tools_dir=root / "tools",
            )

            worker = context_runtime.load_goal_worker_agent(state)

            self.assertEqual("default/goal", worker.name)

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

    def test_subagent_model_overrides_active_provider_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = ensure_user_agents(root / "agents")
            (agents / "default" / "subagents" / "goal" / "MODEL.md").write_text(
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


if __name__ == "__main__":
    unittest.main()
