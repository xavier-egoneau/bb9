"""CLI helpers for short session persistence and compaction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .compaction import CompactionConfig, auto_compact_session, compact_session, estimate_session_tokens
from .models import Session
from .sessions import SessionStore


def remember_turn(cli: Any, user_text: str, assistant_text: str) -> None:
    cli.state.session = cli.state.session.with_message("user", user_text)
    cli.state.session = cli.state.session.with_message("assistant", assistant_text)
    result = auto_compact_session(cli.state.session, config=compaction_config(cli))
    if result.changed:
        cli.state.session = result.session
        print(f"cmp... auto: {result.compacted_messages} message(s)")
    persist(cli)


def cmd_new(cli: Any, _: str) -> bool:
    persist(cli)
    cli.state.session = Session(source="cli")
    persist(cli)
    print(f"Nouvelle session: {cli.state.session.id[:8]}")
    return True


def cmd_compact(cli: Any, _: str) -> bool:
    result = compact_session(cli.state.session, force=True, config=compaction_config(cli))
    cli.state.session = result.session
    persist(cli)
    print(result.notice())
    return True


def persist(cli: Any) -> None:
    if not cli.state.session.messages and not cli.state.session.compaction_summary.strip():
        return
    store = SessionStore(cli.state.session_store_path)
    try:
        store.store(cli.state.session, project_path=Path.cwd())
    finally:
        store.close()


def count(cli: Any) -> int:
    store = SessionStore(cli.state.session_store_path)
    try:
        return store.count()
    finally:
        store.close()


def compaction_config(cli: Any) -> CompactionConfig:
    metadata = cli.active_model_metadata()
    return CompactionConfig(
        context_window_tokens=metadata.context_window_tokens,
        soft_input_limit_tokens=metadata.soft_input_limit_tokens,
    )


def token_estimate(session: Session) -> int:
    return estimate_session_tokens(session)
