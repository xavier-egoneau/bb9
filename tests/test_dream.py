from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from bb9.cli.main import Cli, CliState
from bb9.core.dream import (
    DreamingPlan,
    DreamReport,
    apply_dream_operations,
    apply_dream_plan,
    build_dream_index,
    build_dreaming_context,
    build_dreaming_prompt,
    discover_dreams,
    format_dream_report,
    list_dream_reports,
    load_dream,
    load_dream_contributions,
    load_dream_report,
    load_pending_dream_plan,
    parse_dreaming_response,
    refresh_dream_index,
    run_dreaming,
    save_dream_report,
)
from bb9.core.history import VisibleHistoryStore
from bb9.core.memory import MemoryStore
from bb9.core.models import AgentProfile, Session
from bb9.core.sessions import SessionStore
from bb9.core.tasks import TaskStore


class FakeDreamProvider:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    def complete(self, prompt: str, **_: object) -> str:
        self.prompts.append(prompt)
        return self.response


class FakeProviderCli(Cli):
    def __init__(self, state: CliState, provider: FakeDreamProvider) -> None:
        super().__init__(state)
        self.fake_provider = provider

    def build_provider_for_agent(self, agent: AgentProfile):
        return self.fake_provider


class DreamArchiveTests(unittest.TestCase):
    def test_loads_dream_archive_without_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = root / "nightly"
            item.mkdir()
            item.joinpath("DREAM.md").write_text(
                "# DREAM.md\n\n"
                "## Résumé\n\nConsolidation nocturne.\n\n"
                "## Activation\n\nactive\n\n"
                "## Agent\n\ndefault\n\n"
                "## Scope\n\nproject\n\n"
                "## Sources\n\n- memory\n- sessions\n\n"
                "## Guardrails\n\n- Ne rien exécuter.\n",
                encoding="utf-8",
            )

            dream = load_dream(root, "nightly")

            self.assertEqual(["nightly"], discover_dreams(root))
            self.assertEqual("active", dream.activation)
            self.assertEqual("default", dream.agent)
            self.assertEqual("project", dream.scope)
            self.assertIn("memory", dream.sources)
            self.assertNotIn("Schedule", dream.body)

    def test_dream_index_is_generated_from_archives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = root / "daily"
            item.mkdir()
            item.joinpath("DREAM.md").write_text(
                "# DREAM.md\n\n## Résumé\n\nDaily dream.\n",
                encoding="utf-8",
            )

            index = refresh_dream_index(root)

            self.assertIn("`daily`", build_dream_index((load_dream(root, "daily"),)))
            self.assertIn("`daily`", index)
            self.assertTrue((root / "INDEX.md").is_file())

    def test_loads_skill_and_tool_dream_contributions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "skills" / "rag"
            skill.mkdir(parents=True)
            skill.joinpath("DREAM.md").write_text(
                "# RAG Dream\n\n"
                "## Purpose\n\nPromouvoir les règles durables.\n\n"
                "## Inputs\n\n- Sources taguées always.\n\n"
                "## Signals\n\n- rag.always_rule\n\n"
                "## Proposed Actions\n\n- memory.add\n\n"
                "## Guardrails\n\n- Ne pas recopier tout le corpus.\n",
                encoding="utf-8",
            )
            tool = root / "tools" / "calendar"
            tool.mkdir(parents=True)
            tool.joinpath("DREAM.md").write_text(
                "# Calendar Dream\n\n## Purpose\n\nRepérer les récurrences.\n",
                encoding="utf-8",
            )

            contributions = (
                *load_dream_contributions(root / "skills", "skill", active_names=("rag", "missing")),
                *load_dream_contributions(root / "tools", "tool"),
            )

            self.assertEqual(("skill", "tool"), tuple(item.kind for item in contributions))
            self.assertIn("rag.always_rule", contributions[0].signals)
            self.assertIn("récurrences", contributions[1].purpose)

    def test_builds_dreaming_context_and_prompt_from_memory_and_contributions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            project.joinpath("DECISIONS.md").write_text("# Decisions\n\n- Markdown first.\n", encoding="utf-8")
            project.joinpath("ROADMAP.md").write_text("# Roadmap\n\n- Dreaming.\n", encoding="utf-8")
            store = MemoryStore(root / "memory.db")
            sessions = SessionStore(root / "sessions.db")
            try:
                global_id = store.add("BB9 préfère les contrats Markdown.", tags="bb9")
                project_id = store.add(
                    "Le projet utilise une mémoire SQL graph.",
                    scope="project",
                    project_path=project,
                )
                store.add_edge(global_id, project_id, "supports")
                skill = root / "skills" / "rag"
                skill.mkdir(parents=True)
                skill.joinpath("DREAM.md").write_text(
                    "# RAG Dream\n\n## Signals\n\n- rag.important\n",
                    encoding="utf-8",
                )
                dream_dir = root / "dreams" / "daily"
                dream_dir.mkdir(parents=True)
                dream_dir.joinpath("DREAM.md").write_text(
                    "# DREAM.md\n\n## Activation\n\nactive\n\n## Sources\n\n- memory\n",
                    encoding="utf-8",
                )
                sessions.store(
                    Session(id="session-1").with_message("user", "Décision persistée.", max_messages=10),
                    project_path=project,
                )

                context = build_dreaming_context(
                    store,
                    project_root=project,
                    skill_contributions=load_dream_contributions(root / "skills", "skill"),
                    sessions=("### Session\nDécision utile.",),
                    session_store=sessions,
                )
                prompt = build_dreaming_prompt(load_dream(root / "dreams", "daily"), context)

                self.assertFalse(context.is_empty)
                self.assertIn("BB9 préfère", prompt)
                self.assertIn("rag.important", prompt)
                self.assertIn("Décision persistée", prompt)
                self.assertIn("DECISIONS.md", prompt)
                self.assertIn("supports", prompt)
            finally:
                sessions.close()
                store.close()

    def test_parses_and_applies_dreaming_memory_operations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = MemoryStore(root / "memory.db")
            try:
                old_id = store.add("Ancienne mémoire")
                response = """
                ```json
                {
                  "operations": [
                    {"op": "node.add", "content": "Nouvelle mémoire", "scope": "global", "kind": "fact", "tags": "dream"},
                    {"op": "node.replace", "old": "Ancienne", "new": "Mémoire remplacée"},
                    {"op": "edge.add", "source_id": 1, "target_id": 2, "relation": "supports"}
                  ],
                  "actions": [
                    {"kind": "rag.review_source", "status": "proposed"}
                  ],
                  "summary": "ok"
                }
                ```
                """

                operations, actions, summary = parse_dreaming_response(response)
                result = apply_dream_operations(operations, store)

                self.assertEqual("ok", summary)
                self.assertEqual(1, len(actions))
                self.assertEqual(1, result.added_nodes)
                self.assertEqual(1, result.updated_nodes)
                self.assertEqual(1, result.added_edges)
                self.assertEqual("Mémoire remplacée", store.get(old_id).content)
                self.assertEqual("Nouvelle mémoire", store.search("dream")[0].content)
            finally:
                store.close()

    def test_run_dreaming_calls_provider_and_applies_memory_operations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = MemoryStore(root / "memory.db")
            provider = FakeDreamProvider(
                """
                {
                  "operations": [
                    {"op": "node.add", "content": "Fait consolidé", "scope": "global", "source": "session:test"}
                  ],
                  "actions": [{"kind": "review", "status": "proposed"}],
                  "summary": "Consolidé."
                }
                """
            )
            try:
                dream_dir = root / "dreams" / "daily"
                dream_dir.mkdir(parents=True)
                dream_dir.joinpath("DREAM.md").write_text(
                    "# DREAM.md\n\n## Activation\n\nactive\n",
                    encoding="utf-8",
                )
                dream = load_dream(root / "dreams", "daily")
                context = build_dreaming_context(store, sessions=("### Session\nSignal utile.",))

                result = run_dreaming(dream, context, store, provider)

                self.assertIn("Signal utile", provider.prompts[0])
                self.assertEqual(1, result.added_nodes)
                self.assertEqual(1, len(result.actions))
                self.assertEqual("Consolidé.", result.summary)
                self.assertEqual("Fait consolidé", store.search("consolidé")[0].content)
            finally:
                store.close()

    def test_dream_report_is_saved_as_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = apply_dream_operations(
                [{"op": "node.add", "content": "Fait", "scope": "global"}],
                MemoryStore(root / "memory.db"),
            )
            report = DreamReport.from_result(
                dream="daily",
                mode="run",
                result=result,
                operations=({"op": "node.add", "content": "Fait"},),
                project_path=root / "project",
            )

            saved = save_dream_report(report, root / "reports")
            listed = list_dream_reports(root / "reports")
            loaded = load_dream_report(root / "reports", saved.id[:16])
            markdown = format_dream_report(saved)

            self.assertTrue(Path(saved.json_path).is_file())
            self.assertTrue(Path(saved.markdown_path).is_file())
            self.assertEqual(saved.id, listed[0].id)
            self.assertEqual(saved.id, loaded.id)
            self.assertIn("# Dream Report: daily", markdown)
            self.assertIn("Added nodes: 1", markdown)

    def test_project_memory_operation_requires_project_path_or_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = MemoryStore(root / "memory.db")
            try:
                operations = [
                    {"op": "node.add", "content": "Projet sans chemin", "scope": "project"},
                ]

                failed = apply_dream_operations(operations, store)

                self.assertEqual(1, failed.errors)
                self.assertEqual([], store.search("Projet"))
                applied = apply_dream_operations(operations, store, project_root=root / "project")
                self.assertEqual(1, applied.added_nodes)
                self.assertEqual("project", store.search("Projet")[0].scope)
            finally:
                store.close()

    def test_dream_apply_can_materialize_task_create_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory = MemoryStore(root / "memory.db")
            tasks = TaskStore(root / "tasks.json")
            plan = DreamingPlan(
                dream="daily",
                actions=(
                    {
                        "kind": "task.create",
                        "title": "Relancer le dossier",
                        "content": "Suite repérée dans les sessions.",
                        "priority": "high",
                        "source": "session:test",
                    },
                ),
                summary="Une suite à suivre.",
            )
            try:
                result = apply_dream_plan(plan, memory, project_root=root / "project", task_store=tasks)
            finally:
                memory.close()

            stored = tasks.list()

            self.assertEqual(1, result.created_tasks)
            self.assertEqual("created", result.actions[0]["status"])
            self.assertEqual(stored[0].id, result.actions[0]["task_id"])
            self.assertEqual("Relancer le dossier", stored[0].title)
            self.assertEqual("high", stored[0].priority)
            self.assertIn("Suite repérée", stored[0].prompt)

    def test_cli_dream_context_and_run_use_markdown_archives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents" / "default"
            agents.mkdir(parents=True)
            agents.joinpath("IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            dreams = root / "dreams"
            daily = dreams / "daily"
            daily.mkdir(parents=True)
            daily.joinpath("DREAM.md").write_text(
                "# DREAM.md\n\n"
                "## Résumé\n\nConsolider.\n\n"
                "## Activation\n\nactive\n\n"
                "## Agent\n\ndefault\n",
                encoding="utf-8",
            )
            skills = root / "skills" / "rag"
            skills.mkdir(parents=True)
            skills.joinpath("SKILL.md").write_text(
                "# RAG\n\n## Résumé\n\nRag.\n\n## Activation\n\nalways\n",
                encoding="utf-8",
            )
            skills.joinpath("DREAM.md").write_text(
                "# RAG Dream\n\n## Signals\n\n- rag.signal\n",
                encoding="utf-8",
            )
            workspace = root / "workspace"
            local_skills = workspace / ".bb9" / "skills" / "rag"
            local_skills.mkdir(parents=True)
            local_skills.joinpath("SKILL.md").write_text(
                "# RAG Local\n\n## Résumé\n\nRag local.\n\n## Activation\n\nalways\n",
                encoding="utf-8",
            )
            local_skills.joinpath("DREAM.md").write_text(
                "# RAG Local Dream\n\n## Signals\n\n- rag.local.signal\n",
                encoding="utf-8",
            )
            state = CliState(
                agents_dir=root / "agents",
                skills_dir=root / "skills",
                tools_dir=root / "tools",
                dreams_dir=dreams,
                memory_path=root / "memory.db",
                session_store_path=root / "sessions.db",
                visible_history_path=root / "visible.db",
            )
            sessions = SessionStore(state.session_store_path)
            sessions.store(
                Session(id="session-1").with_message("user", "Décision à consolider.", max_messages=10),
                project_path=workspace,
            )
            sessions.close()
            provider = FakeDreamProvider(
                """
                {
                  "operations": [
                    {"op": "node.add", "content": "Décision consolidée", "scope": "global", "source": "session:session-1"}
                  ],
                  "actions": [],
                  "summary": "ok"
                }
                """
            )
            cli = FakeProviderCli(state, provider)
            output = io.StringIO()
            cwd = Path.cwd()

            try:
                os.chdir(workspace)
                with redirect_stdout(output):
                    self.assertTrue(cli.cmd_dream("context daily"))
                    self.assertTrue(cli.cmd_dream("run daily"))
            finally:
                os.chdir(cwd)

            self.assertIn("dream.. daily", output.getvalue())
            self.assertIn("dream.. ok", output.getvalue())
            self.assertIn("rep...", output.getvalue())
            self.assertIn("Décision à consolider", provider.prompts[0])
            self.assertIn("rag.local.signal", provider.prompts[0])
            self.assertNotIn("rag.signal", provider.prompts[0])
            memory = MemoryStore(state.memory_path)
            try:
                self.assertEqual("Décision consolidée", memory.search("consolidée")[0].content)
            finally:
                memory.close()
            reports = list_dream_reports(dreams / "reports")
            self.assertEqual(1, len(reports))
            visible = VisibleHistoryStore(state.visible_history_path)
            try:
                messages = visible.recent()
                self.assertEqual("report", messages[-1].artifacts[0].kind)
                self.assertEqual(reports[0].markdown_path, messages[-1].artifacts[0].path)
            finally:
                visible.close()

    def test_cli_dream_preview_saves_plan_then_apply_writes_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents" / "default"
            agents.mkdir(parents=True)
            agents.joinpath("IDENTITY.md").write_text("# Default\n", encoding="utf-8")
            dreams = root / "dreams"
            daily = dreams / "daily"
            daily.mkdir(parents=True)
            daily.joinpath("DREAM.md").write_text(
                "# DREAM.md\n\n## Activation\n\nactive\n\n## Agent\n\ndefault\n",
                encoding="utf-8",
            )
            state = CliState(
                agents_dir=root / "agents",
                skills_dir=root / "skills",
                tools_dir=root / "tools",
                dreams_dir=dreams,
                dream_pending_path=root / "pending.json",
                memory_path=root / "memory.db",
                tasks_path=root / "tasks.json",
                session_store_path=root / "sessions.db",
                visible_history_path=root / "visible.db",
            )
            provider = FakeDreamProvider(
                """
                {
                  "operations": [
                    {"op": "node.add", "content": "Plan validé", "scope": "global", "source": "preview"}
                  ],
                  "actions": [{"kind": "task.create", "title": "Relire le plan", "content": "Suite proposée.", "status": "proposed"}],
                  "summary": "preview ok"
                }
                """
            )
            cli = FakeProviderCli(state, provider)
            output = io.StringIO()

            with redirect_stdout(output):
                self.assertTrue(cli.cmd_dream("preview daily"))
                self.assertTrue(cli.cmd_dream("apply daily"))

            self.assertIn("dream.. preview", output.getvalue())
            self.assertIn("dream.. ok", output.getvalue())
            self.assertIn("tasks=1", output.getvalue())
            self.assertIn("rep...", output.getvalue())
            self.assertIsNone(load_pending_dream_plan(state.dream_pending_path))
            self.assertEqual("Relire le plan", TaskStore(state.tasks_path).list()[0].title)
            reports = list_dream_reports(dreams / "reports")
            self.assertEqual(1, len(reports))
            self.assertEqual(1, reports[0].created_tasks)
            report_output = io.StringIO()
            with redirect_stdout(report_output):
                self.assertTrue(cli.cmd_dream(f"report {reports[0].id}"))
                self.assertTrue(cli.cmd_dream("reports"))
            self.assertIn("# Dream Report: daily", report_output.getvalue())
            self.assertIn("report ", report_output.getvalue())
            memory = MemoryStore(state.memory_path)
            try:
                self.assertEqual("Plan validé", memory.search("validé")[0].content)
            finally:
                memory.close()


if __name__ == "__main__":
    unittest.main()
