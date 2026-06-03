from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bb9.cli import goal as goal_cli
from bb9.core.goals import EvaluatorAgent, GoalManager


class GoalManagerTests(unittest.TestCase):
    def test_create_goal_persists_structured_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "goal.json"
            manager = GoalManager(path)

            goal = manager.create_goal("Corrige tous les tests jusqu'a ce que npm test passe")

            self.assertEqual("active", goal.status)
            self.assertEqual(["npm test"], goal.verification_steps)
            self.assertEqual(goal.id, manager.get_active_goal().id)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("successConditions", raw)
            self.assertIn("maxIterations", raw)
            self.assertIn("history", raw)

    def test_pause_resume_cancel_clear_goal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = GoalManager(Path(tmp) / "goal.json")
            goal = manager.create_goal("Verifier avec python3 -m unittest discover")

            self.assertEqual("paused", manager.pause_goal(goal.id).status)
            self.assertIsNone(manager.get_active_goal())
            self.assertEqual("active", manager.resume_goal(goal.id).status)
            self.assertEqual("cancelled", manager.cancel_goal(goal.id).status)
            manager.clear_goal(goal.id)
            self.assertIsNone(manager.get_current_goal())


class EvaluatorAgentTests(unittest.TestCase):
    def test_evaluator_requires_concrete_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            goal = GoalManager(Path(tmp) / "goal.json").create_goal("Refactorise le module auth")

            result = EvaluatorAgent().evaluate(goal, observations=["travail fait"], verification_result="")

            self.assertFalse(result.goal_reached)
            self.assertEqual("ask_user", result.decision)

    def test_evaluator_stops_success_when_all_checks_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            goal = GoalManager(Path(tmp) / "goal.json").create_goal("Corrige jusqu'a npm test passe")

            result = EvaluatorAgent().evaluate(
                goal,
                observations=["tests ok"],
                verification_result="PASS npm test: 42 passed",
            )

            self.assertTrue(result.goal_reached)
            self.assertEqual("stop_success", result.decision)

    def test_evaluator_continues_when_any_check_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            goal = GoalManager(Path(tmp) / "goal.json").create_goal("Corrige jusqu'a npm test passe")

            result = EvaluatorAgent().evaluate(
                goal,
                observations=["tests ko"],
                verification_result="FAIL npm test: 1 failed",
            )

            self.assertFalse(result.goal_reached)
            self.assertEqual("continue", result.decision)


class GoalCliTests(unittest.TestCase):
    def test_goal_cli_handles_status_without_cli_owning_goal_logic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            messages: list[str] = []

            class FakeCli:
                goal_manager = GoalManager(Path(tmp) / "goal.json")

                def build_goal_context(self):
                    raise AssertionError("status should not build a context")

                def build_goal_provider(self):
                    raise AssertionError("status should not build a provider")

                def ask_guardian(self, *_):
                    raise AssertionError("status should not ask guardian")

                def remember_turn(self, *_):
                    raise AssertionError("status should not persist a turn")

            handled = goal_cli.handle(FakeCli(), "status", write=messages.append)

        self.assertTrue(handled)
        self.assertEqual(["Aucun goal courant."], messages)


if __name__ == "__main__":
    unittest.main()
