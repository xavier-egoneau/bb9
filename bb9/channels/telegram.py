"""Telegram channel host."""

from __future__ import annotations

import json
import mimetypes
import re
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import unquote
from urllib.request import Request, urlopen
from uuid import uuid4

from bb9.cli.render import archive_command_parts
from bb9.core import context_runtime, runtime_service
from bb9.core.agent_telegram import AgentTelegramConfig, read_agent_telegram_config
from bb9.core.agents import AgentNotFoundError, discover_subagents, set_agent_skill_enabled, set_agent_tool_enabled
from bb9.core.compaction import CompactionConfig, auto_compact_session, compact_session
from bb9.core.context_budget import context_budget_summary_lines
from bb9.core.history import VisibleHistoryStore
from bb9.core.loop import ApprovalDecision
from bb9.core.markdown import command_aliases
from bb9.core.model_metadata import set_model_context_window
from bb9.core.models import GuardianDecision, PermissionProfile, RunContext, Session
from bb9.core.paths import bb9_home
from bb9.core.projects import resolve_project_target, workspace_switch_from_text
from bb9.core.repl_commands import NATIVE_REPL_COMMANDS
from bb9.core.sessions import SessionStore
from bb9.core.skills import discover_skills, load_effective_skills, load_skill
from bb9.core.tools import discover_tools, load_enabled_tools, load_tool
from bb9.core.veille_rss import run_veille_rss_command, veille_command_from_text
from bb9.providers.config import ProviderEntry
from bb9.providers.providers import ProviderError
from bb9.providers.runtime import active_model_metadata, active_model_name, build_provider_for_agent

TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_MESSAGE_LIMIT = 3900
# Telegram's "typing" status expires ~5s after each sendChatAction; the refresh
# period is interval + HTTP round-trip, so the interval must leave enough margin
# for slow networks or the animation flickers off mid-turn.
TELEGRAM_CHAT_ACTION_INTERVAL = 2.5
TELEGRAM_APPROVAL_TIMEOUT_SECONDS = 300.0
_TELEGRAM_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


class TelegramRuntimeState(Protocol):
    profile: PermissionProfile
    provider_kind: str
    model: str
    reasoning_effort: str
    base_url: str
    api_key_env: str
    api_key_ref: str
    provider_config_path: Path
    active_provider: ProviderEntry | None
    agent_name: str
    subagent_name: str
    agents_dir: Path
    skills_dir: Path
    tools_dir: Path
    session_store_path: Path
    visible_history_path: Path
    session: Session


@dataclass(frozen=True)
class TelegramMessage:
    update_id: int
    chat_id: int | str
    text: str
    message_id: int = 0
    user_label: str = ""


@dataclass(frozen=True)
class TelegramCallback:
    update_id: int
    callback_id: str
    chat_id: int | str
    message_id: int
    data: str
    user_id: int | str = ""


class TelegramApiError(RuntimeError):
    pass


