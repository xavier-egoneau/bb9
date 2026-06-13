"""Durable session archive for recent interaction context."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import Session, SessionMessage, SessionRole
from .paths import bb9_home

AGENT_HOME_SOURCE = "agent_home"

SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bsk-or-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bsk-proj-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
)
ENV_SECRET_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASS|PWD|CREDENTIAL)[A-Za-z0-9_]*)"
    r"\s*=\s*['\"]?([^'\"\s]{8,})['\"]?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class StoredSession:
    id: str
    source: str
    project_path: str | None
    created_at: str
    updated_at: str
    compaction_summary: str = ""
    compacted_count: int = 0
    archived_at: str = ""
    messages: tuple[SessionMessage, ...] = ()

    def as_session(self) -> Session:
        return Session(
            id=self.id,
            source=self.source,
            messages=self.messages,
            compaction_summary=self.compaction_summary,
            compacted_count=self.compacted_count,
        )

    def as_dream_context(self, *, max_messages: int = 8) -> str:
        lines = [f"### Session {self.id[:8]} ({self.source})"]
        if self.project_path:
            lines.append(f"Project: {self.project_path}")
        lines.append(f"Updated: {self.updated_at}")
        if self.archived_at:
            lines.append(f"Archived: {self.archived_at}")
        if self.compaction_summary.strip():
            lines.extend(["", "Summary:", self.compaction_summary.strip()])
        recent = self.messages[-max_messages:] if max_messages >= 0 else self.messages
        if recent:
            lines.extend(["", "Messages:"])
            lines.extend(f"- {message.as_prompt_line()}" for message in recent if message.content.strip())
        return "\n".join(lines).strip()


def default_session_store_path() -> Path:
    return bb9_home() / "sessions.db"


def redact_session_text(text: str) -> str:
    redacted = ENV_SECRET_RE.sub(lambda match: f"{match.group(1)}=<secret-redacted>", text)
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("<secret-redacted>", redacted)
    return redacted


class SessionStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_session_store_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def close(self) -> None:
        self._conn.close()

    def store(self, session: Session, *, project_path: Path | str | None = None) -> StoredSession:
        project = _normalize_project_path(project_path)
        now = datetime.now(UTC).isoformat()
        existing = self._conn.execute(
            "SELECT created_at FROM sessions WHERE session_id = ?",
            (session.id,),
        ).fetchone()
        created_at = str(existing["created_at"]) if existing is not None else now
        self._conn.execute(
            """
            INSERT INTO sessions (
                session_id, source, project_path, created_at, updated_at,
                compaction_summary, compacted_count, archived_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT archived_at FROM sessions WHERE session_id = ?), ''))
            ON CONFLICT(session_id) DO UPDATE SET
                source = excluded.source,
                project_path = excluded.project_path,
                updated_at = excluded.updated_at,
                compaction_summary = excluded.compaction_summary,
                compacted_count = excluded.compacted_count
            """,
            (
                session.id,
                session.source,
                project,
                created_at,
                now,
                redact_session_text(session.compaction_summary),
                session.compacted_count,
                session.id,
            ),
        )
        self._conn.execute("DELETE FROM session_messages WHERE session_id = ?", (session.id,))
        for index, message in enumerate(session.messages):
            self._conn.execute(
                """
                INSERT INTO session_messages (session_id, seq, role, content, time)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    index,
                    message.role,
                    redact_session_text(message.content),
                    message.time,
                ),
            )
        self._conn.commit()
        stored = self.get(session.id)
        if stored is None:
            raise RuntimeError(f"session not stored: {session.id}")
        return stored

    def get(self, session_id: str) -> StoredSession | None:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return self._stored_session(row)

    def ensure_agent_home(self, agent_name: str) -> StoredSession:
        name = normalize_agent_home_name(agent_name)
        session_id = agent_home_session_id(name)
        stored = self.get(session_id)
        if stored is not None:
            return stored
        return self.store(Session(id=session_id, source=AGENT_HOME_SOURCE), project_path=None)

    def agent_homes(self, agent_names: tuple[str, ...] = ()) -> tuple[StoredSession, ...]:
        for name in agent_names:
            self.ensure_agent_home(name)
        rows = self._conn.execute(
            """
            SELECT *
            FROM sessions
            WHERE source = ?
            ORDER BY updated_at DESC
            """,
            (AGENT_HOME_SOURCE,),
        ).fetchall()
        return tuple(self._stored_session(row) for row in rows)

    def recent(
        self,
        *,
        limit: int = 20,
        project_path: Path | str | None = None,
        include_archived: bool = True,
        include_global: bool = True,
    ) -> tuple[StoredSession, ...]:
        project = _normalize_project_path(project_path)
        clauses = []
        params: list[object] = []
        if project is not None:
            if include_global:
                clauses.append("(project_path = ? OR project_path IS NULL OR project_path = '')")
            else:
                clauses.append("project_path = ?")
            params.append(project)
        if not include_archived:
            clauses.append("(archived_at IS NULL OR archived_at = '')")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM sessions{where} ORDER BY updated_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return tuple(self._stored_session(row) for row in rows)

    def projects(self, *, limit: int = 50, filter_existing: bool = False) -> tuple[dict[str, object], ...]:
        rows = self._conn.execute(
            """
            SELECT project_path, updated_at
            FROM sessions
            WHERE project_path IS NOT NULL AND project_path != ''
            ORDER BY updated_at DESC
            """,
        ).fetchall()
        projects: dict[str, dict[str, Any]] = {}
        for row in rows:
            project = _normalize_project_path(row["project_path"])
            if project is None:
                continue
            if filter_existing and not Path(project).is_dir():
                continue
            item = projects.setdefault(project, {"path": project, "updated_at": "", "session_count": 0})
            item["session_count"] = int(item["session_count"]) + 1
            if str(row["updated_at"] or "") > str(item["updated_at"] or ""):
                item["updated_at"] = str(row["updated_at"] or "")
        ordered = sorted(projects.values(), key=lambda item: str(item["updated_at"] or ""), reverse=True)
        return tuple(ordered[: max(0, limit)])

    def recent_dream_context(
        self,
        *,
        limit: int = 12,
        project_path: Path | str | None = None,
        max_messages: int = 8,
    ) -> tuple[str, ...]:
        return tuple(
            session.as_dream_context(max_messages=max_messages)
            for session in self.recent(limit=limit, project_path=project_path)
        )

    def archive(self, session_id: str, *, when: datetime | None = None) -> bool:
        archived_at = (when or datetime.now(UTC)).isoformat()
        cursor = self._conn.execute(
            "UPDATE sessions SET archived_at = ?, updated_at = ? WHERE session_id = ?",
            (archived_at, archived_at, session_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def forget(self, session_id: str) -> bool:
        self._conn.execute("DELETE FROM session_messages WHERE session_id = ?", (session_id,))
        cursor = self._conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS count FROM sessions").fetchone()
        return int(row["count"])

    def _stored_session(self, row: sqlite3.Row) -> StoredSession:
        messages = tuple(
            SessionMessage(
                role=_role(message["role"]),
                content=str(message["content"] or ""),
                time=str(message["time"] or ""),
            )
            for message in self._conn.execute(
                """
                SELECT role, content, time
                FROM session_messages
                WHERE session_id = ?
                ORDER BY seq ASC, id ASC
                """,
                (row["session_id"],),
            ).fetchall()
        )
        return StoredSession(
            id=str(row["session_id"]),
            source=str(row["source"] or ""),
            project_path=str(row["project_path"]) if row["project_path"] else None,
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
            compaction_summary=str(row["compaction_summary"] or ""),
            compacted_count=int(row["compacted_count"] or 0),
            archived_at=str(row["archived_at"] or ""),
            messages=messages,
        )

    def _ensure_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                project_path TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                compaction_summary TEXT NOT NULL DEFAULT '',
                compacted_count INTEGER NOT NULL DEFAULT 0,
                archived_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS session_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                time TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_updated_at
                ON sessions(updated_at);
            CREATE INDEX IF NOT EXISTS idx_sessions_project_path
                ON sessions(project_path);
            CREATE INDEX IF NOT EXISTS idx_session_messages_session_seq
                ON session_messages(session_id, seq);
            """
        )
        self._conn.commit()


def normalize_agent_home_name(agent_name: str) -> str:
    return (agent_name or "default").strip() or "default"


def agent_home_session_id(agent_name: str) -> str:
    return f"agent-home:{normalize_agent_home_name(agent_name)}"


def _normalize_project_path(path: Path | str | None) -> str | None:
    if path is None:
        return None
    text = str(path).strip()
    if not text:
        return None
    return str(Path(text).expanduser().resolve(strict=False))


def _role(value: object) -> SessionRole:
    role = str(value or "").strip()
    if role in {"user", "assistant", "observation"}:
        return role  # type: ignore[return-value]
    return "observation"
