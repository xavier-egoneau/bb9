"""Agent Telegram channel configuration."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from bb9.providers.config import normalize_api_key_ref_input, resolve_secret_ref

from .archives import read_optional_text
from .markdown import extract_section

AGENT_TELEGRAM = "TELEGRAM.md"
TELEGRAM_TEMPLATE = """# Telegram

## Activation

paused

## Token


## AllowedChatIds

[]
"""


@dataclass(frozen=True)
class AgentTelegramConfig:
    enabled: bool = False
    token_ref: str = ""
    allowed_chat_ids: tuple[int | str, ...] = ()
    allowed_chat_ids_text: str = "[]"
    configured: bool = False

    def public_payload(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "token_ref": public_token_ref(self.token_ref),
            "allowed_chat_ids": list(self.allowed_chat_ids),
            "allowed_chat_ids_text": self.allowed_chat_ids_text,
            "configured": self.configured,
        }

    def resolve_token(self) -> str:
        return resolve_secret_ref(self.token_ref)

    def allows(self, chat_id: int | str) -> bool:
        expected = {str(item).strip() for item in self.allowed_chat_ids if str(item).strip()}
        return str(chat_id).strip() in expected


def read_agent_telegram_config(agent_dir: Path) -> AgentTelegramConfig:
    text = read_optional_text(agent_dir / AGENT_TELEGRAM)
    activation = first_section_line(text, "Activation") or "paused"
    token_ref = first_section_line(text, "Token")
    allowed_raw = first_section_line(text, "AllowedChatIds") or first_section_line(text, "Allowed Chat Ids")
    return AgentTelegramConfig(
        enabled=normalize_activation(activation) == "active",
        token_ref=token_ref.strip(),
        allowed_chat_ids=tuple(parse_chat_ids(allowed_raw, strict=False)),
        allowed_chat_ids_text=allowed_raw or "[]",
        configured=bool(text.strip()),
    )


def write_agent_telegram_config(agent_dir: Path, agent_name: str, payload: dict[str, object]) -> None:
    current = read_agent_telegram_config(agent_dir)
    enabled = bool(payload.get("enabled", False))
    token_input = str(payload.get("token") or payload.get("token_ref") or "").strip()
    token_ref, _ = normalize_api_key_ref_input(
        token_input,
        default_ref=current.token_ref,
        secret_name=f"TELEGRAM_{agent_name}_BOT_TOKEN",
    )
    allowed_text = str(payload.get("allowed_chat_ids") or payload.get("allowed_chat_ids_text") or "[]").strip()
    allowed_ids = parse_chat_ids(allowed_text, strict=True)
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / AGENT_TELEGRAM).write_text(
        "\n".join(
            [
                "# Telegram",
                "",
                "## Activation",
                "",
                "active" if enabled else "paused",
                "",
                "## Token",
                "",
                token_ref,
                "",
                "## AllowedChatIds",
                "",
                json.dumps(allowed_ids, ensure_ascii=False),
                "",
            ]
        ),
        encoding="utf-8",
    )


def first_section_line(markdown: str, heading: str) -> str:
    section = extract_section(markdown, heading)
    for line in section.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def normalize_activation(value: str) -> str:
    normalized = normalize_label(value)
    if normalized in {"active", "on", "yes", "oui", "true", "enabled", "enable", "actif"}:
        return "active"
    return "paused"


def public_token_ref(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if text.startswith(("secret:", "env:", "file:")):
        return text
    return "<raw-secret>"


def parse_chat_ids(value: str, *, strict: bool) -> list[int | str]:
    text = value.strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = [item.strip() for item in re.split(r"[\s,;]+", text) if item.strip()]
    if not isinstance(parsed, list):
        if strict:
            raise ValueError("Chat IDs doit être un array JSON ou une liste séparée par virgules.")
        return []
    result: list[int | str] = []
    for item in parsed:
        if isinstance(item, bool) or item is None:
            if strict:
                raise ValueError("Chat IDs ne doit contenir que des nombres ou chaînes.")
            continue
        if isinstance(item, int):
            result.append(item)
            continue
        text_item = str(item).strip()
        if not text_item:
            continue
        if re.fullmatch(r"-?\d+", text_item):
            result.append(int(text_item))
        else:
            result.append(text_item)
    return result


def normalize_label(text: str) -> str:
    replacements = str.maketrans("àâäéèêëîïôöùûüç", "aaaeeeeiioouuuc")
    return " ".join(text.lower().translate(replacements).split())
