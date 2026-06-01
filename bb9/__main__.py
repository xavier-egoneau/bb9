"""Command line entry point."""

from __future__ import annotations

import argparse
import errno
import json
import sys
import webbrowser
from importlib import resources
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from .api.chat import ChatApiApp, ChatApiState
from .api.http import DEFAULT_PORT as WEB_CHAT_DEFAULT_PORT
from .api.http import HOST as WEB_CHAT_HOST
from .api.http import chat_api_server
from .core import runtime_service
from .core.agents import (
    AgentNotFoundError,
    discover_agents,
    discover_subagents,
    refresh_subagents_index,
)
from .core.cli import CliState, run_interactive
from .core.logs import configure_logging
from .core.models import Session
from .core.paths import default_content_dir
from .core.provider_config import (
    AUTH_API,
    PROVIDER_REGISTRY,
    ModelFetchError,
    ProviderEntry,
    ProviderStore,
    default_provider_config_path,
    fetch_models,
)
from .core.providers import ProviderError
from .core.settings import SettingsStore
from .core.skills import discover_skills, refresh_skills_index
from .core.tools import discover_tools, refresh_tools_index


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


def serve_chat_web(state: ChatApiState, *, port: int = WEB_CHAT_DEFAULT_PORT, open_browser: bool = True) -> None:
    app = ChatApiApp(state)
    server = _open_chat_server(app, port)
    actual_port = port if server is None else int(server.server_port)
    url = f"http://{WEB_CHAT_HOST}:{actual_port}"
    if server is None:
        print(f"BB9 web chat already running: {url}")
    else:
        suffix = f" (port {port} unavailable)" if actual_port != port and port != 0 else ""
        print(f"BB9 web chat: {url}{suffix}")
    if open_browser:
        webbrowser.open(url)
    if server is None:
        return
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
        print("Web chat stopped.")
    finally:
        server.server_close()


def _open_chat_server(app: ChatApiApp, port: int):
    static_root = resources.files("bb9").joinpath("chat-web")
    last_error: OSError | None = None
    for candidate in _candidate_web_ports(port):
        try:
            return chat_api_server(app, candidate, static_root=static_root)
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
            last_error = exc
            if candidate == port and _web_chat_is_running(candidate):
                return None
            continue
    if last_error is not None:
        raise last_error
    return chat_api_server(app, port, static_root=static_root)


def _candidate_web_ports(port: int) -> list[int]:
    if port == 0:
        return [0]
    return [port + offset for offset in range(20)]


def _web_chat_is_running(port: int) -> bool:
    try:
        with urlopen(f"http://{WEB_CHAT_HOST}:{port}/health", timeout=1) as response:
            if not 200 <= int(getattr(response, "status", 200)) < 300:
                return False
            payload = json.loads(response.read().decode("utf-8"))
            return "image-api" in (payload.get("features") or [])
    except (OSError, URLError, UnicodeDecodeError, json.JSONDecodeError):
        return False


def _arg_was_passed(name: str) -> bool:
    return any(arg == name or arg.startswith(f"{name}=") for arg in sys.argv[1:])


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="bb9",
        epilog="Commands: bb9 web starts the local web chat channel.",
    )
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
    parser.add_argument("--web-chat", action="store_true", help="start the local web chat channel")
    parser.add_argument("--web-port", type=int, default=WEB_CHAT_DEFAULT_PORT)
    parser.add_argument("--no-open", action="store_true", help="do not open the browser for local web surfaces")
    args = parser.parse_args()
    provider_explicit = _arg_was_passed("--provider")

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

    if args.text == ["web"]:
        args.web_chat = True
        args.text = []

    if args.web_chat and not provider_explicit and args.provider == "echo":
        args.provider = "configured"

    if args.web_chat:
        active_provider = None
        try:
            if args.provider != "echo":
                active_provider = _entry_for_provider_arg(args.provider, args, provider_store, require_model=True)
        except ProviderError as exc:
            print(f"Provider error: {exc}")
            return 2
        serve_chat_web(
            ChatApiState(
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
                session=Session(source="web"),
            ),
            port=args.web_port,
            open_browser=not args.no_open,
        )
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

    active_provider = None
    try:
        if args.provider != "echo":
            active_provider = _entry_for_provider_arg(args.provider, args, provider_store, require_model=True)
    except ProviderError as exc:
        print(f"Provider error: {exc}")
        return 2

    state = CliState(
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
        agents_dir=agents_root,
        skills_dir=skills_root,
        tools_dir=tools_root,
        show_trace=args.show_trace,
        session=Session(source="cli"),
    )
    try:
        turn = runtime_service.run_message(state, " ".join(args.text))
    except AgentNotFoundError as exc:
        print(f"Agent error: {exc}")
        return 2
    except ProviderError as exc:
        print(f"Provider error: {exc}")
        return 2

    print(turn.answer)

    if args.show_trace:
        for event in turn.result.trace:
            print(f"{event.time} {event.event_type}: {event.summary}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