class TelegramClient:
    def __init__(self, token: str, *, api_base: str = TELEGRAM_API_BASE) -> None:
        self.token = token.strip()
        self.api_base = api_base.rstrip("/")

    def get_me(self) -> dict[str, Any]:
        payload = self.call("getMe")
        result = payload.get("result")
        return result if isinstance(result, dict) else {}

    def get_updates(self, *, offset: int = 0, timeout: int = 25, limit: int = 20) -> list[dict[str, Any]]:
        payload = self.call(
            "getUpdates",
            {
                "offset": offset,
                "timeout": timeout,
                "limit": limit,
                "allowed_updates": ["message", "callback_query"],
            },
        )
        result = payload.get("result")
        return result if isinstance(result, list) else []

    def send_message(
        self,
        chat_id: int | str,
        text: str,
        *,
        reply_to_message_id: int = 0,
        reply_markup: dict[str, object] | None = None,
    ) -> None:
        for chunk in telegram_chunks(text):
            data: dict[str, object] = {
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            }
            if reply_to_message_id:
                data["reply_to_message_id"] = reply_to_message_id
                data["allow_sending_without_reply"] = True
            if reply_markup is not None:
                data["reply_markup"] = reply_markup
            self.call("sendMessage", data)
            reply_to_message_id = 0
            reply_markup = None

    def send_chat_action(self, chat_id: int | str, action: str = "typing") -> None:
        # Short timeout: a hung indicator refresh must not block its thread past
        # the ~5s expiry of the previous "typing" status.
        self.call("sendChatAction", {"chat_id": chat_id, "action": action}, timeout=5)

    def send_photo(
        self,
        chat_id: int | str,
        path: Path,
        *,
        caption: str = "",
        reply_to_message_id: int = 0,
    ) -> None:
        data: dict[str, object] = {"chat_id": chat_id}
        if caption.strip():
            data["caption"] = telegram_clip(caption.strip(), 900)
        if reply_to_message_id:
            data["reply_to_message_id"] = reply_to_message_id
            data["allow_sending_without_reply"] = True
        self.upload("sendPhoto", data, file_field="photo", file_path=path)

    def answer_callback_query(self, callback_query_id: str, *, text: str = "", show_alert: bool = False) -> None:
        data: dict[str, object] = {"callback_query_id": callback_query_id, "show_alert": show_alert}
        if text:
            data["text"] = text
        self.call("answerCallbackQuery", data)

    def edit_message_reply_markup(
        self,
        chat_id: int | str,
        message_id: int,
        *,
        reply_markup: dict[str, object] | None = None,
    ) -> None:
        data: dict[str, object] = {"chat_id": chat_id, "message_id": message_id}
        if reply_markup is not None:
            data["reply_markup"] = reply_markup
        self.call("editMessageReplyMarkup", data)

    def set_my_commands(self, commands: tuple[tuple[str, str], ...]) -> None:
        self.call(
            "setMyCommands",
            {"commands": [{"command": command, "description": description} for command, description in commands]},
        )

    def upload(self, method: str, data: dict[str, object], *, file_field: str, file_path: Path) -> dict[str, Any]:
        if not self.token:
            raise TelegramApiError("Telegram token missing")
        boundary = f"bb9-{uuid4().hex}"
        body = _multipart_body(boundary, data, file_field=file_field, file_path=file_path)
        request = Request(
            f"{self.api_base}/bot{self.token}/{method}",
            data=body,
            headers={"content-type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise TelegramApiError(f"Telegram HTTP {exc.code}: {detail}") from exc
        except (OSError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise TelegramApiError(f"Telegram upload failed: {exc}") from exc
        if not isinstance(payload, dict) or not payload.get("ok"):
            raise TelegramApiError(f"Telegram API error: {payload}")
        return payload

    def call(self, method: str, data: dict[str, object] | None = None, *, timeout: int = 35) -> dict[str, Any]:
        if not self.token:
            raise TelegramApiError("Telegram token missing")
        body = json.dumps(data or {}, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.api_base}/bot{self.token}/{method}",
            data=body,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise TelegramApiError(f"Telegram HTTP {exc.code}: {detail}") from exc
        except (OSError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise TelegramApiError(f"Telegram request failed: {exc}") from exc
        if not isinstance(payload, dict) or not payload.get("ok"):
            raise TelegramApiError(f"Telegram API error: {payload}")
        return payload


def run_telegram_host(
    state: TelegramRuntimeState,
    *,
    once: bool = False,
    poll_timeout: int = 25,
    idle_sleep: float = 0.2,
    client: TelegramClient | None = None,
) -> int:
    config = read_agent_telegram_config(state.agents_dir / state.agent_name)
    if not config.enabled:
        print(f"Telegram disabled for agent `{state.agent_name}`.")
        return 2
    if not config.allowed_chat_ids:
        print(f"Telegram has no allowed chat IDs for agent `{state.agent_name}`.")
        return 2
    token = config.resolve_token()
    if not token:
        print(f"Telegram token missing for agent `{state.agent_name}` ({config.token_ref or 'no ref'}).")
        return 2
    if not looks_like_telegram_token(token):
        print(
            "Telegram token looks invalid. Expected a BotFather token like "
            "`123456789:AA...`; update the agent Telegram token."
        )
        return 2
    telegram = client or TelegramClient(token)
    try:
        bot = telegram.get_me()
    except TelegramApiError as exc:
        print(f"Telegram error: {exc}")
        return 2
    bot_name = str(bot.get("username") or bot.get("first_name") or "bot")
    print(f"BB9 Telegram channel ready for @{bot_name} as agent `{state.agent_name}`.")
    host = TelegramHost(state, config, telegram)
    host.configure_bot_commands()
    try:
        host.run(once=once, poll_timeout=poll_timeout, idle_sleep=idle_sleep)
    except KeyboardInterrupt:
        print()
        print("Telegram channel stopped.")
    return 0


class TelegramHost:
    def __init__(self, state: TelegramRuntimeState, config: AgentTelegramConfig, client: TelegramClient) -> None:
        self.state = state
        self.config = config
        self.client = client
        self.offset_path = telegram_offset_path(state.agent_name)
        self._handled_callback_ids: set[str] = set()
        self._last_turn_artifacts: tuple[Any, ...] = ()
        self._last_turn_workspace = Path.cwd().resolve(strict=False)

    def configure_bot_commands(self) -> None:
        try:
            self.client.set_my_commands(telegram_menu_commands(self.telegram_commands_payload()["commands"]))
        except Exception as exc:
            print(f"Telegram command setup warning: {exc}")

    def telegram_commands_payload(self) -> dict[str, Any]:
        native = [
            {
                "name": command,
                "description": description,
                "source": "native",
                "owner": "bb9",
                "local": False,
                "supported": supported,
            }
            for command, description, supported in NATIVE_REPL_COMMANDS
        ]
        archive, collisions = self._archive_command_payloads()
        return {"ok": True, "commands": [*native, *archive], "collisions": collisions}

    def run(
        self,
        *,
        once: bool = False,
        poll_timeout: int = 25,
        idle_sleep: float = 0.2,
        stop_event: threading.Event | None = None,
    ) -> None:
        offset = self._read_offset()
        while stop_event is None or not stop_event.is_set():
            try:
                updates = self.client.get_updates(offset=offset, timeout=0 if once else poll_timeout)
            except TelegramApiError as exc:
                print(f"Telegram poll error: {exc}")
                if once:
                    return
                time.sleep(max(1.0, idle_sleep))
                continue
            for update in updates:
                update_id = int(update.get("update_id") or 0)
                if update_id:
                    offset = max(offset, update_id + 1)
                message = telegram_message_from_update(update)
                if message is not None:
                    self.handle_message(message)
                    continue
                callback = telegram_callback_from_update(update)
                if callback is not None:
                    self.handle_callback(callback)
            if updates:
                self._write_offset(offset)
            if once:
                return
            if not updates:
                if stop_event is not None:
                    stop_event.wait(max(0.1, idle_sleep))
                else:
                    time.sleep(max(0.1, idle_sleep))

    def handle_message(self, message: TelegramMessage) -> str:
        if not self.config.allows(message.chat_id):
            answer = f"Chat Telegram non autorisé. Chat ID: {message.chat_id}"
            self.client.send_message(message.chat_id, answer, reply_to_message_id=message.message_id)
            return answer
        self._ensure_agent_home_session()
        text = message.text.strip()
        effective_text, switch_notice, switch_answer = self._prepare_workspace_text(text)
        if switch_answer:
            self._persist_command_turn(text, switch_answer)
            self.client.send_message(message.chat_id, switch_answer, reply_to_message_id=message.message_id)
            return switch_answer
        text = effective_text
        if text in {"/start", "/start@bb9"}:
            answer = f"BB9 est connecté à l'agent `{self.state.agent_name}`."
            self.client.send_message(message.chat_id, answer, reply_to_message_id=message.message_id)
            return answer
        veille_command = veille_command_from_text(text)
        if veille_command:
            stop_activity = self._start_activity_indicator(message.chat_id)
            try:
                answer = run_veille_rss_command(self.state.skills_dir, veille_command)
            finally:
                stop_activity()
            if switch_notice:
                answer = f"{switch_notice}\n\n{answer}"
            self._persist_command_turn(message.text.strip(), answer)
            self.client.send_message(message.chat_id, answer, reply_to_message_id=message.message_id)
            return answer
        command_answer = self._handle_command(text)
        if command_answer is not None:
            if switch_notice:
                command_answer = f"{switch_notice}\n\n{command_answer}"
            self._persist_command_turn(message.text.strip(), command_answer)
            self.client.send_message(message.chat_id, command_answer, reply_to_message_id=message.message_id)
            return command_answer
        if text in {"help"}:
            answer = self._help_answer()
            self.client.send_message(message.chat_id, answer, reply_to_message_id=message.message_id)
            return answer
        stop_activity = self._start_activity_indicator(message.chat_id)
        try:
            answer = self._run_agent_turn(message, text=text, answer_prefix=switch_notice)
        finally:
            stop_activity()
        self.client.send_message(message.chat_id, answer, reply_to_message_id=message.message_id)
        self._send_visual_artifacts(
            message.chat_id,
            answer,
            artifacts=self._last_turn_artifacts,
            workspace=self._last_turn_workspace,
            reply_to_message_id=message.message_id,
        )
        return answer

    def handle_callback(self, callback: TelegramCallback) -> None:
        if callback.callback_id in self._handled_callback_ids:
            return
        if not callback.data.startswith("bb9:a:"):
            self.client.answer_callback_query(callback.callback_id, text="Action inconnue.")
            return
        self.client.answer_callback_query(callback.callback_id, text="Validation déjà traitée.")

    def _start_activity_indicator(self, chat_id: int | str) -> Any:
        stopped = threading.Event()

        def send_action() -> None:
            try:
                self.client.send_chat_action(chat_id, "typing")
            except Exception as exc:
                print(f"Telegram chat action error: {exc}")

        def refresh_loop() -> None:
            while not stopped.wait(TELEGRAM_CHAT_ACTION_INTERVAL):
                send_action()

        send_action()
        thread = threading.Thread(target=refresh_loop, name="bb9-telegram-chat-action", daemon=True)
        thread.start()

        def stop() -> None:
            stopped.set()
            thread.join(timeout=0.5)

        return stop

    def _run_agent_turn(self, message: TelegramMessage, *, text: str = "", answer_prefix: str = "") -> str:
        run_text = (text or message.text).strip()
        user_text = message.text.strip()
        self._ensure_agent_home_session()
        artifacts: tuple[Any, ...] = ()
        workspace = self._workspace_root()
        turn_agent = None
        try:
            turn = runtime_service.run_message(
                self.state,
                run_text,
                ask_user=lambda decision, context: self._ask_approval(
                    chat_id=message.chat_id,
                    after_update_id=message.update_id,
                    decision=decision,
                    context=context,
                ),
            )
            answer = turn.answer
            artifacts = runtime_service.turn_artifacts(turn, include_decision_trace=True)
            turn_context = getattr(turn, "context", None)
            turn_agent = getattr(turn_context, "agent", None)
            turn_workspace = getattr(turn_context, "workspace", None)
            if turn_workspace is not None:
                workspace = Path(turn_workspace.root)
        except (AgentNotFoundError, ProviderError) as exc:
            answer = f"Erreur BB9: {exc}"
        except Exception as exc:
            answer = f"Erreur runtime Telegram: {exc}"
        if answer_prefix:
            answer = f"{answer_prefix}\n\n{answer}"
        self._last_turn_artifacts = artifacts
        self._last_turn_workspace = workspace
        self.state.session = self.state.session.with_message("user", user_text).with_message("assistant", answer)
        compaction_notice = self._auto_compact_session(turn_agent)
        self._persist_turn(user_text, answer, artifacts)
        if compaction_notice:
            self._persist_notification(compaction_notice)
            answer = f"{answer}\n\n⚙️ {compaction_notice}"
        return answer

    def _auto_compact_session(self, agent: Any) -> str:
        """Compact the shared agent-home session when it grows too large.

        Mirrors the web behavior: the compaction stays synchronous, but the
        user is told it happened instead of silently losing old context.
        """
        try:
            window = active_model_metadata(self.state, agent).context_window_tokens
        except Exception:
            window = 250_000
        config = CompactionConfig(
            context_window_tokens=window,
            soft_input_limit_tokens=0,
        )
        summarizer = None
        try:
            provider = build_provider_for_agent(self.state, agent)
        except Exception:
            provider = None
        if provider is not None:
            summarizer = provider.complete
        try:
            result = auto_compact_session(self.state.session, config=config, summarizer=summarizer)
        except Exception as exc:
            print(f"Telegram auto-compaction warning: {exc}")
            return ""
        if not result.changed:
            return ""
        self.state.session = result.session
        return (
            "Auto-compaction du contexte court : "
            f"{result.compacted_messages} ancien(s) message(s) résumés, "
            f"{len(result.session.messages)} message(s) récent(s) conservés."
        )

    def _persist_notification(self, text: str) -> None:
        history = VisibleHistoryStore(self.state.visible_history_path)
        try:
            history.append_message(
                session_id=self.state.session.id,
                role="notification",
                content=text,
                source="telegram",
                project_path=None,
            )
        finally:
            history.close()

    def _send_visual_artifacts(
        self,
        chat_id: int | str,
        answer: str,
        *,
        artifacts: tuple[Any, ...],
        workspace: Path,
        reply_to_message_id: int = 0,
    ) -> None:
        send_photo = getattr(self.client, "send_photo", None)
        if send_photo is None:
            return
        for path, caption in _visual_artifact_paths(answer, artifacts, workspace):
            try:
                send_photo(chat_id, path, caption=caption, reply_to_message_id=reply_to_message_id)
                reply_to_message_id = 0
            except Exception as exc:
                print(f"Telegram media upload warning: {exc}")

    def _prepare_workspace_text(self, text: str) -> tuple[str, str, str]:
        request = workspace_switch_from_text(text)
        if request is None:
            return text, "", ""
        answer = self._switch_workspace_target(request.target)
        if answer.startswith("Erreur"):
            return "", "", answer
        if not request.remainder.strip():
            return "", answer, answer
        return request.remainder.strip(), answer, ""

    def _switch_workspace_target(self, target: str) -> str:
        resolution = resolve_project_target(
            target,
            session_store_path=self.state.session_store_path,
            settings_path=getattr(self.state, "settings_path", None),
            cwd=self._workspace_root(),
        )
        if not resolution.ok or resolution.path is None:
            return f"Erreur projet: {resolution.message or resolution.error or target}"
        if not hasattr(self.state, "active_project_path"):
            return "Erreur projet: état runtime sans workspace actif."
        self.state.active_project_path = str(resolution.path.resolve(strict=False))
        return f"Workspace actif: `{self.state.active_project_path}`."

    def _workspace_root(self) -> Path:
        active = str(getattr(self.state, "active_project_path", "") or "").strip()
        if active:
            path = Path(active).expanduser().resolve(strict=False)
            if path.is_dir():
                return path
        return Path.cwd().resolve(strict=False)

    def _ensure_agent_home_session(self) -> None:
        store = SessionStore(self.state.session_store_path)
        try:
            stored = store.ensure_agent_home(self.state.agent_name)
            self.state.session = stored.as_session()
        finally:
            store.close()

    def _ask_approval(
        self,
        *,
        chat_id: int | str,
        after_update_id: int,
        decision: GuardianDecision,
        context: RunContext,
    ) -> ApprovalDecision:
        token = uuid4().hex[:12]
        prompt = approval_prompt(decision, context)
        self.client.send_message(
            chat_id,
            prompt,
            reply_markup=approval_keyboard(token),
        )
        deadline = time.monotonic() + TELEGRAM_APPROVAL_TIMEOUT_SECONDS
        offset = max(0, after_update_id + 1)
        while time.monotonic() < deadline:
            timeout = min(5, max(1, int(deadline - time.monotonic())))
            try:
                updates = self.client.get_updates(offset=offset, timeout=timeout, limit=20)
            except TelegramApiError as exc:
                return ApprovalDecision(verdict="defer", summary=f"Validation Telegram indisponible: {exc}")
            for update in updates:
                update_id = int(update.get("update_id") or 0)
                if update_id:
                    offset = max(offset, update_id + 1)
                callback = telegram_callback_from_update(update)
                if callback is None:
                    continue
                verdict = approval_verdict_from_callback(callback.data, token)
                if verdict is None:
                    continue
                self._handled_callback_ids.add(callback.callback_id)
                if not self.config.allows(callback.chat_id):
                    self.client.answer_callback_query(callback.callback_id, text="Chat non autorisé.", show_alert=True)
                    continue
                if callback.chat_id != chat_id:
                    self.client.answer_callback_query(callback.callback_id, text="Validation hors conversation.", show_alert=True)
                    continue
                label = "Action validée." if verdict == "allow" else "Action refusée."
                self.client.answer_callback_query(callback.callback_id, text=label)
                if callback.message_id:
                    try:
                        self.client.edit_message_reply_markup(callback.chat_id, callback.message_id)
                    except Exception as exc:
                        print(f"Telegram approval cleanup warning: {exc}")
                return ApprovalDecision(verdict=verdict, summary=label)
        return ApprovalDecision(verdict="deny", summary="Validation Telegram expirée.")

    def _archive_command_payloads(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        try:
            agent = context_runtime.load_current_agent(self.state)
        except AgentNotFoundError:
            return [], []

        entries: list[dict[str, Any]] = []
        local_skills_root = self._workspace_root() / ".bb9" / "skills"
        for skill in load_effective_skills(self.state.skills_dir, local_skills_root, agent.disabled_skills):
            local = skill.root == local_skills_root
            for line in skill.commands:
                command, description = archive_command_parts(line)
                if command:
                    entries.append(archive_command_payload(command, description or f"skill {skill.name}", owner=skill.name, source="local-skill" if local else "skill", local=local))
            for alias in command_aliases(skill.commands):
                if not any(entry["name"] == alias and entry["owner"] == skill.name for entry in entries):
                    entries.append(archive_command_payload(alias, skill.summary or f"skill {skill.name}", owner=skill.name, source="local-skill" if local else "skill", local=local))

        for tool in load_enabled_tools(self.state.tools_dir, agent.disabled_tools):
            for line in tool.commands:
                command, description = archive_command_parts(line)
                if command:
                    entries.append(archive_command_payload(command, description or f"tool {tool.name}", owner=tool.name, source="tool", local=False))

        owners_by_command: dict[str, list[str]] = {}
        native_names = {command for command, _, _ in NATIVE_REPL_COMMANDS}
        for entry in entries:
            owners = owners_by_command.setdefault(str(entry["name"]), [])
            owner = f"{entry['source']}:{entry['owner']}"
            if owner not in owners:
                owners.append(owner)

        collisions: list[dict[str, Any]] = []
        for command, owners in sorted(owners_by_command.items()):
            if command in native_names:
                collisions.append({"name": command, "owners": ["native", *owners]})
            elif len(owners) > 1:
                collisions.append({"name": command, "owners": owners})

        collided = {str(collision["name"]) for collision in collisions}
        commands: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in entries:
            name = str(entry["name"])
            if name in collided or name in seen:
                continue
            commands.append(entry)
            seen.add(name)
        return commands, collisions

    def _handle_command(self, text: str) -> str | None:
        command, _, rest = text.strip().partition(" ")
        command = command.split("@", 1)[0].lower()
        if command == "/help":
            return self._help_answer()
        if command == "/context":
            return self._context_answer()
        if command == "/history":
            return self._history_answer(rest)
        if command == "/new":
            return self._new_answer()
        if command == "/compact":
            return self._compact_answer()
        if command == "/model-context":
            return self._set_model_context_answer(rest)
        if command in {"/project", "/workspace"}:
            return f"Workspace actif: `{self._workspace_root()}`."
        if command == "/tools":
            return self._archive_activation_answer("tool", rest)
        if command == "/skills":
            return self._archive_activation_answer("skill", rest)
        if command in {item[0] for item in NATIVE_REPL_COMMANDS}:
            return f"La commande `{command}` existe dans le REPL mais n'a pas encore d'équivalent Telegram direct."
        return None

    def _archive_activation_answer(self, kind: str, value: str) -> str:
        try:
            agent = context_runtime.load_current_agent(self.state)
        except AgentNotFoundError as exc:
            return f"Erreur: {exc}"
        parts = value.split()
        action = parts[0].lower() if parts else "list"
        records = self._archive_activation_records(kind)
        disabled = set(agent.disabled_tools if kind == "tool" else agent.disabled_skills)
        title = "Tools" if kind == "tool" else "Skills"
        if action in {"list", "ls", ""}:
            lines = [f"## {title}"]
            if not records:
                lines.append("-")
                return "\n".join(lines)
            for name, summary in records.items():
                marker = "x" if name not in disabled else " "
                lines.append(f"- [{marker}] `{name}` : {summary or '-'}")
            return "\n".join(lines)
        if action not in {"enable", "disable", "on", "off", "activer", "desactiver", "désactiver"} or len(parts) < 2:
            command = "/tools" if kind == "tool" else "/skills"
            return f"Usage : `{command} [list|enable <nom>|disable <nom>]`"
        name = parts[1].strip()
        if name not in records:
            return f"{kind.capitalize()} inconnu : `{name}`"
        enabled = action in {"enable", "on", "activer"}
        if kind == "tool":
            set_agent_tool_enabled(self.state.agents_dir, self.state.agent_name, name, enabled)
        else:
            set_agent_skill_enabled(self.state.agents_dir, self.state.agent_name, name, enabled)
        state = "active" if enabled else "désactivé"
        return f"{kind.capitalize()} `{name}` {state} pour l'agent `{self.state.agent_name}`."

    def _archive_activation_records(self, kind: str) -> dict[str, str]:
        if kind == "tool":
            records: dict[str, str] = {}
            for name in discover_tools(self.state.tools_dir):
                try:
                    records[name] = load_tool(self.state.tools_dir, name).summary
                except Exception:
                    records[name] = ""
            return records
        local_root = self._workspace_root() / ".bb9" / "skills"
        records = {}
        for root in (self.state.skills_dir, local_root):
            for name in discover_skills(root):
                try:
                    skill = load_skill(root, name)
                except Exception:
                    records[name] = ""
                    continue
                source = "local" if root == local_root else "global"
                records[name] = f"{skill.summary} ({source})" if skill.summary else f"({source})"
        return dict(sorted(records.items()))

    def _help_answer(self) -> str:
        lines = ["Commandes disponibles :"]
        commands = self.telegram_commands_payload()["commands"]
        for item in commands:
            suffix = " (non supportée en Telegram)" if not item.get("supported", True) else ""
            lines.append(f"- `{item['name']}` : {item.get('description') or item.get('owner')}{suffix}")
        invalid_menu = [
            str(item["name"])
            for item in commands
            if not telegram_menu_command_name(str(item["name"]))
        ]
        if invalid_menu:
            lines.extend(
                [
                    "",
                    "Note : certaines commandes BB9 sont acceptées au clavier mais absentes du menu Telegram natif.",
                ]
            )
        return "\n".join(lines)

    def _context_answer(self) -> str:
        try:
            context = runtime_service.build_context(self.state)
            status = runtime_service.build_status(self.state)
            metadata = active_model_metadata(self.state, context.agent)
            budget_lines = context_budget_summary_lines(
                context,
                "/context",
                context_window=metadata.context_window_tokens,
            )
        except Exception as exc:
            return f"Erreur contexte: {exc}"
        return "\n".join(
            [
                "## Contexte courant",
                "",
                *budget_lines,
                "",
                f"- Agent : `{status.agent}`",
                f"- Session : `{status.session_id}`",
                f"- Profil : `{status.profile}`",
                f"- Modèle : `{status.provider} · {status.model or '-'}`",
                f"- Workspace : `{status.workspace}`",
                f"- Skills : `{', '.join(skill.name for skill in context.skills) or '-'}`",
                f"- Tools : `{', '.join(tool.name for tool in context.tools) or '-'}`",
                f"- Subagents : `{', '.join(discover_subagents(self.state.agents_dir, self.state.agent_name)) or '-'}`",
                f"- Messages courts : `{len(context.session.messages)}`",
            ]
        )

    def _history_answer(self, rest: str) -> str:
        limit = _positive_int(rest, default=12, maximum=40)
        history = VisibleHistoryStore(self.state.visible_history_path)
        try:
            messages = [
                message
                for message in history.recent(limit=limit, session_id=self.state.session.id, project_path=None)
                if message.source == "telegram" and message.role in {"user", "assistant", "notification"}
            ]
        finally:
            history.close()
        if not messages:
            return "Aucun historique visible Telegram pour cette session."
        return "\n\n".join(f"**{message.role}**\n{telegram_clip(message.content, 900)}" for message in messages)

    def _compact_answer(self) -> str:
        result = compact_session(self.state.session, force=True)
        self.state.session = result.session
        store = SessionStore(self.state.session_store_path)
        try:
            store.store(self.state.session, project_path=None)
        finally:
            store.close()
        return result.notice()

    def _new_answer(self) -> str:
        self.state.session = Session(id=self.state.session.id, source=self.state.session.source)
        store = SessionStore(self.state.session_store_path)
        try:
            store.store(self.state.session, project_path=None)
        finally:
            store.close()
        return "Session d'accueil réinitialisée."

    def _set_model_context_answer(self, rest: str) -> str:
        context = runtime_service.build_context(self.state)
        model = active_model_name(self.state, context.agent)
        if not model:
            return "Aucun modèle actif détecté."
        raw = rest.strip().lower().replace(",", "").replace(" ", "")
        if not raw:
            return f"Usage : `/model-context <taille>` (ex: `/model-context 200000` ou `/model-context 200k`) pour `{model}`."
        multiplier = 1
        if raw.endswith("k"):
            multiplier = 1_000
            raw = raw[:-1]
        elif raw.endswith("m"):
            multiplier = 1_000_000
            raw = raw[:-1]
        try:
            tokens = int(float(raw) * multiplier)
        except ValueError:
            return f"Valeur invalide : `{rest.strip()}`. Exemple : `/model-context 200000` ou `/model-context 200k`."
        if tokens <= 0:
            return "La taille de la fenêtre de contexte doit être positive."
        set_model_context_window(model, tokens)
        return f"Fenêtre de contexte de `{model}` enregistrée : {tokens:,} tokens.".replace(",", " ")

    def _persist_command_turn(self, user_text: str, assistant_text: str) -> None:
        self.state.session = self.state.session.with_message("user", user_text).with_message("assistant", assistant_text)
        self._persist_turn(user_text, assistant_text, ())

    def _persist_turn(self, user_text: str, assistant_text: str, artifacts: tuple[Any, ...]) -> None:
        session_store = SessionStore(self.state.session_store_path)
        try:
            session_store.store(self.state.session, project_path=None)
        finally:
            session_store.close()
        history = VisibleHistoryStore(self.state.visible_history_path)
        try:
            history.append_turn(
                session_id=self.state.session.id,
                user_text=user_text,
                assistant_text=assistant_text,
                source="telegram",
                project_path=None,
                artifacts=artifacts,  # type: ignore[arg-type]
            )
        finally:
            history.close()

    def _read_offset(self) -> int:
        try:
            payload = json.loads(self.offset_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0
        return max(0, int(payload.get("offset") or 0))

    def _write_offset(self, offset: int) -> None:
        self.offset_path.parent.mkdir(parents=True, exist_ok=True)
        self.offset_path.write_text(json.dumps({"offset": offset}, indent=2), encoding="utf-8")


def telegram_message_from_update(update: dict[str, Any]) -> TelegramMessage | None:
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    text = message.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return None
    chat_id = chat.get("id")
    if chat_id is None:
        return None
    sender = message.get("from")
    user_label = ""
    if isinstance(sender, dict):
        user_label = str(sender.get("username") or sender.get("first_name") or "").strip()
    return TelegramMessage(
        update_id=int(update.get("update_id") or 0),
        chat_id=chat_id,
        text=text.strip(),
        message_id=int(message.get("message_id") or 0),
        user_label=user_label,
    )


def telegram_callback_from_update(update: dict[str, Any]) -> TelegramCallback | None:
    query = update.get("callback_query")
    if not isinstance(query, dict):
        return None
    data = query.get("data")
    callback_id = query.get("id")
    if not isinstance(data, str) or not isinstance(callback_id, str):
        return None
    message = query.get("message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return None
    chat_id = chat.get("id")
    if chat_id is None:
        return None
    sender = query.get("from")
    user_id: int | str = ""
    if isinstance(sender, dict):
        user_id = sender.get("id") or ""
    return TelegramCallback(
        update_id=int(update.get("update_id") or 0),
        callback_id=callback_id,
        chat_id=chat_id,
        message_id=int(message.get("message_id") or 0),
        data=data,
        user_id=user_id,
    )


def approval_keyboard(token: str) -> dict[str, object]:
    return {
        "inline_keyboard": [
            [
                {"text": "Valider", "callback_data": f"bb9:a:{token}:allow"},
                {"text": "Refuser", "callback_data": f"bb9:a:{token}:deny"},
            ]
        ]
    }


def approval_verdict_from_callback(data: str, token: str) -> Literal["allow", "deny"] | None:
    prefix = f"bb9:a:{token}:"
    if not data.startswith(prefix):
        return None
    verdict = data[len(prefix) :].strip().lower()
    if verdict == "allow":
        return "allow"
    if verdict == "deny":
        return "deny"
    return None


def approval_prompt(decision: GuardianDecision, context: RunContext) -> str:
    action = decision.action
    lines = ["Validation requise", "", f"Raison: {decision.reason}"]
    if action is not None:
        lines.append(f"Tool: {action.name}")
        if action.risk:
            lines.append(f"Risque: {action.risk}")
        for key in ("cmd", "path", "url"):
            value = str(action.params.get(key) or "").strip()
            if value:
                lines.append(f"{key}: {telegram_clip(value, 900)}")
                break
    lines.append(f"Workspace: {context.workspace.root}")
    return "\n".join(lines)


def telegram_chunks(text: str, *, limit: int = TELEGRAM_MESSAGE_LIMIT) -> Iterable[str]:
    value = str(text or "").strip() or "(vide)"
    while len(value) > limit:
        split_at = value.rfind("\n", 0, limit)
        if split_at < max(1, limit // 2):
            split_at = limit
        yield value[:split_at].strip()
        value = value[split_at:].strip()
    yield value


def telegram_clip(text: str, limit: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 12)].rstrip() + "..."


def archive_command_payload(
    command: str,
    description: str,
    *,
    owner: str,
    source: str,
    local: bool,
) -> dict[str, Any]:
    return {
        "name": command,
        "description": description,
        "source": source,
        "owner": owner,
        "local": local,
        "supported": True,
    }


def telegram_menu_commands(commands: list[dict[str, Any]]) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for command in commands:
        name = telegram_menu_command_name(str(command.get("name") or ""))
        if not name or name in seen:
            continue
        description = telegram_menu_description(str(command.get("description") or command.get("owner") or "commande BB9"))
        result.append((name, description))
        seen.add(name)
    return tuple(result)


def telegram_menu_command_name(command: str) -> str:
    name = command.strip().split(maxsplit=1)[0]
    if name.startswith("/"):
        name = name[1:]
    if not re.fullmatch(r"[A-Za-z0-9_]{1,32}", name):
        return ""
    return name


def telegram_menu_description(value: str) -> str:
    text = " ".join(str(value or "commande BB9").split())
    return text[:256] or "commande BB9"


def _visual_artifact_paths(answer: str, artifacts: tuple[Any, ...], workspace: Path) -> tuple[tuple[Path, str], ...]:
    found: list[tuple[Path, str]] = []
    seen: set[str] = set()

    def add(raw_path: str, caption: str = "") -> None:
        path = _resolve_visual_path(raw_path, workspace)
        if path is None:
            return
        key = str(path)
        if key in seen:
            return
        seen.add(key)
        found.append((path, caption.strip() or path.name))

    for artifact in artifacts:
        kind = str(getattr(artifact, "kind", "") or "").strip()
        if kind not in {"screenshot", "image"}:
            continue
        add(str(getattr(artifact, "path", "") or ""), str(getattr(artifact, "title", "") or ""))

    for match in _MARKDOWN_IMAGE_RE.finditer(answer or ""):
        alt = match.group(1)
        raw = match.group(2)
        add(raw, alt)

    return tuple(found)


def _resolve_visual_path(raw_path: str, workspace: Path) -> Path | None:
    raw = unquote(str(raw_path or "").strip().strip("<>"))
    if not raw or raw.startswith(("http://", "https://")):
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = workspace / path
    path = path.resolve(strict=False)
    if path.suffix.lower() not in _TELEGRAM_IMAGE_SUFFIXES or not path.is_file():
        return None
    return path


def _multipart_body(boundary: str, data: dict[str, object], *, file_field: str, file_path: Path) -> bytes:
    chunks: list[bytes] = []
    marker = f"--{boundary}\r\n".encode()
    for key, value in data.items():
        if value is None:
            continue
        text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        chunks.extend(
            [
                marker,
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
                text.encode("utf-8"),
                b"\r\n",
            ]
        )
    filename = file_path.name
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    chunks.extend(
        [
            marker,
            (
                f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode(),
            file_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks)


def _positive_int(value: str, *, default: int, maximum: int) -> int:
    for token in str(value or "").replace("=", " ").split():
        if token.isdigit():
            return min(maximum, max(1, int(token)))
    return default


def looks_like_telegram_token(value: str) -> bool:
    return bool(re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{20,}", value.strip()))


def telegram_offset_path(agent_name: str) -> Path:
    safe = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in (agent_name or "default"))
    return bb9_home() / "telegram" / f"{safe or 'default'}-offset.json"
