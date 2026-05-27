"""Generic REPL extension loading for tools and skills."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .agents import AgentNotFoundError
from .skills import load_effective_skills, refresh_skills_index
from .tool_runtime import load_skill_module, load_tool_module
from .tools import load_enabled_tools, refresh_tools_index


def refresh_indexes(cli: Any) -> None:
    refresh_skills_index(cli.state.skills_dir)
    local_skills = Path.cwd() / ".bb9" / "skills"
    if local_skills.exists():
        refresh_skills_index(local_skills)
    refresh_tools_index(cli.state.tools_dir)


def load_tool_cli_extensions(cli: Any) -> None:
    try:
        agent = cli.load_current_agent()
    except AgentNotFoundError:
        return
    for tool in load_enabled_tools(cli.state.tools_dir, agent.disabled_tools):
        if tool.name in cli.loaded_tool_cli:
            continue
        module = load_tool_module(tool.name, "cli", cli.state.tools_dir)
        if module is None or not hasattr(module, "register"):
            continue
        module.register(cli)
        cli.loaded_tool_cli.add(tool.name)


def load_skill_cli_extensions(cli: Any) -> None:
    try:
        agent = cli.load_current_agent()
    except AgentNotFoundError:
        return
    for skill in load_effective_skills(
        cli.state.skills_dir,
        Path.cwd() / ".bb9" / "skills",
        agent.disabled_skills,
    ):
        loaded_key = f"{skill.root}:{skill.name}"
        if loaded_key in cli.loaded_skill_cli:
            continue
        if skill.root is None:
            continue
        module = load_skill_module(skill.name, "cli", skill.root)
        if module is None or not hasattr(module, "register"):
            continue
        module.register(cli)
        cli.loaded_skill_cli.add(loaded_key)
