from __future__ import annotations

import importlib
import io
import json
import os
import socket
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from importlib import resources
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from bb9.api.chat import ChatApiApp, ChatApiState
from bb9.api.http import chat_api_server
from bb9.core import context_runtime
from bb9.core.agents import refresh_subagents_index
from bb9.core.cli import (
    Cli,
    CliState,
)
from bb9.core.cli_render import (
    CliActivityIndicator,
    CliTheme,
    fit_words,
    render_cli_diff_artifact,
    render_cli_markdown,
    strip_ansi,
)
from bb9.core.context_index import refresh_context_index
from bb9.core.gateway import execute
from bb9.core.kernel import Kernel
from bb9.core.loop import run_once, tool_budget_for
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
    ToolSpec,
    TraceEvent,
    Workspace,
)
from bb9.core.paths import ensure_user_agents
from bb9.core.provider_config import AUTH_API, ProviderEntry
from bb9.core.settings import SettingsStore
from bb9.core.tool_runtime import load_tool_module


class BoundaryTests(unittest.TestCase):
    def test_context_index_protects_workspace_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "README.md").write_text("# Demo\n", encoding="utf-8")

            index = refresh_context_index(workspace)

            self.assertIn("README.md", index)
            self.assertTrue((workspace / ".bb9" / "context-index.md").is_file())
            self.assertEqual("*\n", (workspace / ".bb9" / ".gitignore").read_text(encoding="utf-8"))

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
                payload = app.run_message("bonjour web")
            finally:
                os.chdir(cwd)

            self.assertTrue(payload["ok"])
            self.assertEqual("bonjour web", payload["answer"])
            self.assertEqual(2, len(app.state.session.messages))
            self.assertEqual("web", app.state.session.source)

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
            cwd = Path.cwd()
            try:
                os.chdir(workspace)
                app = ChatApiApp(
                    ChatApiState(
                        agents_dir=agents,
                        skills_dir=skills,
                        tools_dir=tools,
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
            finally:
                if "server" in locals():
                    server.shutdown()
                    server.server_close()
                os.chdir(cwd)

            self.assertTrue(payload["ok"])
            self.assertEqual("salut", payload["answer"])
            self.assertTrue(history["ok"])
            self.assertEqual(["user", "assistant"], [item["role"] for item in history["messages"]])

    def test_web_chat_server_serves_static_app_over_same_api(self) -> None:
        app = ChatApiApp(ChatApiState())
        server = chat_api_server(app, 0, static_root=resources.files("bb9").joinpath("chat-web"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=5) as response:
                html = response.read().decode("utf-8")
        finally:
            server.shutdown()
            server.server_close()

        self.assertIn("<title>BB9 Web Chat</title>", html)
        self.assertIn("fetch('/api/chat'", html)

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

    def test_cli_web_command_starts_web_chat_channel(self) -> None:
        import bb9.__main__ as main_module

        calls: list[tuple[object, int, bool]] = []

        def fake_serve(state, *, port, open_browser):
            calls.append((state, port, open_browser))

        with patch.object(main_module, "serve_chat_web", fake_serve), patch(
            "sys.argv",
            ["bb9", "web", "--web-port", "8899", "--no-open"],
        ):
            code = main_module.main()

        self.assertEqual(0, code)
        self.assertEqual(1, len(calls))
        state, port, open_browser = calls[0]
        self.assertEqual("web", state.session.source)
        self.assertEqual(8899, port)
        self.assertFalse(open_browser)

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

    def test_shell_unsupported_pipeline_still_requires_confirmation(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "demo.js").write_text("one\ntwo\n", encoding="utf-8")
            context = RunContext(session=Session(), workspace=Workspace(root=workspace), permission_profile="power")
            decision = module.review(module.action_from_text("cat demo.js | sort"), context)

        self.assertEqual("ask", decision.verdict)
        self.assertIn("compound", decision.reason)

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
        self.assertIn("`default`", decision.summary)
        self.assertIn("`shell`", decision.summary)
        self.assertIn("actions controlees", decision.summary)
        self.assertNotIn("pas encore lu", decision.summary)
        self.assertNotIn("si tu veux", decision.summary)

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
        )

        Kernel(provider=provider).decide(Intention("analyse ce projet"), context)

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

        def fake_execute(action: Action) -> Observation:
            executed.append(action)
            return Observation(ok=False, summary="Playwright missing. Install with: python3 -m pip install playwright", retry_policy="block_tool")

        with patch("bb9.core.loop.execute", fake_execute):
            result = run_once(kernel, Intention("teste la page"), context, on_event=events.append)

        self.assertEqual(1, len(executed))
        self.assertTrue(result.observation.ok)
        self.assertIn("Browser indisponible", result.observation.summary)
        browser_actions = [event for event in events if event.event_type == "action" and event.data.get("tool") == "browser"]
        self.assertEqual(1, len(browser_actions))

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

        def fake_execute(action: Action) -> Observation:
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

        def fake_execute(action: Action) -> Observation:
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

        def fake_execute(action: Action) -> Observation:
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
