"""Markdown tool discovery and loading."""

from __future__ import annotations

from pathlib import Path

from .models import ToolSpec
from .archives import (
    ArchiveNotFoundError,
    MarkdownArchive,
    discover_archives,
    load_archive,
    load_enabled_archives,
    parse_markdown_name_list,
)
from .markdown import extract_command_lines, extract_section


TOOL_FILE = "TOOL.md"
INDEX_FILE = "INDEX.md"


def discover_tools(root: Path) -> list[str]:
    return discover_archives(root, TOOL_FILE)


def load_enabled_tools(root: Path, disabled: tuple[str, ...] = ()) -> tuple[ToolSpec, ...]:
    return tuple(
        _tool_from_archive(archive)
        for archive in load_enabled_archives(root, TOOL_FILE, disabled)
    )


def load_tool(root: Path, name: str) -> ToolSpec:
    try:
        archive = load_archive(root, name, TOOL_FILE)
    except ArchiveNotFoundError:
        raise ToolNotFoundError(f"Tool not found: {name}")
    return _tool_from_archive(archive)


def _tool_from_archive(archive: MarkdownArchive) -> ToolSpec:
    body = archive.body
    return ToolSpec(
        name=archive.name,
        body=body,
        summary=extract_section(body, "Résumé").replace("\n", " "),
        usage=_compact_section(extract_section(body, "Quand l'utiliser")),
        protocol=_compact_section(extract_section(body, "Protocole")),
        commands=extract_command_lines(body),
        root=archive.root,
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
    return parse_markdown_name_list(text)


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
