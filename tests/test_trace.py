from __future__ import annotations

import unittest
from pathlib import Path

from bb9.core.kernel import Kernel
from bb9.core.loop import run_once
from bb9.core.models import Intention, RunContext, Session, TraceEvent, Workspace
from bb9.core.trace import decision_trace_artifact, tool_trace_artifact


class ToolTraceArtifactTests(unittest.TestCase):
    def test_builds_decision_trace_artifact_without_private_reasoning(self) -> None:
        artifact = decision_trace_artifact(
            (
                TraceEvent(event_type="intention", summary="fais un truc", session_id="s"),
                TraceEvent(event_type="decision", summary="Request shell: pwd", session_id="s", data={"kind": "action"}),
                TraceEvent(
                    event_type="guardian",
                    summary="read-only shell command allowed",
                    session_id="s",
                    data={"verdict": "allow", "action": "shell"},
                ),
                TraceEvent(event_type="action", summary="shell", session_id="s", data={"tool": "shell", "cmd": "pwd"}),
                TraceEvent(event_type="observation", summary="commande ok", session_id="s", data={"ok": True, "tool": "shell"}),
            )
        )

        self.assertIsNotNone(artifact)
        assert artifact is not None
        self.assertEqual("report", artifact.kind)
        self.assertEqual("Trace de décision", artifact.title)
        self.assertTrue(artifact.metadata["default_hidden"])
        self.assertEqual(4, artifact.metadata["count"])
        self.assertEqual("decision", artifact.metadata["entries"][0]["type"])
        self.assertIn("sans raisonnement privé", artifact.metadata["note"])

    def test_builds_tool_trace_artifact_from_action_observation_pairs(self) -> None:
        artifact = tool_trace_artifact(
            (
                TraceEvent(event_type="intention", summary="fais un truc", session_id="s"),
                TraceEvent(event_type="action", summary="shell", session_id="s", data={"tool": "shell", "cmd": "pwd"}),
                TraceEvent(event_type="observation", summary="commande ok", session_id="s", data={"ok": True, "tool": "shell"}),
                TraceEvent(event_type="observation", summary="réponse finale", session_id="s", data={"ok": True}),
                TraceEvent(event_type="action", summary="tasks", session_id="s", data={"tool": "tasks"}),
                TraceEvent(event_type="observation", summary="task refusée", session_id="s", data={"ok": False, "tool": "tasks"}),
            )
        )

        self.assertIsNotNone(artifact)
        assert artifact is not None
        self.assertEqual("tool_trace", artifact.kind)
        self.assertEqual("2 outils utilisés, 1 échec(s)", artifact.title)
        self.assertEqual(2, artifact.metadata["count"])
        self.assertEqual(1, artifact.metadata["failures"])
        self.assertEqual("shell", artifact.metadata["entries"][0]["tool"])
        self.assertEqual("pwd", artifact.metadata["entries"][0]["cmd"])
        self.assertEqual("tasks", artifact.metadata["entries"][1]["tool"])

    def test_returns_none_without_executed_tools(self) -> None:
        artifact = tool_trace_artifact(
            (
                TraceEvent(event_type="intention", summary="bonjour", session_id="s"),
                TraceEvent(event_type="observation", summary="salut", session_id="s", data={"ok": True}),
            )
        )

        self.assertIsNone(artifact)

    def test_redacts_shell_trace_metadata(self) -> None:
        artifact = tool_trace_artifact(
            (
                TraceEvent(
                    event_type="action",
                    summary="shell",
                    session_id="s",
                    data={"tool": "shell", "cmd": "echo OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz123456"},
                ),
                TraceEvent(
                    event_type="observation",
                    summary="OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz123456",
                    session_id="s",
                    data={"ok": True, "tool": "shell"},
                ),
            )
        )

        self.assertIsNotNone(artifact)
        assert artifact is not None
        entry = artifact.metadata["entries"][0]
        self.assertNotIn("sk-proj-", entry["cmd"])
        self.assertNotIn("sk-proj-", entry["summary"])
        self.assertIn("<secret-redacted>", entry["cmd"])
        self.assertIn("<secret-redacted>", entry["summary"])

    def test_run_once_emits_live_tool_events(self) -> None:
        class Provider:
            calls = 0

            def complete(self, _: str, **___: object) -> str:
                self.calls += 1
                if self.calls == 1:
                    return "BB9_ACTION shell pwd"
                return "J'ai vérifié le dossier courant."

        events: list[TraceEvent] = []
        context = RunContext(
            session=Session(id="session-1"),
            workspace=Workspace(root=Path.cwd()),
            permission_profile="limited",
        )

        result = run_once(
            Kernel(provider=Provider()),
            Intention("explore le projet"),
            context,
            on_event=events.append,
        )

        live_actions = [event for event in events if event.event_type == "action" and event.data.get("tool") == "shell"]
        tool_observations = [
            event for event in events if event.event_type == "observation" and event.data.get("tool") == "shell"
        ]
        self.assertTrue(result.observation.ok)
        self.assertEqual(1, len(live_actions))
        self.assertEqual(1, len(tool_observations))
        self.assertEqual("pwd", live_actions[0].data["cmd"])
        self.assertTrue(tool_observations[0].data["ok"])

    def test_provider_action_can_be_wrapped_in_inline_backticks(self) -> None:
        class Provider:
            calls = 0

            def complete(self, _: str, **___: object) -> str:
                self.calls += 1
                if self.calls == 1:
                    return "`BB9_ACTION shell pwd`"
                return "J'ai vérifié le dossier courant."

        events: list[TraceEvent] = []
        context = RunContext(
            session=Session(id="session-1"),
            workspace=Workspace(root=Path.cwd()),
            permission_profile="limited",
        )

        result = run_once(
            Kernel(provider=Provider()),
            Intention("explore le projet"),
            context,
            on_event=events.append,
        )

        live_actions = [event for event in events if event.event_type == "action" and event.data.get("tool") == "shell"]
        self.assertTrue(result.observation.ok)
        self.assertEqual(1, len(live_actions))
        self.assertEqual("pwd", live_actions[0].data["cmd"])

    def test_provider_placeholder_action_stays_ignored_with_backticks(self) -> None:
        class Provider:
            def complete(self, _: str, **___: object) -> str:
                return "`BB9_ACTION shell <commande>`"

        context = RunContext(
            session=Session(id="session-1"),
            workspace=Workspace(root=Path.cwd()),
            permission_profile="limited",
        )

        result = run_once(
            Kernel(provider=Provider()),
            Intention("explore le projet"),
            context,
        )

        self.assertTrue(result.observation.ok)
        self.assertIn("Action ignoree", result.observation.summary)


if __name__ == "__main__":
    unittest.main()
