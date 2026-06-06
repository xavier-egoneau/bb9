from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from bb9.core.diffs import capture_worktree_snapshot, diff_artifact_since


class DiffArtifactTests(unittest.TestCase):
    def test_creates_diff_artifact_for_files_changed_since_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            home = root / "home"
            repo.mkdir()
            _git(repo, "init")
            (repo / "README.md").write_text("hello\n", encoding="utf-8")
            _git(repo, "add", "README.md")
            _git(repo, "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init")

            previous_home = os.environ.get("BB9_HOME")
            os.environ["BB9_HOME"] = str(home)
            try:
                snapshot = capture_worktree_snapshot(repo)
                (repo / "README.md").write_text("hello\nworld\n", encoding="utf-8")
                (repo / "new.md").write_text("new\nfile\n", encoding="utf-8")

                artifact = diff_artifact_since(snapshot, workspace=repo)
            finally:
                if previous_home is None:
                    os.environ.pop("BB9_HOME", None)
                else:
                    os.environ["BB9_HOME"] = previous_home

            self.assertIsNotNone(artifact)
            assert artifact is not None
            self.assertEqual("diff", artifact.kind)
            self.assertEqual(2, artifact.metadata["files_changed"])
            self.assertEqual(3, artifact.metadata["insertions"])
            self.assertEqual(0, artifact.metadata["deletions"])
            self.assertTrue(artifact.metadata["default_collapsed"])
            paths = {entry["path"] for entry in artifact.metadata["files"]}
            self.assertEqual({"README.md", "new.md"}, paths)
            self.assertTrue(Path(artifact.path).is_file())

    def test_ignores_dirty_files_unchanged_during_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _git(repo, "init")
            (repo / "README.md").write_text("hello\n", encoding="utf-8")
            _git(repo, "add", "README.md")
            _git(repo, "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init")
            (repo / "README.md").write_text("dirty before\n", encoding="utf-8")

            snapshot = capture_worktree_snapshot(repo)

            self.assertIsNone(diff_artifact_since(snapshot, workspace=repo))


def _git(cwd: Path, *args: str) -> None:
    result = subprocess.run(
        ("git", *args),
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
