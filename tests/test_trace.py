from __future__ import annotations

import unittest

from bb9.core.models import TraceEvent
from bb9.core.trace import tool_trace_artifact


class ToolTraceArtifactTests(unittest.TestCase):
    def test_builds_tool_trace_artifact_from_action_observation_pairs(self) -> None:
        artifact = tool_trace_artifact(
            (
                TraceEvent(event_type="intention", summary="fais un truc", session_id="s"),
                TraceEvent(event_type="action", summary="shell", session_id="s", data={"tool": "shell"}),
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
        self.assertEqual("tasks", artifact.metadata["entries"][1]["tool"])

    def test_returns_none_without_executed_tools(self) -> None:
        artifact = tool_trace_artifact(
            (
                TraceEvent(event_type="intention", summary="bonjour", session_id="s"),
                TraceEvent(event_type="observation", summary="salut", session_id="s", data={"ok": True}),
            )
        )

        self.assertIsNone(artifact)


if __name__ == "__main__":
    unittest.main()
