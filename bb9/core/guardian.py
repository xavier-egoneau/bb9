"""Action permission checks."""

from __future__ import annotations

from .models import Action, GuardianDecision, RunContext
from .tool_runtime import review_runtime_action

BLOCK_CATEGORY_SECURITY = "security"
BLOCK_CATEGORY_INVALID_ACTION = "invalid_action"
BLOCK_CATEGORY_UNSUPPORTED_SYNTAX = "unsupported_syntax"
BLOCK_CATEGORY_POLICY = "policy"


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


def block_category(decision: GuardianDecision) -> str:
    """Classify a guardian block for UI/trace diagnostics."""
    if decision.verdict != "block":
        return ""
    return block_category_from_reason(decision.reason)


def block_category_from_reason(reason: str) -> str:
    text = " ".join(str(reason or "").strip().lower().split())
    if not text:
        return BLOCK_CATEGORY_POLICY
    if _has_any(
        text,
        (
            "protected path",
            "local/private urls are blocked",
            "provider status text leaked",
            "secret",
        ),
    ):
        return BLOCK_CATEGORY_SECURITY
    if _has_any(
        text,
        (
            "unsupported",
            "shell=true is disabled",
            "heredoc",
            "compound shell command",
        ),
    ):
        return BLOCK_CATEGORY_UNSUPPORTED_SYNTAX
    if _has_any(
        text,
        (
            "invalid",
            "missing",
            "empty",
            "placeholder",
            "unterminated",
            "parse",
            "provider prose leaked",
            "only http(s) urls are allowed",
            "not read-only",
            "forbidden action risk",
        ),
    ):
        return BLOCK_CATEGORY_INVALID_ACTION
    return BLOCK_CATEGORY_POLICY


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)
