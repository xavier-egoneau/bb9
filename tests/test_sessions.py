from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bb9.core.models import Session
from bb9.core.sessions import SessionStore, redact_session_text


class SessionStoreTests(unittest.TestCase):
    def test_stores_and_loads_session_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            session = (
                Session(id="session-1", source="cli")
                .with_message("user", "bonjour", max_messages=10)
                .with_message("assistant", "salut", max_messages=10)
            )
            store = SessionStore(root / "sessions.db")
            try:
                stored = store.store(session, project_path=project)
                loaded = store.get("session-1")

                self.assertEqual("session-1", stored.id)
                self.assertIsNotNone(loaded)
                self.assertEqual(str(project.resolve()), loaded.project_path)
                self.assertEqual(("user", "assistant"), tuple(message.role for message in loaded.messages))
                self.assertEqual("bonjour", loaded.messages[0].content)
            finally:
                store.close()

    def test_store_replaces_messages_for_same_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "sessions.db")
            try:
                first = Session(id="session-1").with_message("user", "ancien", max_messages=10)
                second = Session(id="session-1").with_message("assistant", "nouveau", max_messages=10)

                store.store(first)
                store.store(second)

                loaded = store.get("session-1")
                self.assertEqual(1, len(loaded.messages))
                self.assertEqual("nouveau", loaded.messages[0].content)
            finally:
                store.close()

    def test_recent_dream_context_filters_to_project_or_global_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            other = root / "other"
            store = SessionStore(root / "sessions.db")
            try:
                global_session = Session(id="global").with_message("user", "global", max_messages=10)
                project_session = Session(id="project").with_message("assistant", "projet", max_messages=10)
                other_session = Session(id="other").with_message("assistant", "autre", max_messages=10)

                store.store(global_session)
                store.store(project_session, project_path=project)
                store.store(other_session, project_path=other)

                context = "\n\n".join(store.recent_dream_context(project_path=project))

                self.assertIn("global", context)
                self.assertIn("projet", context)
                self.assertNotIn("autre", context)
                exact = store.recent(project_path=project, include_global=False)
                self.assertEqual(("project",), tuple(session.id for session in exact))
            finally:
                store.close()

    def test_projects_are_canonical_and_grouped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "workspace"
            project.mkdir()
            store = SessionStore(root / "sessions.db")
            try:
                store.store(Session(id="one", source="web"), project_path=project)
                store.store(Session(id="two", source="web"), project_path=project / ".." / "workspace")

                projects = store.projects()

                self.assertEqual((str(project.resolve()),), tuple(project["path"] for project in projects))
                self.assertEqual(2, projects[0]["session_count"])
            finally:
                store.close()

    def test_can_archive_and_forget_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "sessions.db")
            try:
                store.store(Session(id="session-1").with_message("user", "hello"))

                self.assertTrue(store.archive("session-1"))
                self.assertEqual(0, len(store.recent(include_archived=False)))
                self.assertTrue(store.forget("session-1"))
                self.assertIsNone(store.get("session-1"))
            finally:
                store.close()

    def test_redacts_likely_secrets_before_persistence(self) -> None:
        text = "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz123456"

        self.assertNotIn("sk-proj-", redact_session_text(text))
        self.assertEqual("MY_TOKEN=<secret-redacted>", redact_session_text("MY_TOKEN=very-secret-value"))


if __name__ == "__main__":
    unittest.main()
