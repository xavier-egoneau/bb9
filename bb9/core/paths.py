"""Runtime path helpers."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def product_root() -> Path:
    return Path(__file__).resolve().parent.parent


def bb9_home() -> Path:
    return Path(os.environ.get("BB9_HOME", Path.home() / ".bb9")).expanduser()


def default_tools_dir() -> Path:
    return product_root() / "tools"


def default_agent_templates_dir() -> Path:
    return product_root() / "templates" / "agents"


def default_agents_dir() -> Path:
    root = bb9_home() / "agents"
    ensure_user_agents(root)
    return root


def default_skills_dir() -> Path:
    return bb9_home() / "skills"


def default_content_dir(name: str) -> Path:
    if name == "agents":
        return default_agents_dir()
    if name == "tools":
        return default_tools_dir()
    if name == "skills":
        return default_skills_dir()
    local = Path(name)
    if local.exists():
        return local
    return repo_root() / name


def ensure_user_agents(root: Path | None = None) -> Path:
    target = root or bb9_home() / "agents"
    target.mkdir(parents=True, exist_ok=True)
    templates = default_agent_templates_dir()
    if not templates.exists():
        return target
    for template in templates.iterdir():
        if not template.is_dir():
            continue
        destination = target / template.name
        _copy_missing_tree(template, destination)
    return target


def _copy_missing_tree(source: Path, destination: Path) -> None:
    if not destination.exists():
        shutil.copytree(source, destination)
        return
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        target = destination / relative
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
