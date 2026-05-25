"""CLI handlers for the BB9 dreaming commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .agents import AgentNotFoundError
from .dream import (
    DreamNotFoundError,
    DreamSpec,
    apply_dream_plan,
    build_dreaming_context,
    build_dreaming_prompt,
    clear_pending_dream_plan,
    discover_dreams,
    load_dream,
    load_dream_contribution,
    load_dream_contributions,
    load_enabled_dreams,
    load_pending_dream_plan,
    plan_dreaming,
    refresh_dream_index,
    run_dreaming,
    save_pending_dream_plan,
)
from .memory import MemoryStore
from .providers import ProviderError
from .sessions import SessionStore
from .skills import load_effective_skills
from .tools import load_enabled_tools


def handle(cli: Any, value: str) -> bool:
    command, _, rest = value.strip().partition(" ")
    command = command.lower() or "status"
    name = rest.strip()
    if command in {"status", "list", "ls"}:
        print_status(cli)
        return True
    if command == "index":
        index = refresh_dream_index(cli.state.dreams_dir)
        print(f"idx... {len(index.splitlines())} ligne(s)")
        return True
    if command == "context":
        print_context(cli, name)
        return True
    if command == "prompt":
        print_prompt(cli, name)
        return True
    if command == "preview":
        preview(cli, name)
        return True
    if command == "apply":
        apply_pending(cli, name)
        return True
    if command == "run":
        use_preview, clean_name = _preview_arg(name)
        if use_preview:
            preview(cli, clean_name)
        else:
            run(cli, clean_name)
        return True
    print("Usage: /dream [status|index|context [name]|prompt [name]|preview [name]|apply [name]|run [name]]")
    return True


def print_status(cli: Any) -> None:
    dreams = load_all(cli)
    if not dreams:
        print("Aucun dream configure.")
        return
    active = {dream.name for dream in load_enabled_dreams(cli.state.dreams_dir)}
    for dream in dreams:
        marker = "active" if dream.name in active else dream.activation
        summary = _short_message(dream.summary or "-")
        print(f"dream.. {dream.name} [{marker}/{dream.scope}] agent={dream.agent} -> {summary}")


def print_context(cli: Any, name: str = "") -> None:
    try:
        dream = select(cli, name)
        context, _agent = build_context(cli, dream)
    except (DreamNotFoundError, AgentNotFoundError) as exc:
        print(f"Erreur: {exc}")
        return
    print(f"dream.. {dream.name} [{dream.activation}/{dream.scope}]")
    print(f"mem... {len(context.memories)} noeud(s)")
    print(f"edge.. {len(context.edges)} relation(s)")
    print(f"ses... {len(context.sessions)} session(s)")
    print(f"con... {len(context.contributions)} contribution(s)")
    print(f"doc... decisions={'yes' if context.decisions_doc.strip() else 'no'} roadmap={'yes' if context.roadmap_doc.strip() else 'no'}")
    print(f"prm... {len(build_dreaming_prompt(dream, context))} caractère(s)")


def print_prompt(cli: Any, name: str = "") -> None:
    try:
        dream = select(cli, name)
        context, _agent = build_context(cli, dream)
    except (DreamNotFoundError, AgentNotFoundError) as exc:
        print(f"Erreur: {exc}")
        return
    print(build_dreaming_prompt(dream, context))


def preview(cli: Any, name: str = ""):
    try:
        dream = select(cli, name)
        context, agent = build_context(cli, dream)
        provider = cli.build_provider_for_agent(agent)
        if provider is None:
            print("Provider requis pour /dream preview. Utilise /dream prompt pour inspecter le contexte.")
            return None
        plan = plan_dreaming(dream, context, provider)
        save_pending_dream_plan(plan, cli.state.dream_pending_path)
    except (DreamNotFoundError, AgentNotFoundError, ProviderError) as exc:
        print(f"Erreur: {exc}")
        return None
    print_plan(cli, plan, saved=True)
    return plan


def apply_pending(cli: Any, name: str = ""):
    plan = load_pending_dream_plan(cli.state.dream_pending_path)
    if plan is None:
        print("Aucun dream en attente.")
        return None
    if name.strip() and plan.dream != name.strip():
        print(f"Dream en attente: {plan.dream}.")
        return None
    memory = MemoryStore(cli.state.memory_path)
    try:
        result = apply_dream_plan(plan, memory, project_root=Path.cwd())
    finally:
        memory.close()
    clear_pending_dream_plan(cli.state.dream_pending_path)
    print_result(result)
    cli.remember_turn(f"/dream apply {plan.dream}", result.summary or "Dreaming appliqué.")
    return result


def run(cli: Any, name: str = "", *, remember: bool = True):
    try:
        dream = select(cli, name)
        context, agent = build_context(cli, dream)
        provider = cli.build_provider_for_agent(agent)
        if provider is None:
            print("Provider requis pour /dream run. Utilise /dream prompt pour inspecter le contexte.")
            return None
        memory = MemoryStore(cli.state.memory_path)
        try:
            result = run_dreaming(
                dream,
                context,
                memory,
                provider,
                project_root=Path.cwd(),
            )
        finally:
            memory.close()
    except (DreamNotFoundError, AgentNotFoundError, ProviderError) as exc:
        print(f"Erreur: {exc}")
        return None

    print_result(result)
    if remember:
        cli.remember_turn(f"/dream run {dream.name}", result.summary or "Dreaming terminé.")
    return result


def print_plan(cli: Any, plan: Any, *, saved: bool = False) -> None:
    print(
        "dream.. preview "
        f"ops={len(plan.operations)} "
        f"actions={len(plan.actions)}"
    )
    if saved:
        print(f"pend.. {cli.state.dream_pending_path}")
    for operation in plan.operations[:5]:
        print("op.... " + _short_message(str(operation), limit=120))
    if len(plan.operations) > 5:
        print(f"op.... +{len(plan.operations) - 5}")
    if plan.actions:
        print(f"act... {len(plan.actions)} proposée(s)")
    if plan.summary.strip():
        print("sum... " + _short_message(plan.summary, limit=120))


def print_result(result: Any) -> None:
    print(
        "dream.. ok "
        f"add={result.added_nodes} "
        f"upd={result.updated_nodes} "
        f"del={result.removed_nodes} "
        f"edge={result.added_edges} "
        f"err={result.errors}"
    )
    if result.actions:
        print(f"act... {len(result.actions)} proposée(s)")
    if result.summary.strip():
        print("sum... " + _short_message(result.summary, limit=120))


def load_all(cli: Any) -> tuple[DreamSpec, ...]:
    return tuple(
        load_dream(cli.state.dreams_dir, name)
        for name in discover_dreams(cli.state.dreams_dir)
    )


def select(cli: Any, name: str = "") -> DreamSpec:
    requested = name.strip()
    if requested:
        return load_dream(cli.state.dreams_dir, requested)
    active = load_enabled_dreams(cli.state.dreams_dir)
    if active:
        return active[0]
    dreams = load_all(cli)
    if dreams:
        return dreams[0]
    raise DreamNotFoundError("Aucun dream configure.")


def build_context(cli: Any, dream: DreamSpec):
    agent = cli.load_agent_for_cron(dream.agent)
    local_skills_dir = Path.cwd() / ".bb9" / "skills"
    skills = load_effective_skills(cli.state.skills_dir, local_skills_dir, agent.disabled_skills)
    tools = load_enabled_tools(cli.state.tools_dir, agent.disabled_tools)
    memory = MemoryStore(cli.state.memory_path)
    sessions = SessionStore(cli.state.session_store_path)
    try:
        context = build_dreaming_context(
            memory,
            project_root=Path.cwd(),
            skill_contributions=_skill_dream_contributions(skills),
            tool_contributions=load_dream_contributions(
                cli.state.tools_dir,
                "tool",
                active_names=tuple(tool.name for tool in tools),
            ),
            session_store=sessions,
        )
    finally:
        sessions.close()
        memory.close()
    return context, agent


def _skill_dream_contributions(skills) -> tuple:
    contributions = []
    for skill in skills:
        if skill.root is None:
            continue
        try:
            contributions.append(load_dream_contribution(skill.root, skill.name, "skill"))
        except DreamNotFoundError:
            continue
    return tuple(contributions)


def _preview_arg(value: str) -> tuple[bool, str]:
    tokens = value.split()
    if not tokens:
        return False, ""
    preview_flags = {"--preview", "--dry-run", "--plan"}
    preview_flag = any(token in preview_flags for token in tokens)
    name = " ".join(token for token in tokens if token not in preview_flags)
    return preview_flag, name


def _short_message(text: str, limit: int = 64) -> str:
    plain = " ".join(text.split())
    if len(plain) <= limit:
        return plain
    return plain[: limit - 1] + "..."
