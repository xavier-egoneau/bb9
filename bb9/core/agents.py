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
AGENT_SUBAGENTS_DISABLED = "SUBAGENTS_DISABLED.md"
AGENT_SUBAGENTS_DIR = "subagents"
DELEGATE_TOOL_NAME = "delegate"
SUBAGENT_TYPE_LABEL = "Type"
SUBAGENT_TYPE_VALUE = "subagent"
RESERVED_AGENT_NAMES = {"goal"}


def discover_agents(root: Path) -> list[str]:
    return [
        name
        for name in discover_archives_any(root, (AGENT_IDENTITY, AGENT_SOUL))
        if name not in RESERVED_AGENT_NAMES
    ]


def is_subagent(root: Path, name: str) -> bool:
    """Une archive du pool plat est un subagent si IDENTITY.md porte `Type : subagent`."""
    if not valid_archive_name(name):
        return False
    identity = read_optional_text(root / name / AGENT_IDENTITY)
    return _normalize_label(_field_value(identity, SUBAGENT_TYPE_LABEL)) == SUBAGENT_TYPE_VALUE


def read_disabled_subagents(root: Path, agent_name: str) -> tuple[str, ...]:
    return parse_markdown_name_list(read_optional_text(root / agent_name / AGENT_SUBAGENTS_DISABLED))


def read_disabled_skills(root: Path, agent_name: str) -> tuple[str, ...]:
    return parse_markdown_name_list(read_optional_text(root / agent_name / AGENT_SKILLS_DISABLED))


def read_disabled_tools(root: Path, agent_name: str) -> tuple[str, ...]:
    return parse_markdown_name_list(read_optional_text(root / agent_name / AGENT_TOOLS_DISABLED))


def set_agent_skill_enabled(root: Path, agent_name: str, skill_name: str, enabled: bool) -> None:
    _set_agent_archive_enabled(root, agent_name, AGENT_SKILLS_DISABLED, skill_name, enabled)


def set_agent_tool_enabled(root: Path, agent_name: str, tool_name: str, enabled: bool) -> None:
    _set_agent_archive_enabled(root, agent_name, AGENT_TOOLS_DISABLED, tool_name, enabled)


def discover_pool_subagents(root: Path, agent_name: str) -> list[str]:
    """Subagents du pool plat spawnables par cet agent (défaut tous actifs)."""
    if not valid_archive_name(agent_name) or agent_name in RESERVED_AGENT_NAMES:
        return []
    disabled = set(read_disabled_subagents(root, agent_name))
    names = [
        name
        for name in discover_agents(root)
        if name != agent_name and name not in disabled and is_subagent(root, name)
    ]
    legacy_markers = (
        AGENT_IDENTITY,
        AGENT_SOUL,
        AGENT_MODEL,
        AGENT_SKILLS_DISABLED,
        AGENT_TOOLS_DISABLED,
    )
    for name in discover_archives_any(_legacy_subagents_root(root, agent_name), legacy_markers):
        if name not in RESERVED_AGENT_NAMES and name not in disabled and name not in names:
            names.append(name)
    return names


def load_agent(root: Path, name: str) -> AgentProfile:
    if not valid_archive_name(name) or name in RESERVED_AGENT_NAMES:
        raise AgentNotFoundError(f"Agent not found: {name}")
    agent_dir = root / name
    if not agent_dir.is_dir():
        raise AgentNotFoundError(f"Agent not found: {name}")
    disabled_tools = _read_disabled_tools(agent_dir / AGENT_TOOLS_DISABLED)
    if is_subagent(root, name):
        disabled_tools = _merge_names(disabled_tools, (DELEGATE_TOOL_NAME,))
    return AgentProfile(
        name=name,
        identity=_read_optional(agent_dir / AGENT_IDENTITY),
        soul=_read_optional(agent_dir / AGENT_SOUL),
        model=_read_model(agent_dir / AGENT_MODEL),
        reasoning_effort=_read_reasoning_effort(agent_dir / AGENT_MODEL),
        disabled_skills=_read_disabled_skills(agent_dir / AGENT_SKILLS_DISABLED),
        disabled_tools=disabled_tools,
    )


