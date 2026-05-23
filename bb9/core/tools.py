"""Markdown tool discovery and loading."""

from __future__ import annotations

from pathlib import Path

from .models import ToolSpec
from .markdown import extract_section


TOOL_FILE = "TOOL.md"
INDEX_FILE = "INDEX.md"


def discover_tools(root: Path) -> list[str]:
    if not root.exists():
        return []
    names: list[str] = []
    for item in sorted(root.iterdir()):
        if item.is_dir() and (item / TOOL_FILE).exists():
            names.append(item.name)
    return names


def load_enabled_tools(root: Path, disabled: tuple[str, ...] = ()) -> tuple[ToolSpec, ...]:
    disabled_set = set(disabled)
    tools: list[ToolSpec] = []
    for name in discover_tools(root):
        if name in disabled_set:
            continue
        tools.append(load_tool(root, name))
    return tuple(tools)


def load_tool(root: Path, name: str) -> ToolSpec:
    path = root / name / TOOL_FILE
    if not path.is_file():
        raise ToolNotFoundError(f"Tool not found: {name}")
    body = path.read_text(encoding="utf-8")
    return ToolSpec(
        name=name,
        body=body,
        summary=extract_section(body, "Résumé").replace("\n", " "),
        usage=_compact_section(extract_section(body, "Quand l'utiliser")),
        protocol=_compact_section(extract_section(body, "Protocole")),
    )


def build_tools_index(tools: tuple[ToolSpec, ...]) -> str:
    lines = ["# Tools Index", ""]
    lines.extend(tool.as_index_line() for tool in tools)
    return "\n".join(lines).strip() + "\n"


def refresh_tools_index(root: Path) -> str:
    index = build_tools_index(load_enabled_tools(root))
    root.mkdir(parents=True, exist_ok=True)
    (root / INDEX_FILE).write_text(index, encoding="utf-8")
    return index


def parse_disabled_tools(text: str) -> tuple[str, ...]:
    names: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(("-", "*")):
            continue
        value = stripped[1:].strip()
        if not value:
            continue
        name = value.split()[0].strip("`")
        if name:
            names.append(name)
    return tuple(names)


class ToolNotFoundError(RuntimeError):
    pass


def _compact_section(text: str, *, limit: int = 240) -> str:
    if not text.strip():
        return ""
    compact = " ".join(line.strip().strip("-") for line in text.splitlines() if line.strip() and not line.strip().startswith("```"))
    compact = " ".join(compact.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "..."
