"""Controlled execution boundary."""

from __future__ import annotations

from .models import Action, Observation
from .tool_runtime import execute_runtime_tool


def execute(action: Action) -> Observation:
    runtime_observation = execute_runtime_tool(action)
    if runtime_observation is not None:
        return runtime_observation
    return Observation(ok=False, summary=f"Tool not implemented: {action.name}")
