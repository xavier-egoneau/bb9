from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bb9.core.memory import MemoryStore


class MemoryStoreTests(unittest.TestCase):
    def test_add_deduplicates_memory_nodes_by_scope_and_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(Path(tmp) / "memory.db")
            try:
                first = store.add("Xavier préfère les interfaces sobres.", tags="profile")
                second = store.add("Xavier préfère les interfaces sobres.", tags="profile")
                project = store.add(
                    "Xavier préfère les interfaces sobres.",
                    scope="project",
                    project_path=Path(tmp) / "project",
                )

                self.assertEqual(first, second)
                self.assertNotEqual(first, project)
            finally:
                store.close()

    def test_active_context_includes_global_and_current_project_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = MemoryStore(root / "memory.db")
            current_project = root / "current"
            other_project = root / "other"
            try:
                store.add("Mémoire globale", scope="global")
                store.add("Mémoire du projet courant", scope="project", project_path=current_project)
                store.add("Mémoire d'un autre projet", scope="project", project_path=other_project)

                entries = store.get_active_context(current_project)

                self.assertEqual(
                    {"Mémoire globale", "Mémoire du projet courant"},
                    {entry.content for entry in entries},
                )
            finally:
                store.close()

    def test_search_matches_content_tags_and_scope_filters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(Path(tmp) / "memory.db")
            try:
                store.add("La mémoire durable utilise SQLite.", tags="graph,sql")
                store.add("La session reste courte.", scope="project", project_path=Path(tmp) / "project")

                self.assertEqual("La mémoire durable utilise SQLite.", store.search("SQLite")[0].content)
                self.assertEqual("La mémoire durable utilise SQLite.", store.search("graph")[0].content)
                self.assertEqual([], store.search("session", scope="global"))
            finally:
                store.close()

    def test_edges_connect_memory_nodes_without_duplication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(Path(tmp) / "memory.db")
            try:
                source = store.add("BB9 garde la complexité dans Markdown.")
                target = store.add("Le dreaming consolide la mémoire.")

                first = store.add_edge(source, target, "supports", weight=0.8)
                second = store.add_edge(source, target, "supports", weight=0.8)
                edges = store.edges_for(source)
                related = store.related(source, relation="supports")

                self.assertEqual(first, second)
                self.assertEqual(1, len(edges))
                self.assertEqual(["Le dreaming consolide la mémoire."], [node.content for node in related])
            finally:
                store.close()

    def test_replace_and_remove_memory_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(Path(tmp) / "memory.db")
            try:
                node_id = store.add("Ancienne formulation")

                self.assertTrue(store.replace("Ancienne", "Nouvelle formulation"))
                self.assertEqual("Nouvelle formulation", store.get(node_id).content)
                self.assertTrue(store.remove(node_id))
                self.assertIsNone(store.get(node_id))
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
