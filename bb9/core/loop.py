"""Minimal synchronous agent loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from .gateway import execute
from .guardian import review_action
from .hooks import after_action, before_action
from .kernel import Kernel
from .models import Action, GuardianDecision, Intention, Observation, PermissionProfile, RunContext, RunResult
from .trace import Trace


ApprovalResult = Literal["allow", "deny", "defer"]


@dataclass(frozen=True)
class ApprovalDecision:
    verdict: ApprovalResult
    action: Action | None = None
    summary: str = ""


ApprovalCallback = Callable[[GuardianDecision, RunContext], ApprovalResult | ApprovalDecision]

TOOL_BUDGETS: dict[PermissionProfile, int] = {
    "safe": 16,
    "limited": 32,
    "power": 64,
}


def run_once(
    kernel: Kernel,
    intention: Intention,
    context: RunContext,
    ask_user: ApprovalCallback | None = None,
) -> RunResult:
    trace = Trace(context.session.id)
    trace.add("intention", intention.text)

    tool_budget = tool_budget_for(
        context.permission_profile,
        context.agent.soul if context.agent is not None else "",
    )
    tool_observations: list[dict[str, str]] = []
    decision = None
    observation: Observation | None = None
    final_retry_used = False

    for step in range(tool_budget + 3):
        tool_limit_reached = len(tool_observations) >= tool_budget
        current_intention = Intention(
            text=intention.text,
            source=intention.source,
            metadata={
                **intention.metadata,
                "tool_observations": tuple(tool_observations),
                "tool_budget": tool_budget,
                "tool_budget_remaining": max(0, tool_budget - len(tool_observations)),
                "tool_limit_reached": tool_limit_reached,
            },
        )
        decision = kernel.decide(current_intention, context)
        trace.add("decision", decision.summary, {"kind": decision.kind, "step": step})

        if decision.kind == "answer":
            observation = Observation(ok=True, summary=decision.summary)
            trace.add("observation", observation.summary, {"ok": observation.ok})
            return RunResult(decision=decision, observation=observation, trace=trace.events)

        if decision.kind == "action" and decision.action is not None:
            if tool_limit_reached and not intention.text.strip().startswith("/action "):
                if final_retry_used:
                    observation = Observation(ok=False, summary="Tool budget reached before final answer.")
                    trace.add("stop", observation.summary)
                    return RunResult(decision=decision, observation=observation, trace=trace.events)
                final_retry_used = True
                tool_observations.append(
                    {
                        "cmd": str(decision.action.params.get("cmd", "")),
                        "ok": "False",
                        "output": (
                            "Internal tool budget exhausted. Do not request more commands. "
                            "Answer from the observations already available, without mentioning this internal budget."
                        ),
                    }
                )
                continue

            review = before_action(decision.action, context)
            guardian_decision = review_action(review.action, context)
            trace.add(
                "guardian",
                guardian_decision.reason,
                {
                    "verdict": guardian_decision.verdict,
                    "action": guardian_decision.action.name if guardian_decision.action else None,
                },
            )

            if guardian_decision.verdict == "ask" and ask_user is not None:
                approval = _normalize_approval(ask_user(guardian_decision, context))
                trace.add("guardian", f"user approval: {approval.verdict}", {"verdict": guardian_decision.verdict})
                if approval.verdict == "defer":
                    observation = Observation(ok=True, summary=approval.summary or "Action deferred.")
                    trace.add("observation", observation.summary, {"ok": observation.ok})
                    return RunResult(decision=decision, observation=observation, trace=trace.events)
                if approval.verdict == "allow":
                    guardian_decision = GuardianDecision(
                        verdict="allow",
                        reason=f"user approved: {guardian_decision.reason}",
                        action=approval.action or guardian_decision.action,
                    )

            if guardian_decision.verdict != "allow":
                observation = Observation(
                    ok=False,
                    summary=f"Action not executed: {guardian_decision.verdict}",
                )
                trace.add("observation", observation.summary, {"ok": observation.ok})
                if intention.text.strip().startswith("/action "):
                    return RunResult(decision=decision, observation=observation, trace=trace.events)
                tool_observations.append(
                    {
                        "cmd": str(decision.action.params.get("cmd", "")),
                        "ok": "False",
                        "output": f"Guardian refused ({guardian_decision.verdict}): {guardian_decision.reason}",
                    }
                )
                continue

            observation = execute(guardian_decision.action)
            observation = after_action(observation, context)
            trace.add("action", decision.action.name)
            trace.add("observation", observation.summary, {"ok": observation.ok})
            if intention.text.strip().startswith("/action "):
                return RunResult(decision=decision, observation=observation, trace=trace.events)
            tool_observations.append(
                {
                    "cmd": str(guardian_decision.action.params.get("cmd", "")),
                    "ok": str(observation.ok),
                    "output": observation.summary,
                }
            )
            continue

        observation = Observation(ok=True, summary=decision.summary)
        trace.add("stop", observation.summary)
        return RunResult(decision=decision, observation=observation, trace=trace.events)

    assert decision is not None
    observation = Observation(ok=False, summary="Tool step limit reached.")
    trace.add("stop", observation.summary)
    return RunResult(decision=decision, observation=observation, trace=trace.events)


def tool_budget_for(profile: PermissionProfile, soul: str = "") -> int:
    budget = TOOL_BUDGETS.get(profile, TOOL_BUDGETS["safe"])
    if _soul_asks_for_initiative(soul):
        return min(budget + _initiative_bonus(profile), TOOL_BUDGETS["power"])
    return budget


def _soul_asks_for_initiative(soul: str) -> bool:
    normalized = _normalize(soul)
    markers = (
        "debrouillard",
        "audacieux",
        "initiative",
        "explore",
        "lis le fichier",
        "verifie",
        "cherche",
    )
    return any(marker in normalized for marker in markers)


def _initiative_bonus(profile: PermissionProfile) -> int:
    if profile == "power":
        return 0
    if profile == "limited":
        return 8
    return 4


def _normalize(text: str) -> str:
    replacements = str.maketrans("àâäéèêëîïôöùûüç", "aaaeeeeiioouuuc")
    return " ".join(text.lower().translate(replacements).split())


def _normalize_approval(value: ApprovalResult | ApprovalDecision) -> ApprovalDecision:
    if isinstance(value, ApprovalDecision):
        return value
    return ApprovalDecision(verdict=value)
