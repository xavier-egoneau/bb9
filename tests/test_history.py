from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from bb9.core.history import VisibleHistoryStore
from bb9.core.models import Artifact, Session
from bb9.core import session_cli


class VisibleHistoryStoreTests(unittest.TestCase):
    def test_appends_visible_turn_and_exports_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = VisibleHistoryStore(Path(tmp) / "visible.db")
            try:
                artifact = Artifact(kind="report", title="Rapport de test", path="reports/test.md")

                store.append_turn(
                    session_id="session-1",
                    user_text="Bonjour",
                    assistant_text="Salut",
                    project_path=Path(tmp) / "project",
                    artifacts=(artifact,),
                )

                messages = store.recent()
                markdown = store.export_markdown()

                self.assertEqual(2, len(messages))
                self.assertEqual(("user", "assistant"), tuple(message.role for message in messages))
                self.assertEqual("Salut", messages[1].content)
                self.assertEqual("Rapport de test", messages[1].artifacts[0].title)
                self.assertIn("# BB9 Visible History", markdown)
                self.assertIn("Artifact `report`: Rapport de test", markdown)
            finally:
                store.close()

    def test_redacts_secrets_before_visible_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = VisibleHistoryStore(Path(tmp) / "visible.db")
            try:
                store.append_message(
                    session_id="session-1",
                    role="user",
                    content="OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz123456",
                )

                message = store.recent()[0]

                self.assertNotIn("sk-proj-", message.content)
                self.assertIn("<secret-redacted>", message.content)
            finally:
                store.close()

    def test_export_markdown_renders_diff_artifact_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = VisibleHistoryStore(Path(tmp) / "visible.db")
            try:
                artifact = Artifact(
                    kind="diff",
                    title="2 fichiers modifiés (+3/-1)",
                    path="/tmp/bb9.diff",
                    metadata={
                        "files_changed": 2,
                        "insertions": 3,
                        "deletions": 1,
                        "default_collapsed": True,
                        "files": [
                            {"path": "README.md", "status": "M", "insertions": 2, "deletions": 1},
                            {"path": "docs/history.md", "status": "??", "insertions": 1, "deletions": 0},
                        ],
                    },
                )

                store.append_turn(
                    session_id="session-1",
                    user_text="Modifie le projet",
                    assistant_text="C'est fait.",
                    artifacts=(artifact,),
                )

                markdown = store.export_markdown()

                self.assertIn("Artifact `diff`: 2 fichiers modifiés (+3/-1)", markdown)
                self.assertIn("`README.md` (M): +2/-1", markdown)
                self.assertIn("`docs/history.md` (??): +1/-0", markdown)
                self.assertIn("Patch: `/tmp/bb9.diff`", markdown)
            finally:
                store.close()

    def test_export_markdown_renders_tool_trace_artifact_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = VisibleHistoryStore(Path(tmp) / "visible.db")
            try:
                artifact = Artifact(
                    kind="tool_trace",
                    title="2 outils utilisés, 1 échec(s)",
                    metadata={
                        "count": 2,
                        "failures": 1,
                        "default_collapsed": True,
                        "entries": [
                            {"tool": "shell", "ok": True, "summary": "commande terminée"},
                            {"tool": "tasks", "ok": False, "summary": "validation requise"},
                        ],
                    },
                )

                store.append_turn(
                    session_id="session-1",
                    user_text="Fais le point",
                    assistant_text="Voilà le bilan.",
                    artifacts=(artifact,),
                )

                markdown = store.export_markdown()

                self.assertIn("Artifact `tool_trace`: 2 outils utilisés, 1 échec(s)", markdown)
                self.assertIn("`shell`: ok - commande terminée", markdown)
                self.assertIn("`tasks`: error - validation requise", markdown)
            finally:
                store.close()

    def test_visible_process_is_persisted_outside_session_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = VisibleHistoryStore(Path(tmp) / "visible.db")
            try:
                process = store.append_process(
                    session_id="session-1",
                    content="Je vérifie les tests ciblés.",
                    project_path=Path(tmp) / "project",
                )

                messages = store.recent()
                markdown = store.export_markdown()

                self.assertEqual("process", process.role)
                self.assertEqual(("process",), tuple(message.role for message in messages))
                self.assertIn("Je vérifie les tests ciblés.", markdown)
                self.assertIn("## process", markdown)
            finally:
                store.close()

    def test_session_cli_persists_context_and_visible_history_separately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cli = SimpleNamespace(
                state=SimpleNamespace(
                    session=Session(id="session-1", source="cli"),
                    session_store_path=root / "sessions.db",
                    visible_history_path=root / "visible.db",
                ),
                active_model_metadata=lambda: SimpleNamespace(
                    context_window_tokens=100_000,
                    soft_input_limit_tokens=80_000,
                ),
            )

            session_cli.remember_turn(cli, "Question", "Réponse")

            visible = VisibleHistoryStore(root / "visible.db")
            try:
                self.assertEqual(2, len(cli.state.session.messages))
                self.assertEqual(2, visible.count())
                self.assertIn("Question", visible.export_markdown())
                self.assertIn("Réponse", visible.export_markdown())
            finally:
                visible.close()


if __name__ == "__main__":
    unittest.main()
