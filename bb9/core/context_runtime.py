"""Runtime context assembly."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .agents import AgentNotFoundError, load_agent, load_subagent, refresh_subagents_index
from .context_index import refresh_context_index
from .models import AgentProfile, PermissionProfile, RunContext, Session, Workspace
from .skills import build_skills_index, load_enabled_skills
from .tools import build_tools_index, load_enabled_tools
from .trust import TrustedRoots


class ContextRuntimeState(Protocol):
    profile: PermissionProfile
    session: Session
    agent_name: str
    subagent_name: str
    agents_dir: Path
    skills_dir: Path
    tools_dir: Path


def load_current_agent(state: ContextRuntimeState) -> AgentProfile:
    if state.subagent_name:
        return load_subagent(
            state.agents_dir,
            state.agent_name,
            state.subagent_name,
        )
    return load_agent(state.agents_dir, state.agent_name)


def load_goal_worker_agent(state: ContextRuntimeState) -> AgentProfile:
    for subagent_name in ("goal", "default"):
        try:
            return load_subagent(state.agents_dir, state.agent_name, subagent_name)
        except AgentNotFoundError:
            continue
    return load_current_agent(state)


def load_agent_by_name(state: ContextRuntimeState, agent_name: str) -> AgentProfile:
    name = agent_name.strip() or state.agent_name
    if "/" in name:
        parent, _, subagent = name.partition("/")
        return load_subagent(state.agents_dir, parent, subagent)
    return load_agent(state.agents_dir, name)


def build_context(state: ContextRuntimeState) -> RunContext:
    return build_context_with_agent(state, load_current_agent(state))


def build_goal_context(state: ContextRuntimeState) -> RunContext:
    return build_context_with_agent(state, load_goal_worker_agent(state))


def build_context_with_agent(state: ContextRuntimeState, agent: AgentProfile) -> RunContext:
    skills = load_enabled_skills(state.skills_dir, agent.disabled_skills)
    tools = load_enabled_tools(state.tools_dir, agent.disabled_tools)
    workspace = Workspace.current()
    return RunContext(
        session=state.session,
        workspace=workspace,
        permission_profile=state.profile,
        trusted_roots=TrustedRoots.load(),
        agent=agent,
        skills=skills,
        tools=tools,
        skills_index=build_skills_index(skills),
        tools_index=build_tools_index(tools),
        subagents_index=refresh_subagents_index(state.agents_dir, state.agent_name),
        context_index=refresh_context_index(workspace.root),
    )
