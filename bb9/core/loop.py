"""Minimal synchronous agent loop."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from .gateway import execute
from .guardian import review_action
from .hooks import after_action, before_action
from .kernel import Kernel
from .models import (
    Action,
    Artifact,
    Decision,
    GuardianDecision,
    Intention,
    Observation,
    PermissionProfile,
    RunContext,
    RunResult,
    TraceEvent,
)
from .trace import Trace

ApprovalResult = Literal["allow", "deny", "defer"]


@dataclass(frozen=True)
class ApprovalDecision:
    verdict: ApprovalResult
    action: Action | None = None
    summary: str = ""


ApprovalCallback = Callable[[GuardianDecision, RunContext], ApprovalResult | ApprovalDecision]
TraceCallback = Callable[[TraceEvent], None]
CancelCallback = Callable[[], bool]


class RunCancelled(RuntimeError):
    pass

TOOL_BUDGETS: dict[PermissionProfile, int] = {
    "safe": 16,
    "limited": 32,
    "power": 64,
}

ActionSignature = tuple[str, tuple[tuple[str, str], ...]]


@dataclass
class LoopState:
    tool_budget: int
    tool_observations: list[dict[str, str]] = field(default_factory=list)
    tool_artifacts: list[Artifact] = field(default_factory=list)
    final_retry_used: bool = False
    force_final_answer: bool = False
    unavailable_tools: set[str] = field(default_factory=set)
    failed_actions: set[ActionSignature] = field(default_factory=set)
    recoverable_failed_actions: set[ActionSignature] = field(default_factory=set)
    blocked_retry_counts: dict[ActionSignature, int] = field(default_factory=dict)
    guardian_block_counts: dict[str, int] = field(default_factory=dict)
    denied_asks: dict[str, int] = field(default_factory=dict)

    @property
    def tool_limit_reached(self) -> bool:
        return self.force_final_answer or len(self.tool_observations) >= self.tool_budget

    def tool_budget_remaining(self) -> int:
        return max(0, self.tool_budget - len(self.tool_observations))


def run_once(
    kernel: Kernel,
    intention: Intention,
    context: RunContext,
    ask_user: ApprovalCallback | None = None,
    on_event: TraceCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> RunResult:
    trace = Trace(context.session.id)
    _raise_if_cancelled(should_cancel)
    _emit(trace.add("intention", intention.text), on_event)
    state = LoopState(
        tool_budget=tool_budget_for(
            context.permission_profile,
            context.agent.soul if context.agent is not None else "",
        ),
    )

    return _run_loop(kernel, intention, context, state, trace, ask_user, on_event, should_cancel)


def continue_after_approved_action(
    kernel: Kernel,
    intention: Intention,
    context: RunContext,
    action: Action,
    observation: Observation,
    *,
    initial_trace: tuple[TraceEvent, ...] = (),
    ask_user: ApprovalCallback | None = None,
    on_event: TraceCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> RunResult:
    trace = Trace(context.session.id)
    _raise_if_cancelled(should_cancel)
    state = LoopState(
        tool_budget=tool_budget_for(
            context.permission_profile,
            context.agent.soul if context.agent is not None else "",
        ),
    )
    state.tool_artifacts.extend(observation.artifacts)
    state.tool_observations.append(
        {
            "tool": action.name,
            "cmd": str(action.params.get("cmd", action.params.get("path", ""))),
            "ok": str(observation.ok),
            "output": observation.summary,
        }
    )
    result = _run_loop(kernel, intention, context, state, trace, ask_user, on_event, should_cancel)
    return RunResult(
        decision=result.decision,
        observation=result.observation,
        trace=(*initial_trace, *result.trace),
    )


def _run_loop(
    kernel: Kernel,
    intention: Intention,
    context: RunContext,
    state: LoopState,
    trace: Trace,
    ask_user: ApprovalCallback | None,
    on_event: TraceCallback | None,
    should_cancel: CancelCallback | None,
) -> RunResult:
    decision: Decision | None = None

    for step in range(state.tool_budget + 3):
        _raise_if_cancelled(should_cancel)
        current_intention = _prepare_intention(intention, state)
        decision = kernel.decide(current_intention, context)
        _emit(trace.add("decision", decision.summary, {"kind": decision.kind, "step": step}), on_event)
        _raise_if_cancelled(should_cancel)

        if decision.kind == "answer":
            result = _handle_answer_decision(decision, intention, state, trace, on_event)
            if result is not None:
                return result
            continue

        if decision.kind == "action" and decision.action is not None:
            result = _handle_action_decision(
                decision, intention, context, state, trace, on_event, ask_user, should_cancel
            )
            if result is not None:
                return result
            continue

        observation = _with_tool_artifacts(Observation(ok=True, summary=decision.summary), state.tool_artifacts)
        _emit(trace.add("stop", observation.summary), on_event)
        return RunResult(decision=decision, observation=observation, trace=trace.events)

    assert decision is not None
    observation = _with_tool_artifacts(Observation(ok=False, summary="Tool step limit reached."), state.tool_artifacts)
    _emit(trace.add("stop", observation.summary), on_event)
    return RunResult(decision=decision, observation=observation, trace=trace.events)


def _prepare_intention(intention: Intention, state: LoopState) -> Intention:
    return Intention(
        text=intention.text,
        source=intention.source,
        metadata={
            **intention.metadata,
            "tool_observations": tuple(state.tool_observations),
            "tool_budget": state.tool_budget,
            "tool_budget_remaining": state.tool_budget_remaining(),
            "tool_limit_reached": state.tool_limit_reached,
        },
    )


def _handle_answer_decision(
    decision: Decision,
    intention: Intention,
    state: LoopState,
    trace: Trace,
    on_event: TraceCallback | None,
) -> RunResult | None:
    if _needs_workspace_artifact(intention.text):
        guard_message = None
        if not _has_successful_files_attempt(state.tool_observations):
            guard_message = _workspace_artifact_guard_message(intention.text, decision.summary)
        elif _looks_like_raw_artifact_dump(decision.summary):
            guard_message = _raw_artifact_dump_guard_message(intention.text)
        elif not _has_browser_attempt(state.tool_observations):
            guard_message = _workspace_preview_guard_message(intention.text)
        if guard_message is not None:
            observation = Observation(ok=False, summary=guard_message)
            _emit(
                trace.add("observation", observation.summary, {"ok": observation.ok, "tool": "runtime"}),
                on_event,
            )
            state.tool_observations.append(
                {
                    "tool": "runtime",
                    "cmd": _first_token(intention.text),
                    "ok": "False",
                    "output": guard_message,
                }
            )
            return None
    if _looks_like_vision_refusal(decision.summary) and not _has_vision_attempt(state.tool_observations):
        hint = _vision_tool_hint(intention.text)
        observation = Observation(ok=False, summary=hint)
        _emit(
            trace.add("observation", observation.summary, {"ok": observation.ok, "tool": "runtime"}),
            on_event,
        )
        state.tool_observations.append(
            {
                "tool": "runtime",
                "cmd": _first_token(intention.text),
                "ok": "False",
                "output": hint,
            }
        )
        return None
    observation = _with_tool_artifacts(Observation(ok=True, summary=decision.summary), state.tool_artifacts)
    _emit(trace.add("observation", observation.summary, {"ok": observation.ok}), on_event)
    return RunResult(decision=decision, observation=observation, trace=trace.events)


def _handle_action_decision(
    decision: Decision,
    intention: Intention,
    context: RunContext,
    state: LoopState,
    trace: Trace,
    on_event: TraceCallback | None,
    ask_user: ApprovalCallback | None,
    should_cancel: CancelCallback | None,
) -> RunResult | None:
    action = decision.action
    if action is None:
        return None

    is_direct = intention.text.strip().startswith("/action ")

    if state.tool_limit_reached and not is_direct:
        if state.final_retry_used:
            observation = _with_tool_artifacts(
                Observation(ok=True, summary=_fallback_final_answer(state.tool_observations)),
                state.tool_artifacts,
            )
            _emit(trace.add("stop", observation.summary), on_event)
            return RunResult(decision=decision, observation=observation, trace=trace.events)
        state.final_retry_used = True
        state.tool_observations.append(
            {
                "tool": action.name,
                "cmd": str(action.params.get("cmd", "")),
                "ok": "False",
                "output": (
                    "Internal tool budget exhausted. Do not request more commands. "
                    "Answer from the observations already available, without mentioning this internal budget."
                ),
            }
        )
        return None

    requested_tool = action.name
    requested_signature = _action_signature(action)
    is_repeated_failed_action = requested_signature in state.failed_actions

    if not is_direct and (requested_tool in state.unavailable_tools or is_repeated_failed_action):
        state.blocked_retry_counts[requested_signature] = state.blocked_retry_counts.get(requested_signature, 0) + 1
        allow_recovery_step = (
            is_repeated_failed_action
            and requested_signature in state.recoverable_failed_actions
            and requested_tool not in state.unavailable_tools
            and state.blocked_retry_counts[requested_signature] == 1
        )
        reason = (
            f"Tool {requested_tool} unavailable for this turn; do not retry it. "
            "Answer from the observations already available."
            if requested_tool in state.unavailable_tools
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
        state.tool_observations.append(
            {"tool": requested_tool, "cmd": str(action.params.get("cmd", "")), "ok": "False", "output": reason}
        )
        if not allow_recovery_step:
            state.force_final_answer = True
        return None

    review = before_action(action, context)
    guardian_decision = review_action(review.action, context)
    _emit(
        trace.add("guardian", guardian_decision.reason,
                   {"verdict": guardian_decision.verdict,
                    "action": guardian_decision.action.name if guardian_decision.action else None}),
        on_event,
    )

    if guardian_decision.verdict == "ask" and ask_user is not None:
        approval = _normalize_approval(ask_user(guardian_decision, context))
        _emit(trace.add("guardian", f"user approval: {approval.verdict}", {"verdict": guardian_decision.verdict}), on_event)
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
        elif approval.verdict == "deny":
            state.denied_asks[action.name] = state.denied_asks.get(action.name, 0) + 1
            if state.denied_asks[action.name] >= 3:
                state.force_final_answer = True

    if guardian_decision.verdict != "allow":
        observation = Observation(ok=False, summary=f"Action not executed: {guardian_decision.verdict}")
        _emit(trace.add("observation", observation.summary, {"ok": observation.ok}), on_event)
        if is_direct:
            return RunResult(decision=decision, observation=observation, trace=trace.events)
        state.tool_observations.append(
            {
                "tool": action.name,
                "cmd": str(action.params.get("cmd", "")),
                "ok": "False",
                "output": f"Guardian refused ({guardian_decision.verdict}): {guardian_decision.reason}",
            }
        )
        if guardian_decision.verdict == "block":
            state.guardian_block_counts[action.name] = state.guardian_block_counts.get(action.name, 0) + 1
            state.failed_actions.add(_action_signature(action))
            if state.guardian_block_counts[action.name] >= 2:
                state.force_final_answer = True
        return None

    action_for_tracking = guardian_decision.action or action
    tool_name = action_for_tracking.name
    _raise_if_cancelled(should_cancel)
    observation = _execute_allowed_action(guardian_decision, context, trace, on_event)
    _raise_if_cancelled(should_cancel)

    if is_direct:
        return RunResult(decision=decision, observation=observation, trace=trace.events)

    state.tool_artifacts.extend(observation.artifacts)
    if not observation.ok:
        policy = observation.retry_policy
        if policy == "block_tool":
            state.unavailable_tools.add(tool_name)
            state.force_final_answer = True
        elif policy == "block_exact":
            state.failed_actions.add(_action_signature(action_for_tracking))
        elif policy == "recoverable":
            signature = _action_signature(action_for_tracking)
            state.failed_actions.add(signature)
            state.recoverable_failed_actions.add(signature)
    state.tool_observations.append(
        {
            "tool": tool_name,
            "cmd": str(action_for_tracking.params.get("cmd", "")),
            "ok": str(observation.ok),
            "output": observation.summary,
        }
    )
    return None


def execute_approved_action(
    guardian_decision: GuardianDecision,
    context: RunContext,
    on_event: TraceCallback | None = None,
) -> tuple[Observation, tuple[TraceEvent, ...]]:
    trace = Trace(context.session.id)
    observation = _execute_allowed_action(guardian_decision, context, trace, on_event)
    return observation, trace.events


def _emit(event: TraceEvent, callback: TraceCallback | None) -> None:
    if callback is not None:
        callback(event)


def _raise_if_cancelled(should_cancel: CancelCallback | None) -> None:
    if should_cancel is not None and should_cancel():
        raise RunCancelled("Run cancelled.")


def _execute_allowed_action(
    guardian_decision: GuardianDecision,
    context: RunContext,
    trace: Trace,
    on_event: TraceCallback | None,
) -> Observation:
    action = guardian_decision.action
    if action is None:
        observation = Observation(ok=False, summary="Action not executed: missing action.")
        _emit(trace.add("observation", observation.summary, {"ok": observation.ok}), on_event)
        return observation

    tool_name = action.name
    action_data = {"tool": tool_name}
    if tool_name == "shell":
        action_data["cmd"] = str(action.params.get("cmd", ""))
    _emit(trace.add("action", action.name, action_data), on_event)
    observation = after_action(execute(action, context), context)
    _emit(
        trace.add(
            "observation",
            observation.summary,
            {"ok": observation.ok, "tool": tool_name},
        ),
        on_event,
    )
    return observation


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


def _with_tool_artifacts(observation: Observation, artifacts: list[Artifact]) -> Observation:
    if not artifacts:
        return observation
    return Observation(
        ok=observation.ok,
        summary=observation.summary,
        data=observation.data,
        artifacts=(*artifacts, *observation.artifacts),
        retry_policy=observation.retry_policy,
    )


def _first_token(text: str) -> str:
    return text.strip().split(maxsplit=1)[0] if text.strip() else ""


def _needs_workspace_artifact(text: str) -> bool:
    return _first_token(text).lower() in {"/open-ui-sketch"}


def _has_successful_files_attempt(tool_observations: list[dict[str, str]]) -> bool:
    return any(item.get("tool") == "files" and item.get("ok") == "True" for item in tool_observations)


def _has_browser_attempt(tool_observations: list[dict[str, str]]) -> bool:
    return any(item.get("tool") == "browser" for item in tool_observations)


def _workspace_artifact_guard_message(intention: str, answer: str) -> str | None:
    if _looks_like_clarifying_question(answer):
        return None
    command = _first_token(intention) or "cette commande"
    return (
        f"{command} attend une livraison dans le workspace, pas une proposition textuelle seule. "
        "Utilise d'abord `BB9_ACTION files write ...` pour creer les maquettes HTML/CSS "
        "dans `public/sketches/<slug>/` (par exemple `index.html` et `style.css`). "
        "N'utilise pas `shell mkdir` pour preparer le dossier : `files write` cree les dossiers parents. "
        "Pour un gros contenu, utilise `text=\"\"\"...\"\"\"` ou `b64=...`; n'utilise pas un script shell de contournement. "
        "Ne reponds en texte qu'apres avoir tente une ecriture fichier, ou pose au maximum "
        "3 questions courtes si une information bloque vraiment l'execution."
    )


def _workspace_preview_guard_message(intention: str) -> str:
    command = _first_token(intention) or "cette commande"
    return (
        f"{command} doit livrer une maquette visualisable, pas seulement des fichiers. "
        "Maintenant que les fichiers sont ecrits, utilise `BB9_ACTION browser check ... screenshot=true` "
        "sur la page produite pour verifier le rendu et capturer une preuve visuelle. "
        "Ensuite seulement, reponds avec les liens `/api/file/...`, le screenshot si disponible, "
        "et un bilan court. Si le navigateur echoue, explique cette limite au lieu de coller le code."
    )


def _raw_artifact_dump_guard_message(intention: str) -> str:
    command = _first_token(intention) or "cette commande"
    return (
        f"{command} ne doit pas coller le contenu brut des fichiers dans le chat. "
        "Reponds par une livraison exploitable : liens `/api/file/...`, chemin workspace, "
        "screenshot/artefact visuel si disponible, et synthese courte des choix et limites."
    )


def _looks_like_raw_artifact_dump(text: str) -> bool:
    stripped = text.lstrip().lower()
    if stripped.startswith(("<!doctype html", "<html", "body {", ":root {", "/*")):
        return True
    return len(text) > 3000 and any(marker in stripped for marker in ("</html>", "<body", "{", "</section>"))


def _looks_like_clarifying_question(text: str) -> bool:
    stripped = text.strip()
    if "?" not in stripped:
        return False
    question_count = stripped.count("?")
    line_count = len([line for line in stripped.splitlines() if line.strip()])
    return question_count <= 3 and line_count <= 8 and len(stripped) <= 900


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


def _looks_like_vision_refusal(text: str) -> bool:
    lower = text.lower()
    markers = (
        "cannot read",
        "does not support image",
        "can't read",
        "cannot view",
        "unable to read",
        "ne peux pas voir",
        "ne peut pas voir",
        "ne supporte pas les images",
        "inform the user",
    )
    return any(marker in lower for marker in markers)


def _has_vision_attempt(tool_observations: list[dict[str, str]]) -> bool:
    return any(item.get("tool") == "vision" for item in tool_observations)


def _vision_tool_hint(intention_text: str) -> str:
    return (
        "Tu ne peux pas voir les images jointes. "
        "Utilise BB9_ACTION vision describe path=<chemin_image> pour CHAQUE image "
        "avant de repondre a l'utilisateur. Ne dis pas que tu ne peux pas voir les images, "
        "appelle simplement le tool vision et integre la description dans ta reponse."
    )
