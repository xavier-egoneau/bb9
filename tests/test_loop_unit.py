"""Unit tests for LoopState and extracted loop helpers."""

from __future__ import annotations

import unittest
from pathlib import Path

from bb9.core.loop import (
    LoopState,
    _handle_action_decision,
    _handle_answer_decision,
    _prepare_intention,
)
from bb9.core.models import (
    Action,
    Artifact,
    Decision,
    Intention,
    RunContext,
    Session,
    Workspace,
)
from bb9.core.trace import Trace


class LoopStateTests(unittest.TestCase):
    def test_default_values(self) -> None:
        state = LoopState(tool_budget=32)
        self.assertEqual(state.tool_budget, 32)
        self.assertEqual(state.tool_observations, [])
        self.assertEqual(state.tool_artifacts, [])
        self.assertFalse(state.final_retry_used)
        self.assertFalse(state.force_final_answer)
        self.assertEqual(state.unavailable_tools, set())
        self.assertEqual(state.failed_actions, set())
        self.assertEqual(state.recoverable_failed_actions, set())
        self.assertEqual(state.blocked_retry_counts, {})
        self.assertEqual(state.guardian_block_counts, {})
        self.assertEqual(state.denied_asks, {})
        self.assertEqual(state.runtime_guard_counts, {})

    def test_tool_limit_not_reached_initially(self) -> None:
        state = LoopState(tool_budget=32)
        self.assertFalse(state.tool_limit_reached)

    def test_tool_limit_reached_by_count(self) -> None:
        state = LoopState(tool_budget=2)
        state.tool_observations = [{"tool": "shell"}, {"tool": "files"}]
        self.assertTrue(state.tool_limit_reached)

    def test_tool_limit_reached_by_force(self) -> None:
        state = LoopState(tool_budget=32)
        state.force_final_answer = True
        self.assertTrue(state.tool_limit_reached)

    def test_tool_budget_remaining(self) -> None:
        state = LoopState(tool_budget=10)
        self.assertEqual(state.tool_budget_remaining(), 10)
        state.tool_observations = [{"tool": "shell"}] * 3
        self.assertEqual(state.tool_budget_remaining(), 7)

    def test_tool_budget_remaining_floor_zero(self) -> None:
        state = LoopState(tool_budget=5)
        state.tool_observations = [{"tool": "shell"}] * 10
        self.assertEqual(state.tool_budget_remaining(), 0)


class PrepareIntentionTests(unittest.TestCase):
    def test_injects_metadata(self) -> None:
        intention = Intention(text="hello", source="cli")
        state = LoopState(tool_budget=32)
        state.tool_observations = [{"tool": "shell", "ok": "True"}]
        state.force_final_answer = True

        result = _prepare_intention(intention, state)
        self.assertEqual(result.text, "hello")
        self.assertEqual(result.metadata["tool_budget"], 32)
        self.assertEqual(result.metadata["tool_budget_remaining"], 31)
        self.assertTrue(result.metadata["tool_limit_reached"])

    def test_preserves_existing_metadata(self) -> None:
        intention = Intention(text="hi", source="cli", metadata={"custom": "value"})
        state = LoopState(tool_budget=16)

        result = _prepare_intention(intention, state)
        self.assertEqual(result.metadata["custom"], "value")


