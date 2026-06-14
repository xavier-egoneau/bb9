from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bb9.core.models import RunContext, Session, Workspace
from bb9.core.tool_runtime import load_tool_module


class LocalAutoEditToolTests(unittest.TestCase):
    def test_parse_repeated_files_and_dry_run_review(self) -> None:
        module = load_tool_module("local_auto_edit", "runtime")
        assert module is not None
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            action = module.action_from_text('run prompt="Fix it" file=app.py file=tests/test_app.py')
            context = RunContext(session=Session(), workspace=Workspace(workspace), permission_profile="safe")

            decision = module.review(action, context)

        self.assertEqual("medium", action.risk)
        self.assertEqual(["app.py", "tests/test_app.py"], action.params["file"])
        self.assertEqual("allow", decision.verdict)

    def test_apply_requires_confirmation_in_safe_profile(self) -> None:
        module = load_tool_module("local_auto_edit", "runtime")
        assert module is not None
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            action = module.action_from_text('run prompt="Fix it" file=app.py apply=true')
            context = RunContext(session=Session(), workspace=Workspace(workspace), permission_profile="safe")

            decision = module.review(action, context)

        self.assertEqual("high", action.risk)
        self.assertEqual("ask", decision.verdict)

    def test_execute_builds_local_runtime_command(self) -> None:
        module = load_tool_module("local_auto_edit", "runtime")
        assert module is not None
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "repo"
            runtime_root = root / "runtime"
            workspace.mkdir()
            (runtime_root / "src").mkdir(parents=True)
            python = runtime_root / ".venv-sglang" / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.write_text("#!/bin/sh\n", encoding="utf-8")
            action = module.action_from_text(
                'run prompt="Fix it" file=app.py test_command="python3 -m unittest" '
                f"runtime_root={runtime_root} apply=true disable_thinking=true"
            )
            context = RunContext(session=Session(), workspace=Workspace(workspace), permission_profile="power")
            completed = SimpleNamespace(returncode=0, stdout="ok", stderr="")

            with patch.object(module.subprocess, "run") as run:
                run.return_value = completed
                observation = module.execute(action, context)

        self.assertTrue(observation.ok)
        argv = run.call_args.args[0]
        self.assertEqual(str(python), argv[0])
        self.assertIn("local_runtime.cli", argv)
        self.assertIn("--apply", argv)
        self.assertIn("--disable-thinking", argv)
        self.assertIn("--test-command", argv)


if __name__ == "__main__":
    unittest.main()
