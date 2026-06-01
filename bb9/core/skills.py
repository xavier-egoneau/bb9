"""Markdown skill discovery and loading."""

from __future__ import annotations

from pathlib import Path

from .archives import (
    ArchiveNotFoundError,
    MarkdownArchive,
    discover_archives,
    load_archive,
    load_enabled_archives,
    parse_markdown_name_list,
)
from .markdown import extract_command_lines, extract_section
from .models import Skill

SKILL_FILE = "SKILL.md"
INDEX_FILE = "INDEX.md"


def discover_skills(root: Path) -> list[str]:
    return discover_archives(root, SKILL_FILE)


def load_enabled_skills(root: Path, disabled: tuple[str, ...] = ()) -> tuple[Skill, ...]:
    return tuple(
        _skill_from_archive(archive)
        for archive in load_enabled_archives(root, SKILL_FILE, disabled)
    )


def load_effective_skills(
    global_root: Path,
    local_root: Path,
    disabled: tuple[str, ...] = (),
) -> tuple[Skill, ...]:
    skills: dict[str, Skill] = {
        skill.name: skill
        for skill in load_enabled_skills(global_root, disabled)
    }
    for skill in load_enabled_skills(local_root, disabled):
        skills[skill.name] = skill
    return tuple(skills[name] for name in sorted(skills))


def load_skill(root: Path, name: str) -> Skill:
    try:
        archive = load_archive(root, name, SKILL_FILE)
    except ArchiveNotFoundError as err:
        raise SkillNotFoundError(f"Skill not found: {name}") from err
    return _skill_from_archive(archive)


def _skill_from_archive(archive: MarkdownArchive) -> Skill:
    body = archive.body
    activation = archive.metadata.get("activation", "").strip() or _first_section_line(body, "Activation")
    commands = _unique_commands((*_metadata_commands(archive.metadata.get("commands", "")), *extract_command_lines(body)))
    return Skill(
        name=archive.name,
        body=body,
        summary=(extract_section(body, "Résumé").replace("\n", " ") or archive.metadata.get("description", "").strip()),
        activation=activation or "on-demand",
        commands=commands,
        root=archive.root,
    )


def build_skills_index(skills: tuple[Skill, ...]) -> str:
    lines = ["# Skills Index", ""]
    lines.extend(skill.as_index_line() for skill in skills)
    return "\n".join(lines).strip() + "\n"


def refresh_skills_index(root: Path) -> str:
    index = build_skills_index(load_enabled_skills(root))
    root.mkdir(parents=True, exist_ok=True)
    (root / INDEX_FILE).write_text(index, encoding="utf-8")
    return index


def parse_disabled_skills(text: str) -> tuple[str, ...]:
    return parse_markdown_name_list(text)


def _first_section_line(body: str, section: str) -> str:
    for line in extract_section(body, section).splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _metadata_commands(value: str) -> tuple[str, ...]:
    commands: list[str] = []
    for item in value.replace("\n", ",").split(","):
        command = item.strip().strip("`")
        if not command:
            continue
        if not command.startswith("/"):
            command = f"/{command}"
        commands.append(f"`{command}`")
    return tuple(commands)


def _unique_commands(commands: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for command in commands:
        if command not in result:
            result.append(command)
    return tuple(result)


class SkillNotFoundError(RuntimeError):
    pass