class HandleAnswerDecisionTests(unittest.TestCase):
    def _make_helpers(self):
        trace = Trace("s1")
        trace_events: list = []

        def on_event(event):
            trace_events.append(event)

        return trace, trace_events, on_event

    def test_normal_answer_returns_result(self) -> None:
        trace, trace_events, on_event = self._make_helpers()
        decision = Decision(kind="answer", summary="Voici la reponse.")
        intention = Intention(text="question", source="cli")
        state = LoopState(tool_budget=32)

        result = _handle_answer_decision(decision, intention, state, trace, on_event)
        self.assertIsNotNone(result)
        if result is not None:
            self.assertEqual(result.observation.summary, "Voici la reponse.")
            self.assertTrue(result.observation.ok)

    def test_workspace_artifact_guard_blocks_text_only_answer(self) -> None:
        trace, trace_events, on_event = self._make_helpers()
        decision = Decision(kind="answer", summary="Voici un plan textuel.")
        intention = Intention(text="/open-ui-sketch", source="cli")
        state = LoopState(tool_budget=32)

        result = _handle_answer_decision(decision, intention, state, trace, on_event)
        self.assertIsNone(result)
        self.assertEqual(len(state.tool_observations), 1)
        self.assertEqual(state.tool_observations[0]["tool"], "runtime")
        self.assertIn("attend une livraison", state.tool_observations[0]["output"])

    def test_workspace_artifact_guard_passes_after_files_and_preview_success(self) -> None:
        trace, trace_events, on_event = self._make_helpers()
        decision = Decision(kind="answer", summary="Maquette créée : /api/file/public/sketches/demo/index.html")
        intention = Intention(text="/open-ui-sketch", source="cli")
        state = LoopState(tool_budget=32)
        state.tool_observations = [{"tool": "files", "ok": "True"}, {"tool": "browser", "ok": "True"}]

        result = _handle_answer_decision(decision, intention, state, trace, on_event)
        self.assertIsNotNone(result)
        if result is not None:
            self.assertTrue(result.observation.ok)

    def test_workspace_artifact_guard_blocks_stale_answer_after_failed_preview(self) -> None:
        trace, trace_events, on_event = self._make_helpers()
        decision = Decision(kind="answer", summary="Check vision fait sur la landing Planty. Verdict : bonne base.")
        intention = Intention(text="/open-ui-sketch fait moi 3 maquettes", source="cli")
        state = LoopState(tool_budget=32)
        state.tool_observations = [
            {"tool": "files", "ok": "True", "output": "File written: public/sketches/demo/index.html"},
            {"tool": "browser", "ok": "False", "output": "ERR_CONNECTION_REFUSED"},
        ]

        result = _handle_answer_decision(decision, intention, state, trace, on_event)

        self.assertIsNone(result)
        self.assertIn("intention courante", state.tool_observations[-1]["output"])
        self.assertIn("Ne continue pas le tour precedent", state.tool_observations[-1]["output"])

    def test_workspace_artifact_guard_requires_failed_preview_to_be_reported(self) -> None:
        trace, trace_events, on_event = self._make_helpers()
        decision = Decision(kind="answer", summary="Maquette créée : /api/file/public/sketches/demo/index.html")
        intention = Intention(text="/open-ui-sketch", source="cli")
        state = LoopState(tool_budget=32)
        state.tool_observations = [
            {"tool": "files", "ok": "True", "output": "File written: public/sketches/demo/index.html"},
            {"tool": "browser", "ok": "False", "output": "ERR_CONNECTION_REFUSED"},
        ]

        result = _handle_answer_decision(decision, intention, state, trace, on_event)

        self.assertIsNone(result)
        self.assertIn("preview navigateur", state.tool_observations[-1]["output"])

    def test_workspace_artifact_guard_allows_reported_failed_preview(self) -> None:
        trace, trace_events, on_event = self._make_helpers()
        decision = Decision(
            kind="answer",
            summary=(
                "Fichiers : /api/file/public/sketches/demo/index.html. "
                "Preview navigateur échouée : ERR_CONNECTION_REFUSED."
            ),
        )
        intention = Intention(text="/open-ui-sketch", source="cli")
        state = LoopState(tool_budget=32)
        state.tool_observations = [
            {"tool": "files", "ok": "True", "output": "File written: public/sketches/demo/index.html"},
            {"tool": "browser", "ok": "False", "output": "ERR_CONNECTION_REFUSED"},
        ]

        result = _handle_answer_decision(decision, intention, state, trace, on_event)

        self.assertIsNotNone(result)
        if result is not None:
            self.assertTrue(result.observation.ok)

    def test_workspace_artifact_guard_blocks_after_failed_files_attempt(self) -> None:
        trace, trace_events, on_event = self._make_helpers()
        decision = Decision(kind="answer", summary="<html>Demo</html>")
        intention = Intention(text="/open-ui-sketch", source="cli")
        state = LoopState(tool_budget=32)
        state.tool_observations = [{"tool": "files", "ok": "False"}]

        result = _handle_answer_decision(decision, intention, state, trace, on_event)

        self.assertIsNone(result)
        self.assertIn("attend une livraison", state.tool_observations[-1]["output"])

    def test_workspace_artifact_guard_requires_browser_attempt_after_files(self) -> None:
        trace, trace_events, on_event = self._make_helpers()
        decision = Decision(kind="answer", summary="Fichiers crees.")
        intention = Intention(text="/open-ui-sketch", source="cli")
        state = LoopState(tool_budget=32)
        state.tool_observations = [{"tool": "files", "ok": "True"}]

        result = _handle_answer_decision(decision, intention, state, trace, on_event)

        self.assertIsNone(result)
        self.assertIn("browser check", state.tool_observations[-1]["output"])

    def test_workspace_artifact_guard_blocks_raw_html_dump(self) -> None:
        trace, trace_events, on_event = self._make_helpers()
        decision = Decision(kind="answer", summary="<html><body>Demo</body></html>")
        intention = Intention(text="/open-ui-sketch", source="cli")
        state = LoopState(tool_budget=32)
        state.tool_observations = [{"tool": "files", "ok": "True"}, {"tool": "browser", "ok": "True"}]

        result = _handle_answer_decision(decision, intention, state, trace, on_event)

        self.assertIsNone(result)
        self.assertIn("ne doit pas coller", state.tool_observations[-1]["output"])

    def test_clarifying_question_passes_guard(self) -> None:
        trace, trace_events, on_event = self._make_helpers()
        decision = Decision(kind="answer", summary="Quelle palette de couleurs preferes-tu?")
        intention = Intention(text="/open-ui-sketch", source="cli")
        state = LoopState(tool_budget=32)

        result = _handle_answer_decision(decision, intention, state, trace, on_event)
        self.assertIsNotNone(result)
        if result is not None:
            self.assertTrue(result.observation.ok)

    def test_attaches_tool_artifacts_to_answer(self) -> None:
        trace, trace_events, on_event = self._make_helpers()
        decision = Decision(kind="answer", summary="Done.")
        intention = Intention(text="ok", source="cli")
        state = LoopState(tool_budget=32)
        state.tool_artifacts = [Artifact(kind="diff", title="diff.patch", path="/tmp/diff.patch")]

        result = _handle_answer_decision(decision, intention, state, trace, on_event)
        self.assertIsNotNone(result)
        if result is not None:
            self.assertEqual(len(result.observation.artifacts), 1)
            self.assertEqual(result.observation.artifacts[0].kind, "diff")