def discover_subagents(root: Path, agent_name: str) -> list[str]:
    """Subagents du pool plat spawnables par cet agent."""
    return discover_pool_subagents(root, agent_name)


def build_subagents_index(root: Path, agent_name: str) -> str:
    names = discover_subagents(root, agent_name)
    lines = [
        "# Subagents Index",
        "",
        "Subagents disponibles pour l'agent parent. Choisir le worker le plus adapte a la tache.",
        "",
    ]
    if not names:
        lines.append("Aucun subagent configure.")
        return "\n".join(lines) + "\n"

    for name in names:
        agent_dir = _subagent_dir(root, agent_name, name)
        identity = _read_optional(agent_dir / AGENT_IDENTITY)
        soul = _read_optional(agent_dir / AGENT_SOUL)
        lines.append(f"- `{name}` : {_subagent_summary(identity, soul)}")
    return "\n".join(lines) + "\n"


def refresh_subagents_index(root: Path, agent_name: str) -> str:
    text = build_subagents_index(root, agent_name)
    subagents_root = _legacy_subagents_root(root, agent_name)
    if subagents_root.exists() or discover_subagents(root, agent_name):
        subagents_root.mkdir(parents=True, exist_ok=True)
        (subagents_root / "INDEX.md").write_text(text, encoding="utf-8")
    return text


def build_agents_index(root: Path) -> str:
    names = discover_agents(root)
    lines = [
        "# Agents Index",
        "",
        "Tous les agents et subagents du pool. Les subagents (Type : subagent) peuvent être délégués via `delegate`.",
        "",
    ]
    if not names:
        lines.append("Aucun agent configuré.")
        return "\n".join(lines) + "\n"
    for name in names:
        agent_dir = root / name
        identity = _read_optional(agent_dir / AGENT_IDENTITY)
        soul = _read_optional(agent_dir / AGENT_SOUL)
        kind = "subagent" if is_subagent(root, name) else "agent"
        lines.append(f"- `{name}` ({kind}) : {_subagent_summary(identity, soul)}")
    return "\n".join(lines) + "\n"


def refresh_agents_index(root: Path) -> str:
    text = build_agents_index(root)
    if root.exists():
        (root / "INDEX.md").write_text(text, encoding="utf-8")
    return text


def load_subagent(root: Path, agent_name: str, subagent_name: str) -> AgentProfile:
    parent = load_agent(root, agent_name)
    if not valid_archive_name(subagent_name) or subagent_name in RESERVED_AGENT_NAMES:
        raise AgentNotFoundError(f"Subagent not found: {agent_name}/{subagent_name}")
    subagent_dir = _subagent_dir(root, agent_name, subagent_name)
    if (
        not subagent_dir.is_dir()
        or subagent_name in read_disabled_subagents(root, agent_name)
    ):
        raise AgentNotFoundError(f"Subagent not found: {agent_name}/{subagent_name}")

    model = _read_model(subagent_dir / AGENT_MODEL) or parent.model
    reasoning_effort = _read_reasoning_effort(subagent_dir / AGENT_MODEL) or parent.reasoning_effort
    disabled_skills = _merge_names(
        parent.disabled_skills,
        _read_disabled_skills(subagent_dir / AGENT_SKILLS_DISABLED),
    )
    disabled_tools = _merge_names(
        parent.disabled_tools,
        _read_disabled_tools(subagent_dir / AGENT_TOOLS_DISABLED),
        (DELEGATE_TOOL_NAME,),
    )

    return AgentProfile(
        name=f"{agent_name}/{subagent_name}",
        identity=_read_optional(subagent_dir / AGENT_IDENTITY) or parent.identity,
        soul=_read_optional(subagent_dir / AGENT_SOUL) or parent.soul,
        model=model,
        reasoning_effort=reasoning_effort,
        disabled_skills=disabled_skills,
        disabled_tools=disabled_tools,
    )


_WORKER_TEMPLATE_DIR = Path(__file__).parent.parent / "templates" / "agents" / "worker"
_LEGACY_WORKER_TEMPLATE_DIR = (
    Path(__file__).parent.parent / "templates" / "agents" / "default" / "subagents" / "default"
)


