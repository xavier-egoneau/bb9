"""Runtime context assembly."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .agents import AgentNotFoundError, load_agent, load_subagent, refresh_subagents_index, spawn_ephemeral_worker
from .context_index import load_or_refresh_context_index
from .models import AgentProfile, PermissionProfile, RunContext, Session, Workspace
from .notes import build_agent_notes_context
from .skills import build_skills_index, load_effective_skills
from .tools import build_tools_index, load_enabled_tools
from .trust import TrustedRoots
from .workspace_status import build_workspace_status


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
    try:
        return load_subagent(state.agents_dir, state.agent_name, "dev")
    except AgentNotFoundError:
        pass
    return spawn_ephemeral_worker(state.agents_dir, state.agent_name)


def load_agent_by_name(state: ContextRuntimeState, agent_name: str) -> AgentProfile:
    name = agent_name.strip() or state.agent_name
    if "/" in name:
        parent, _, subagent = name.partition("/")
        return load_subagent(state.agents_dir, parent, subagent)
    return load_agent(state.agents_dir, name)


def build_context(state: ContextRuntimeState, *, light: bool = False) -> RunContext:
    return build_context_with_agent(state, load_current_agent(state), light=light)


def build_goal_context(state: ContextRuntimeState) -> RunContext:
    return build_context_with_agent(state, load_goal_worker_agent(state))


def build_context_with_agent(state: ContextRuntimeState, agent: AgentProfile, *, light: bool = False) -> RunContext:
    workspace = _workspace_for_state(state)
    skills = load_effective_skills(
        state.skills_dir,
        workspace.root / ".bb9" / "skills",
        agent.disabled_skills,
    )
    tools = load_enabled_tools(state.tools_dir, agent.disabled_tools)
    notes_context = build_agent_notes_context(state.agents_dir, state.agent_name)
    if light:
        # Light skips the expensive workspace scans (context index, git status),
        # never the capabilities: the model must always know its skills/tools,
        # otherwise a simple question like "what's on my agenda?" gets a false
        # "I have no calendar access" instead of a caldav action.
        return RunContext(
            session=state.session,
            workspace=workspace,
            permission_profile=state.profile,
            agents_dir=state.agents_dir,
            trusted_roots=TrustedRoots.load(),
            agent=agent,
            skills=skills,
            tools=tools,
            skills_index=build_skills_index(skills),
            tools_index=build_tools_index(tools),
            subagents_index=refresh_subagents_index(state.agents_dir, state.agent_name),
            workspace_status=f"# Workspace Status\n\n- Root: `{workspace.root}`",
            notes_context=notes_context,
        )
    context_index = load_or_refresh_context_index(workspace.root)
    return RunContext(
        session=state.session,
        workspace=workspace,
        permission_profile=state.profile,
        agents_dir=state.agents_dir,
        trusted_roots=TrustedRoots.load(),
        agent=agent,
        skills=skills,
        tools=tools,
        skills_index=build_skills_index(skills),
        tools_index=build_tools_index(tools),
        subagents_index=refresh_subagents_index(state.agents_dir, state.agent_name),
        notes_context=notes_context,
        context_index=context_index,
        workspace_status=build_workspace_status(workspace.root, context_index=context_index),
    )


def _workspace_for_state(state: ContextRuntimeState) -> Workspace:
    active_project_path = str(getattr(state, "active_project_path", "") or "").strip()
    if active_project_path:
        path = Path(active_project_path).expanduser().resolve(strict=False)
        if path.is_dir():
            return Workspace(root=path)
    return Workspace.current()
