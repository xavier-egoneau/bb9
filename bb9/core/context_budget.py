"""Prompt context budget estimation."""

from __future__ import annotations

from dataclasses import dataclass

from bb9.core.compaction import estimate_tokens_from_chars
from bb9.core.kernel import Kernel, _intention_matches_skill, _load_system_prompt
from bb9.core.models import RunContext


@dataclass(frozen=True)
class ContextBudget:
    total_chars: int
    total_tokens: int
    before_session_chars: int
    before_session_tokens: int
    session_chars: int
    session_tokens: int
    context_window: int = 0


def prompt_context_parts(context: RunContext, intention: str) -> list[tuple[str, int, int]]:
    kernel = Kernel()
    session_context = context.session.as_prompt_context()
    parts: list[tuple[str, str]] = [
        ("Système", "# BB9 runtime context\n\n" + _load_system_prompt()),
        ("Contrat comportemental", kernel.agent_behavior_context(context)),
        ("Autonomie", kernel.autonomy_context(context)),
        ("Agent identity/soul/model", context.agent.as_prompt_context() if context.agent is not None else ""),
        ("Session courte", session_context),
        ("Workspace status", context.workspace_status.strip()),
        ("Context index", context.context_index.strip()),
        ("Notes agent", context.notes_context.strip()),
        ("Subagents index", context.subagents_index.strip()),
        ("Skills index", context.skills_index.strip()),
        ("Tools index", context.tools_index.strip()),
    ]
    for skill in context.skills:
        if skill.activation == "always" or _intention_matches_skill(intention, skill.name, skill.commands, skill.activation):
            parts.append((f"Skill body actif `{skill.name}`", skill.as_prompt_context()))
    parts.append(("Protocole BB9_ACTION", kernel.provider_action_protocol_context()))
    parts.append(
        (
            "Frontière de tour et intention",
            "# Frontiere de tour\n\n"
            "L'intention courante ci-dessous est l'autorite de ce tour. "
            "La session recente sert seulement de contexte.\n\n"
            f"# Intention courante\n\n{intention.strip()}",
        )
    )
    return [(label, len(text), estimate_tokens_from_chars(len(text))) for label, text in parts]


def context_budget(context: RunContext, intention: str, *, context_window: int = 0) -> ContextBudget:
    parts = prompt_context_parts(context, intention)
    total_chars = sum(chars for _, chars, _ in parts)
    session_chars = sum(chars for label, chars, _ in parts if label == "Session courte")
    before_session_chars = max(0, total_chars - session_chars)
    return ContextBudget(
        total_chars=total_chars,
        total_tokens=estimate_tokens_from_chars(total_chars),
        before_session_chars=before_session_chars,
        before_session_tokens=estimate_tokens_from_chars(before_session_chars),
        session_chars=session_chars,
        session_tokens=estimate_tokens_from_chars(session_chars),
        context_window=context_window,
    )


def context_budget_summary_lines(context: RunContext, intention: str, *, context_window: int = 0) -> list[str]:
    budget = context_budget(context, intention, context_window=context_window)
    total = f"- Fenêtre utilisée, session incluse : `~{budget.total_tokens} tok` ({budget.total_chars} car.)"
    before = (
        f"- Avant session courte : `~{budget.before_session_tokens} tok` "
        f"({budget.before_session_chars} car.)"
    )
    if context_window > 0:
        total += f" · `{(budget.total_tokens / context_window) * 100:.2f}%` de `{context_window}`"
        before += f" · `{(budget.before_session_tokens / context_window) * 100:.2f}%`"
    return [
        "## Budget contexte",
        "",
        total,
        before,
        f"- Session courte seule : `~{budget.session_tokens} tok` ({len(context.session.messages)} message(s))",
    ]


def potential_skill_body_costs(context: RunContext, intention: str) -> list[tuple[str, int, int]]:
    result: list[tuple[str, int, int]] = []
    for skill in context.skills:
        if skill.activation == "always" or _intention_matches_skill(intention, skill.name, skill.commands, skill.activation):
            continue
        body = skill.as_prompt_context()
        result.append((skill.name, len(body), estimate_tokens_from_chars(len(body))))
    return result


def context_budget_lines(context: RunContext, intention: str, *, context_window: int = 0) -> list[str]:
    parts = prompt_context_parts(context, intention)
    budget = context_budget(context, intention, context_window=context_window)
    lines = [
        "## Coût contexte estimé",
        "",
        "Estimation locale : `~1 token / 4 caractères`. Le coût réel dépend du tokenizer provider.",
        "N'inclut pas les futures observations de tools ni les images jointes du tour.",
        "",
    ]
    for label, chars, tokens in parts:
        if chars <= 0:
            continue
        lines.append(f"- {label} : `~{tokens} tok` ({chars} car.)")
    total = f"- Total prompt avant réponse : `~{budget.total_tokens} tok` ({budget.total_chars} car.)"
    if context_window > 0:
        total += f" · `{(budget.total_tokens / context_window) * 100:.2f}%` de la fenêtre `{context_window}`"
    lines.append(total)
    potential = potential_skill_body_costs(context, intention)
    if potential:
        lines.extend(["", "### Corps de skills on-demand non inclus"])
        lines.extend(
            f"- {name} : `~{tokens} tok` ({chars} car.) si cette commande/intention l'active"
            for name, chars, tokens in potential
        )
    return lines
