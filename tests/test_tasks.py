from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bb9.core.models import RunContext, Session, Workspace
from bb9.core.tasks import TaskStore
from bb9.core.tool_runtime import load_tool_module


class TaskStoreTests(unittest.TestCase):
    def test_creates_updates_and_persists_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            store = TaskStore(path)

            task = store.create(title="Relancer le dossier", priority="high", scheduled_for="2026-06-01T09:00:00+02:00")
            updated = store.update(task.id, status="done", prompt="Fait avec preuve.")

            reloaded = TaskStore(path).get(task.id)

            self.assertIsNotNone(updated)
            self.assertIsNotNone(reloaded)
            self.assertEqual("queued", task.status)
            self.assertEqual("done", reloaded.status)
            self.assertEqual("Fait avec preuve.", reloaded.prompt)
            self.assertEqual(("created", "status_changed"), tuple(event["kind"] for event in reloaded.events))

    def test_lists_open_tasks_before_done_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(Path(tmp) / "tasks.json")
            done = store.create(title="Ancienne tâche")
            store.update(done.id, status="done")
            open_task = store.create(title="Tâche ouverte")

            tasks = store.list()

            self.assertEqual((open_task.id, done.id), tuple(task.id for task in tasks))

    def test_rejects_invalid_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(Path(tmp) / "tasks.json")

            with self.assertRaises(ValueError):
                store.create(title="Invalide", scheduled_for="demain matin")

    def test_rejects_invalid_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(Path(tmp) / "tasks.json")
            task = store.create(title="Valide")

            with self.assertRaises(ValueError):
                store.update(task.id, status="maybe")

            with self.assertRaises(ValueError):
                store.list(status="maybe")


class TasksToolTests(unittest.TestCase):
    def test_runtime_creates_lists_and_updates_tasks(self) -> None:
        module = load_tool_module("tasks", "runtime")
        self.assertIsNotNone(module)
        with tempfile.TemporaryDirectory() as tmp:
            old_path = module.TASKS_PATH
            module.TASKS_PATH = Path(tmp) / "tasks.json"
            try:
                create = module.action_from_text('create title="Relancer le dossier" priority=high')
                created = module.execute(create)
                task_id = created.data["task"]["id"]

                listed = module.execute(module.action_from_text("list include_done=false"))
                updated = module.execute(module.action_from_text(f"update id={task_id} status=done"))
                open_list = module.execute(module.action_from_text("list include_done=false"))
            finally:
                module.TASKS_PATH = old_path

            self.assertTrue(created.ok)
            self.assertTrue(listed.ok)
            self.assertTrue(updated.ok)
            self.assertEqual(1, len(listed.data["tasks"]))
            self.assertEqual("done", updated.data["task"]["status"])
            self.assertEqual([], open_list.data["tasks"])

    def test_runtime_reviews_writes_as_ask(self) -> None:
        module = load_tool_module("tasks", "runtime")
        self.assertIsNotNone(module)
        context = RunContext(session=Session(), workspace=Workspace(root=Path.cwd()))

        decision = module.review(module.action_from_text('create "Relancer le dossier"'), context)
        listed = module.review(module.action_from_text("list"), context)

        self.assertEqual("ask", decision.verdict)
        self.assertEqual("allow", listed.verdict)


if __name__ == "__main__":
    unittest.main()
