from __future__ import annotations

import importlib
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from bb9.core.agents import refresh_subagents_index
from bb9.core.cli import Cli, CliState
from bb9.core import context_runtime
from bb9.core.context_index import refresh_context_index
from bb9.core.gateway import execute
from bb9.core.kernel import Kernel
from bb9.core.loop import tool_budget_for
from bb9.core.models import AgentProfile, Intention, RunContext, Session, Skill, ToolSpec, Workspace
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
            self.assertIn("- `/demo` : commande principale via `cli.py`.", skill_text)
            self.assertIn("/demo-<action>", skill_text)
            self.assertIn('cli.add_command("/demo"', cli_text)

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
            self.assertIn("- `/demo` : commande principale via `cli.py`.", skill_text)

    def test_shell_tool_returns_observation_for_missing_command(self) -> None:
        module = load_tool_module("shell", "runtime")
        self.assertIsNotNone(module)

        observation = module.execute(module.action_from_text("...`"))

        self.assertFalse(observation.ok)
        self.assertIn("command not found", observation.summary)

    def test_kernel_ignores_placeholder_provider_actions(self) -> None:
        class PlaceholderProvider:
            def complete(self, _: str) -> str:
                return "BB9_ACTION shell <commande>`"

        context = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()))

        decision = Kernel(provider=PlaceholderProvider()).decide(Intention("analyse ce projet"), context)

        self.assertEqual("answer", decision.kind)
        self.assertIsNone(decision.action)
        self.assertIn("placeholder", decision.summary)

    def test_kernel_answers_context_inventory_without_provider(self) -> None:
        class FailingProvider:
            def complete(self, _: str) -> str:
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

            def complete(self, prompt: str) -> str:
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
        self.assertIn("demande directement", provider.prompt)
        self.assertIn("Evite les fins timides", provider.prompt)
        self.assertIn("Ne termine pas par une limite passive", provider.prompt)

    def test_kernel_prompt_includes_called_skill_body(self) -> None:
        class CapturingProvider:
            prompt = ""

            def complete(self, prompt: str) -> str:
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

            def complete(self, prompt: str) -> str:
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

            def complete(self, prompt: str) -> str:
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
