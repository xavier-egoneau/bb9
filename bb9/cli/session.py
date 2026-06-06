"""CLI helpers for short session persistence and compaction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.compaction import CompactionConfig, auto_compact_session, compact_session, estimate_session_tokens
from ..core.history import VisibleHistoryStore
from ..core.models import Artifact, Session
from ..core.sessions import SessionStore


def remember_turn(
    cli: Any,
    user_text: str,
    assistant_text: str,
    *,
    artifacts: tuple[Artifact, ...] = (),
) -> None:
    cli.state.session = cli.state.session.with_message("user", user_text)
    cli.state.session = cli.state.session.with_message("assistant", assistant_text)
    result = auto_compact_session(cli.state.session, config=compaction_config(cli))
    if result.changed:
        cli.state.session = result.session
        notice = auto_compaction_notice(result)
        print(notice)
    persist(cli)
    persist_visible_turn(cli, user_text, assistant_text, artifacts=artifacts)
    if result.changed:
        persist_visible_notification(cli, notice)


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


def persist_visible_turn(
    cli: Any,
    user_text: str,
    assistant_text: str,
    *,
    artifacts: tuple[Artifact, ...] = (),
) -> None:
    store = VisibleHistoryStore(cli.state.visible_history_path)
    try:
        store.append_turn(
            session_id=cli.state.session.id,
            user_text=user_text,
            assistant_text=assistant_text,
            source=cli.state.session.source,
            project_path=Path.cwd(),
            artifacts=artifacts,
        )
    finally:
        store.close()


def persist_visible_notification(cli: Any, content: str) -> None:
    store = VisibleHistoryStore(cli.state.visible_history_path)
    try:
        store.append_message(
            session_id=cli.state.session.id,
            role="notification",
            content=content,
            source=cli.state.session.source,
            project_path=Path.cwd(),
        )
    finally:
        store.close()


def auto_compaction_notice(result: Any) -> str:
    return (
        "Auto-compaction du contexte court : "
        f"{result.compacted_messages} ancien(s) message(s) résumés, "
        f"{len(result.session.messages)} message(s) récent(s) conservés."
    )


def count(cli: Any) -> int:
    store = SessionStore(cli.state.session_store_path)
    try:
        return store.count()
    finally:
        store.close()


def visible_count(cli: Any) -> int:
    store = VisibleHistoryStore(cli.state.visible_history_path)
    try:
        return store.count()
    finally:
        store.close()


def cmd_history(cli: Any, value: str) -> bool:
    limit = _limit(value, default=12)
    store = VisibleHistoryStore(cli.state.visible_history_path)
    try:
        markdown = store.export_markdown(limit=limit).strip()
        if hasattr(cli, "print_markdown"):
            cli.print_markdown(markdown)
        else:
            print(markdown)
    finally:
        store.close()
    return True


def compaction_config(cli: Any) -> CompactionConfig:
    metadata = cli.active_model_metadata()
    return CompactionConfig(
        context_window_tokens=metadata.context_window_tokens,
        soft_input_limit_tokens=metadata.soft_input_limit_tokens,
    )


def token_estimate(session: Session) -> int:
    return estimate_session_tokens(session)


def _limit(value: str, *, default: int) -> int:
    for token in str(value or "").replace("=", " ").split():
        if token.isdigit():
            return max(1, int(token))
    return default
