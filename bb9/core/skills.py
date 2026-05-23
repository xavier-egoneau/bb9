"""Markdown skill discovery and loading."""

from __future__ import annotations

from pathlib import Path

from .models import Skill
from .markdown import extract_section


SKILL_FILE = "SKILL.md"
INDEX_FILE = "INDEX.md"


def discover_skills(root: Path) -> list[str]:
    if not root.exists():
        return []
    names: list[str] = []
    for item in sorted(root.iterdir()):
        if item.is_dir() and (item / SKILL_FILE).exists():
            names.append(item.name)
    return names


def load_enabled_skills(root: Path, disabled: tuple[str, ...] = ()) -> tuple[Skill, ...]:
    disabled_set = set(disabled)
    skills: list[Skill] = []
    for name in discover_skills(root):
        if name in disabled_set:
            continue
        skills.append(load_skill(root, name))
    return tuple(skills)


def load_skill(root: Path, name: str) -> Skill:
    path = root / name / SKILL_FILE
    if not path.is_file():
        raise SkillNotFoundError(f"Skill not found: {name}")
    body = path.read_text(encoding="utf-8")
    return Skill(
        name=name,
        body=body,
        summary=extract_section(body, "Résumé").replace("\n", " "),
        activation=extract_section(body, "Activation").splitlines()[0].strip() or "on-demand",
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


class SkillNotFoundError(RuntimeError):
    pass
