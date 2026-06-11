from __future__ import annotations

import io
import os
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from bb9.core.delegation import (
    build_delegation_context,
    delegate,
    effective_permission,
    scoped_tools,
    summary_has_error_marker,
    task_prompt,
    task_result_from_run,
)
from bb9.core.gateway import execute
from bb9.core.models import (
    AgentProfile,
    Decision,
    Observation,
    RunContext,
    RunResult,
    Session,
    Task,
    TaskResult,
    ToolSpec,
    TraceEvent,
    Workspace,
)
from bb9.core.tool_runtime import load_skill_module, load_tool_module
from bb9.core.trust import TrustedRoots


class DelegationTests(unittest.TestCase):
    def test_task_result_allows_explicit_empty_blockers_section(self) -> None:
        task = _task()
        result = RunResult(
            decision=Decision(kind="answer", summary="done"),
            observation=Observation(
                ok=True,
                summary=(
                    "Evidence:\n"
                    "- fichier vérifié\n"
                    "Blockers: aucun\n"
                    "Next suggestion: relire si besoin."
                ),
            ),
            trace=(),
        )

        task_result = task_result_from_run(task, result)

        self.assertFalse(summary_has_error_marker(result.observation.summary))
        self.assertEqual("done", task_result.status)
        self.assertEqual((), task_result.blockers)

    def test_task_result_keeps_real_blockers_as_error_marker(self) -> None:
        self.assertTrue(summary_has_error_marker("Blockers: fichier manquant"))

    def test_error_marker_ignores_providererror_in_code_block(self) -> None:
        # ProviderError mentioned inside code should not mark the task as failed
        summary = "Voici le code :\n```python\nexcept ProviderError:\n    pass\n```\nStatus: done"
        self.assertFalse(summary_has_error_marker(summary))

    def test_error_marker_ignores_providererror_in_inline_code(self) -> None:
        self.assertFalse(summary_has_error_marker("Attraper `ProviderError` est recommandé. Status: done"))

    def test_error_marker_detects_providererror_in_prose(self) -> None:
        self.assertTrue(summary_has_error_marker("La tâche a échoué: ProviderError durant l'appel"))

    def test_delegate_refuses_incomplete_task(self) -> None:
        task = Task(
            id="T1",
            title="Flou",
            goal="",
            context="",
            expected_output="",
        )
        parent = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()))
        subagent = AgentProfile(name="default/research")

        result = delegate(task, subagent, parent, lambda *_: self.fail("runner should not be called"))

        self.assertEqual("error", result.status)
        self.assertIn("missing goal", result.blockers)
        self.assertIn("missing context", result.blockers)
        self.assertIn("missing expected output", result.blockers)

    def test_build_delegation_context_is_reduced_and_caps_permission(self) -> None:
        parent = RunContext(
            session=Session(source="cli").with_message("user", "secret context", max_messages=10),
            workspace=Workspace(root=Path("/tmp/project")),
            permission_profile="limited",
            trusted_roots=TrustedRoots((Path("/tmp/other"),)),
            agent=AgentProfile(name="default"),
            tools=(
                ToolSpec(name="shell", body="shell"),
                ToolSpec(name="files", body="files"),
                ToolSpec(name="delegate", body="delegate"),
                ToolSpec(name="secret", body="secret"),
            ),
            subagents_index="# Subagents Index\n\n- `research`\n",
        )
        subagent = AgentProfile(name="default/research")
        task = _task(permission_profile="power")

        context = build_delegation_context(parent, subagent, task)

        self.assertEqual("default/research", context.agent.name)
        self.assertEqual("limited", context.permission_profile)
        self.assertEqual((), context.session.messages)
        self.assertEqual("delegation:T1", context.session.source)
        self.assertEqual("", context.subagents_index)
        self.assertEqual((), context.trusted_roots.roots)
        self.assertEqual(("shell", "files"), tuple(tool.name for tool in context.tools))
        self.assertIn("`shell`", context.tools_index)
        self.assertNotIn("`delegate`", context.tools_index)
        self.assertEqual(parent.workspace, context.workspace)

    def test_scoped_tools_dev_excludes_non_dev_tools(self) -> None:
        tools = (
            ToolSpec(name="shell", body=""),
            ToolSpec(name="files", body=""),
            ToolSpec(name="browser", body=""),
            ToolSpec(name="web", body=""),
            ToolSpec(name="vision", body=""),
            ToolSpec(name="delegate", body=""),
            ToolSpec(name="secret", body=""),
            ToolSpec(name="tasks", body=""),
        )

        filtered = scoped_tools(tools, "dev")

        self.assertEqual(("shell", "files", "browser", "web", "vision"), tuple(tool.name for tool in filtered))

    def test_task_prompt_tells_worker_to_stay_in_workspace(self) -> None:
        prompt = task_prompt(_task())

        self.assertIn("ToolScope: dev", prompt)
        self.assertIn("Work only inside the active workspace", prompt)
        self.assertIn("Do not try to access files outside", prompt)

    def test_delegate_returns_task_result_from_runner_observation(self) -> None:
        parent = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()), permission_profile="power")
        subagent = AgentProfile(name="default/research")
        seen = {}

        def runner(intention, context):
            seen["intention"] = intention.text
            seen["agent"] = context.agent.name
            return RunResult(
                decision=Decision(kind="answer", summary="done"),
                observation=Observation(
                    ok=True,
                    summary="Analyse terminée.",
                    data={
                        "changed": ("notes.md",),
                        "observed": ("3 fichiers lus",),
                        "evidence": ("test evidence",),
                    },
                ),
                trace=(TraceEvent(event_type="observation", summary="trace evidence", session_id="s"),),
            )

        result = delegate(_task(), subagent, parent, runner)

        self.assertEqual("done", result.status)
        self.assertEqual("T1", result.task_id)
        self.assertEqual("Analyse terminée.", result.summary)
        self.assertEqual(("notes.md",), result.changed)
        self.assertEqual(("3 fichiers lus",), result.observed)
        self.assertEqual(("test evidence",), result.evidence)
        self.assertIn("## Return Contract", seen["intention"])
        self.assertEqual("default/research", seen["agent"])

    def test_delegate_treats_explicit_status_error_summary_as_error(self) -> None:
        parent = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()), permission_profile="power")
        subagent = AgentProfile(name="default/research")

        def runner(_intention, _context):
            return RunResult(
                decision=Decision(kind="answer", summary="Status: error\nSummary: ProviderError"),
                observation=Observation(ok=True, summary="Status: error\nSummary: ProviderError"),
                trace=(),
            )

        result = delegate(_task(), subagent, parent, runner)

        self.assertEqual("error", result.status)
        self.assertIn("ProviderError", result.blockers)

    def test_delegate_treats_explicit_status_done_as_done_even_with_caveats(self) -> None:
        parent = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()), permission_profile="power")
        subagent = AgentProfile(name="default/research")

        def runner(_intention, _context):
            return RunResult(
                decision=Decision(kind="answer", summary="done"),
                observation=Observation(
                    ok=True,
                    summary=(
                        "Status: done\n"
                        "Summary: lecture statique utile.\n"
                        "Blockers: - Aucun accès hors workspace requis. - Vérification runtime non faite."
                    ),
                ),
                trace=(),
            )

        result = delegate(_task(), subagent, parent, runner)

        self.assertEqual("done", result.status)
        self.assertEqual((), result.blockers)

    def test_effective_permission_never_exceeds_parent(self) -> None:
        self.assertEqual("safe", effective_permission("safe", "power"))
        self.assertEqual("limited", effective_permission("power", "limited"))
        self.assertEqual("power", effective_permission("power", None))

    def test_task_prompt_contains_standalone_contract(self) -> None:
        prompt = task_prompt(_task())

        self.assertIn("TaskId: T1", prompt)
        self.assertIn("## Goal", prompt)
        self.assertIn("## Context", prompt)
        self.assertIn("## Expected Output", prompt)
        self.assertIn("Do not address the user directly.", prompt)

    def test_delegate_tool_runs_configured_subagent(self) -> None:
        module = load_tool_module("delegate", "runtime")
        self.assertIsNotNone(module)

        class Provider:
            prompts: list[str]

            def __init__(self) -> None:
                self.prompts = []

            def complete(self, prompt: str, **_: object) -> str:
                self.prompts.append(prompt)
                return "Subagent result with evidence."

        provider = Provider()
        with tempfile_agents() as agents_dir:
            context = RunContext(
                session=Session(),
                workspace=Workspace(root=Path.cwd()),
                permission_profile="power",
                agents_dir=agents_dir,
                agent=AgentProfile(name="default"),
                provider_for_agent=lambda agent: provider,
            )
            action = module.action_from_text(
                'run worker=worker id=T1 goal="Analyser" context="Contexte standalone" expected="Résumé avec preuve" profile=safe'
            )

            decision = module.review(action, context)
            observation = execute(action, context)

        self.assertEqual("allow", decision.verdict)
        self.assertTrue(observation.ok)
        self.assertEqual("Subagent result with evidence.", observation.summary)
        self.assertEqual("T1", observation.data["task_id"])
        self.assertEqual("done", observation.data["status"])
        self.assertIn("# Delegated Task", provider.prompts[0])
        self.assertIn("Subagent worker", provider.prompts[0])

    def test_delegate_tool_refuses_incomplete_task(self) -> None:
        module = load_tool_module("delegate", "runtime")
        self.assertIsNotNone(module)
        context = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()), agent=AgentProfile(name="default"))

        action = module.action_from_text('run worker=worker id=T1 goal=""')
        decision = module.review(action, context)

        self.assertEqual("block", decision.verdict)
        self.assertIn("delegate missing", decision.reason)

    def test_dev_skill_cli_delegates_standalone_task(self) -> None:
        module = load_skill_module(
            "dev",
            "cli",
            Path(__file__).resolve().parents[1] / "bb9" / "templates" / "skills",
        )
        self.assertIsNotNone(module)

        class Provider:
            prompts: list[str] = []

            def complete(self, prompt: str, **_: object) -> str:
                self.prompts.append(prompt)
                return "Délégation ok."

        class FakeCli:
            def __init__(self, agents_dir: Path) -> None:
                self.state = SimpleNamespace(agents_dir=agents_dir, agent_name="default")
                self.commands = {}
                self.forwarded: list[str] = []
                self.provider = Provider()

            def add_command(self, command, handler, description):
                self.commands[command] = handler

            def run_intention(self, text):
                self.forwarded.append(text)

            def build_context(self):
                return RunContext(
                    session=Session(source="cli").with_message("user", "parent context", max_messages=10),
                    workspace=Workspace(root=Path.cwd()),
                    permission_profile="limited",
                    agent=AgentProfile(name="default"),
                    subagents_index="# Subagents Index\n\n- `worker`\n",
                )

            def build_provider_for_agent(self, agent):
                return self.provider

            def ask_guardian(self, *_):
                return "deny"

        with tempfile_agents() as agents_dir:
            cli = FakeCli(agents_dir)
            module.register(cli)
            output = io.StringIO()

            with redirect_stdout(output):
                cli.commands["/build"](
                    'delegate id=T1 worker=worker goal="Analyser" '
                    'context="Contexte fourni par le parent." expected="Résumé avec preuve."'
                )
                cli.commands["/build"]("planifie ça")

            self.assertIn("task... Analyser: done", output.getvalue())
            self.assertIn("sum... Délégation ok.", output.getvalue())
            self.assertIn("Goal", cli.provider.prompts[0])
            self.assertIn("Analyser", cli.provider.prompts[0])
            self.assertIn("default/worker", cli.provider.prompts[0])
            self.assertEqual(["/build planifie ça"], cli.forwarded)

    def test_plan_skill_cli_writes_current_workspace_plan(self) -> None:
        module = load_skill_module(
            "plan",
            "cli",
            Path(__file__).resolve().parents[1] / "bb9" / "templates" / "skills",
        )
        self.assertIsNotNone(module)

        class Provider:
            def __init__(self, response: str) -> None:
                self.response = response

            def complete(self, prompt: str, **_: object) -> str:
                return self.response

        class FakeCli:
            def __init__(self, provider) -> None:
                self.commands = {}
                self.provider = provider

            def add_command(self, command, handler, description):
                self.commands[command] = handler

            def build_context(self):
                return RunContext(session=Session(source="cli"), workspace=Workspace(root=Path.cwd()))

            def build_provider(self):
                return self.provider

            def ask_guardian(self, *_):
                return "deny"

        cwd = Path.cwd()
        with tempfile_agents() as agents_dir:
            workspace = agents_dir / "_workspace"
            workspace.mkdir()
            plan_path = workspace / ".bb9" / "plan.md"
            plan_path.parent.mkdir()
            plan_path.write_text("old plan\n", encoding="utf-8")
            cli = FakeCli(
                Provider(
                    "# BB9 Plan\n\n"
                    "Objective: Tester.\n\n"
                    "## Tasks\n\n"
                    "- [ ] T1 Lire\n"
                    "  worker: default\n"
                    "  goal: Lire.\n"
                    "  context: Contexte.\n"
                    "  expected: Résumé.\n"
                )
            )
            module.register(cli)
            output = io.StringIO()

            try:
                os.chdir(workspace)
                with redirect_stdout(output):
                    cli.commands["/plan"]("tester le plan")
            finally:
                os.chdir(cwd)

            self.assertIn("plan... écrit", output.getvalue())
            self.assertIn("# BB9 Plan", plan_path.read_text(encoding="utf-8"))
            self.assertNotIn("old plan", plan_path.read_text(encoding="utf-8"))

    def test_dev_skill_cli_runs_markdown_plan_sequentially(self) -> None:
        module = load_skill_module(
            "dev",
            "cli",
            Path(__file__).resolve().parents[1] / "bb9" / "templates" / "skills",
        )
        self.assertIsNotNone(module)

        class Provider:
            prompts: list[str] = []

            def complete(self, prompt: str, **_: object) -> str:
                self.prompts.append(prompt)
                return f"ok {len(self.prompts)}"

        class FakeCli:
            def __init__(self, agents_dir: Path) -> None:
                self.state = SimpleNamespace(agents_dir=agents_dir, agent_name="default")
                self.commands = {}
                self.provider = Provider()

            def add_command(self, command, handler, description):
                self.commands[command] = handler

            def run_intention(self, text):
                raise AssertionError(text)

            def build_context(self):
                return RunContext(
                    session=Session(source="cli"),
                    workspace=Workspace(root=Path.cwd()),
                    permission_profile="limited",
                    agent=AgentProfile(name="default"),
                    subagents_index="# Subagents Index\n\n- `worker`\n",
                )

            def build_provider_for_agent(self, agent):
                return self.provider

            def ask_guardian(self, *_):
                return "deny"

        cwd = Path.cwd()
        with tempfile_agents() as agents_dir:
            workspace = agents_dir / "_workspace"
            workspace.mkdir()
            plan = workspace / ".bb9" / "plan.md"
            plan.parent.mkdir()
            plan.write_text(
                "# Plan\n\n"
                "## Tasks\n\n"
                "- [ ] T1 Lire le contexte\n"
                "  worker: default\n"
                "  parallelizable: false\n"
                "  depends:\n"
                "  goal: Lire le contexte.\n"
                "  context: Le parent a cadré le besoin.\n"
                "  expected: Résumé court.\n\n"
                "- [ ] T2 Synthétiser\n"
                "  worker: default\n"
                "  depends: T1\n"
                "  goal: Synthétiser.\n"
                "  context: T1 est terminé.\n"
                "  expected: Synthèse finale.\n",
                encoding="utf-8",
            )
            cli = FakeCli(agents_dir)
            module.register(cli)
            output = io.StringIO()

            try:
                os.chdir(workspace)
                with redirect_stdout(output):
                    cli.commands["/build"]("")
            finally:
                os.chdir(cwd)

            self.assertIn("plan... 2 task(s)", output.getvalue())
            self.assertIn("task... Lire le contexte: done", output.getvalue())
            self.assertIn("task... Synthétiser: done", output.getvalue())
            self.assertIn("J'ai terminé Lire le contexte et Synthétiser.", output.getvalue())
            self.assertEqual(2, len(cli.provider.prompts))
            self.assertIn("Lire le contexte", cli.provider.prompts[0])
            self.assertIn("Synthétiser", cli.provider.prompts[1])
            updated_plan = plan.read_text(encoding="utf-8")
            self.assertIn("- [x] T1 Lire le contexte", updated_plan)
            self.assertIn("  status: done", updated_plan)
            self.assertIn("  summary: ok 1", updated_plan)
            self.assertIn("- [x] T2 Synthétiser", updated_plan)
            self.assertIn("  summary: ok 2", updated_plan)

    def test_dev_skill_cli_does_not_check_task_when_worker_reports_status_error(self) -> None:
        module = load_skill_module(
            "dev",
            "cli",
            Path(__file__).resolve().parents[1] / "bb9" / "templates" / "skills",
        )
        self.assertIsNotNone(module)

        class Provider:
            def complete(self, _prompt: str, **_: object) -> str:
                return "Status: error\nSummary: Action not executed: block"

        class FakeCli:
            def __init__(self, agents_dir: Path) -> None:
                self.state = SimpleNamespace(agents_dir=agents_dir, agent_name="default")
                self.commands = {}
                self.provider = Provider()

            def add_command(self, command, handler, description):
                self.commands[command] = handler

            def build_context(self):
                return RunContext(session=Session(source="cli"), workspace=Workspace(root=Path.cwd()), permission_profile="power")

            def build_provider_for_agent(self, agent):
                return self.provider

            def ask_guardian(self, *_):
                return "deny"

        cwd = Path.cwd()
        with tempfile_agents() as agents_dir:
            workspace = agents_dir / "_workspace"
            workspace.mkdir()
            plan = workspace / ".bb9" / "plan.md"
            plan.parent.mkdir()
            plan.write_text(
                "# Plan\n\n"
                "## Tasks\n\n"
                "- [ ] T1 Valider\n"
                "  worker: default\n"
                "  goal: Valider.\n"
                "  context: Contexte.\n"
                "  expected: Tests.\n",
                encoding="utf-8",
            )
            cli = FakeCli(agents_dir)
            module.register(cli)
            output = io.StringIO()

            try:
                os.chdir(workspace)
                with redirect_stdout(output):
                    cli.commands["/build"]("")
            finally:
                os.chdir(cwd)

            updated_plan = plan.read_text(encoding="utf-8")

        self.assertIn("task... Valider: error", output.getvalue())
        self.assertIn("- [ ] T1 Valider", updated_plan)
        self.assertIn("  status: error", updated_plan)
        self.assertIn("Action not executed", updated_plan)

    def test_dev_skill_cli_treats_summary_starting_with_error_as_failed(self) -> None:
        module = load_skill_module(
            "dev",
            "cli",
            Path(__file__).resolve().parents[1] / "bb9" / "templates" / "skills",
        )
        self.assertIsNotNone(module)

        class Provider:
            def complete(self, _prompt: str, **_: object) -> str:
                return (
                    "error\n\n"
                    "Evidence:\n"
                    "- README.md was read.\n"
                    "Blocker:\n"
                    "- Final verification failed.\n"
                )

        class FakeCli:
            def __init__(self, agents_dir: Path) -> None:
                self.state = SimpleNamespace(agents_dir=agents_dir, agent_name="default")
                self.commands = {}
                self.provider = Provider()

            def add_command(self, command, handler, description):
                self.commands[command] = handler

            def build_context(self):
                return RunContext(session=Session(source="cli"), workspace=Workspace(root=Path.cwd()), permission_profile="power")

            def build_provider_for_agent(self, agent):
                return self.provider

            def ask_guardian(self, *_):
                return "deny"

        cwd = Path.cwd()
        with tempfile_agents() as agents_dir:
            workspace = agents_dir / "_workspace"
            workspace.mkdir()
            plan = workspace / ".bb9" / "plan.md"
            plan.parent.mkdir()
            plan.write_text(
                "# Plan\n\n"
                "## Tasks\n\n"
                "- [ ] T1 Documenter\n"
                "  worker: default\n"
                "  goal: Documenter.\n"
                "  context: Contexte.\n"
                "  expected: README vérifié.\n",
                encoding="utf-8",
            )
            cli = FakeCli(agents_dir)
            module.register(cli)
            output = io.StringIO()

            try:
                os.chdir(workspace)
                with redirect_stdout(output):
                    cli.commands["/build"]("")
            finally:
                os.chdir(cwd)

            updated_plan = plan.read_text(encoding="utf-8")

        self.assertIn("task... Documenter: error", output.getvalue())
        self.assertIn("- [ ] T1 Documenter", updated_plan)
        self.assertIn("  status: error", updated_plan)

    def test_dev_skill_cli_skips_task_when_dependency_failed(self) -> None:
        module = load_skill_module(
            "dev",
            "cli",
            Path(__file__).resolve().parents[1] / "bb9" / "templates" / "skills",
        )
        self.assertIsNotNone(module)

        class FakeCli:
            def __init__(self, agents_dir: Path) -> None:
                self.state = SimpleNamespace(agents_dir=agents_dir, agent_name="default")
                self.commands = {}

            def add_command(self, command, handler, description):
                self.commands[command] = handler

            def build_context(self):
                return RunContext(session=Session(), workspace=Workspace(root=Path.cwd()))

            def build_provider_for_agent(self, agent):
                raise AssertionError("runner should not be called")

            def ask_guardian(self, *_):
                return "deny"

        cwd = Path.cwd()
        with tempfile_agents() as agents_dir:
            workspace = agents_dir / "_workspace"
            workspace.mkdir()
            plan = workspace / ".bb9" / "plan.md"
            plan.parent.mkdir()
            plan.write_text(
                "# Plan\n\n"
                "## Tasks\n\n"
                "- [ ] T1 Flou\n"
                "  goal:\n"
                "  context:\n"
                "  expected:\n\n"
                "- [ ] T2 Synthétiser\n"
                "  depends: T1\n"
                "  goal: Synthétiser.\n"
                "  context: T1 est terminé.\n"
                "  expected: Synthèse finale.\n",
                encoding="utf-8",
            )
            cli = FakeCli(agents_dir)
            module.register(cli)
            output = io.StringIO()

            try:
                os.chdir(workspace)
                with redirect_stdout(output):
                    cli.commands["/build"]("")
            finally:
                os.chdir(cwd)

            self.assertIn("task... Flou: error", output.getvalue())
            self.assertIn("missing goal", output.getvalue())
            self.assertIn("task... Synthétiser: error", output.getvalue())
            self.assertIn("la tâche 'Flou' n'est pas terminée", output.getvalue())
            self.assertIn("Je n'ai pas pu terminer Flou et Synthétiser", output.getvalue())
            self.assertNotIn("dependency:T1", output.getvalue())
            updated_plan = plan.read_text(encoding="utf-8")
            self.assertIn("  status: error", updated_plan)
            self.assertIn("  blockers: missing goal; missing context; missing expected output", updated_plan)
            self.assertIn("  blockers: dependency:T1", updated_plan)

    def test_dev_skill_cli_treats_checked_tasks_as_done_dependencies(self) -> None:
        module = load_skill_module(
            "dev",
            "cli",
            Path(__file__).resolve().parents[1] / "bb9" / "templates" / "skills",
        )
        self.assertIsNotNone(module)

        class Provider:
            prompts: list[str] = []

            def complete(self, prompt: str, **_: object) -> str:
                self.prompts.append(prompt)
                return "ok"

        class FakeCli:
            def __init__(self, agents_dir: Path) -> None:
                self.state = SimpleNamespace(agents_dir=agents_dir, agent_name="default")
                self.commands = {}
                self.provider = Provider()

            def add_command(self, command, handler, description):
                self.commands[command] = handler

            def build_context(self):
                return RunContext(session=Session(), workspace=Workspace(root=Path.cwd()))

            def build_provider_for_agent(self, agent):
                return self.provider

            def ask_guardian(self, *_):
                return "deny"

        cwd = Path.cwd()
        with tempfile_agents() as agents_dir:
            workspace = agents_dir / "_workspace"
            workspace.mkdir()
            plan = workspace / ".bb9" / "plan.md"
            plan.parent.mkdir()
            plan.write_text(
                "# BB9 Plan\n\n"
                "## Tasks\n\n"
                "- [x] T1 Déjà fait\n"
                "  goal: Déjà fait.\n"
                "  context: Déjà fait.\n"
                "  expected: Déjà fait.\n\n"
                "- [ ] T2 Suite\n"
                "  depends: T1\n"
                "  goal: Continuer.\n"
                "  context: T1 est déjà coché.\n"
                "  expected: Résumé.\n",
                encoding="utf-8",
            )
            cli = FakeCli(agents_dir)
            module.register(cli)
            output = io.StringIO()

            try:
                os.chdir(workspace)
                with redirect_stdout(output):
                    cli.commands["/build"]("")
            finally:
                os.chdir(cwd)

            self.assertIn("task... Suite: done", output.getvalue())
            self.assertEqual(1, len(cli.provider.prompts))

    def test_dev_skill_treats_stored_status_done_as_completed_and_dependency_blocks_as_retriable(self) -> None:
        module = load_skill_module(
            "dev",
            "cli",
            Path(__file__).resolve().parents[1] / "bb9" / "templates" / "skills",
        )
        self.assertIsNotNone(module)

        plan_text = (
            "# Plan\n\n"
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

        self.assertEqual({"T1"}, module.completed_task_ids(plan_text))
        self.assertEqual(set(), module.errored_task_ids(plan_text))

    def test_dev_skill_build_retry_errors_option_uses_default_plan_path(self) -> None:
        module = load_skill_module(
            "dev",
            "cli",
            Path(__file__).resolve().parents[1] / "bb9" / "templates" / "skills",
        )
        self.assertIsNotNone(module)

        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            plan_path = workspace / ".bb9" / "plan.md"
            plan_path.parent.mkdir()
            plan_path.write_text("# Plan\n\n- [x] T1 Fait\n", encoding="utf-8")
            try:
                os.chdir(workspace)

                resolved = module._plan_path("--retry-errors")
            finally:
                os.chdir(cwd)

        self.assertEqual(plan_path.resolve(), resolved)

    def test_dev_skill_build_summary_does_not_report_dependency_skips_as_errors(self) -> None:
        module = load_skill_module(
            "dev",
            "cli",
            Path(__file__).resolve().parents[1] / "bb9" / "templates" / "skills",
        )
        self.assertIsNotNone(module)

        result = module.BuildResult(
            plan_path=Path(".bb9/plan.md"),
            total=2,
            reports=(
                module.BuildTaskReport(
                    task=Task(id="T4", title="Ajouter une vérification API minimale sans dépendances lourdes", goal="", context=""),
                    result=TaskResult(
                        task_id="T4",
                        status="error",
                        summary="Task skipped because dependencies could not be resolved.",
                    ),
                    title="Ajouter une vérification API minimale sans dépendances lourdes",
                ),
                module.BuildTaskReport(
                    task=Task(id="T7", title="Synthétiser la critique finale et les priorités restantes", goal="", context=""),
                    result=TaskResult(
                        task_id="T7",
                        status="error",
                        summary="Task skipped because dependencies could not be resolved.",
                    ),
                    title="Synthétiser la critique finale et les priorités restantes",
                ),
            ),
        )

        summary = module.build_summary(result)

        self.assertNotIn("En erreur", summary)
        self.assertIn(
            "Bloqué par dépendance : Ajouter une vérification API minimale sans dépendances lourdes et Synthétiser la critique finale et les priorités restantes.",
            summary,
        )

    def test_dev_skill_plan_parser_accepts_permission_profile_and_tool_scope(self) -> None:
        module = load_skill_module(
            "dev",
            "cli",
            Path(__file__).resolve().parents[1] / "bb9" / "templates" / "skills",
        )
        self.assertIsNotNone(module)

        tasks = module.parse_plan(
            "# Plan\n\n"
            "- [ ] T1 Tester\n"
            "  permission_profile: safe\n"
            "  tool_scope: none\n"
            "  goal: Tester.\n"
            "  context: Contexte.\n"
            "  expected: Résultat.\n"
        )

        self.assertEqual("safe", tasks[0].permission_profile)
        self.assertEqual("none", tasks[0].tool_scope)

    def test_dev_skill_parallel_group_rejects_parent_child_path_conflicts(self) -> None:
        module = load_skill_module(
            "dev",
            "cli",
            Path(__file__).resolve().parents[1] / "bb9" / "templates" / "skills",
        )
        self.assertIsNotNone(module)

        tasks = list(
            module.parse_plan(
                "# Plan\n\n"
                "- [ ] T1 Docs root\n"
                "  parallelizable: true\n"
                "  paths: docs\n"
                "  goal: Adapter docs.\n"
                "  context: Contexte.\n"
                "  expected: Docs.\n\n"
                "- [ ] T2 Docs file\n"
                "  parallelizable: true\n"
                "  paths: docs/skills.md\n"
                "  goal: Adapter fichier.\n"
                "  context: Contexte.\n"
                "  expected: Fichier.\n"
            )
        )

        group = module._parallel_group(tasks)

        self.assertEqual(["T1"], [task.id for task in group])

    def test_dev_skill_cli_reports_completed_plan_naturally(self) -> None:
        module = load_skill_module(
            "dev",
            "cli",
            Path(__file__).resolve().parents[1] / "bb9" / "templates" / "skills",
        )
        self.assertIsNotNone(module)

        class FakeCli:
            def __init__(self) -> None:
                self.commands = {}

            def add_command(self, command, handler, description):
                self.commands[command] = handler

        cwd = Path.cwd()
        with tempfile_agents() as agents_dir:
            workspace = agents_dir / "_workspace"
            workspace.mkdir()
            plan = workspace / ".bb9" / "plan.md"
            plan.parent.mkdir()
            plan.write_text(
                "# BB9 Plan\n\n"
                "## Tasks\n\n"
                "- [x] T1 Déjà terminé\n"
                "  goal: Déjà terminé.\n"
                "  context: Déjà terminé.\n"
                "  expected: Déjà terminé.\n",
                encoding="utf-8",
            )
            cli = FakeCli()
            module.register(cli)
            output = io.StringIO()

            try:
                os.chdir(workspace)
                with redirect_stdout(output):
                    cli.commands["/build"]("")
            finally:
                os.chdir(cwd)

            self.assertIn("Rien de nouveau à exécuter.", output.getvalue())
            self.assertNotIn("plan... error", output.getvalue())

    def test_dev_skill_cli_runs_non_conflicting_parallel_tasks_together(self) -> None:
        module = load_skill_module(
            "dev",
            "cli",
            Path(__file__).resolve().parents[1] / "bb9" / "templates" / "skills",
        )
        self.assertIsNotNone(module)

        class Provider:
            prompts: list[str] = []

            def complete(self, prompt: str, **_: object) -> str:
                time.sleep(0.01)
                self.prompts.append(prompt)
                return "ok"

        class FakeCli:
            def __init__(self, agents_dir: Path) -> None:
                self.state = SimpleNamespace(agents_dir=agents_dir, agent_name="default")
                self.commands = {}
                self.provider = Provider()

            def add_command(self, command, handler, description):
                self.commands[command] = handler

            def build_context(self):
                return RunContext(session=Session(), workspace=Workspace(root=Path.cwd()))

            def build_provider_for_agent(self, agent):
                return self.provider

            def ask_guardian(self, *_):
                return "deny"

        cwd = Path.cwd()
        with tempfile_agents() as agents_dir:
            workspace = agents_dir / "_workspace"
            workspace.mkdir()
            plan = workspace / ".bb9" / "plan.md"
            plan.parent.mkdir()
            plan.write_text(
                "# BB9 Plan\n\n"
                "## Tasks\n\n"
                "- [ ] T1 Docs\n"
                "  worker: default\n"
                "  parallelizable: true\n"
                "  paths: docs/skills.md\n"
                "  goal: Adapter docs.\n"
                "  context: Aucun conflit avec tests.\n"
                "  expected: Docs adaptées.\n\n"
                "- [ ] T2 Tests\n"
                "  worker: default\n"
                "  parallelizable: true\n"
                "  paths: tests/test_skills.py\n"
                "  goal: Adapter tests.\n"
                "  context: Aucun conflit avec docs.\n"
                "  expected: Tests adaptés.\n\n"
                "- [ ] T3 Synthèse\n"
                "  worker: default\n"
                "  depends: T1 T2\n"
                "  goal: Synthétiser.\n"
                "  context: T1 et T2 terminées.\n"
                "  expected: Synthèse.\n",
                encoding="utf-8",
            )
            cli = FakeCli(agents_dir)
            module.register(cli)
            output = io.StringIO()

            try:
                os.chdir(workspace)
                with redirect_stdout(output):
                    cli.commands["/build"]("")
            finally:
                os.chdir(cwd)

            self.assertIn("parallel... Docs et Tests", output.getvalue())
            self.assertIn("task... Synthèse: done", output.getvalue())
            updated_plan = plan.read_text(encoding="utf-8")
            self.assertIn("- [x] T1 Docs", updated_plan)
            self.assertIn("- [x] T2 Tests", updated_plan)
            self.assertIn("- [x] T3 Synthèse", updated_plan)

    def test_dev_skill_cli_does_not_parallelize_conflicting_paths(self) -> None:
        module = load_skill_module(
            "dev",
            "cli",
            Path(__file__).resolve().parents[1] / "bb9" / "templates" / "skills",
        )
        self.assertIsNotNone(module)

        class Provider:
            prompts: list[str] = []

            def complete(self, prompt: str, **_: object) -> str:
                self.prompts.append(prompt)
                return "ok"

        class FakeCli:
            def __init__(self, agents_dir: Path) -> None:
                self.state = SimpleNamespace(agents_dir=agents_dir, agent_name="default")
                self.commands = {}
                self.provider = Provider()

            def add_command(self, command, handler, description):
                self.commands[command] = handler

            def build_context(self):
                return RunContext(session=Session(), workspace=Workspace(root=Path.cwd()))

            def build_provider_for_agent(self, agent):
                return self.provider

            def ask_guardian(self, *_):
                return "deny"

        cwd = Path.cwd()
        with tempfile_agents() as agents_dir:
            workspace = agents_dir / "_workspace"
            workspace.mkdir()
            plan = workspace / ".bb9" / "plan.md"
            plan.parent.mkdir()
            plan.write_text(
                "# BB9 Plan\n\n"
                "## Tasks\n\n"
                "- [ ] T1 Docs A\n"
                "  parallelizable: true\n"
                "  paths: docs/skills.md\n"
                "  goal: Adapter docs A.\n"
                "  context: Conflit docs.\n"
                "  expected: A.\n\n"
                "- [ ] T2 Docs B\n"
                "  parallelizable: true\n"
                "  paths: docs/skills.md\n"
                "  goal: Adapter docs B.\n"
                "  context: Conflit docs.\n"
                "  expected: B.\n",
                encoding="utf-8",
            )
            cli = FakeCli(agents_dir)
            module.register(cli)
            output = io.StringIO()

            try:
                os.chdir(workspace)
                with redirect_stdout(output):
                    cli.commands["/build"]("")
            finally:
                os.chdir(cwd)

            self.assertNotIn("parallel...", output.getvalue())
            self.assertIn("task... Docs A: done", output.getvalue())
            self.assertIn("task... Docs B: done", output.getvalue())


def _task(permission_profile=None) -> Task:
    return Task(
        id="T1",
        title="Analyser une brique",
        goal="Identifier les risques principaux.",
        context="Le parent a déjà lu la roadmap et les contrats.",
        inputs=("docs/subagents.md",),
        expected_output="Résumé court avec preuves.",
        done_criteria=("Risques listés",),
        suggested_worker="research",
        permission_profile=permission_profile,
    )


class tempfile_agents:
    def __enter__(self) -> Path:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        # Pool plat : agent parent + subagent worker au même niveau
        parent = root / "default"
        parent.mkdir(parents=True)
        parent.joinpath("IDENTITY.md").write_text("# Default\n", encoding="utf-8")
        worker = root / "worker"
        worker.mkdir(parents=True)
        worker.joinpath("IDENTITY.md").write_text(
            "# Worker\n\nType : subagent\nDescription : Subagent de travail générique.\n",
            encoding="utf-8",
        )
        return root

    def __exit__(self, *_) -> None:
        self._tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
