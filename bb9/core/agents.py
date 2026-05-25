"""Markdown agent discovery and loading."""

from __future__ import annotations

from pathlib import Path

from .archives import (
    discover_archives_any,
    parse_markdown_name_list,
    read_optional_text,
    valid_archive_name,
)
from .models import AgentProfile


AGENT_IDENTITY = "IDENTITY.md"
AGENT_SOUL = "SOUL.md"
AGENT_MODEL = "MODEL.md"
AGENT_SKILLS_DISABLED = "SKILLS_DISABLED.md"
AGENT_TOOLS_DISABLED = "TOOLS_DISABLED.md"
SUBAGENTS_DIR = "subagents"
SUBAGENTS_INDEX = "INDEX.md"


def discover_agents(root: Path) -> list[str]:
    return discover_archives_any(root, (AGENT_IDENTITY, AGENT_SOUL))


def load_agent(root: Path, name: str) -> AgentProfile:
    if not valid_archive_name(name):
        raise AgentNotFoundError(f"Agent not found: {name}")
    agent_dir = root / name
    if not agent_dir.is_dir():
        raise AgentNotFoundError(f"Agent not found: {name}")
    return AgentProfile(
        name=name,
        identity=_read_optional(agent_dir / AGENT_IDENTITY),
        soul=_read_optional(agent_dir / AGENT_SOUL),
        model=_read_model(agent_dir / AGENT_MODEL),
        reasoning_effort=_read_reasoning_effort(agent_dir / AGENT_MODEL),
        disabled_skills=_read_disabled_skills(agent_dir / AGENT_SKILLS_DISABLED),
        disabled_tools=_read_disabled_tools(agent_dir / AGENT_TOOLS_DISABLED),
    )


def discover_subagents(root: Path, agent_name: str) -> list[str]:
    if not valid_archive_name(agent_name):
        return []
    subagents_root = root / agent_name / SUBAGENTS_DIR
    return discover_archives_any(
        subagents_root,
        (AGENT_IDENTITY, AGENT_SOUL, AGENT_MODEL),
    )


def build_subagents_index(root: Path, agent_name: str) -> str:
    subagents_root = root / agent_name / SUBAGENTS_DIR
    names = discover_subagents(root, agent_name)
    lines = [
        "# Subagents Index",
        "",
        "Liste generee des subagents disponibles pour l'agent parent.",
        "Le subagent `default` sert de fallback quand aucune specialisation ne colle mieux.",
        "",
    ]
    if not names:
        lines.append("Aucun subagent configure.")
        return "\n".join(lines) + "\n"

    for name in names:
        identity = _read_optional(subagents_root / name / AGENT_IDENTITY)
        soul = _read_optional(subagents_root / name / AGENT_SOUL)
        lines.append(f"- `{name}` : {_subagent_summary(identity, soul)}")
    return "\n".join(lines) + "\n"


def refresh_subagents_index(root: Path, agent_name: str) -> str:
    text = build_subagents_index(root, agent_name)
    subagents_root = root / agent_name / SUBAGENTS_DIR
    if subagents_root.exists() or discover_subagents(root, agent_name):
        subagents_root.mkdir(parents=True, exist_ok=True)
        (subagents_root / SUBAGENTS_INDEX).write_text(text, encoding="utf-8")
    return text


def load_subagent(root: Path, agent_name: str, subagent_name: str) -> AgentProfile:
    parent = load_agent(root, agent_name)
    if not valid_archive_name(subagent_name):
        raise AgentNotFoundError(f"Subagent not found: {agent_name}/{subagent_name}")
    subagent_dir = root / agent_name / SUBAGENTS_DIR / subagent_name
    if not subagent_dir.is_dir():
        raise AgentNotFoundError(f"Subagent not found: {agent_name}/{subagent_name}")

    identity = _read_optional(subagent_dir / AGENT_IDENTITY) or parent.identity
    soul = _read_optional(subagent_dir / AGENT_SOUL) or parent.soul
    model = _read_model(subagent_dir / AGENT_MODEL) or parent.model
    reasoning_effort = _read_reasoning_effort(subagent_dir / AGENT_MODEL) or parent.reasoning_effort
    disabled_skills = _merge_names(
        parent.disabled_skills,
        _read_disabled_skills(subagent_dir / AGENT_SKILLS_DISABLED),
    )
    disabled_tools = _merge_names(
        parent.disabled_tools,
        _read_disabled_tools(subagent_dir / AGENT_TOOLS_DISABLED),
    )

    return AgentProfile(
        name=f"{agent_name}/{subagent_name}",
        identity=identity,
        soul=soul,
        model=model,
        reasoning_effort=reasoning_effort,
        disabled_skills=disabled_skills,
        disabled_tools=disabled_tools,
    )


def _read_optional(path: Path) -> str:
    return read_optional_text(path)


def _read_model(path: Path) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    for label in ("Model", "Modele", "Modèle"):
        value = _field_value(text, label)
        if value:
            return value
    return ""


def _read_reasoning_effort(path: Path) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    for label in ("ReasoningEffort", "Reasoning Effort", "Reasoning", "Effort"):
        value = _field_value(text, label)
        if value:
            normalized = value.strip().lower()
            if normalized in {"none", "low", "medium", "high", "xhigh"}:
                return normalized
            return value.strip()
    return ""


def _subagent_summary(identity: str, soul: str) -> str:
    for label in ("Quand l'utiliser", "Rôle", "Role", "Responsabilité", "Responsabilite"):
        value = _field_value(identity, label)
        if value:
            return value
    first_identity_line = _first_content_line(identity)
    if first_identity_line:
        return first_identity_line
    first_soul_line = _first_content_line(soul)
    if first_soul_line:
        return first_soul_line
    return "subagent configure."


def _field_value(markdown: str, label: str) -> str:
    normalized_label = _normalize_label(label)
    for line in markdown.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        if _normalize_label(key) == normalized_label:
            return value.strip()
    return ""


def _first_content_line(markdown: str) -> str:
    for line in markdown.splitlines():
        clean = line.strip().strip("#").strip()
        if not clean or clean.lower() in {"identity", "soul"}:
            continue
        return clean
    return ""


def _normalize_label(text: str) -> str:
    replacements = str.maketrans("àâäéèêëîïôöùûüç", "aaaeeeeiioouuuc")
    return " ".join(text.lower().translate(replacements).split())


def _merge_names(first: tuple[str, ...], second: tuple[str, ...]) -> tuple[str, ...]:
    merged: list[str] = []
    for name in first + second:
        if name and name not in merged:
            merged.append(name)
    return tuple(merged)


def _read_disabled_skills(path: Path) -> tuple[str, ...]:
    return parse_markdown_name_list(_read_optional(path))


def _read_disabled_tools(path: Path) -> tuple[str, ...]:
    return parse_markdown_name_list(_read_optional(path))


class AgentNotFoundError(RuntimeError):
    pass
