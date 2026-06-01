"""Context budget estimation helpers for the chat API."""

from __future__ import annotations

from bb9.core.compaction import estimate_tokens_from_chars
from bb9.core.kernel import Kernel, _intention_matches_skill, _load_system_prompt
from bb9.core.models import RunContext


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
        ("Subagents index", context.subagents_index.strip()),
        ("Skills index", context.skills_index.strip()),
        ("Tools index", context.tools_index.strip()),
    ]
    for skill in context.skills:
        if skill.activation == "always" or _intention_matches_skill(intention, skill.name, skill.commands, skill.activation):
            parts.append((f"Skill body actif `{skill.name}`", skill.as_prompt_context()))
    parts.append(("Intention courante", f"# Intention courante\n\n{intention.strip()}"))
    return [(label, len(text), estimate_tokens_from_chars(len(text))) for label, text in parts]


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
    total_chars = sum(chars for _, chars, _ in parts)
    total_tokens = estimate_tokens_from_chars(total_chars)
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
    total = f"- Total prompt avant réponse : `~{total_tokens} tok` ({total_chars} car.)"
    if context_window > 0:
        ratio = (total_tokens / context_window) * 100
        total += f" · `{ratio:.2f}%` de la fenêtre `{context_window}`"
    lines.append(total)
    potential = potential_skill_body_costs(context, intention)
    if potential:
        lines.extend(["", "### Corps de skills on-demand non inclus"])
        lines.extend(
            f"- {name} : `~{tokens} tok` ({chars} car.) si cette commande/intention l'active"
            for name, chars, tokens in potential
        )
    return lines
