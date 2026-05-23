"""Short session compaction."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .models import Session, SessionMessage


class CompactionLevel(str, Enum):
    NONE = "none"
    TRIM = "trim"
    SUMMARIZE = "summarize"
    RESET = "reset"


@dataclass(frozen=True)
class CompactionConfig:
    context_window_tokens: int = 250_000
    soft_input_limit_tokens: int = 0
    trim_threshold: float = 0.60
    summarize_threshold: float = 0.80
    reset_threshold: float = 0.90
    keep_recent_messages: int = 8
    auto_message_threshold: int = 18
    max_summary_chars: int = 4_000


@dataclass(frozen=True)
class CompactionResult:
    session: Session
    level: CompactionLevel
    compacted_messages: int = 0
    estimated_tokens: int = 0

    @property
    def changed(self) -> bool:
        return self.compacted_messages > 0

    def notice(self) -> str:
        if not self.changed:
            return "Aucune compaction necessaire."
        return (
            f"Contexte compacte: {self.compacted_messages} message(s), "
            f"{len(self.session.messages)} conserve(s), niveau {self.level.value}."
        )


def compact_session(
    session: Session,
    *,
    force: bool = False,
    config: CompactionConfig | None = None,
) -> CompactionResult:
    cfg = config or CompactionConfig()
    token_count = estimate_session_tokens(session)
    level = compaction_level(token_count, cfg)
    if not force and level is CompactionLevel.NONE and len(session.messages) < cfg.auto_message_threshold:
        return CompactionResult(session=session, level=level, estimated_tokens=token_count)

    keep = max(0, cfg.keep_recent_messages)
    if keep and len(session.messages) <= keep:
        return CompactionResult(session=session, level=level, estimated_tokens=token_count)

    older = session.messages[:-keep] if keep else session.messages
    recent = session.messages[-keep:] if keep else ()
    if not older:
        return CompactionResult(session=session, level=level, estimated_tokens=token_count)

    summary = _merge_summary(
        session.compaction_summary,
        older,
        limit=cfg.max_summary_chars,
    )
    compacted = session.with_compaction_summary(
        summary,
        messages=recent,
        compacted_count=session.compacted_count + len(older),
    )
    effective_level = level if level is not CompactionLevel.NONE else CompactionLevel.TRIM
    return CompactionResult(
        session=compacted,
        level=effective_level,
        compacted_messages=len(older),
        estimated_tokens=token_count,
    )


def auto_compact_session(
    session: Session,
    *,
    config: CompactionConfig | None = None,
) -> CompactionResult:
    return compact_session(session, force=False, config=config)


def total_message_characters(messages: tuple[SessionMessage, ...]) -> int:
    return sum(len(message.content) for message in messages)


def estimate_tokens_from_chars(char_count: int, *, chars_per_token: int = 4) -> int:
    if char_count <= 0:
        return 0
    return max(1, char_count // max(1, chars_per_token))


def estimate_session_tokens(session: Session) -> int:
    return estimate_tokens_from_chars(
        len(session.compaction_summary) + total_message_characters(session.messages)
    )


def compaction_level(token_count: int, config: CompactionConfig) -> CompactionLevel:
    if config.soft_input_limit_tokens > 0 and token_count >= config.soft_input_limit_tokens:
        return CompactionLevel.SUMMARIZE
    if config.context_window_tokens <= 0:
        return CompactionLevel.NONE
    ratio = token_count / config.context_window_tokens
    if ratio >= config.reset_threshold:
        return CompactionLevel.RESET
    if ratio >= config.summarize_threshold:
        return CompactionLevel.SUMMARIZE
    if ratio >= config.trim_threshold:
        return CompactionLevel.TRIM
    return CompactionLevel.NONE


def _merge_summary(previous: str, messages: tuple[SessionMessage, ...], *, limit: int) -> str:
    parts = ["Contexte court compacte."]
    if previous.strip():
        parts.extend(["", "Resume precedent:", _clip(previous.strip(), limit // 2)])
    parts.extend(["", "Messages compactes:"])
    for message in messages:
        content = _one_line(message.content)
        if not content:
            continue
        parts.append(f"- {message.role}: {_clip(content, 240)}")
    return _clip("\n".join(parts).strip(), limit)


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 16:
        return text[:limit]
    return text[: limit - 16].rstrip() + "\n...[compacte]"
