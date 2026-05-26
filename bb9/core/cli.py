"""Interactive command line interface."""

from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from getpass import getpass
from pathlib import Path
from typing import Callable, cast

from .agents import AgentNotFoundError
from .channels import intention_from_text
from .compaction import CompactionConfig
from . import context_runtime
from .cron import (
    CronSpec,
    CronStateStore,
    cron_intention_text,
    default_cron_state_path,
    default_crons_dir,
)
from . import cron_cli, dream_cli, extensions_cli, goal_cli, provider_cli, session_cli
from .dream import default_dream_pending_path, default_dreams_dir
from .diffs import WorktreeSnapshot, capture_worktree_snapshot, diff_artifact_since
from .goals import GoalManager
from .history import default_visible_history_path
from .kernel import Kernel
from .loop import ApprovalDecision, ApprovalResult, run_once, tool_budget_for
from .memory import default_memory_path
from .models import AgentProfile, Artifact, GuardianDecision, PermissionProfile, RunContext, Session, TraceEvent
from .paths import default_content_dir
from .provider_config import ProviderEntry, ProviderStore, default_provider_config_path
from .provider_runtime import (
    active_model_metadata,
    active_model_name,
    build_provider_for_agent,
    load_saved_provider,
    set_active_provider,
)
from .providers import Provider, ProviderError
from .sessions import default_session_store_path
from .settings import PROFILES, SettingsStore
from .skills import load_effective_skills
from .tasks import default_tasks_path
from .trace import tool_trace_artifact
from .markdown import command_aliases
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
    crons_dir: Path = field(default_factory=default_crons_dir)
    cron_state_path: Path = field(default_factory=default_cron_state_path)
    dreams_dir: Path = field(default_factory=default_dreams_dir)
    dream_pending_path: Path = field(default_factory=default_dream_pending_path)
    memory_path: Path = field(default_factory=default_memory_path)
    tasks_path: Path = field(default_factory=default_tasks_path)
    session_store_path: Path = field(default_factory=default_session_store_path)
    visible_history_path: Path = field(default_factory=default_visible_history_path)
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
        self.add_command("/history", self.cmd_history, "afficher l'historique visible", show_in_banner=True)
        self.add_command("/compact", self.cmd_compact, "compacter le contexte court", show_in_banner=True)
        self.add_command("/new", self.cmd_new, "nouvelle session", show_in_banner=True)
        self.add_command("/model", self.cmd_model, "choisir provider et modele", show_in_banner=True)
        self.add_command("/goal", self.cmd_goal, "objectif autonome", show_in_banner=True)
        self.add_command("/cron", self.cmd_cron, "routines et tâches planifiées", show_in_banner=True)
        self.add_command("/dream", self.cmd_dream, "consolidation mémoire", show_in_banner=True)
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
            if self.handle_skill_command(command, line):
                return True
            print(f"Commande inconnue: {command}")
            print("Tape /help pour la liste.")
            return True
        return handler(rest.strip())

    def handle_skill_command(self, command: str, line: str) -> bool:
        if not command.startswith("/") or len(command) <= 1:
            return False
        collisions = dict(self.archive_command_collisions())
        if command in collisions:
            print(f"Commande d'archive ambiguë: {command}")
            print("Conflits: " + ", ".join(collisions[command]))
            return True
        skill_name = command[1:]
        try:
            agent = self.load_current_agent()
        except AgentNotFoundError:
            return False
        for skill in load_effective_skills(
            self.state.skills_dir,
            Path.cwd() / ".bb9" / "skills",
            agent.disabled_skills,
        ):
            aliases = command_aliases(skill.commands)
            if skill.name == skill_name or command in aliases:
                self.run_intention(line)
                return True
        return False

    def run_intention(self, text: str) -> None:
        for interceptor in self.input_interceptors:
            if interceptor(text):
                return
        self.print_user_turn(text)
        diff_snapshot = capture_worktree_snapshot(Path.cwd())
        try:
            context = self.build_context()
            result = run_once(
                Kernel(provider=self.build_provider()),
                intention_from_text(text),
                context,
                ask_user=self.ask_guardian,
                on_event=self.render_live_event,
            )
        except (AgentNotFoundError, ProviderError) as exc:
            print(f"Erreur: {exc}")
            return
        except KeyboardInterrupt:
            print()
            print("Interrompu.")
            return

        if result.observation is not None:
            self.print_markdown(result.observation.summary)
            self.remember_turn(
                text,
                result.observation.summary,
                artifacts=_turn_artifacts(result.observation.artifacts, result.trace, diff_snapshot),
            )
        else:
            self.print_markdown(result.decision.summary)
            self.remember_turn(
                text,
                result.decision.summary,
                artifacts=_turn_artifacts((), result.trace, diff_snapshot),
            )
        if self.state.show_trace:
            for event in result.trace:
                print(f"{event.time} {event.event_type}: {event.summary}")

    def render_live_event(self, event: TraceEvent) -> None:
        tool = str(event.data.get("tool") or "").strip()
        if event.event_type == "action" and tool:
            print(self.theme.dim(f"tool... {tool} en cours"))
            return
        if event.event_type != "observation" or not tool:
            return
        status = "ok" if event.data.get("ok") else "error"
        summary = _short_message(event.summary, limit=96)
        suffix = f" - {summary}" if summary else ""
        print(self.theme.dim(f"tool... {tool} {status}{suffix}"))

    def remember_turn(
        self,
        user_text: str,
        assistant_text: str,
        *,
        artifacts: tuple[Artifact, ...] = (),
    ) -> None:
        session_cli.remember_turn(self, user_text, assistant_text, artifacts=artifacts)

    def build_context(self) -> RunContext:
        return context_runtime.build_context(self.state)

    def print_markdown(self, text: str) -> None:
        print(render_cli_markdown(text, self.theme))

    def print_user_turn(self, text: str) -> None:
        print(render_user_turn(text, self.theme))

    def build_goal_context(self) -> RunContext:
        return context_runtime.build_goal_context(self.state)

    def build_context_with_agent(self, agent) -> RunContext:
        return context_runtime.build_context_with_agent(self.state, agent)

    def refresh_indexes(self) -> None:
        extensions_cli.refresh_indexes(self)

    def load_tool_cli_extensions(self) -> None:
        extensions_cli.load_tool_cli_extensions(self)

    def load_skill_cli_extensions(self) -> None:
        extensions_cli.load_skill_cli_extensions(self)

    def load_current_agent(self) -> AgentProfile:
        return context_runtime.load_current_agent(self.state)

    def load_goal_worker_agent(self) -> AgentProfile:
        return context_runtime.load_goal_worker_agent(self.state)

    def build_provider(self) -> Provider | None:
        return self.build_provider_for_agent(self.load_current_agent())

    def build_goal_provider(self) -> Provider | None:
        return self.build_provider_for_agent(self.load_goal_worker_agent())

    def build_provider_for_agent(self, agent: AgentProfile) -> Provider | None:
        return build_provider_for_agent(self.state, agent)

    def load_saved_provider(self) -> None:
        load_saved_provider(self.state)

    def set_active_provider(self, entry: ProviderEntry) -> None:
        set_active_provider(self.state, entry)

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

        split = max(36, min(46, inner // 2 - 2))
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
                desc_width = max(0, right_width - 16)
                right = _pad_visible(self.theme.command(command), 14) + self.theme.dim(_fit_words(desc, desc_width))
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
            self._status_line("Profil", self.state.profile),
            self._status_line("Modele", f"{provider} · {model}"),
            self._status_line("Agent", agent),
            self._status_line("Session", self.state.session.id[:8]),
            self._status_line("Contexte", context),
        ]

    def _status_line(self, label: str, value: str) -> str:
        return f"{self.theme.dim(label + ':')} {value}"

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
        for command, description in self.archive_commands():
            print(_pad_visible(self.theme.command(command), 18) + self.theme.dim(description))
        return True

    def archive_commands(self) -> list[tuple[str, str]]:
        commands, _ = self.archive_command_resolution()
        return commands

    def archive_command_collisions(self) -> list[tuple[str, tuple[str, ...]]]:
        _, collisions = self.archive_command_resolution()
        return collisions

    def archive_command_resolution(self) -> tuple[list[tuple[str, str]], list[tuple[str, tuple[str, ...]]]]:
        try:
            context = self.build_context()
        except AgentNotFoundError:
            return [], []

        entries = self.archive_command_entries(context)
        owners_by_command: dict[str, list[str]] = {}
        for command, _, owner in entries:
            owners_by_command.setdefault(command, []).append(owner)

        collisions: list[tuple[str, tuple[str, ...]]] = []
        native_commands = set(self.commands)
        for command in sorted(owners_by_command):
            owners = owners_by_command[command]
            if command in native_commands:
                collisions.append((command, tuple(("native", *owners))))
            elif len(owners) > 1:
                collisions.append((command, tuple(owners)))

        collided = {command for command, _ in collisions}
        commands: list[tuple[str, str]] = []
        seen: set[str] = set()
        for command, description, _ in entries:
            if command in collided:
                continue
            if command and command not in seen:
                commands.append((command, description))
                seen.add(command)
        return commands, collisions

    def archive_command_entries(self, context: RunContext) -> list[tuple[str, str, str]]:
        entries: list[tuple[str, str, str]] = []
        for skill in context.skills:
            for line in skill.commands:
                command, description = _archive_command_parts(line)
                if command:
                    entries.append((command, description or f"skill {skill.name}", f"skill:{skill.name}"))
        for tool in context.tools:
            for line in tool.commands:
                command, description = _archive_command_parts(line)
                if command:
                    entries.append((command, description or f"tool {tool.name}", f"tool:{tool.name}"))
        return entries

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
        commands = [command for command, _ in self.archive_commands()]
        print(f"cmd... {', '.join(commands) or '-'}")
        collisions = self.archive_command_collisions()
        if collisions:
            text = "; ".join(f"{command} ({', '.join(owners)})" for command, owners in collisions)
            print(f"cmd!... {text}")
        print(f"sub... {_short_index_names(context.subagents_index) or '-'}")
        trusted = context.trusted_roots.roots if context.trusted_roots else ()
        print(f"tru... {len(trusted)} trusted root(s)")
        soul = context.agent.soul if context.agent is not None else ""
        print(f"bud... {tool_budget_for(context.permission_profile, soul)} tool step(s)")
        print(f"ctx... {len(context.session.messages)} message(s) courts")
        print(f"ses... {self.session_count()} session(s) persistée(s)")
        print(f"his... {self.visible_history_count()} message(s) visible(s)")
        metadata = self.active_model_metadata()
        print(
            f"cmp... {context.session.compacted_count} message(s), "
            f"~{session_cli.token_estimate(context.session)} tok / {metadata.context_window_tokens}"
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
        return session_cli.cmd_new(self, _)

    def cmd_compact(self, _: str) -> bool:
        return session_cli.cmd_compact(self, _)

    def cmd_history(self, value: str) -> bool:
        return session_cli.cmd_history(self, value)

    def persist_session(self) -> None:
        session_cli.persist(self)

    def session_count(self) -> int:
        return session_cli.count(self)

    def visible_history_count(self) -> int:
        return session_cli.visible_count(self)

    def cmd_model(self, value: str) -> bool:
        return provider_cli.cmd_model(self, value)

    def cmd_goal(self, value: str) -> bool:
        return goal_cli.handle(self, value)

    def cmd_cron(self, value: str) -> bool:
        return cron_cli.handle(self, value)

    def cmd_dream(self, value: str) -> bool:
        return dream_cli.handle(self, value)

    def print_cron_status(self) -> None:
        cron_cli.print_status(self)

    def print_due_crons(self) -> None:
        cron_cli.print_due(self)

    def run_cron_tick(self) -> None:
        cron_cli.tick(self)

    def run_due_cron(self, cron: CronSpec, store: CronStateStore, now: datetime) -> None:
        cron_cli.run_due(self, cron, store, now)

    def run_cron_command(self, command: str) -> tuple[bool, str]:
        return cron_cli.run_command(self, command)

    def load_crons(self) -> tuple[CronSpec, ...]:
        return cron_cli.load_all(self)

    def run_once_for_cron(self, agent: AgentProfile, cron: CronSpec, context: RunContext):
        return run_once(
            Kernel(provider=self.build_provider_for_agent(agent)),
            intention_from_text(cron_intention_text(cron)),
            context,
            ask_user=self.ask_guardian,
        )

    def load_agent_for_cron(self, agent_name: str) -> AgentProfile:
        return context_runtime.load_agent_by_name(self.state, agent_name)

    def print_dream_status(self) -> None:
        dream_cli.print_status(self)

    def print_dream_context(self, name: str = "") -> None:
        dream_cli.print_context(self, name)

    def print_dream_prompt(self, name: str = "") -> None:
        dream_cli.print_prompt(self, name)

    def preview_dream(self, name: str = ""):
        return dream_cli.preview(self, name)

    def apply_pending_dream(self, name: str = ""):
        return dream_cli.apply_pending(self, name)

    def run_dream(self, name: str = "", *, remember: bool = True):
        return dream_cli.run(self, name, remember=remember)

    def print_dream_plan(self, plan, *, saved: bool = False) -> None:
        dream_cli.print_plan(self, plan, saved=saved)

    def print_dream_result(self, result) -> None:
        dream_cli.print_result(result)

    def load_dreams(self):
        return dream_cli.load_all(self)

    def select_dream(self, name: str = ""):
        return dream_cli.select(self, name)

    def build_dream_context(self, dream):
        return dream_cli.build_context(self, dream)

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
        provider_cli.print_details(self)

    def active_model_metadata(self):
        try:
            agent = self.load_current_agent()
        except AgentNotFoundError:
            agent = None
        return active_model_metadata(self.state, agent)

    def compaction_config(self) -> CompactionConfig:
        return session_cli.compaction_config(self)

    def active_model_name(self) -> str:
        try:
            agent = self.load_current_agent()
        except AgentNotFoundError:
            agent = None
        return active_model_name(self.state, agent)

    def run_model_wizard(self) -> None:
        provider_cli.run_wizard(self)

    def configure_existing_provider(self, store: ProviderStore, entry: ProviderEntry) -> None:
        provider_cli.configure_existing(self, store, entry)

    def add_provider(self, store: ProviderStore) -> None:
        provider_cli.add_provider(self, store)

    def fetch_models_for_wizard(self, entry: ProviderEntry) -> list[str]:
        return provider_cli.fetch_models_for_wizard(entry)

    def choose_model(self, models: list[str], current: str = "") -> str:
        return provider_cli.choose_model(models, current=current)


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

    def keyword(self, text: str) -> str:
        return self._wrap("38;5;81;1", text)

    def string(self, text: str) -> str:
        return self._wrap("38;5;114", text)

    def number(self, text: str) -> str:
        return self._wrap("38;5;141", text)

    def comment(self, text: str) -> str:
        return self._wrap("38;5;244", text)

    def dim(self, text: str) -> str:
        return self._wrap("38;5;94", text)

    def border(self, text: str) -> str:
        return self._wrap("38;5;94", text)


def render_cli_markdown(text: str, theme: CliTheme) -> str:
    if not theme.enabled:
        return text
    lines: list[str] = []
    in_fence = False
    fence_label = ""
    for raw_line in str(text or "").splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            if not in_fence:
                fence_label = stripped.removeprefix("```").strip()
                label = f" {fence_label}" if fence_label else ""
                lines.append(theme.border(f"╭─ code{label}"))
                in_fence = True
            else:
                lines.append(theme.border("╰─"))
                in_fence = False
                fence_label = ""
            continue
        if in_fence:
            lines.append(theme.border("│ ") + _highlight_code(raw_line, fence_label, theme))
            continue
        lines.append(_render_markdown_line(raw_line, theme))
    if in_fence:
        lines.append(theme.border("╰─"))
    return "\n".join(lines)


def render_user_turn(text: str, theme: CliTheme) -> str:
    label = "user"
    body = str(text or "").strip()
    if not theme.enabled:
        return f"> {body}"
    return theme.accent(f"╭─ {label}") + "\n" + theme.accent("│ ") + body + "\n" + theme.accent("╰─")


def _render_markdown_line(line: str, theme: CliTheme) -> str:
    stripped = line.lstrip()
    indent = line[: len(line) - len(stripped)]
    if not stripped:
        return ""
    heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
    if heading:
        level = len(heading.group(1))
        marker = "━" if level <= 2 else "─"
        return theme.title(f"{marker} {_render_inline_markdown(heading.group(2), theme)}")
    quote = re.match(r"^>\s?(.*)$", stripped)
    if quote:
        return indent + theme.dim("│ " + _render_inline_markdown(quote.group(1), theme))
    task = re.match(r"^[-*]\s+\[( |x|X)\]\s+(.+)$", stripped)
    if task:
        checked = task.group(1).lower() == "x"
        box = theme.accent("[x]") if checked else theme.dim("[ ]")
        return indent + box + " " + _render_inline_markdown(task.group(2), theme)
    bullet = re.match(r"^([-*])\s+(.+)$", stripped)
    if bullet:
        return indent + theme.accent("•") + " " + _render_inline_markdown(bullet.group(2), theme)
    numbered = re.match(r"^(\d+)\.\s+(.+)$", stripped)
    if numbered:
        return indent + theme.accent(numbered.group(1) + ".") + " " + _render_inline_markdown(numbered.group(2), theme)
    return indent + _render_inline_markdown(stripped, theme)


def _render_inline_markdown(text: str, theme: CliTheme) -> str:
    rendered = re.sub(r"`([^`]+)`", lambda match: theme.command(match.group(1)), text)
    rendered = re.sub(r"\*\*([^*]+)\*\*", lambda match: theme.title(match.group(1)), rendered)
    rendered = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", lambda match: theme.accent(match.group(1)), rendered)
    return rendered


def _highlight_code(line: str, language: str, theme: CliTheme) -> str:
    lang = _normalize_language(language)
    if lang in {"javascript", "typescript", "python", "bash", "json"}:
        return _highlight_code_tokens(line, theme, lang)
    return line


def _highlight_code_tokens(line: str, theme: CliTheme, language: str) -> str:
    tokens = _CODE_TOKEN_RE.split(line)
    rendered: list[str] = []
    for token in tokens:
        if not token:
            continue
        rendered.append(_highlight_token(token, theme, language))
    return "".join(rendered)


def _highlight_token(token: str, theme: CliTheme, language: str) -> str:
    if token.startswith(("//", "#")):
        return theme.comment(token)
    if token.startswith(("'", '"', "`")):
        return theme.string(token)
    if re.fullmatch(r"\b\d+(?:\.\d+)?\b", token):
        return theme.number(token)
    if token in _KEYWORDS.get(language, set()):
        return theme.keyword(token)
    if language == "json" and token in {"true", "false", "null"}:
        return theme.keyword(token)
    return token


def _normalize_language(language: str) -> str:
    lang = str(language or "").strip().lower()
    aliases = {
        "js": "javascript",
        "jsx": "javascript",
        "mjs": "javascript",
        "cjs": "javascript",
        "ts": "typescript",
        "tsx": "typescript",
        "py": "python",
        "sh": "bash",
        "shell": "bash",
        "zsh": "bash",
    }
    return aliases.get(lang, lang)


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


def _fit_words(text: str, width: int) -> str:
    plain = " ".join(_strip_ansi(str(text or "")).split())
    if width <= 0:
        return ""
    if len(plain) <= width:
        return plain
    if width <= 1:
        return "…"
    words = plain.split()
    fitted = ""
    for word in words:
        candidate = word if not fitted else f"{fitted} {word}"
        if len(candidate) > width - 1:
            break
        fitted = candidate
    if fitted:
        return fitted.rstrip(" ,.;:") + "…"
    return plain[: max(1, width - 1)] + "…"


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


def _archive_command_parts(line: str) -> tuple[str, str]:
    text = line.strip()
    if text.startswith("`"):
        raw, _, rest = text[1:].partition("`")
        command = raw.strip()
        description = rest.strip(" :-")
        return command, description
    command, _, rest = text.partition(" ")
    return command.strip(), rest.strip(" :-")


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


def _turn_artifacts(
    artifacts: tuple[Artifact, ...],
    trace_events: tuple[TraceEvent, ...],
    snapshot: WorktreeSnapshot,
) -> tuple[Artifact, ...]:
    tool_trace = tool_trace_artifact(trace_events)
    if tool_trace is not None:
        artifacts = (*artifacts, tool_trace)
    diff = diff_artifact_since(snapshot)
    if diff is None:
        return artifacts
    return (*artifacts, diff)


if __name__ == "__main__":
    raise SystemExit(main())