def spawn_ephemeral_worker(root: Path, agent_name: str) -> AgentProfile:
    """Retourne un worker éphémère (non persisté) avec l'identity/soul dev.
    Ordre de priorité : agent dev local → template worker → template subagent default legacy → vide.
    Hérite du modèle et des skills/tools du parent, delegate toujours désactivé."""
    try:
        parent = load_agent(root, agent_name)
    except AgentNotFoundError:
        parent = AgentProfile(name=agent_name)
    dev_dir = root / "dev"
    identity = (
        _read_optional(dev_dir / AGENT_IDENTITY)
        or _read_optional(_WORKER_TEMPLATE_DIR / AGENT_IDENTITY)
        or _read_optional(_LEGACY_WORKER_TEMPLATE_DIR / AGENT_IDENTITY)
    )
    soul = (
        _read_optional(dev_dir / AGENT_SOUL)
        or _read_optional(_WORKER_TEMPLATE_DIR / AGENT_SOUL)
        or _read_optional(_LEGACY_WORKER_TEMPLATE_DIR / AGENT_SOUL)
    )
    return AgentProfile(
        name=f"{agent_name}/dev",
        identity=identity,
        soul=soul,
        model=parent.model,
        reasoning_effort=parent.reasoning_effort,
        disabled_skills=parent.disabled_skills,
        disabled_tools=_merge_names(parent.disabled_tools, (DELEGATE_TOOL_NAME,)),
    )


def _read_optional(path: Path) -> str:
    return read_optional_text(path)


def _legacy_subagents_root(root: Path, agent_name: str) -> Path:
    return root / agent_name / AGENT_SUBAGENTS_DIR


def _subagent_dir(root: Path, agent_name: str, subagent_name: str) -> Path:
    legacy_dir = _legacy_subagents_root(root, agent_name) / subagent_name
    if legacy_dir.is_dir():
        return legacy_dir
    flat_dir = root / subagent_name
    if flat_dir.is_dir() and is_subagent(root, subagent_name):
        return flat_dir
    return legacy_dir


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
    for label in ("Description", "Quand l'utiliser", "Rôle", "Role", "Responsabilité", "Responsabilite"):
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


def _merge_names(*groups: tuple[str, ...]) -> tuple[str, ...]:
    merged: list[str] = []
    for group in groups:
        for name in group:
            if name and name not in merged:
                merged.append(name)
    return tuple(merged)


def _read_disabled_skills(path: Path) -> tuple[str, ...]:
    return parse_markdown_name_list(_read_optional(path))


def _read_disabled_tools(path: Path) -> tuple[str, ...]:
    return parse_markdown_name_list(_read_optional(path))


def _set_agent_archive_enabled(root: Path, agent_name: str, filename: str, archive_name: str, enabled: bool) -> None:
    if not valid_archive_name(agent_name):
        raise AgentNotFoundError(f"Agent not found: {agent_name}")
    if not valid_archive_name(archive_name):
        raise ValueError(f"Invalid archive name: {archive_name}")
    agent_dir = root / agent_name
    if not agent_dir.is_dir():
        raise AgentNotFoundError(f"Agent not found: {agent_name}")
    path = agent_dir / filename
    disabled = set(parse_markdown_name_list(read_optional_text(path)))
    if enabled:
        disabled.discard(archive_name)
    else:
        disabled.add(archive_name)
    _write_disabled_markdown(path, disabled)


def _write_disabled_markdown(path: Path, names: set[str]) -> None:
    preamble = _disabled_markdown_preamble(path)
    if not preamble:
        title = "Skills Disabled" if path.name == AGENT_SKILLS_DISABLED else "Tools Disabled"
        preamble = [f"# {title}", "", "Les archives sont actives par défaut."]
    lines = [*preamble]
    for name in sorted(names):
        if valid_archive_name(name):
            lines.append(f"- `{name}`")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _disabled_markdown_preamble(path: Path) -> list[str]:
    lines: list[str] = []
    for line in read_optional_text(path).splitlines():
        stripped = line.strip()
        if stripped.startswith(("-", "*")):
            break
        lines.append(line.rstrip())
    while lines and not lines[-1].strip():
        lines.pop()
    if lines:
        lines.append("")
    return lines


class AgentNotFoundError(RuntimeError):
    pass
