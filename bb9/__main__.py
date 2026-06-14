"""Command line entry point."""

from __future__ import annotations

import argparse
import errno
import json
import os
import signal
import subprocess
import sys
import time
import webbrowser
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from .api.chat import ChatApiApp, ChatApiState
from .api.http import DEFAULT_PORT as WEB_CHAT_DEFAULT_PORT
from .api.http import HOST as WEB_CHAT_HOST
from .api.http import chat_api_server
from .channels.telegram import run_telegram_host
from .cli.main import CliState, run_interactive
from .core import runtime_service
from .core.agents import (
    AgentNotFoundError,
    discover_agents,
    discover_subagents,
    refresh_agents_index,
    refresh_subagents_index,
)
from .core.logs import configure_logging
from .core.models import Session
from .core.paths import default_content_dir
from .core.projects import (
    resolve_project_target,
    switch_process_workspace,
    workspace_safety_warning,
    workspace_switch_from_text,
)
from .core.sessions import AGENT_HOME_SOURCE, agent_home_session_id
from .core.settings import SettingsStore
from .core.skills import discover_skills, refresh_skills_index
from .core.tools import discover_tools, refresh_tools_index
from .providers.config import (
    AUTH_API,
    PROVIDER_REGISTRY,
    ModelFetchError,
    ProviderEntry,
    ProviderStore,
    default_provider_config_path,
    fetch_models,
)
from .providers.providers import ProviderError


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
        status = _web_chat_status(actual_port)
        workspace = str(status.get("workspace") or "")
        suffix = f" (workspace {workspace})" if workspace else ""
        print(f"BB9 web chat already running: {url}{suffix}")
    else:
        suffix = ""
        if actual_port != port and port != 0:
            requested_workspace = str(Path.cwd().resolve(strict=False))
            running = _web_chat_status(port)
            existing_workspace = str(running.get("workspace") or "")
            if existing_workspace and existing_workspace != requested_workspace:
                suffix = f" (port {port} sert déjà {existing_workspace})"
            else:
                suffix = f" (port {port} unavailable)"
        print(f"BB9 web chat: {url}{suffix}")
    if open_browser:
        opened = _open_browser_quietly(url)
        if not opened:
            print("Browser did not open automatically; open the URL above.")
    if server is None:
        return
    warning = workspace_safety_warning(Path.cwd())
    if warning:
        print(warning)
    app.start_routine_scheduler()
    app.start_telegram_channel()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
        print("Web chat stopped.")
    finally:
        app.stop_telegram_channel()
        app.stop_routine_scheduler()
        server.server_close()


def _open_chat_server(app: ChatApiApp, port: int):
    static_root = resources.files("bb9").joinpath("chat-web")
    last_error: OSError | None = None
    requested_workspace = str(Path.cwd().resolve(strict=False))
    for candidate in _candidate_web_ports(port):
        try:
            return chat_api_server(app, candidate, static_root=static_root)
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
            last_error = exc
            running = _web_chat_status(candidate)
            if candidate == port and running:
                if running.get("workspace") == requested_workspace:
                    return None
                switched = _switch_web_chat_project(candidate, requested_workspace)
                if switched.get("ok") and switched.get("workspace") == requested_workspace:
                    return None
            continue
    if last_error is not None:
        raise last_error
    return chat_api_server(app, port, static_root=static_root)


def _open_browser_quietly(url: str) -> bool:
    with _silenced_process_output():
        return bool(webbrowser.open(url))


@contextmanager
def _silenced_process_output() -> Iterator[None]:
    saved_fds: list[int] = []
    try:
        sys.stdout.flush()
        sys.stderr.flush()
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        saved_fds.append(devnull_fd)
        stdout_fd = os.dup(1)
        saved_fds.append(stdout_fd)
        stderr_fd = os.dup(2)
        saved_fds.append(stderr_fd)
    except OSError:
        for fd in saved_fds:
            os.close(fd)
        yield
        return
    try:
        os.dup2(devnull_fd, 1)
        os.dup2(devnull_fd, 2)
        yield
    finally:
        os.dup2(stdout_fd, 1)
        os.dup2(stderr_fd, 2)
        os.close(stderr_fd)
        os.close(stdout_fd)
        os.close(devnull_fd)


def _candidate_web_ports(port: int) -> list[int]:
    if port == 0:
        return [0]
    return [port + offset for offset in range(20)]


