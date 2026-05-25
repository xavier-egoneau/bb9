"""Markdown skill discovery and loading."""

from __future__ import annotations

from pathlib import Path

from .models import Skill
from .archives import (
    ArchiveNotFoundError,
    MarkdownArchive,
    discover_archives,
    load_archive,
    load_enabled_archives,
    parse_markdown_name_list,
)
from .markdown import extract_section


SKILL_FILE = "SKILL.md"
INDEX_FILE = "INDEX.md"


def discover_skills(root: Path) -> list[str]:
    return discover_archives(root, SKILL_FILE)


def load_enabled_skills(root: Path, disabled: tuple[str, ...] = ()) -> tuple[Skill, ...]:
    return tuple(
        _skill_from_archive(archive)
        for archive in load_enabled_archives(root, SKILL_FILE, disabled)
    )


def load_skill(root: Path, name: str) -> Skill:
    try:
        archive = load_archive(root, name, SKILL_FILE)
    except ArchiveNotFoundError:
        raise SkillNotFoundError(f"Skill not found: {name}")
    return _skill_from_archive(archive)


def _skill_from_archive(archive: MarkdownArchive) -> Skill:
    body = archive.body
    return Skill(
        name=archive.name,
        body=body,
        summary=extract_section(body, "Résumé").replace("\n", " "),
        activation=archive.metadata.get("activation", "").strip()
        or extract_section(body, "Activation").splitlines()[0].strip()
        or "on-demand",
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


class SkillNotFoundError(RuntimeError):
    pass
