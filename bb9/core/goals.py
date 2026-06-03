"""Persistent goal orchestration for autonomous runs."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from bb9.providers.providers import Provider, ProviderError

from .channels import intention_from_text
from .kernel import Kernel
from .loop import ApprovalCallback, run_once
from .models import RunContext
from .paths import bb9_home

GoalStatus = Literal["active", "paused", "achieved", "blocked", "failed", "cancelled", "limit_reached"]
EvaluatorDecision = Literal["continue", "stop_success", "stop_blocked", "ask_user", "stop_limit"]

DEFAULT_MAX_ITERATIONS = 20
NO_PROGRESS_LIMIT = 3
CRITICAL_FAILURE_LIMIT = 3


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class GoalIteration:
    iteration: int
    plan: str
    actions: list[str]
    observations: list[str]
    verification_result: str
    evaluator_decision: EvaluatorDecision
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, object]:
        return {
            "iteration": self.iteration,
            "plan": self.plan,
            "actions": self.actions,
            "observations": self.observations,
            "verificationResult": self.verification_result,
            "evaluatorDecision": self.evaluator_decision,
            "createdAt": self.created_at,
        }

    @staticmethod
    def from_dict(data: dict[str, object]) -> GoalIteration:
        return GoalIteration(
            iteration=int(data.get("iteration") or 0),
            plan=str(data.get("plan") or ""),
            actions=[str(item) for item in data.get("actions", []) if str(item).strip()],
            observations=[str(item) for item in data.get("observations", []) if str(item).strip()],
            verification_result=str(data.get("verificationResult") or ""),
            evaluator_decision=_decision(str(data.get("evaluatorDecision") or "continue")),
            created_at=str(data.get("createdAt") or _now()),
        )


@dataclass(frozen=True)
class GoalState:
    id: str
    title: str
    objective: str
    success_conditions: list[str]
    constraints: list[str]
    verification_steps: list[str]
    status: GoalStatus
    iteration: int
    max_iterations: int
    created_at: str
    updated_at: str
    last_result: str = ""
    blockers: list[str] = field(default_factory=list)
    history: list[GoalIteration] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "objective": self.objective,
            "successConditions": self.success_conditions,
            "constraints": self.constraints,
            "verificationSteps": self.verification_steps,
            "status": self.status,
            "iteration": self.iteration,
            "maxIterations": self.max_iterations,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "lastResult": self.last_result,
            "blockers": self.blockers,
            "history": [item.to_dict() for item in self.history],
        }

    @staticmethod
    def from_dict(data: dict[str, object]) -> GoalState:
        return GoalState(
            id=str(data.get("id") or uuid4()),
            title=str(data.get("title") or "Goal"),
            objective=str(data.get("objective") or ""),
            success_conditions=[str(item) for item in data.get("successConditions", []) if str(item).strip()],
            constraints=[str(item) for item in data.get("constraints", []) if str(item).strip()],
            verification_steps=[str(item) for item in data.get("verificationSteps", []) if str(item).strip()],
            status=_status(str(data.get("status") or "active")),
            iteration=int(data.get("iteration") or 0),
            max_iterations=int(data.get("maxIterations") or DEFAULT_MAX_ITERATIONS),
            created_at=str(data.get("createdAt") or _now()),
            updated_at=str(data.get("updatedAt") or _now()),
            last_result=str(data.get("lastResult") or ""),
            blockers=[str(item) for item in data.get("blockers", []) if str(item).strip()],
            history=[
                GoalIteration.from_dict(item)
                for item in data.get("history", [])
                if isinstance(item, dict)
            ],
        )

    def with_updates(self, **changes: object) -> GoalState:
        data = self.to_dict()
        data.update(changes)
        data["updatedAt"] = _now()
        return GoalState.from_dict(data)


@dataclass(frozen=True)
class EvaluatorResult:
    goal_reached: bool
    confidence: float
    evidence: list[str]
    remaining_work: list[str]
    decision: EvaluatorDecision
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "goalReached": self.goal_reached,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "remainingWork": self.remaining_work,
            "decision": self.decision,
            "reason": self.reason,
        }

    def summary(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class GoalManager:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_goal_path()

    def create_goal(self, objective: str, *, max_iterations: int = DEFAULT_MAX_ITERATIONS) -> GoalState:
        text = objective.strip()
        if not text:
            raise GoalError("goal objective is empty")
        current = self.get_current_goal()
        if current is not None and current.status in {"active", "paused"}:
            raise GoalError("a goal already exists; pause, cancel or clear it first")
        now = _now()
        goal = GoalState(
            id=str(uuid4()),
            title=_title(text),
            objective=text,
            success_conditions=_success_conditions(text),
            constraints=_constraints(text),
            verification_steps=_verification_steps(text),
            status="active",
            iteration=0,
            max_iterations=max(1, max_iterations),
            created_at=now,
            updated_at=now,
        )
        self.update_goal(goal)
        return goal

    def get_active_goal(self) -> GoalState | None:
        goal = self.get_current_goal()
        if goal is None or goal.status != "active":
            return None
        return goal

    def get_current_goal(self) -> GoalState | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise GoalError(f"invalid goal file: {self.path}") from exc
        if not isinstance(data, dict):
            raise GoalError(f"invalid goal file: {self.path}")
        return GoalState.from_dict(data)

    def update_goal(self, goal: GoalState) -> GoalState:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        updated = goal.with_updates() if goal.updated_at else goal
        self.path.write_text(json.dumps(updated.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return updated

    def pause_goal(self, goal_id: str) -> GoalState:
        return self._set_status(goal_id, "paused")

    def resume_goal(self, goal_id: str) -> GoalState:
        return self._set_status(goal_id, "active")

    def cancel_goal(self, goal_id: str) -> GoalState:
        return self._set_status(goal_id, "cancelled")

    def clear_goal(self, goal_id: str) -> None:
        goal = self.get_current_goal()
        if goal is None:
            return
        if goal.id != goal_id:
            raise GoalError("goal id mismatch")
        self.path.unlink(missing_ok=True)

    def append_iteration(self, goal_id: str, iteration: GoalIteration) -> GoalState:
        goal = self._require_goal(goal_id)
        history = [*goal.history, iteration]
        updated = goal.with_updates(
            iteration=iteration.iteration,
            history=[item.to_dict() for item in history],
            lastResult=iteration.verification_result,
        )
        return self.update_goal(updated)

    createGoal = create_goal
    getActiveGoal = get_active_goal
    updateGoal = update_goal
    pauseGoal = pause_goal
    resumeGoal = resume_goal
    cancelGoal = cancel_goal
    clearGoal = clear_goal
    appendIteration = append_iteration

    def _require_goal(self, goal_id: str) -> GoalState:
        goal = self.get_current_goal()
        if goal is None:
            raise GoalError("no goal exists")
        if goal.id != goal_id:
            raise GoalError("goal id mismatch")
        return goal

    def _set_status(self, goal_id: str, status: GoalStatus) -> GoalState:
        goal = self._require_goal(goal_id)
        return self.update_goal(goal.with_updates(status=status))


class EvaluatorAgent:
    def evaluate(
        self,
        goal: GoalState,
        *,
        observations: list[str],
        verification_result: str,
        critical_failures: int = 0,
    ) -> EvaluatorResult:
        if goal.iteration > goal.max_iterations:
            return EvaluatorResult(False, 1.0, [], ["iteration limit reached"], "stop_limit", "maximum iterations reached")
        if critical_failures >= CRITICAL_FAILURE_LIMIT:
            return EvaluatorResult(False, 0.9, [], ["fix repeated verification/tool failure"], "stop_blocked", "critical tool failed repeatedly")
        if not goal.verification_steps:
            return EvaluatorResult(
                False,
                0.2,
                [],
                ["define concrete verification steps"],
                "ask_user",
                "no concrete verification step is available",
            )

        failed = [line for line in verification_result.splitlines() if line.startswith("FAIL ")]
        passed = [line for line in verification_result.splitlines() if line.startswith("PASS ")]
        if failed:
            return EvaluatorResult(
                False,
                0.45,
                passed,
                failed,
                "continue",
                "some verification steps failed",
            )
        if len(passed) < len(goal.verification_steps):
            return EvaluatorResult(
                False,
                0.35,
                passed,
                ["not all verification steps produced evidence"],
                "continue",
                "verification evidence is incomplete",
            )
        evidence = [*passed, *_matching_evidence(goal.success_conditions, observations, verification_result)]
        return EvaluatorResult(
            True,
            0.85,
            evidence,
            [],
            "stop_success",
            "all concrete verification steps passed",
        )


class GoalLoopRunner:
    def __init__(
        self,
        manager: GoalManager,
        *,
        build_context: Callable[[], RunContext],
        build_provider: Callable[[], Provider | None],
        ask_user: ApprovalCallback | None = None,
        remember_turn: Callable[[str, str], None] | None = None,
        write: Callable[[str], None] = print,
        evaluator: EvaluatorAgent | None = None,
    ) -> None:
        self.manager = manager
        self.build_context = build_context
        self.build_provider = build_provider
        self.ask_user = ask_user
        self.remember_turn = remember_turn
        self.write = write
        self.evaluator = evaluator or EvaluatorAgent()

    def run_active_goal(self) -> GoalState | None:
        goal = self.manager.get_active_goal()
        if goal is None:
            self.write("Aucun goal actif.")
            return None

        critical_failures = _recent_failure_count(goal)
        try:
            while goal.status == "active":
                reloaded = self.manager.get_current_goal()
                if reloaded is None or reloaded.id != goal.id:
                    self.write("Goal arrete: etat courant indisponible.")
                    return goal
                goal = reloaded
                if goal.status != "active":
                    return goal
                if goal.iteration >= goal.max_iterations:
                    goal = self.manager.update_goal(goal.with_updates(status="limit_reached"))
                    self.write("Goal limite: nombre maximal d'iterations atteint.")
                    return goal

                next_iteration = goal.iteration + 1
                self.write(f"goal... iteration {next_iteration}/{goal.max_iterations}")
                worker_intention = self._worker_intention(goal, next_iteration)
                result = run_once(
                    Kernel(provider=self.build_provider()),
                    intention_from_text(worker_intention),
                    self.build_context(),
                    ask_user=self.ask_user,
                )

                actions = [event.summary for event in result.trace if event.event_type == "action"]
                observations = [event.summary for event in result.trace if event.event_type == "observation"]
                if result.observation is not None and result.observation.summary not in observations:
                    observations.append(result.observation.summary)

                verification = self._verify(goal)
                critical_failures = critical_failures + 1 if "FAIL " in verification else 0
                interim_goal = goal.with_updates(iteration=next_iteration)
                evaluation = self.evaluator.evaluate(
                    interim_goal,
                    observations=observations,
                    verification_result=verification,
                    critical_failures=critical_failures,
                )
                iteration = GoalIteration(
                    iteration=next_iteration,
                    plan=_first_line(worker_intention),
                    actions=actions,
                    observations=observations,
                    verification_result=verification,
                    evaluator_decision=evaluation.decision,
                )
                goal = self.manager.append_iteration(goal.id, iteration)
                goal = self._apply_evaluation(goal, evaluation)
                if self.remember_turn is not None:
                    self.remember_turn(f"/goal iteration {next_iteration}", _goal_iteration_summary(goal, evaluation))
                self.write(f"eval... {evaluation.decision}: {evaluation.reason}")

                if goal.status != "active":
                    return goal
                if _no_progress(goal):
                    goal = self.manager.update_goal(
                        goal.with_updates(status="blocked", blockers=[*goal.blockers, "no progress detected after 3 iterations"])
                    )
                    self.write("Goal bloque: aucune progression detectee apres 3 iterations.")
                    return goal
        except KeyboardInterrupt:
            goal = self.manager.update_goal(goal.with_updates(status="paused", blockers=[*goal.blockers, "paused by user interrupt"]))
            self.write("\nGoal mis en pause.")
            return goal
        except ProviderError as exc:
            goal = self.manager.update_goal(goal.with_updates(status="blocked", blockers=[*goal.blockers, str(exc)]))
            self.write(f"Goal bloque: {exc}")
            return goal
        except Exception as exc:
            goal = self.manager.update_goal(goal.with_updates(status="blocked", blockers=[*goal.blockers, str(exc)]))
            self.write(f"Goal bloque: {exc}")
            return goal
        return goal

    def _worker_intention(self, goal: GoalState, iteration: int) -> str:
        history = "\n".join(
            f"- iteration {item.iteration}: {item.evaluator_decision} / {item.verification_result[:240]}"
            for item in goal.history[-3:]
        ) or "- aucune iteration precedente"
        return (
            f"Goal iteration {iteration}: {goal.title}\n\n"
            f"Objectif:\n{goal.objective}\n\n"
            "Conditions de succes:\n"
            + _bullets(goal.success_conditions)
            + "\nContraintes:\n"
            + _bullets(goal.constraints)
            + "\nVerification concrete prevue:\n"
            + _bullets(goal.verification_steps)
            + "\nHistorique recent:\n"
            + history
            + "\n\nTravaille sur la prochaine action utile. "
            "Utilise les tools avec BB9_ACTION si necessaire. "
            "Ne declare jamais le goal termine : l'evaluateur le fera apres verification concrete."
        )

    def _verify(self, goal: GoalState) -> str:
        if not goal.verification_steps:
            return "FAIL verification: aucune etape concrete configuree"
        lines: list[str] = []
        for step in goal.verification_steps:
            result = run_once(
                Kernel(provider=None),
                intention_from_text(f"/action shell {step}"),
                self.build_context(),
                ask_user=self.ask_user,
            )
            summary = result.observation.summary if result.observation is not None else result.decision.summary
            prefix = "PASS" if result.observation is not None and result.observation.ok else "FAIL"
            lines.append(f"{prefix} {step}: {summary[:1000]}")
        return "\n".join(lines)

    def _apply_evaluation(self, goal: GoalState, evaluation: EvaluatorResult) -> GoalState:
        if evaluation.decision == "stop_success":
            return self.manager.update_goal(goal.with_updates(status="achieved", lastResult=evaluation.summary()))
        if evaluation.decision == "stop_blocked":
            return self.manager.update_goal(goal.with_updates(status="blocked", lastResult=evaluation.summary(), blockers=evaluation.remaining_work))
        if evaluation.decision == "stop_limit":
            return self.manager.update_goal(goal.with_updates(status="limit_reached", lastResult=evaluation.summary()))
        if evaluation.decision == "ask_user":
            return self.manager.update_goal(goal.with_updates(status="paused", lastResult=evaluation.summary(), blockers=evaluation.remaining_work))
        return self.manager.update_goal(goal.with_updates(status="active", lastResult=evaluation.summary()))


class GoalCommandHandler:
    def __init__(
        self,
        manager: GoalManager,
        runner: GoalLoopRunner,
        *,
        write: Callable[[str], None] = print,
    ) -> None:
        self.manager = manager
        self.runner = runner
        self.write = write

    def handle(self, value: str) -> bool:
        text = value.strip()
        if not text or text == "status":
            self.write(self.status())
            return True
        if text == "pause":
            goal = self._current()
            if goal is not None:
                self.manager.pause_goal(goal.id)
                self.write("Goal en pause.")
            return True
        if text == "resume":
            goal = self._current()
            if goal is not None:
                self.manager.resume_goal(goal.id)
                self.write("Goal relance.")
                self.runner.run_active_goal()
            return True
        if text == "cancel":
            goal = self._current()
            if goal is not None:
                self.manager.cancel_goal(goal.id)
                self.write("Goal annule.")
            return True
        if text == "clear":
            goal = self._current()
            if goal is not None:
                self.manager.clear_goal(goal.id)
                self.write("Goal supprime.")
            return True

        try:
            goal = self.manager.create_goal(text)
        except GoalError as exc:
            self.write(f"Goal erreur: {exc}")
            return True
        self.write(f"Goal cree: {goal.title}")
        self.runner.run_active_goal()
        return True

    def status(self) -> str:
        goal = self.manager.get_current_goal()
        if goal is None:
            return "Aucun goal courant."
        lines = [
            f"goal... {goal.title}",
            f"sta.... {goal.status}",
            f"ite.... {goal.iteration}/{goal.max_iterations}",
            f"id..... {goal.id[:8]}",
        ]
        if goal.verification_steps:
            lines.append("ver.... " + " | ".join(goal.verification_steps))
        if goal.blockers:
            lines.append("blo.... " + " | ".join(goal.blockers[-3:]))
        if goal.history:
            last = goal.history[-1]
            lines.append("act.... " + (" | ".join(last.actions[-3:]) or "-"))
            lines.append("obs.... " + (" | ".join(_short(item) for item in last.observations[-2:]) or "-"))
            lines.append("next... " + _next_hint(goal))
        return "\n".join(lines)

    def _current(self) -> GoalState | None:
        goal = self.manager.get_current_goal()
        if goal is None:
            self.write("Aucun goal courant.")
            return None
        return goal


class GoalError(RuntimeError):
    pass


def default_goal_path() -> Path:
    return bb9_home() / "goals" / "active.json"


def _title(text: str) -> str:
    line = " ".join(text.split())
    return line[:80] or "Goal"


def _success_conditions(text: str) -> list[str]:
    checks = _verification_steps(text)
    conditions = [f"`{check}` passe" for check in checks]
    lower = text.lower()
    if "sans changer l'api" in lower or "sans changer l api" in lower:
        conditions.append("l'API publique reste compatible")
    if not conditions:
        conditions.append("des preuves concretes valident l'objectif")
    return conditions


def _constraints(text: str) -> list[str]:
    constraints = [
        "respecter le guardian et les permissions",
        "ne pas declarer le succes sans verification concrete",
    ]
    lower = text.lower()
    if "sans changer" in lower:
        constraints.append("respecter les contraintes explicites de non-regression")
    return constraints


def _verification_steps(text: str) -> list[str]:
    patterns = [
        r"\bnpm\s+(?:run\s+)?[A-Za-z0-9:_-]+(?:\s+[A-Za-z0-9:_./-]+)*",
        r"\bpnpm\s+(?:run\s+)?[A-Za-z0-9:_-]+(?:\s+[A-Za-z0-9:_./-]+)*",
        r"\byarn\s+(?:run\s+)?[A-Za-z0-9:_-]+(?:\s+[A-Za-z0-9:_./-]+)*",
        r"\bpytest(?:\s+[A-Za-z0-9:_./-]+)*",
        r"\bpython3?\s+-m\s+unittest(?:\s+[A-Za-z0-9:_./-]+)*",
        r"\bmake\s+[A-Za-z0-9:_-]+",
        r"\bcargo\s+test(?:\s+[A-Za-z0-9:_./-]+)*",
        r"\bgo\s+test(?:\s+[A-Za-z0-9:_./-]+)*",
    ]
    steps: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            step = _clean_verification_step(match.group(0))
            if step not in steps:
                steps.append(step)
    return steps


def _clean_verification_step(text: str) -> str:
    stop_words = {"passe", "passes", "reussisse", "réussisse", "fonctionne"}
    words = text.strip().rstrip(".,;").split()
    for index, word in enumerate(words):
        if word.lower().strip(".,;") in stop_words:
            words = words[:index]
            break
    return " ".join(words)


def _matching_evidence(conditions: list[str], observations: list[str], verification_result: str) -> list[str]:
    evidence: list[str] = []
    haystack = "\n".join([*observations, verification_result]).lower()
    for condition in conditions:
        words = [word.lower().strip("`'.,:;") for word in condition.split() if len(word.strip("`'.,:;")) > 3]
        if words and any(word in haystack for word in words):
            evidence.append(condition)
    return evidence


def _status(value: str) -> GoalStatus:
    if value in {"active", "paused", "achieved", "blocked", "failed", "cancelled", "limit_reached"}:
        return value  # type: ignore[return-value]
    return "active"


def _decision(value: str) -> EvaluatorDecision:
    if value in {"continue", "stop_success", "stop_blocked", "ask_user", "stop_limit"}:
        return value  # type: ignore[return-value]
    return "continue"


def _bullets(items: list[str]) -> str:
    if not items:
        return "- aucune\n"
    return "".join(f"- {item}\n" for item in items)


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def _short(text: str, limit: int = 120) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _next_hint(goal: GoalState) -> str:
    if not goal.history:
        return "demarrer"
    last = goal.history[-1]
    if last.evaluator_decision == "continue":
        return "continuer vers les conditions restantes"
    if last.evaluator_decision == "ask_user":
        return "attendre clarification utilisateur"
    return last.evaluator_decision


def _goal_iteration_summary(goal: GoalState, evaluation: EvaluatorResult) -> str:
    return f"{goal.status}: {evaluation.reason}"


def _no_progress(goal: GoalState) -> bool:
    recent = goal.history[-NO_PROGRESS_LIMIT:]
    if len(recent) < NO_PROGRESS_LIMIT:
        return False
    if any(item.actions for item in recent):
        return False
    results = {item.verification_result for item in recent}
    decisions = {item.evaluator_decision for item in recent}
    return len(results) == 1 and decisions == {"continue"}


def _recent_failure_count(goal: GoalState) -> int:
    count = 0
    for item in reversed(goal.history):
        if "FAIL " in item.verification_result:
            count += 1
        else:
            break
    return count
