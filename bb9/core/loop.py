"""Minimal synchronous agent loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from .gateway import execute
from .guardian import review_action
from .hooks import after_action, before_action
from .kernel import Kernel
from .models import Action, GuardianDecision, Intention, Observation, PermissionProfile, RunContext, RunResult, TraceEvent
from .trace import Trace


ApprovalResult = Literal["allow", "deny", "defer"]


@dataclass(frozen=True)
class ApprovalDecision:
    verdict: ApprovalResult
    action: Action | None = None
    summary: str = ""


ApprovalCallback = Callable[[GuardianDecision, RunContext], ApprovalResult | ApprovalDecision]
TraceCallback = Callable[[TraceEvent], None]

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
    on_event: TraceCallback | None = None,
) -> RunResult:
    trace = Trace(context.session.id)
    _emit(trace.add("intention", intention.text), on_event)

    tool_budget = tool_budget_for(
        context.permission_profile,
        context.agent.soul if context.agent is not None else "",
    )
    tool_observations: list[dict[str, str]] = []
    decision = None
    observation: Observation | None = None
    final_retry_used = False
    force_final_answer = False
    unavailable_tools: set[str] = set()
    failed_actions: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    recoverable_failed_actions: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    blocked_retry_counts: dict[tuple[str, tuple[tuple[str, str], ...]], int] = {}

    for step in range(tool_budget + 3):
        tool_limit_reached = force_final_answer or len(tool_observations) >= tool_budget
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
        _emit(trace.add("decision", decision.summary, {"kind": decision.kind, "step": step}), on_event)

        if decision.kind == "answer":
            observation = Observation(ok=True, summary=decision.summary)
            _emit(trace.add("observation", observation.summary, {"ok": observation.ok}), on_event)
            return RunResult(decision=decision, observation=observation, trace=trace.events)

        if decision.kind == "action" and decision.action is not None:
            if tool_limit_reached and not intention.text.strip().startswith("/action "):
                if final_retry_used:
                    observation = Observation(ok=True, summary=_fallback_final_answer(tool_observations))
                    _emit(trace.add("stop", observation.summary), on_event)
                    return RunResult(decision=decision, observation=observation, trace=trace.events)
                final_retry_used = True
                tool_observations.append(
                    {
                        "tool": decision.action.name,
                        "cmd": str(decision.action.params.get("cmd", "")),
                        "ok": "False",
                        "output": (
                            "Internal tool budget exhausted. Do not request more commands. "
                            "Answer from the observations already available, without mentioning this internal budget."
                        ),
                    }
                )
                continue

            requested_tool = decision.action.name
            requested_signature = _action_signature(decision.action)
            is_repeated_failed_action = requested_signature in failed_actions
            if (
                not intention.text.strip().startswith("/action ")
                and (requested_tool in unavailable_tools or is_repeated_failed_action)
            ):
                blocked_retry_counts[requested_signature] = blocked_retry_counts.get(requested_signature, 0) + 1
                allow_recovery_step = (
                    is_repeated_failed_action
                    and requested_signature in recoverable_failed_actions
                    and requested_tool not in unavailable_tools
                    and blocked_retry_counts[requested_signature] == 1
                )
                reason = (
                    f"Tool {requested_tool} unavailable for this turn; do not retry it. "
                    "Answer from the observations already available."
                    if requested_tool in unavailable_tools
                    else (
                        f"This exact {requested_tool} action already failed in this turn; do not retry it. "
                        + (
                            "Use a different action that can change the situation, or answer from the observations already available."
                            if allow_recovery_step
                            else "Answer from the observations already available."
                        )
                    )
                )
                observation = Observation(ok=False, summary=reason)
                _emit(trace.add("observation", observation.summary, {"ok": observation.ok, "tool": requested_tool}), on_event)
                tool_observations.append(
                    {
                        "tool": requested_tool,
                        "cmd": str(decision.action.params.get("cmd", "")),
                        "ok": "False",
                        "output": reason,
                    }
                )
                if not allow_recovery_step:
                    force_final_answer = True
                continue

            review = before_action(decision.action, context)
            guardian_decision = review_action(review.action, context)
            _emit(
                trace.add(
                    "guardian",
                    guardian_decision.reason,
                    {
                        "verdict": guardian_decision.verdict,
                        "action": guardian_decision.action.name if guardian_decision.action else None,
                    },
                ),
                on_event,
            )

            if guardian_decision.verdict == "ask" and ask_user is not None:
                approval = _normalize_approval(ask_user(guardian_decision, context))
                _emit(
                    trace.add("guardian", f"user approval: {approval.verdict}", {"verdict": guardian_decision.verdict}),
                    on_event,
                )
                if approval.verdict == "defer":
                    observation = Observation(ok=True, summary=approval.summary or "Action deferred.")
                    _emit(trace.add("observation", observation.summary, {"ok": observation.ok}), on_event)
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
                _emit(trace.add("observation", observation.summary, {"ok": observation.ok}), on_event)
                if intention.text.strip().startswith("/action "):
                    return RunResult(decision=decision, observation=observation, trace=trace.events)
                tool_observations.append(
                    {
                        "tool": decision.action.name,
                        "cmd": str(decision.action.params.get("cmd", "")),
                        "ok": "False",
                        "output": f"Guardian refused ({guardian_decision.verdict}): {guardian_decision.reason}",
                    }
                )
                continue

            tool_name = guardian_decision.action.name
            action_data = {"tool": tool_name}
            if tool_name == "shell":
                action_data["cmd"] = str(guardian_decision.action.params.get("cmd", ""))
            _emit(trace.add("action", decision.action.name, action_data), on_event)
            observation = execute(guardian_decision.action)
            observation = after_action(observation, context)
            _emit(
                trace.add(
                    "observation",
                    observation.summary,
                    {"ok": observation.ok, "tool": tool_name},
                ),
                on_event,
            )
            if intention.text.strip().startswith("/action "):
                return RunResult(decision=decision, observation=observation, trace=trace.events)
            if not observation.ok:
                if _blocks_exact_retry(tool_name, observation):
                    signature = _action_signature(guardian_decision.action)
                    failed_actions.add(signature)
                    if _is_recoverable_tool_failure(tool_name, observation):
                        recoverable_failed_actions.add(signature)
                if _is_structural_tool_failure(tool_name, observation):
                    unavailable_tools.add(tool_name)
                    force_final_answer = True
            tool_observations.append(
                {
                    "tool": tool_name,
                    "cmd": str(guardian_decision.action.params.get("cmd", "")),
                    "ok": str(observation.ok),
                    "output": observation.summary,
                }
            )
            continue

        observation = Observation(ok=True, summary=decision.summary)
        _emit(trace.add("stop", observation.summary), on_event)
        return RunResult(decision=decision, observation=observation, trace=trace.events)

    assert decision is not None
    observation = Observation(ok=False, summary="Tool step limit reached.")
    _emit(trace.add("stop", observation.summary), on_event)
    return RunResult(decision=decision, observation=observation, trace=trace.events)


def _emit(event: TraceEvent, callback: TraceCallback | None) -> None:
    if callback is not None:
        callback(event)


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


def _action_signature(action: Action) -> tuple[str, tuple[tuple[str, str], ...]]:
    return (
        action.name,
        tuple(sorted((str(key), str(value)) for key, value in action.params.items())),
    )


def _is_structural_tool_failure(tool: str, observation: Observation) -> bool:
    if observation.ok:
        return False
    summary = observation.summary.lower()
    if tool == "browser":
        return "playwright missing" in summary or "could not start playwright chromium" in summary
    return False


def _blocks_exact_retry(tool: str, observation: Observation) -> bool:
    if observation.ok:
        return False
    summary = observation.summary.lower()
    if tool == "browser" and "no page open" in summary:
        return False
    return True


def _is_recoverable_tool_failure(tool: str, observation: Observation) -> bool:
    if observation.ok:
        return False
    if tool != "browser":
        return False
    summary = observation.summary.lower()
    url = str(observation.data.get("url", "") if isinstance(observation.data, dict) else "").lower()
    combined = f"{summary} {url}"
    if not any(host in combined for host in ("127.0.0.1", "localhost", "::1")):
        return False
    recoverable_markers = (
        "browser navigation failed",
        "err_empty_response",
        "err_connection_refused",
        "err_connection_reset",
        "err_address_unreachable",
    )
    return any(marker in combined for marker in recoverable_markers)


def _fallback_final_answer(tool_observations: list[dict[str, str]]) -> str:
    failed = [
        item
        for item in tool_observations
        if item.get("ok") == "False" and not str(item.get("output") or "").startswith("Internal tool budget exhausted.")
    ]
    if failed:
        last = failed[-1]
        tool = last.get("tool") or "tool"
        output = last.get("output") or "action non finalisee"
        return f"Je m'arrête ici : `{tool}` n'a pas pu aller plus loin. Dernière observation utile : {output}"
    if tool_observations:
        return "Je m'arrête ici avec les observations disponibles, sans relancer de tool."
    return "Je m'arrête ici : aucun résultat exploitable n'a été produit."
