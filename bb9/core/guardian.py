"""Action permission checks."""

from __future__ import annotations

from .models import Action, GuardianDecision, RunContext
from .tool_runtime import review_runtime_action


def review_action(action: Action, context: RunContext) -> GuardianDecision:
    profile = context.permission_profile
    runtime_decision = review_runtime_action(action, context)
    if runtime_decision is not None:
        return runtime_decision

    if action.risk == "forbidden":
        return GuardianDecision(verdict="block", reason="forbidden action risk", action=action)
    if action.risk == "low":
        return GuardianDecision(verdict="allow", reason="low risk action", action=action)
    if action.risk == "medium" and profile in {"limited", "power"}:
        return GuardianDecision(verdict="allow", reason=f"allowed by {profile} profile", action=action)
    if action.risk == "high" and profile == "power":
        return GuardianDecision(verdict="ask", reason="high risk action requires confirmation", action=action)
    return GuardianDecision(verdict="ask", reason="confirmation required", action=action)
