"""Interactive command line interface."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from getpass import getpass
from pathlib import Path
from typing import cast

from ..core import context_runtime, runtime_service
from ..core.agents import AgentNotFoundError
from ..core.channels import intention_from_text
from ..core.compaction import CompactionConfig
from ..core.cron import (
    CronSpec,
    CronStateStore,
    cron_intention_text,
    default_cron_state_path,
    default_crons_dir,
)
from ..core.dream import default_dream_pending_path, default_dreams_dir
from ..core.goals import GoalManager
from ..core.history import default_visible_history_path
from ..core.kernel import Kernel
from ..core.loop import ApprovalDecision, ApprovalResult, run_once, tool_budget_for
from ..core.markdown import command_aliases
from ..core.memory import default_memory_path
from ..core.model_metadata import resolve_model_metadata
from ..core.models import AgentProfile, Artifact, GuardianDecision, PermissionProfile, RunContext, Session, TraceEvent
from ..core.paths import default_content_dir
from ..core.sessions import default_session_store_path
from ..core.settings import PROFILES, SettingsStore
from ..core.skills import load_effective_skills
from ..core.tasks import default_tasks_path
from ..core.utils import workspace_status_summary
from ..providers.config import ProviderEntry, ProviderStore, default_provider_config_path
from ..providers.providers import Provider, ProviderError
from ..providers.runtime import (
    active_model_metadata,
    active_model_name,
    build_provider_for_agent,
    load_saved_provider,
    set_active_provider,
)
from .approval import ask_guardian as _ask_guardian
from .approval import paused_activity as _paused_activity
from .cron import handle as _cron_handle
from .cron import load_all as _load_crons
from .cron import print_due as _print_due_crons
from .cron import print_status as _print_cron_status
from .cron import run_command as _run_cron_command
from .cron import run_due as _run_due_cron
from .cron import tick as _cron_tick
from .dream import (
    apply_pending as _apply_pending_dream,
)
from .dream import (
    build_context as _build_dream_context,
)
from .dream import (
    handle as _dream_handle,
)
from .dream import (
    load_all as _load_dreams,
)
from .dream import (
    preview as _preview_dream,
)
from .dream import (
    print_context as _print_dream_context,
)
from .dream import (
    print_plan as _print_dream_plan,
)
from .dream import (
    print_prompt as _print_dream_prompt,
)
from .dream import (
    print_result as _print_dream_result,
)
from .dream import (
    print_status as _print_dream_status,
)
from .dream import (
    run as _run_dream,
)
from .dream import (
    select as _select_dream,
)
from .extensions import (
    load_skill_cli_extensions,
    load_tool_cli_extensions,
    refresh_indexes,
)
from .goal import handle as _goal_handle
from .provider import (
    add_provider,
    choose_model,
    cmd_model,
    fetch_models_for_wizard,
)
from .provider import (
    configure_existing as configure_existing_provider,
)
from .provider import (
    print_details as print_provider_details,
)
from .provider import (
    run_wizard as run_model_wizard,
)
from .render import (
    CliActivityIndicator,
    CliTheme,
    archive_command_parts,
    banner_width,
    bb9_logo,
    fit_words,
    live_tool_summary,
    pad_visible,
    render_cli_diff_artifact,
    render_cli_markdown,
    short_index_names,
    short_message,
    strip_ansi,
    supports_color,
    truncate_visible,
    visible_len,
)
from .session import (
    cmd_compact,
    cmd_history,
    cmd_new,
    compaction_config,
    count,
    persist,
    remember_turn,
    token_estimate,
    visible_count,
)

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
        self.theme = CliTheme(enabled=supports_color())
        self.commands: dict[str, CommandHandler] = {}
        self.command_specs: list[CliCommand] = []
        self.input_interceptors: list[InputInterceptor] = []
        self.approval_handlers: list[ApprovalHandler] = []
        self.context_line_providers: list[ContextLineProvider] = []
        self.local_capture: LocalCapture | None = None
        self.activity: CliActivityIndicator | None = None
        self.loaded_tool_cli: set[str] = set()
        self.loaded_skill_cli: set[str] = set()
        self.session_allowed_tools: set[str] = set()
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
        self.print_turn_gap()
        try:
            with self.activity_indicator("BB9 prepare une reponse"):
                turn = runtime_service.run_message(
                    self.state,
                    text,
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

        assistant_text = turn.answer
        artifacts = runtime_service.turn_artifacts(turn)
        self.print_markdown(assistant_text)
        self.print_turn_artifacts(artifacts)
        self.remember_turn(text, assistant_text, artifacts=artifacts)
        if self.state.show_trace:
            for event in turn.result.trace:
                print(f"{event.time} {event.event_type}: {event.summary}")
        self.print_turn_gap()

    def render_live_event(self, event: TraceEvent) -> None:
        tool = str(event.data.get("tool") or "").strip()
        if event.event_type == "action" and tool:
            self.print_live_line(self.theme.dim(f"tool... {tool} en cours"))
            if tool == "shell":
                cmd = str(event.data.get("cmd") or "").strip()
                if cmd:
                    self.print_live_line(render_cli_markdown(f"```bash\n{cmd}\n```", self.theme))
            self.set_activity_text(f"{tool} en cours")
            return
        if event.event_type != "observation" or not tool:
            return
        status = "ok" if event.data.get("ok") else "error"
        summary = live_tool_summary(tool, event.summary, limit=96)
        suffix = f" - {summary}" if summary else ""
        self.print_live_line(self.theme.dim(f"tool... {tool} {status}{suffix}"))
        self.set_activity_text("BB9 prepare une reponse")

    def remember_turn(
        self,
        user_text: str,
        assistant_text: str,
        *,
        artifacts: tuple[Artifact, ...] = (),
    ) -> None:
        remember_turn(self, user_text, assistant_text, artifacts=artifacts)

    def build_context(self) -> RunContext:
        return runtime_service.build_context(self.state)

    def print_markdown(self, text: str) -> None:
        print(render_cli_markdown(text, self.theme))

    def print_turn_gap(self) -> None:
        print()

    def print_turn_artifacts(self, artifacts: tuple[Artifact, ...]) -> None:
        for artifact in artifacts:
            if artifact.kind != "diff":
                continue
            rendered = render_cli_diff_artifact(artifact, self.theme)
            if rendered:
                print(rendered)

    @contextmanager
    def activity_indicator(self, text: str) -> Iterator[None]:
        previous = self.activity
        indicator = CliActivityIndicator(self.theme, text)
        self.activity = indicator
        indicator.start()
        try:
            yield
        finally:
            indicator.stop()
            self.activity = previous

    def print_live_line(self, text: str) -> None:
        if self.activity is not None:
            self.activity.interrupt(lambda: print(text))
            return
        print(text)

    def set_activity_text(self, text: str) -> None:
        if self.activity is not None:
            self.activity.set_text(text)

    def build_goal_context(self) -> RunContext:
        return context_runtime.build_goal_context(self.state)

    def build_context_with_agent(self, agent) -> RunContext:
        return context_runtime.build_context_with_agent(self.state, agent)

    def refresh_indexes(self) -> None:
        refresh_indexes(self)

    def load_tool_cli_extensions(self) -> None:
        load_tool_cli_extensions(self)

    def load_skill_cli_extensions(self) -> None:
        load_skill_cli_extensions(self)

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
        resolve_model_metadata(entry.model)

    def print_banner(self) -> None:
        width = banner_width()
        inner = width - 4
        logo = bb9_logo()
        status = self.status_lines()
        commands = [
            (spec.command, spec.description)
            for spec in self.command_specs
            if spec.show_in_banner and spec.description
        ]

        print()
        print(self.theme.border("╭" + "─" * (width - 2) + "╮"))
        for i, line in enumerate(logo):
            print(self._box_line(self.theme.logo_line(line, i), inner))
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
                right = pad_visible(self.theme.command(command), 14) + self.theme.dim(fit_words(desc, desc_width))
            else:
                right = ""
            line = pad_visible(left, split) + "   " + pad_visible(right, right_width)
            print(self._box_line(line, inner))

        print(self._box_line("", inner))
        print(self._box_line(self.theme.title("Activite recente"), inner))
        print(self._box_line(self.theme.dim("Aucune activite recente"), inner))
        print(self.theme.border("╰" + "─" * (width - 2) + "╯"))
        print(self.theme.dim("? pour les raccourcis  ·  /exit ou Ctrl-D pour quitter"))
        print()

    def print_status(self) -> None:
        for line in self.status_lines():
            print(strip_ansi(line))

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
        visible = visible_len(text)
        if visible > inner_width:
            text = truncate_visible(text, inner_width)
            visible = visible_len(text)
        return self.theme.border("│ ") + text + " " * (inner_width - visible) + self.theme.border(" │")

    def cmd_help(self, _: str) -> bool:
        print(self.theme.title("Commandes disponibles"))
        for spec in self.command_specs:
            if spec.show_in_help and spec.description:
                print(pad_visible(self.theme.command(spec.command), 18) + self.theme.dim(spec.description))
        for command, description in self.archive_commands():
            print(pad_visible(self.theme.command(command), 18) + self.theme.dim(description))
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
            owners = owners_by_command.setdefault(command, [])
            if owner not in owners:
                owners.append(owner)

        collisions: list[tuple[str, tuple[str, ...]]] = []
        native_commands = set(self.commands)
        for command in sorted(owners_by_command):
            owners = owners_by_command[command]
            if command in native_commands:
                collisions.append((command, ("native", *owners)))
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
                command, description = archive_command_parts(line)
                if command:
                    entries.append((command, description or f"skill {skill.name}", f"skill:{skill.name}"))
        for tool in context.tools:
            for line in tool.commands:
                command, description = archive_command_parts(line)
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
        if context.workspace_status.strip():
            print("wst... " + " | ".join(workspace_status_summary(context.workspace_status)))
        print(f"ski... {', '.join(skill.name for skill in context.skills) or '-'}")
        print(f"too... {', '.join(tool.name for tool in context.tools) or '-'}")
        commands = [command for command, _ in self.archive_commands()]
        print(f"cmd... {', '.join(commands) or '-'}")
        collisions = self.archive_command_collisions()
        if collisions:
            text = "; ".join(f"{command} ({', '.join(owners)})" for command, owners in collisions)
            print(f"cmd!... {text}")
        print(f"sub... {short_index_names(context.subagents_index) or '-'}")
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
            f"~{token_estimate(context.session)} tok / {metadata.context_window_tokens}"
        )
        print(f"cix... {len(context.context_index.splitlines())} ligne(s)")
        for provider in self.context_line_providers:
            line = provider(context)
            if line:
                print(line)
        if context.session.messages:
            print("rec... " + " | ".join(short_message(message.as_prompt_line()) for message in context.session.messages[-4:]))
        print("tra... conversation")
        return True

    def ask_guardian(self, decision: GuardianDecision, context: RunContext) -> ApprovalResult | ApprovalDecision:
        return _ask_guardian(self, decision, context)

    @contextmanager
    def paused_activity(self) -> Iterator[None]:
        with _paused_activity(self):
            yield

    def cmd_new(self, _: str) -> bool:
        return cmd_new(self, _)

    def cmd_compact(self, _: str) -> bool:
        return cmd_compact(self, _)

    def cmd_history(self, value: str) -> bool:
        return cmd_history(self, value)

    def persist_session(self) -> None:
        persist(self)

    def session_count(self) -> int:
        return count(self)

    def visible_history_count(self) -> int:
        return visible_count(self)

    def cmd_model(self, value: str) -> bool:
        return cmd_model(self, value)

    def cmd_goal(self, value: str) -> bool:
        return _goal_handle(self, value)

    def cmd_cron(self, value: str) -> bool:
        return _cron_handle(self, value)

    def cmd_dream(self, value: str) -> bool:
        return _dream_handle(self, value)

    def print_cron_status(self) -> None:
        _print_cron_status(self)

    def print_due_crons(self) -> None:
        _print_due_crons(self)

    def run_cron_tick(self) -> None:
        _cron_tick(self)

    def run_due_cron(self, cron: CronSpec, store: CronStateStore, now: datetime) -> None:
        _run_due_cron(self, cron, store, now)

    def run_cron_command(self, command: str) -> tuple[bool, str]:
        return _run_cron_command(self, command)

    def load_crons(self) -> tuple[CronSpec, ...]:
        return _load_crons(self)

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
        _print_dream_status(self)

    def print_dream_context(self, name: str = "") -> None:
        _print_dream_context(self, name)

    def print_dream_prompt(self, name: str = "") -> None:
        _print_dream_prompt(self, name)

    def preview_dream(self, name: str = ""):
        return _preview_dream(self, name)

    def apply_pending_dream(self, name: str = ""):
        return _apply_pending_dream(self, name)

    def run_dream(self, name: str = "", *, remember: bool = True):
        return _run_dream(self, name, remember=remember)

    def print_dream_plan(self, plan, *, saved: bool = False) -> None:
        _print_dream_plan(self, plan, saved=saved)

    def print_dream_result(self, result) -> None:
        _print_dream_result(result)

    def load_dreams(self):
        return _load_dreams(self)

    def select_dream(self, name: str = ""):
        return _select_dream(self, name)

    def build_dream_context(self, dream):
        return _build_dream_context(self, dream)

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
        print_provider_details(self)

    def active_model_metadata(self):
        try:
            agent = self.load_current_agent()
        except AgentNotFoundError:
            agent = None
        return active_model_metadata(self.state, agent)

    def compaction_config(self) -> CompactionConfig:
        return compaction_config(self)

    def active_model_name(self) -> str:
        try:
            agent = self.load_current_agent()
        except AgentNotFoundError:
            agent = None
        return active_model_name(self.state, agent)

    def run_model_wizard(self) -> None:
        run_model_wizard(self)

    def configure_existing_provider(self, store: ProviderStore, entry: ProviderEntry) -> None:
        configure_existing_provider(self, store, entry)

    def add_provider(self, store: ProviderStore) -> None:
        add_provider(self, store)

    def fetch_models_for_wizard(self, entry: ProviderEntry) -> list[str]:
        return fetch_models_for_wizard(entry)

    def choose_model(self, models: list[str], current: str = "") -> str:
        return choose_model(models, current=current)


def run_interactive(state: CliState | None = None) -> int:
    return Cli(state).run()


def main() -> int:
    return run_interactive()


if __name__ == "__main__":
    raise SystemExit(main())