class HandleActionDecisionTests(unittest.TestCase):
    def _make_helpers(self, *, ask_user=None, should_cancel=None):
        context = RunContext(
            session=Session(id="s1"),
            workspace=Workspace(root=Path("/tmp")),
        )
        trace = Trace("s1")
        trace_events: list = []

        def on_event(event):
            trace_events.append(event)

        return context, trace, trace_events, on_event, ask_user, should_cancel

    def test_budget_exhausted_final_retry_used_returns_fallback(self) -> None:
        context, trace, events, on_event, _, _ = self._make_helpers()
        decision = Decision(
            kind="action",
            summary="Request shell: ls",
            action=Action(name="shell", risk="low", params={"cmd": "ls"}),
        )
        intention = Intention(text="list files", source="cli")
        state = LoopState(tool_budget=32)
        state.force_final_answer = True
        state.final_retry_used = True

        result = _handle_action_decision(decision, intention, context, state, trace, on_event, None, None)
        self.assertIsNotNone(result)
        if result is not None:
            self.assertTrue(result.observation.ok)

    def test_budget_exhausted_first_time_adds_observation_and_continues(self) -> None:
        context, trace, events, on_event, _, _ = self._make_helpers()
        decision = Decision(
            kind="action",
            summary="Request shell",
            action=Action(name="shell", risk="low", params={"cmd": "ls"}),
        )
        intention = Intention(text="list files", source="cli")
        state = LoopState(tool_budget=32)
        state.force_final_answer = True

        result = _handle_action_decision(decision, intention, context, state, trace, on_event, None, None)
        self.assertIsNone(result)
        self.assertTrue(state.final_retry_used)
        self.assertEqual(len(state.tool_observations), 1)
        self.assertIn("budget exhausted", state.tool_observations[0]["output"])

    def test_direct_action_skips_budget_exhausted_check(self) -> None:
        context, trace, events, on_event, _, _ = self._make_helpers()
        decision = Decision(
            kind="action",
            summary="Request shell",
            action=Action(name="shell", risk="low", params={"cmd": "ls"}),
        )
        intention = Intention(text="/action shell ls", source="cli")
        state = LoopState(tool_budget=32)
        state.force_final_answer = True
        state.final_retry_used = True

        result = _handle_action_decision(decision, intention, context, state, trace, on_event, None, None)
        self.assertIsNotNone(result)

    def test_unavailable_tool_is_blocked_with_force_final_answer(self) -> None:
        context, trace, events, on_event, _, _ = self._make_helpers()
        decision = Decision(
            kind="action",
            summary="Request shell",
            action=Action(name="shell", risk="low", params={"cmd": "ls"}),
        )
        intention = Intention(text="list files", source="cli")
        state = LoopState(tool_budget=32)
        state.unavailable_tools.add("shell")

        result = _handle_action_decision(decision, intention, context, state, trace, on_event, None, None)
        self.assertIsNone(result)
        self.assertTrue(state.force_final_answer)
        self.assertEqual(len(state.tool_observations), 1)
        self.assertIn("unavailable", state.tool_observations[0]["output"])

    def test_guardian_block_tracks_counts_and_forces_final_answer(self) -> None:
        context, trace, events, on_event, _, _ = self._make_helpers()
        decision = Decision(
            kind="action",
            summary="Request forbidden action",
            action=Action(name="shell", risk="forbidden", params={"cmd": "rm -rf /etc"}),
        )
        intention = Intention(text="delete protected files", source="cli")
        state = LoopState(tool_budget=32)

        result = _handle_action_decision(decision, intention, context, state, trace, on_event, None, None)
        self.assertIsNone(result)
        self.assertIn("shell", state.guardian_block_counts)
        self.assertEqual(state.guardian_block_counts["shell"], 1)
        self.assertFalse(state.force_final_answer)

    def test_direct_action_blocked_by_guardian_returns_immediately(self) -> None:
        context, trace, events, on_event, _, _ = self._make_helpers()
        decision = Decision(
            kind="action",
            summary="Request forbidden",
            action=Action(name="shell", risk="forbidden", params={"cmd": "rm -rf /"}),
        )
        intention = Intention(text="/action shell rm -rf /", source="cli")
        state = LoopState(tool_budget=32)

        result = _handle_action_decision(decision, intention, context, state, trace, on_event, None, None)
        self.assertIsNotNone(result)
        if result is not None:
            self.assertFalse(result.observation.ok)


if __name__ == "__main__":
    unittest.main()
