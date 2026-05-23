"""Interactive command line interface."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field, replace
from getpass import getpass
from pathlib import Path
from typing import Callable, cast

from .agents import AgentNotFoundError, load_agent, load_subagent, refresh_subagents_index
from .auth_flow import ChatGPTOAuthFlow, OAuthError
from .channels import intention_from_text
from .compaction import CompactionConfig, auto_compact_session, compact_session, estimate_session_tokens
from .context_index import refresh_context_index
from .goals import GoalCommandHandler, GoalLoopRunner, GoalManager
from .kernel import Kernel
from .loop import ApprovalDecision, ApprovalResult, run_once, tool_budget_for
from .models import AgentProfile, GuardianDecision, PermissionProfile, RunContext, Session, Workspace
from .model_metadata import resolve_model_metadata
from .paths import default_content_dir
from .provider_config import (
    AUTH_API,
    AUTH_WEB,
    ModelFetchError,
    PROVIDER_REGISTRY,
    ProviderEntry,
    ProviderStore,
    default_web_token_path,
    default_provider_config_path,
    fetch_models,
    normalize_base_url,
    public_secret_label,
    write_web_token,
)
from .providers import OpenAICompatibleProvider, Provider, ProviderError, provider_from_entry
from .settings import PROFILES, SettingsStore
from .skills import build_skills_index, load_enabled_skills, refresh_skills_index
from .tool_runtime import load_skill_module, load_tool_module
from .tools import build_tools_index, load_enabled_tools, refresh_tools_index
from .trust import TrustedRoots


CommandHandler = Callable[[str], bool]
InputInterceptor = Callable[[str], bool]
ApprovalHandler = Callable[[GuardianDecision, RunContext], ApprovalResult | ApprovalDecision | None]
ContextLineProvider = Callable[[RunContext], str]


@dataclass
class CliCommand:
    command: str
    description: str
    show_in_help: bool = True
    show_in_banner: bool = False


@dataclass
class LocalCapture:
    prompt: str
    label: str
    on_value: Callable[[str], None]
    cancel_summary: str = "Capture annulee."


@dataclass
class CliState:
    profile: PermissionProfile = "safe"
    profile_explicit: bool = False
    provider_kind: str = "echo"
    model: str = ""
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    api_key_ref: str = ""
    provider_config_path: Path = field(default_factory=default_provider_config_path)
    active_provider: ProviderEntry | None = None
    agent_name: str = "default"
    subagent_name: str = ""
    agents_dir: Path = field(default_factory=lambda: default_content_dir("agents"))
    skills_dir: Path = field(default_factory=lambda: default_content_dir("skills"))
    tools_dir: Path = field(default_factory=lambda: default_content_dir("tools"))
    show_trace: bool = False
    session: Session = field(default_factory=lambda: Session(source="cli"))


class Cli:
    def __init__(self, state: CliState | None = None) -> None:
        self.state = state or CliState()
        self.theme = CliTheme(enabled=_supports_color())
        self.commands: dict[str, CommandHandler] = {}
        self.command_specs: list[CliCommand] = []
        self.input_interceptors: list[InputInterceptor] = []
        self.approval_handlers: list[ApprovalHandler] = []
        self.context_line_providers: list[ContextLineProvider] = []
        self.local_capture: LocalCapture | None = None
        self.loaded_tool_cli: set[str] = set()
        self.loaded_skill_cli: set[str] = set()
        self.goal_manager = GoalManager()
        if not self.state.profile_explicit:
            self.state.profile = SettingsStore().load().profile
        self.add_command("/help", self.cmd_help, "afficher l'aide", show_in_banner=True)
        self.add_command("/exit", self.cmd_exit, "quitter", show_in_banner=True)
        self.add_command("/quit", self.cmd_exit, "", show_in_help=False)
        self.add_command("/context", self.cmd_context, "afficher l'etat courant", show_in_banner=True)
        self.add_command("/compact", self.cmd_compact, "compacter le contexte court", show_in_banner=True)
        self.add_command("/new", self.cmd_new, "nouvelle session", show_in_banner=True)
        self.add_command("/model", self.cmd_model, "choisir provider et modele", show_in_banner=True)
        self.add_command("/goal", self.cmd_goal, "objectif autonome", show_in_banner=True)
        self.add_command("/profil", self.cmd_profile, "changer le niveau de permission", show_in_banner=True)
        self.add_command("/profile", self.cmd_profile, "", show_in_help=False)

    def add_command(
        self,
        command: str,
        handler: CommandHandler,
        description: str,
        *,
        show_in_help: bool = True,
        show_in_banner: bool = False,
    ) -> None:
        self.commands[command] = handler
        self.command_specs = [spec for spec in self.command_specs if spec.command != command]
        self.command_specs.append(CliCommand(command, description, show_in_help, show_in_banner))

    def add_input_interceptor(self, handler: InputInterceptor) -> None:
        self.input_interceptors.append(handler)

    def add_approval_handler(self, handler: ApprovalHandler) -> None:
        self.approval_handlers.append(handler)

    def add_context_line(self, handler: ContextLineProvider) -> None:
        self.context_line_providers.append(handler)

    def open_local_capture(
        self,
        *,
        prompt: str,
        label: str,
        on_value: Callable[[str], None],
        cancel_summary: str = "Capture annulee.",
    ) -> None:
        self.local_capture = LocalCapture(prompt=prompt, label=label, on_value=on_value, cancel_summary=cancel_summary)

    def run(self) -> int:
        self.refresh_indexes()
        self.load_tool_cli_extensions()
        self.load_skill_cli_extensions()
        self.load_saved_provider()
        self.print_banner()
        while True:
            try:
                if self.local_capture is not None:
                    line = getpass(self.theme.accent(self.local_capture.prompt) + " ").strip()
                else:
                    line = input(self.theme.accent(">") + " ").strip()
            except EOFError:
                print()
                return 0
            except KeyboardInterrupt:
                print()
                return 130

            if not line:
                continue
            if self.local_capture is not None:
                if line == "/cancel":
                    print(self.local_capture.cancel_summary)
                    self.local_capture = None
                    continue
                capture = self.local_capture
                self.local_capture = None
                capture.on_value(line)
                continue
            if line == "?":
                self.cmd_help("")
                continue
            if line.startswith("/"):
                if not self.handle_command(line):
                    return 0
                continue
            self.run_intention(line)

    def handle_command(self, line: str) -> bool:
        command, _, rest = line.partition(" ")
        handler = self.commands.get(command)
        if handler is None:
            print(f"Commande inconnue: {command}")
            print("Tape /help pour la liste.")
            return True
        return handler(rest.strip())

    def run_intention(self, text: str) -> None:
        for interceptor in self.input_interceptors:
            if interceptor(text):
                return
        try:
            context = self.build_context()
            result = run_once(
                Kernel(provider=self.build_provider()),
                intention_from_text(text),
                context,
                ask_user=self.ask_guardian,
            )
        except (AgentNotFoundError, ProviderError) as exc:
            print(f"Erreur: {exc}")
            return
        except KeyboardInterrupt:
            print()
            print("Interrompu.")
            return

        if result.observation is not None:
            print(result.observation.summary)
            self.remember_turn(text, result.observation.summary)
        else:
            print(result.decision.summary)
            self.remember_turn(text, result.decision.summary)
        if self.state.show_trace:
            for event in result.trace:
                print(f"{event.time} {event.event_type}: {event.summary}")

    def remember_turn(self, user_text: str, assistant_text: str) -> None:
        self.state.session = self.state.session.with_message("user", user_text)
        self.state.session = self.state.session.with_message("assistant", assistant_text)
        result = auto_compact_session(self.state.session, config=self.compaction_config())
        if result.changed:
            self.state.session = result.session
            print(f"cmp... auto: {result.compacted_messages} message(s)")

    def build_context(self) -> RunContext:
        return self.build_context_with_agent(self.load_current_agent())

    def build_goal_context(self) -> RunContext:
        return self.build_context_with_agent(self.load_goal_worker_agent())

    def build_context_with_agent(self, agent) -> RunContext:
        skills = load_enabled_skills(self.state.skills_dir, agent.disabled_skills)
        tools = load_enabled_tools(self.state.tools_dir, agent.disabled_tools)
        workspace = Workspace.current()
        return RunContext(
            session=self.state.session,
            workspace=workspace,
            permission_profile=self.state.profile,
            trusted_roots=TrustedRoots.load(),
            agent=agent,
            skills=skills,
            tools=tools,
            skills_index=build_skills_index(skills),
            tools_index=build_tools_index(tools),
            subagents_index=refresh_subagents_index(self.state.agents_dir, self.state.agent_name),
            context_index=refresh_context_index(workspace.root),
        )

    def refresh_indexes(self) -> None:
        refresh_skills_index(self.state.skills_dir)
        refresh_tools_index(self.state.tools_dir)

    def load_tool_cli_extensions(self) -> None:
        try:
            agent = self.load_current_agent()
        except AgentNotFoundError:
            return
        for tool in load_enabled_tools(self.state.tools_dir, agent.disabled_tools):
            if tool.name in self.loaded_tool_cli:
                continue
            module = load_tool_module(tool.name, "cli", self.state.tools_dir)
            if module is None or not hasattr(module, "register"):
                continue
            module.register(self)
            self.loaded_tool_cli.add(tool.name)

    def load_skill_cli_extensions(self) -> None:
        try:
            agent = self.load_current_agent()
        except AgentNotFoundError:
            return
        for skill in load_enabled_skills(self.state.skills_dir, agent.disabled_skills):
            if skill.name in self.loaded_skill_cli:
                continue
            module = load_skill_module(skill.name, "cli", self.state.skills_dir)
            if module is None or not hasattr(module, "register"):
                continue
            module.register(self)
            self.loaded_skill_cli.add(skill.name)

    def load_current_agent(self) -> AgentProfile:
        if self.state.subagent_name:
            return load_subagent(
                self.state.agents_dir,
                self.state.agent_name,
                self.state.subagent_name,
            )
        return load_agent(self.state.agents_dir, self.state.agent_name)

    def load_goal_worker_agent(self) -> AgentProfile:
        for subagent_name in ("goal", "default"):
            try:
                return load_subagent(self.state.agents_dir, self.state.agent_name, subagent_name)
            except AgentNotFoundError:
                continue
        return self.load_current_agent()

    def build_provider(self) -> Provider | None:
        return self.build_provider_for_agent(self.load_current_agent())

    def build_goal_provider(self) -> Provider | None:
        return self.build_provider_for_agent(self.load_goal_worker_agent())

    def build_provider_for_agent(self, agent: AgentProfile) -> Provider | None:
        model_override = agent.model.strip()
        reasoning_effort = agent.reasoning_effort.strip()
        if self.state.active_provider is not None:
            entry = self.state.active_provider
            metadata = dict(entry.metadata)
            if reasoning_effort:
                metadata["reasoning_effort"] = reasoning_effort
            if model_override or reasoning_effort:
                entry = replace(
                    entry,
                    model=model_override or entry.model,
                    metadata=metadata,
                )
            return provider_from_entry(entry)
        if self.state.provider_kind == "echo":
            return None
        if self.state.provider_kind == "openai-compatible":
            model = model_override or self.state.model
            if not model:
                raise ProviderError("model is required for openai-compatible provider")
            return OpenAICompatibleProvider(
                model=model,
                base_url=self.state.base_url,
                api_key_env=self.state.api_key_env,
                api_key_ref=self.state.api_key_ref,
                reasoning_effort=reasoning_effort,
            )
        raise ProviderError(f"unknown provider: {self.state.provider_kind}")

    def load_saved_provider(self) -> None:
        entry = ProviderStore(self.state.provider_config_path).load().active_entry()
        if entry is not None:
            self.set_active_provider(entry)

    def set_active_provider(self, entry: ProviderEntry) -> None:
        self.state.active_provider = entry
        self.state.provider_kind = entry.provider
        self.state.model = entry.model
        self.state.base_url = entry.base_url
        self.state.api_key_ref = entry.api_key_ref

    def print_banner(self) -> None:
        width = _banner_width()
        inner = width - 4
        logo = _bb9_logo()
        status = self.status_lines()
        commands = [
            (spec.command, spec.description)
            for spec in self.command_specs
            if spec.show_in_banner and spec.description
        ]

        print()
        print(self.theme.border("╭" + "─" * (width - 2) + "╮"))
        for line in logo:
            print(self._box_line(self.theme.logo(line), inner))
        print(self._box_line("", inner))

        split = max(28, min(34, inner // 2 - 2))
        right_width = inner - split - 3
        print(
            self._box_line(
                self.theme.title("Etat") + " " * max(1, split - 4)
                + self.theme.title("Pour demarrer"),
                inner,
            )
        )
        rows = max(len(status), len(commands))
        for index in range(rows):
            left = status[index] if index < len(status) else ""
            if index < len(commands):
                command, desc = commands[index]
                right = _pad_visible(self.theme.command(command), 18) + self.theme.dim(desc)
            else:
                right = ""
            line = _pad_visible(left, split) + "   " + _pad_visible(right, right_width)
            print(self._box_line(line, inner))

        print(self._box_line("", inner))
        print(self._box_line(self.theme.title("Activite recente"), inner))
        print(self._box_line("  " + self.theme.dim("Aucune activite recente"), inner))
        print(self.theme.border("╰" + "─" * (width - 2) + "╯"))
        print(self.theme.dim("? pour les raccourcis  ·  /exit ou Ctrl-D pour quitter"))
        print()

    def print_status(self) -> None:
        for line in self.status_lines():
            print(_strip_ansi(line))

    def status_lines(self) -> list[str]:
        agent = self.state.agent_name
        if self.state.subagent_name:
            agent = f"{agent}/{self.state.subagent_name}"
        provider = self.state.provider_kind
        if self.state.active_provider is not None:
            provider = self.state.active_provider.name
        model = self.state.model or "-"
        context = self._context_hint()
        return [
            self._status_line("pro", self.state.profile),
            self._status_line("llm", f"{provider} · {model}"),
            self._status_line("age", agent),
            self._status_line("ses", self.state.session.id[:8]),
            self._status_line("con", context),
        ]

    def _status_line(self, label: str, value: str) -> str:
        return f"{self.theme.dim(label + '...')} {value}"

    def _context_hint(self) -> str:
        try:
            agent = self.load_current_agent()
        except AgentNotFoundError:
            return "-"
        parts = []
        if agent.soul.strip():
            parts.append("soul")
        if agent.identity.strip():
            parts.append("identity")
        if agent.disabled_skills:
            parts.append("skills-")
        if agent.disabled_tools:
            parts.append("tools-")
        return " · ".join(parts) or "-"

    def _box_line(self, text: str, inner_width: int) -> str:
        visible = _visible_len(text)
        if visible > inner_width:
            text = _truncate_visible(text, inner_width)
            visible = _visible_len(text)
        return self.theme.border("│ ") + text + " " * (inner_width - visible) + self.theme.border(" │")

    def cmd_help(self, _: str) -> bool:
        print(self.theme.title("Commandes disponibles"))
        for spec in self.command_specs:
            if spec.show_in_help and spec.description:
                print(_pad_visible(self.theme.command(spec.command), 18) + self.theme.dim(spec.description))
        return True

    def cmd_exit(self, _: str) -> bool:
        return False

    def cmd_context(self, _: str) -> bool:
        try:
            context = self.build_context()
        except AgentNotFoundError as exc:
            print(f"Erreur: {exc}")
            return True
        self.print_status()
        print(f"wrk... {context.workspace.root}")
        print(f"ski... {', '.join(skill.name for skill in context.skills) or '-'}")
        print(f"too... {', '.join(tool.name for tool in context.tools) or '-'}")
        print(f"sub... {_short_index_names(context.subagents_index) or '-'}")
        trusted = context.trusted_roots.roots if context.trusted_roots else ()
        print(f"tru... {len(trusted)} trusted root(s)")
        soul = context.agent.soul if context.agent is not None else ""
        print(f"bud... {tool_budget_for(context.permission_profile, soul)} tool step(s)")
        print(f"ctx... {len(context.session.messages)} message(s) courts")
        metadata = self.active_model_metadata()
        print(
            f"cmp... {context.session.compacted_count} message(s), "
            f"~{estimate_session_tokens(context.session)} tok / {metadata.context_window_tokens}"
        )
        print(f"cix... {len(context.context_index.splitlines())} ligne(s)")
        for provider in self.context_line_providers:
            line = provider(context)
            if line:
                print(line)
        if context.session.messages:
            print("rec... " + " | ".join(_short_message(message.as_prompt_line()) for message in context.session.messages[-4:]))
        print("tra... conversation")
        return True

    def ask_guardian(self, decision: GuardianDecision, _: RunContext) -> ApprovalResult | ApprovalDecision:
        action = decision.action
        print()
        print(self.theme.title("Validation requise"))
        print(f"raison... {decision.reason}")
        if action is not None:
            print(f"tool..... {action.name}")
            if action.name == "shell":
                print(f"cmd...... {action.params.get('cmd', '')}")

        for handler in self.approval_handlers:
            handled = handler(decision, _)
            if handled is not None:
                return handled

        trust_root = _trusted_root_candidate(decision.reason)
        if trust_root is not None:
            print(f"trust.... {trust_root}")
            raw = input("Autoriser ? [y] une fois, [t] ajouter trusted root, [N] refuser : ").strip().lower()
            if raw == "t":
                try:
                    added = TrustedRoots.add(trust_root)
                except ValueError as exc:
                    print(f"Trusted root refuse: {exc}")
                    return "deny"
                print(f"Trusted root ajoute: {added}")
                return "allow"
            if raw in {"y", "yes", "o", "oui"}:
                return "allow"
            return "deny"

        raw = input("Autoriser une fois ? [y/N] : ").strip().lower()
        if raw in {"y", "yes", "o", "oui"}:
            return "allow"
        return "deny"

    def cmd_new(self, _: str) -> bool:
        self.state.session = Session(source="cli")
        print(f"Nouvelle session: {self.state.session.id[:8]}")
        return True

    def cmd_compact(self, _: str) -> bool:
        result = compact_session(self.state.session, force=True, config=self.compaction_config())
        self.state.session = result.session
        print(result.notice())
        return True

    def cmd_model(self, value: str) -> bool:
        if value.strip() == "show":
            self.print_provider_details()
            return True
        self.run_model_wizard()
        return True

    def cmd_goal(self, value: str) -> bool:
        runner = GoalLoopRunner(
            self.goal_manager,
            build_context=self.build_goal_context,
            build_provider=self.build_goal_provider,
            ask_user=self.ask_guardian,
            remember_turn=self.remember_turn,
            write=print,
        )
        return GoalCommandHandler(self.goal_manager, runner, write=print).handle(value)

    def cmd_profile(self, value: str) -> bool:
        profiles = PROFILES
        requested = value.strip().lower()
        if requested:
            if requested not in profiles:
                print("Profil inconnu. Choix possibles: safe, limited, power")
                return True
            self.state.profile = cast(PermissionProfile, requested)
            SettingsStore().set_profile(self.state.profile)
            print(f"Profil actif: {self.state.profile}")
            return True

        print(f"Profil actif: {self.state.profile}")
        for index, profile in enumerate(profiles, 1):
            marker = "*" if profile == self.state.profile else " "
            print(f"{index}. {marker} {profile}")
        raw = input("Profil [1-3] : ").strip()
        if not raw:
            return True
        try:
            choice = int(raw)
        except ValueError:
            print("Choix annule.")
            return True
        if not 1 <= choice <= len(profiles):
            print("Choix annule.")
            return True
        self.state.profile = profiles[choice - 1]
        SettingsStore().set_profile(self.state.profile)
        print(f"Profil actif: {self.state.profile}")
        return True

    def print_provider_details(self) -> None:
        if self.state.active_provider is None:
            print(self.state.model or "-")
            return
        entry = self.state.active_provider
        print(f"provider... {entry.name} ({entry.provider})")
        print(f"auth....... {entry.auth_type}")
        print(f"base....... {normalize_base_url(entry.provider, entry.base_url) or '-'}")
        print(f"secret..... {public_secret_label(entry.api_key_ref) or '-'}")
        print(f"model...... {entry.model or '-'}")
        metadata = self.active_model_metadata()
        print(f"context.... {metadata.context_window_tokens} ({metadata.source})")
        if metadata.soft_input_limit_tokens:
            print(f"soft....... {metadata.soft_input_limit_tokens}")

    def active_model_metadata(self):
        return resolve_model_metadata(self.active_model_name())

    def compaction_config(self) -> CompactionConfig:
        metadata = self.active_model_metadata()
        return CompactionConfig(
            context_window_tokens=metadata.context_window_tokens,
            soft_input_limit_tokens=metadata.soft_input_limit_tokens,
        )

    def active_model_name(self) -> str:
        try:
            agent = self.load_current_agent()
        except AgentNotFoundError:
            agent = None
        if agent is not None and agent.model.strip():
            return agent.model.strip()
        if self.state.active_provider is not None and self.state.active_provider.model.strip():
            return self.state.active_provider.model.strip()
        return self.state.model.strip()

    def run_model_wizard(self) -> None:
        store = ProviderStore(self.state.provider_config_path)
        config = store.load()
        entries = list(config.entries)

        print()
        print("Choix du provider et du modele")
        if entries:
            active = config.active_entry()
            if active is not None:
                print(f"Actif: {active.name} / {active.model or '-'}")
            print()
            for index, entry in enumerate(entries, 1):
                marker = "*" if active and entry.id == active.id else " "
                print(f"{index}. {marker} {entry.name} ({entry.provider}, {entry.auth_type}) / {entry.model or '-'}")
            print(f"{len(entries) + 1}. + ajouter un provider")
            raw = input(f"Choix [1-{len(entries) + 1}] : ").strip()
            if not raw and active is not None:
                self.configure_existing_provider(store, active)
                return
            try:
                choice = int(raw)
            except ValueError:
                print("Choix annule.")
                return
            if 1 <= choice <= len(entries):
                self.configure_existing_provider(store, entries[choice - 1])
                return
            if choice != len(entries) + 1:
                print("Choix annule.")
                return

        self.add_provider(store)

    def configure_existing_provider(self, store: ProviderStore, entry: ProviderEntry) -> None:
        models = self.fetch_models_for_wizard(entry)
        model = self.choose_model(models, current=entry.model)
        if not model:
            print("Choix annule.")
            return
        updated = ProviderEntry(
            id=entry.id,
            name=entry.name,
            provider=entry.provider,
            auth_type=entry.auth_type,
            base_url=entry.base_url,
            api_key_ref=entry.api_key_ref,
            model=model,
            added_at=entry.added_at,
            metadata=entry.metadata,
        )
        store.upsert(updated, active=True)
        self.set_active_provider(updated)
        print(f"Modele actif: {updated.name} / {updated.model}")

    def add_provider(self, store: ProviderStore) -> None:
        definitions = list(PROVIDER_REGISTRY.values())
        print()
        print("Providers")
        for index, definition in enumerate(definitions, 1):
            print(f"{index}. {definition.label} ({definition.kind})")
        raw = input(f"Provider [1-{len(definitions)}] : ").strip()
        try:
            provider_choice = int(raw)
        except ValueError:
            print("Ajout annule.")
            return
        if not 1 <= provider_choice <= len(definitions):
            print("Ajout annule.")
            return

        definition = definitions[provider_choice - 1]
        auth_types = list(definition.supported_auth_types)
        print()
        print("Authentification")
        for index, auth_type in enumerate(auth_types, 1):
            label = "API key via env/file" if auth_type == AUTH_API else "web/auth locale"
            print(f"{index}. {auth_type} - {label}")
        raw = input(f"Auth [1-{len(auth_types)}] : ").strip()
        try:
            auth_choice = int(raw)
        except ValueError:
            print("Ajout annule.")
            return
        if not 1 <= auth_choice <= len(auth_types):
            print("Ajout annule.")
            return

        auth_type = auth_types[auth_choice - 1]
        provider_id = ProviderEntry.new_id()
        base_url = definition.default_base_url
        api_key_ref = ""
        metadata = {}

        if auth_type == AUTH_API:
            base_url = input(f"Base URL [{definition.default_base_url}] : ").strip() or definition.default_base_url
            if definition.default_api_key_env:
                default_ref = f"env:{definition.default_api_key_env}"
                api_key_ref = input(f"Secret ref [{default_ref}] : ").strip() or default_ref
                if ":" not in api_key_ref:
                    api_key_ref = f"env:{api_key_ref}"
            elif definition.requires_api_key:
                api_key_ref = input("Secret ref (env:NAME, file:/path ou secret:NAME) : ").strip()
        elif auth_type == AUTH_WEB:
            print("Auth web: un navigateur va s'ouvrir, puis BB9 attend le retour local.")
            try:
                token = ChatGPTOAuthFlow().run()
            except OAuthError as exc:
                print(f"Auth web echouee: {exc}")
                return
            token_path = default_web_token_path(provider_id)
            write_web_token(token_path, token)
            metadata = {
                "auth_method": "chatgpt_oauth_pkce",
                "token_path": str(token_path),
            }
            print(f"Auth web OK. Token local: {token_path}")

        draft = ProviderEntry(
            id=provider_id,
            name="",
            provider=definition.kind,
            auth_type=auth_type,
            base_url=base_url,
            api_key_ref=api_key_ref,
            metadata=metadata,
        )
        models = self.fetch_models_for_wizard(draft)
        model = self.choose_model(models)
        if not model:
            print("Ajout annule.")
            return

        config = store.load()
        default_name = f"{definition.kind}-{len(config.entries) + 1}"
        name = input(f"Nom [{default_name}] : ").strip() or default_name
        entry = ProviderEntry(
            id=draft.id,
            name=name,
            provider=draft.provider,
            auth_type=draft.auth_type,
            base_url=draft.base_url,
            api_key_ref=draft.api_key_ref,
            model=model,
            metadata=draft.metadata,
        )
        store.upsert(entry, active=True)
        self.set_active_provider(entry)
        print(f"Provider actif: {entry.name} / {entry.model}")

    def fetch_models_for_wizard(self, entry: ProviderEntry) -> list[str]:
        try:
            models = fetch_models(entry)
        except ModelFetchError as exc:
            print(f"Modeles non recuperes: {exc}")
            return []
        if models:
            print(f"{len(models)} modele(s) trouve(s).")
        return models

    def choose_model(self, models: list[str], current: str = "") -> str:
        if not models:
            prompt = f"Modele [{current}] : " if current else "Modele : "
            return input(prompt).strip() or current

        filtered = models
        query = input("Filtre modele (Entree pour tout afficher) : ").strip().lower()
        if query:
            filtered = [model for model in models if query in model.lower()]
            if not filtered:
                print("Aucun modele ne correspond au filtre.")
                filtered = models

        shown = filtered[:40]
        for index, model in enumerate(shown, 1):
            print(f"{index}. {model}")
        if len(filtered) > len(shown):
            print(f"... {len(filtered) - len(shown)} autre(s), utilisez un filtre.")
        print("0. saisir manuellement")

        raw = input(f"Modele [1-{len(shown)}] : ").strip()
        if not raw and current:
            return current
        try:
            choice = int(raw)
        except ValueError:
            return ""
        if choice == 0:
            return input(f"Modele [{current}] : ").strip() or current
        if 1 <= choice <= len(shown):
            return shown[choice - 1]
        return ""


def run_interactive(state: CliState | None = None) -> int:
    return Cli(state).run()


def main() -> int:
    return run_interactive()


class CliTheme:
    def __init__(self, *, enabled: bool) -> None:
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        if not self.enabled or not text:
            return text
        return f"\033[{code}m{text}\033[0m"

    def accent(self, text: str) -> str:
        return self._wrap("38;5;208;1", text)

    def logo(self, text: str) -> str:
        return self._wrap("38;5;202;1", text)

    def title(self, text: str) -> str:
        return self._wrap("38;5;214;1", text)

    def command(self, text: str) -> str:
        return self._wrap("38;5;208;1", text)

    def dim(self, text: str) -> str:
        return self._wrap("38;5;94", text)

    def border(self, text: str) -> str:
        return self._wrap("38;5;94", text)


def _supports_color() -> bool:
    return (
        sys.stdout.isatty()
        and os.environ.get("NO_COLOR") is None
        and os.environ.get("TERM", "") != "dumb"
    )


def _banner_width() -> int:
    columns = shutil.get_terminal_size((88, 24)).columns
    return max(54, min(columns - 2, 98))


def _bb9_logo() -> tuple[str, ...]:
    return (
        "██████╗  ██████╗   █████╗ ",
        "██╔══██╗ ██╔══██╗ ██╔══██╗",
        "██████╔╝ ██████╔╝ ╚██████║",
        "██╔══██╗ ██╔══██╗  ╚═══██║",
        "██████╔╝ ██████╔╝  █████╔╝",
        "╚═════╝  ╚═════╝   ╚════╝ ",
    )


def _visible_len(text: str) -> int:
    return len(_strip_ansi(text))


def _strip_ansi(text: str) -> str:
    result = []
    index = 0
    while index < len(text):
        if text[index:index + 2] == "\033[":
            index += 2
            while index < len(text) and text[index] != "m":
                index += 1
            index += 1
            continue
        result.append(text[index])
        index += 1
    return "".join(result)


def _truncate_visible(text: str, width: int) -> str:
    plain = _strip_ansi(text)
    if len(plain) <= width:
        return text
    if width <= 1:
        return plain[:width]
    return plain[: width - 1] + "…"


def _pad_visible(text: str, width: int) -> str:
    visible = _visible_len(text)
    if visible >= width:
        return _truncate_visible(text, width)
    return text + " " * (width - visible)


def _short_message(text: str, limit: int = 64) -> str:
    plain = " ".join(text.split())
    if len(plain) <= limit:
        return plain
    return plain[: limit - 1] + "..."


def _short_index_names(index: str, limit: int = 6) -> str:
    names: list[str] = []
    for line in index.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- `"):
            continue
        rest = stripped[3:]
        name, _, _ = rest.partition("`")
        if name:
            names.append(name)
        if len(names) >= limit:
            break
    return ", ".join(names)


def _trusted_root_candidate(reason: str) -> Path | None:
    prefix = "path outside workspace/trusted roots:"
    if not reason.startswith(prefix):
        return None
    raw = reason.removeprefix(prefix).strip()
    if not raw:
        return None
    path = Path(raw).expanduser().resolve()
    if path.exists() and path.is_dir():
        return path
    if path.suffix:
        return path.parent
    return path


if __name__ == "__main__":
    raise SystemExit(main())
