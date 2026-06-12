"""Telegram channel host."""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from bb9.core import runtime_service
from bb9.core.agent_telegram import AgentTelegramConfig, read_agent_telegram_config
from bb9.core.agents import AgentNotFoundError
from bb9.core.history import VisibleHistoryStore
from bb9.core.loop import ApprovalDecision
from bb9.core.models import PermissionProfile, Session
from bb9.core.paths import bb9_home
from bb9.core.sessions import SessionStore
from bb9.providers.config import ProviderEntry
from bb9.providers.providers import ProviderError

TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_MESSAGE_LIMIT = 3900


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
                "allowed_updates": ["message"],
            },
        )
        result = payload.get("result")
        return result if isinstance(result, list) else []

    def send_message(self, chat_id: int | str, text: str, *, reply_to_message_id: int = 0) -> None:
        for chunk in telegram_chunks(text):
            data: dict[str, object] = {
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            }
            if reply_to_message_id:
                data["reply_to_message_id"] = reply_to_message_id
                data["allow_sending_without_reply"] = True
            self.call("sendMessage", data)
            reply_to_message_id = 0

    def call(self, method: str, data: dict[str, object] | None = None) -> dict[str, Any]:
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
            with urlopen(request, timeout=35) as response:
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
    idle_sleep: float = 1.5,
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

    def run(
        self,
        *,
        once: bool = False,
        poll_timeout: int = 25,
        idle_sleep: float = 1.5,
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
        text = message.text.strip()
        if text in {"/start", "/start@bb9"}:
            answer = f"BB9 est connecté à l'agent `{self.state.agent_name}`."
            self.client.send_message(message.chat_id, answer, reply_to_message_id=message.message_id)
            return answer
        if text in {"/help", "help"}:
            answer = "Envoie un message à BB9. Les actions nécessitant validation restent à confirmer depuis le web ou le CLI."
            self.client.send_message(message.chat_id, answer, reply_to_message_id=message.message_id)
            return answer
        answer = self._run_agent_turn(text)
        self.client.send_message(message.chat_id, answer, reply_to_message_id=message.message_id)
        return answer

    def _run_agent_turn(self, text: str) -> str:
        store = SessionStore(self.state.session_store_path)
        try:
            stored = store.ensure_agent_home(self.state.agent_name)
            self.state.session = stored.as_session()
        finally:
            store.close()
        try:
            turn = runtime_service.run_message(
                self.state,
                text,
                ask_user=lambda decision, context: ApprovalDecision(
                    verdict="defer",
                    summary=(
                        "Validation requise. Ouvre BB9 web ou le CLI pour confirmer cette action "
                        "avant de la lancer depuis Telegram."
                    ),
                ),
            )
            answer = turn.answer
            artifacts = runtime_service.turn_artifacts(turn, include_decision_trace=True)
        except (AgentNotFoundError, ProviderError) as exc:
            answer = f"Erreur BB9: {exc}"
            artifacts = ()
        except Exception as exc:
            answer = f"Erreur runtime Telegram: {exc}"
            artifacts = ()
        self.state.session = self.state.session.with_message("user", text).with_message("assistant", answer)
        self._persist_turn(text, answer, artifacts)
        return answer

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


def telegram_chunks(text: str, *, limit: int = TELEGRAM_MESSAGE_LIMIT) -> Iterable[str]:
    value = str(text or "").strip() or "(vide)"
    while len(value) > limit:
        split_at = value.rfind("\n", 0, limit)
        if split_at < max(1, limit // 2):
            split_at = limit
        yield value[:split_at].strip()
        value = value[split_at:].strip()
    yield value


def looks_like_telegram_token(value: str) -> bool:
    return bool(re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{20,}", value.strip()))


def telegram_offset_path(agent_name: str) -> Path:
    safe = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in (agent_name or "default"))
    return bb9_home() / "telegram" / f"{safe or 'default'}-offset.json"
