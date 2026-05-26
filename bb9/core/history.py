"""Visible user history and artifact persistence."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from .models import Artifact, VisibleMessage, VisibleRole
from .paths import bb9_home
from .sessions import redact_session_text


HISTORY_DB = "visible-history.db"


def default_visible_history_path() -> Path:
    return bb9_home() / HISTORY_DB


class VisibleHistoryStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_visible_history_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def close(self) -> None:
        self._conn.close()

    def append_message(
        self,
        *,
        session_id: str,
        role: VisibleRole,
        content: str,
        source: str = "cli",
        project_path: Path | str | None = None,
        artifacts: tuple[Artifact, ...] = (),
    ) -> VisibleMessage:
        message = VisibleMessage(
            id=str(uuid4()),
            role=role,
            content=redact_session_text(content),
            session_id=session_id,
            source=source,
            project_path=_normalize_project_path(project_path),
            artifacts=artifacts,
        )
        self._conn.execute(
            """
            INSERT INTO visible_messages (
                message_id, session_id, role, content, source, project_path, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message.id,
                message.session_id,
                message.role,
                message.content,
                message.source,
                message.project_path,
                message.created_at,
            ),
        )
        for artifact in artifacts:
            self._store_artifact(artifact, message_id=message.id)
        self._conn.commit()
        return message

    def append_turn(
        self,
        *,
        session_id: str,
        user_text: str,
        assistant_text: str,
        source: str = "cli",
        project_path: Path | str | None = None,
        artifacts: tuple[Artifact, ...] = (),
    ) -> tuple[VisibleMessage, VisibleMessage]:
        user = self.append_message(
            session_id=session_id,
            role="user",
            content=user_text,
            source=source,
            project_path=project_path,
        )
        assistant = self.append_message(
            session_id=session_id,
            role="assistant",
            content=assistant_text,
            source=source,
            project_path=project_path,
            artifacts=artifacts,
        )
        return user, assistant

    def append_process(
        self,
        *,
        session_id: str,
        content: str,
        source: str = "cli",
        project_path: Path | str | None = None,
        artifacts: tuple[Artifact, ...] = (),
    ) -> VisibleMessage:
        return self.append_message(
            session_id=session_id,
            role="process",
            content=content,
            source=source,
            project_path=project_path,
            artifacts=artifacts,
        )

    def store_artifact(self, artifact: Artifact, *, message_id: str = "") -> Artifact:
        self._store_artifact(artifact, message_id=message_id or None)
        self._conn.commit()
        return artifact

    def recent(
        self,
        *,
        limit: int = 50,
        session_id: str = "",
        project_path: Path | str | None = None,
    ) -> tuple[VisibleMessage, ...]:
        project = _normalize_project_path(project_path)
        clauses: list[str] = []
        params: list[object] = []
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if project is not None:
            clauses.append("(project_path = ? OR project_path IS NULL OR project_path = '')")
            params.append(project)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM visible_messages{where} ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (*params, max(0, limit)),
        ).fetchall()
        messages = [self._message(row) for row in rows]
        return tuple(reversed(messages))

    def artifacts_for_message(self, message_id: str) -> tuple[Artifact, ...]:
        rows = self._conn.execute(
            "SELECT * FROM artifacts WHERE message_id = ? ORDER BY created_at ASC, rowid ASC",
            (message_id,),
        ).fetchall()
        return tuple(_artifact(row) for row in rows)

    def export_markdown(
        self,
        *,
        limit: int = 50,
        session_id: str = "",
        project_path: Path | str | None = None,
    ) -> str:
        messages = self.recent(limit=limit, session_id=session_id, project_path=project_path)
        lines = ["# BB9 Visible History", ""]
        if not messages:
            lines.append("Aucun message visible.")
            return "\n".join(lines).strip() + "\n"
        for message in messages:
            lines.append(f"## {message.role} - {message.created_at}")
            if message.project_path:
                lines.append(f"Project: {message.project_path}")
            lines.extend(["", message.content.strip() or "(vide)", ""])
            for artifact in message.artifacts:
                lines.extend(_artifact_markdown_lines(artifact))
            if message.artifacts:
                lines.append("")
        return "\n".join(lines).strip() + "\n"

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS count FROM visible_messages").fetchone()
        return int(row["count"])

    def _message(self, row: sqlite3.Row) -> VisibleMessage:
        message_id = str(row["message_id"])
        return VisibleMessage(
            id=message_id,
            role=_role(row["role"]),
            content=str(row["content"] or ""),
            session_id=str(row["session_id"] or ""),
            source=str(row["source"] or ""),
            project_path=str(row["project_path"]) if row["project_path"] else None,
            created_at=str(row["created_at"] or ""),
            artifacts=self.artifacts_for_message(message_id),
        )

    def _store_artifact(self, artifact: Artifact, *, message_id: str | None) -> None:
        self._conn.execute(
            """
            INSERT INTO artifacts (
                artifact_id, message_id, kind, title, path, source, created_at, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(artifact_id) DO UPDATE SET
                message_id = excluded.message_id,
                kind = excluded.kind,
                title = excluded.title,
                path = excluded.path,
                source = excluded.source,
                metadata_json = excluded.metadata_json
            """,
            (
                artifact.id,
                message_id,
                artifact.kind,
                artifact.title,
                artifact.path,
                artifact.source,
                artifact.created_at,
                json.dumps(artifact.metadata, ensure_ascii=False, sort_keys=True),
            ),
        )

    def _ensure_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS visible_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT NOT NULL UNIQUE,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'cli',
                project_path TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artifact_id TEXT NOT NULL UNIQUE,
                message_id TEXT,
                kind TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                path TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(message_id) REFERENCES visible_messages(message_id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_visible_messages_session
                ON visible_messages(session_id);
            CREATE INDEX IF NOT EXISTS idx_visible_messages_project
                ON visible_messages(project_path);
            CREATE INDEX IF NOT EXISTS idx_visible_messages_created
                ON visible_messages(created_at);
            CREATE INDEX IF NOT EXISTS idx_artifacts_message
                ON artifacts(message_id);
            """
        )
        self._conn.commit()


def _artifact(row: sqlite3.Row) -> Artifact:
    try:
        metadata = json.loads(str(row["metadata_json"] or "{}"))
    except json.JSONDecodeError:
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    return Artifact(
        id=str(row["artifact_id"] or ""),
        kind=str(row["kind"] or "file"),  # type: ignore[arg-type]
        title=str(row["title"] or ""),
        path=str(row["path"] or ""),
        source=str(row["source"] or ""),
        created_at=str(row["created_at"] or ""),
        metadata=metadata,
    )


def _artifact_markdown_lines(artifact: Artifact) -> list[str]:
    if artifact.kind == "diff":
        return _diff_artifact_markdown_lines(artifact)
    if artifact.kind == "tool_trace":
        return _tool_trace_artifact_markdown_lines(artifact)
    label = artifact.title or artifact.path or artifact.id
    return [f"- Artifact `{artifact.kind}`: {label}"]


def _diff_artifact_markdown_lines(artifact: Artifact) -> list[str]:
    metadata = artifact.metadata
    title = artifact.title or _diff_title(metadata)
    lines = [f"- Artifact `diff`: {title}"]
    files = metadata.get("files")
    if isinstance(files, list):
        for item in files[:20]:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            status = str(item.get("status") or "").strip()
            status_part = f" ({status})" if status else ""
            insertions = _metadata_int(item.get("insertions"))
            deletions = _metadata_int(item.get("deletions"))
            lines.append(f"  - `{path}`{status_part}: +{insertions}/-{deletions}")
        if len(files) > 20:
            lines.append(f"  - ... {len(files) - 20} fichier(s) masqué(s)")
    if artifact.path:
        lines.append(f"  - Patch: `{artifact.path}`")
    return lines


def _diff_title(metadata: dict[str, object]) -> str:
    files = _metadata_int(metadata.get("files_changed"))
    insertions = _metadata_int(metadata.get("insertions"))
    deletions = _metadata_int(metadata.get("deletions"))
    suffix = "fichier modifié" if files == 1 else "fichiers modifiés"
    return f"{files} {suffix} (+{insertions}/-{deletions})"


def _metadata_int(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value or "").strip()
    return int(text) if text.isdigit() else 0


def _tool_trace_artifact_markdown_lines(artifact: Artifact) -> list[str]:
    title = artifact.title or "Trace tools"
    lines = [f"- Artifact `tool_trace`: {title}"]
    entries = artifact.metadata.get("entries")
    if isinstance(entries, list):
        for item in entries[:20]:
            if not isinstance(item, dict):
                continue
            tool = str(item.get("tool") or "").strip()
            if not tool:
                continue
            status = "ok" if item.get("ok") else "error"
            summary = " ".join(str(item.get("summary") or "").split())
            suffix = f" - {summary}" if summary else ""
            lines.append(f"  - `{tool}`: {status}{suffix}")
        if len(entries) > 20:
            lines.append(f"  - ... {len(entries) - 20} trace(s) masquée(s)")
    return lines


def _role(value: object) -> VisibleRole:
    role = str(value or "").strip()
    if role in {"user", "assistant", "notification", "system", "process"}:
        return role  # type: ignore[return-value]
    return "system"


def _normalize_project_path(path: Path | str | None) -> str | None:
    if path is None:
        return None
    text = str(path).strip()
    if not text:
        return None
    return str(Path(text).expanduser().resolve(strict=False))
