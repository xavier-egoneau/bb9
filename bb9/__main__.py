"""Command line entry point."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from .core.agents import (
    AgentNotFoundError,
    discover_agents,
    discover_subagents,
    load_agent,
    load_subagent,
    refresh_subagents_index,
)
from .core.channels import intention_from_text
from .core.cli import CliState, run_interactive
from .core.context_index import refresh_context_index
from .core.kernel import Kernel
from .core.logs import configure_logging
from .core.loop import run_once
from .core.models import RunContext, Session, Workspace
from .core.paths import default_content_dir
from .core.provider_config import (
    AUTH_API,
    ModelFetchError,
    PROVIDER_REGISTRY,
    ProviderEntry,
    ProviderStore,
    default_provider_config_path,
    fetch_models,
)
from .core.providers import ProviderError, provider_from_entry
from .core.settings import SettingsStore
from .core.skills import build_skills_index, discover_skills, load_enabled_skills, refresh_skills_index
from .core.tools import build_tools_index, discover_tools, load_enabled_tools, refresh_tools_index
from .core.trust import TrustedRoots


def _entry_for_provider_arg(
    provider: str,
    args: argparse.Namespace,
    store: ProviderStore,
    *,
    require_model: bool,
) -> ProviderEntry:
    if provider == "configured":
        entry = store.load().active_entry()
        if entry is None:
            if require_model:
                raise ProviderError("No configured provider. Lance /model en mode interactif ou utilise --provider echo.")
            raise ProviderError("No configured provider")
        if require_model and not entry.model:
            raise ProviderError(f"Configured provider has no model: {entry.name}")
        return entry

    definition = PROVIDER_REGISTRY.get(provider)
    if definition is None:
        if provider == "echo":
            raise ProviderError("echo has no model list")
        raise ProviderError(f"Unknown provider: {provider}")
    if require_model and not args.model:
        raise ProviderError(f"--model is required with --provider {provider}")
    api_key_ref = args.api_key_ref
    if not api_key_ref and definition.default_api_key_env:
        env_name = args.api_key_env if provider == "openai-compatible" else definition.default_api_key_env
        api_key_ref = f"env:{env_name}"
    return ProviderEntry(
        id="cli",
        name=provider,
        provider=provider,
        auth_type=AUTH_API,
        base_url=args.base_url or definition.default_base_url,
        api_key_ref=api_key_ref,
        model=args.model,
    )


def main() -> int:
    parser = argparse.ArgumentParser(prog="bb9")
    parser.add_argument("text", nargs="*", help="intention text")
    parser.add_argument("--log-level", default="WARNING")
    parser.add_argument("--profile", choices=["safe", "limited", "power"], default="")
    parser.add_argument("--show-trace", action="store_true")
    parser.add_argument("--provider", choices=["configured", "echo", "openai-compatible", "openai", "openrouter"], default="echo")
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--api-key-ref", default="")
    parser.add_argument("--provider-config-path", default=str(default_provider_config_path()))
    parser.add_argument("--list-providers", action="store_true")
    parser.add_argument("--list-models", nargs="?", const="configured", default="")
    parser.add_argument("--shell", default="", help="run a shell tool command")
    parser.add_argument("--agent", default="default")
    parser.add_argument("--subagent", default="")
    parser.add_argument("--agents-dir", default=str(default_content_dir("agents")))
    parser.add_argument("--list-agents", action="store_true")
    parser.add_argument("--list-subagents", action="store_true")
    parser.add_argument("--skills-dir", default=str(default_content_dir("skills")))
    parser.add_argument("--list-skills", action="store_true")
    parser.add_argument("--tools-dir", default=str(default_content_dir("tools")))
    parser.add_argument("--list-tools", action="store_true")
    parser.add_argument("--refresh-indexes", action="store_true")
    args = parser.parse_args()

    configure_logging(args.log_level)

    provider_store = ProviderStore(Path(args.provider_config_path))

    profile = args.profile or SettingsStore().load().profile

    if args.list_providers:
        print("Registry")
        for definition in PROVIDER_REGISTRY.values():
            auth = ", ".join(definition.supported_auth_types)
            print(f"- {definition.kind}: {definition.label} ({auth})")
        config = provider_store.load()
        if config.entries:
            print()
            print("Configured")
            active = config.active_entry()
            for entry in config.entries:
                marker = "*" if active and active.id == entry.id else "-"
                print(f"{marker} {entry.name}: {entry.provider} / {entry.model or '-'} ({entry.auth_type})")
        return 0

    if args.list_models:
        try:
            entry = _entry_for_provider_arg(args.list_models, args, provider_store, require_model=False)
            for model in fetch_models(entry):
                print(model)
        except (ModelFetchError, ProviderError) as exc:
            print(f"Provider error: {exc}")
            return 2
        return 0

    agents_root = Path(args.agents_dir)
    if args.list_agents:
        for name in discover_agents(agents_root):
            print(name)
        return 0

    if args.list_subagents:
        for name in discover_subagents(agents_root, args.agent):
            print(name)
        return 0

    skills_root = Path(args.skills_dir)
    tools_root = Path(args.tools_dir)
    refresh_skills_index(skills_root)
    refresh_tools_index(tools_root)
    subagents_index = refresh_subagents_index(agents_root, args.agent)

    if args.list_skills:
        for name in discover_skills(skills_root):
            print(name)
        return 0

    if args.refresh_indexes:
        print(refresh_skills_index(skills_root).strip())
        print()
        print(refresh_tools_index(tools_root).strip())
        print()
        print(subagents_index.strip())
        return 0

    if args.list_tools:
        for name in discover_tools(tools_root):
            print(name)
        return 0

    if args.shell:
        args.text = ["/action", "shell", args.shell]

    if not args.text:
        active_provider = None
        if args.provider == "configured":
            active_provider = provider_store.load().active_entry()
        return run_interactive(
            CliState(
                profile=profile,
                profile_explicit=bool(args.profile),
                provider_kind=args.provider,
                model=args.model,
                base_url=args.base_url,
                api_key_env=args.api_key_env,
                api_key_ref=args.api_key_ref,
                provider_config_path=Path(args.provider_config_path),
                active_provider=active_provider,
                agent_name=args.agent,
                subagent_name=args.subagent,
                agents_dir=Path(args.agents_dir),
                skills_dir=Path(args.skills_dir),
                tools_dir=Path(args.tools_dir),
                show_trace=args.show_trace,
                session=Session(source="cli"),
            )
        )

    try:
        if args.subagent:
            agent = load_subagent(agents_root, args.agent, args.subagent)
        else:
            agent = load_agent(agents_root, args.agent)
    except AgentNotFoundError as exc:
        print(f"Agent error: {exc}")
        return 2

    skills = load_enabled_skills(skills_root, agent.disabled_skills)
    tools = load_enabled_tools(tools_root, agent.disabled_tools)

    provider = None
    try:
        if args.provider != "echo":
            entry = _entry_for_provider_arg(args.provider, args, provider_store, require_model=True)
            if agent.model.strip() or agent.reasoning_effort.strip():
                metadata = dict(entry.metadata)
                if agent.reasoning_effort.strip():
                    metadata["reasoning_effort"] = agent.reasoning_effort.strip()
                entry = replace(
                    entry,
                    model=agent.model.strip() or entry.model,
                    metadata=metadata,
                )
            provider = provider_from_entry(entry)
    except ProviderError as exc:
        print(f"Provider error: {exc}")
        return 2

    intention = intention_from_text(" ".join(args.text))
    workspace = Workspace.current()
    context = RunContext(
        session=Session(source="cli"),
        workspace=workspace,
        permission_profile=profile,
        trusted_roots=TrustedRoots.load(),
        agent=agent,
        skills=skills,
        tools=tools,
        skills_index=build_skills_index(skills),
        tools_index=build_tools_index(tools),
        subagents_index=subagents_index,
        context_index=refresh_context_index(workspace.root),
    )
    try:
        result = run_once(Kernel(provider=provider), intention, context)
    except ProviderError as exc:
        print(f"Provider error: {exc}")
        return 2

    if result.observation is not None:
        print(result.observation.summary)
    else:
        print(result.decision.summary)

    if args.show_trace:
        for event in result.trace:
            print(f"{event.time} {event.event_type}: {event.summary}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