def _web_chat_status(port: int) -> dict[str, object]:
    try:
        with urlopen(f"http://{WEB_CHAT_HOST}:{port}/health", timeout=1) as response:
            if not 200 <= int(getattr(response, "status", 200)) < 300:
                return {}
            payload = json.loads(response.read().decode("utf-8"))
            if "image-api" not in (payload.get("features") or []):
                return {}
        with urlopen(f"http://{WEB_CHAT_HOST}:{port}/api/status", timeout=1) as response:
            if not 200 <= int(getattr(response, "status", 200)) < 300:
                return {"ok": True}
            status = json.loads(response.read().decode("utf-8"))
            if isinstance(status, dict):
                return status
            return {"ok": True}
    except (OSError, URLError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _switch_web_chat_project(port: int, workspace: str) -> dict[str, object]:
    body = json.dumps({"path": workspace}).encode("utf-8")
    request = Request(
        f"http://{WEB_CHAT_HOST}:{port}/api/project",
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=3) as response:
            if not 200 <= int(getattr(response, "status", 200)) < 300:
                return {}
            payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict):
                return payload
            return {}
    except (OSError, URLError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _arg_was_passed(name: str) -> bool:
    return any(arg == name or arg.startswith(f"{name}=") for arg in sys.argv[1:])


def stop_bb9_processes(*, grace_seconds: float = 3.0) -> int:
    current_pid = os.getpid()
    parent_pid = os.getppid()
    targets = _bb9_process_targets(current_pid=current_pid, parent_pid=parent_pid)
    if not targets:
        print("Aucun process BB9 à arrêter.")
        return 0
    for pid, command in targets:
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"Arrêt demandé: {pid} {command}")
        except ProcessLookupError:
            continue
        except PermissionError:
            print(f"Permission refusée: {pid} {command}")
    deadline = time.monotonic() + max(0.1, grace_seconds)
    while time.monotonic() < deadline:
        remaining = [(pid, command) for pid, command in targets if _process_exists(pid)]
        if not remaining:
            print(f"BB9 stoppé ({len(targets)} process).")
            return 0
        time.sleep(0.1)
    forced = 0
    for pid, command in targets:
        if not _process_exists(pid):
            continue
        try:
            os.kill(pid, signal.SIGKILL)
            forced += 1
            print(f"Arrêt forcé: {pid} {command}")
        except ProcessLookupError:
            pass
        except PermissionError:
            print(f"Permission refusée pour arrêt forcé: {pid} {command}")
    print(f"BB9 stoppé ({len(targets)} process, {forced} forcé{'' if forced <= 1 else 's'}).")
    return 0


def _bb9_process_targets(*, current_pid: int, parent_pid: int) -> list[tuple[int, str]]:
    try:
        output = subprocess.check_output(
            ["ps", "-eo", "pid=,ppid=,args="],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    targets: list[tuple[int, str]] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(maxsplit=2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        command = parts[2]
        if pid in {current_pid, parent_pid}:
            continue
        if _is_bb9_process_command(command):
            targets.append((pid, command))
    return targets


def _is_bb9_process_command(command: str) -> bool:
    text = f" {command} "
    if " -m bb9 " in text:
        return True
    first = command.split(maxsplit=1)[0] if command.split() else ""
    return first.endswith("/bb9") or first == "bb9"


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="bb9",
        epilog="Commands: bb9 web starts the local web chat channel; bb9 telegram starts Telegram; bb9 stop stops local BB9 processes.",
    )
    parser.add_argument("text", nargs="*", help="intention text")
    parser.add_argument("--log-level", default="WARNING")
    parser.add_argument("--profile", choices=["safe", "limited", "power"], default="")
    parser.add_argument("--show-trace", action="store_true")
    parser.add_argument("--provider", choices=["configured", "echo", *PROVIDER_REGISTRY.keys()], default="echo")
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
    parser.add_argument("--telegram", action="store_true", help="start the Telegram channel for the active agent")
    parser.add_argument("--telegram-once", action="store_true", help="poll Telegram once then exit")
    parser.add_argument("--telegram-poll-timeout", type=int, default=25)
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
    refresh_agents_index(agents_root)
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
        print(refresh_agents_index(agents_root).strip())
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
    if args.text == ["telegram"]:
        args.telegram = True
        args.text = []
    if args.text == ["stop"]:
        return stop_bb9_processes()

    if args.web_chat and not provider_explicit and args.provider == "echo":
        args.provider = "configured"
    if args.telegram and not provider_explicit and args.provider == "echo":
        args.provider = "configured"

    if args.web_chat:
        active_provider = None
        try:
            if args.provider != "echo":
                active_provider = _entry_for_provider_arg(args.provider, args, provider_store, require_model=False)
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
                restore_web_project=True,
                session=Session(source="web"),
            ),
            port=args.web_port,
            open_browser=not args.no_open,
        )
        return 0

    if args.telegram:
        active_provider = None
        try:
            if args.provider != "echo":
                active_provider = _entry_for_provider_arg(args.provider, args, provider_store, require_model=False)
        except ProviderError as exc:
            print(f"Provider error: {exc}")
            return 2
        return run_telegram_host(
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
                session=Session(id=agent_home_session_id(args.agent), source=AGENT_HOME_SOURCE),
            ),
            once=args.telegram_once,
            poll_timeout=max(1, int(args.telegram_poll_timeout or 25)),
        )

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
    text = " ".join(args.text)
    switch_notice = ""
    request = workspace_switch_from_text(text)
    if request is not None:
        resolution = resolve_project_target(
            request.target,
            session_store_path=state.session_store_path,
            cwd=Path.cwd(),
        )
        if not resolution.ok or resolution.path is None:
            print(f"Project error: {resolution.message or resolution.error or request.target}")
            return 2
        try:
            path = switch_process_workspace(resolution.path)
        except OSError as exc:
            print(f"Project error: {exc}")
            return 2
        switch_notice = f"Workspace actif: `{path}`."
        if not request.remainder.strip():
            print(switch_notice)
            return 0
        text = request.remainder.strip()
    try:
        turn = runtime_service.run_message(state, text)
    except AgentNotFoundError as exc:
        print(f"Agent error: {exc}")
        return 2
    except ProviderError as exc:
        print(f"Provider error: {exc}")
        return 2

    answer = f"{switch_notice}\n\n{turn.answer}" if switch_notice else turn.answer
    print(answer)

    if args.show_trace:
        for event in turn.result.trace:
            print(f"{event.time} {event.event_type}: {event.summary}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
