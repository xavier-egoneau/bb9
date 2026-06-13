"""Durable SQL graph memory for BB9."""

from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from .paths import bb9_home

MEMORY_DB = "memory.db"

_NODE_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_nodes (
    node_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    content      TEXT NOT NULL,
    scope        TEXT NOT NULL DEFAULT 'global',
    project_path TEXT,
    kind         TEXT NOT NULL DEFAULT 'fact',
    tags         TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT '',
    confidence   TEXT NOT NULL DEFAULT 'medium',
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(content, scope, project_path)
);
"""

_EDGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_edges (
    edge_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id    INTEGER NOT NULL,
    target_id    INTEGER NOT NULL,
    relation     TEXT NOT NULL,
    weight       REAL NOT NULL DEFAULT 1.0,
    source       TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_id, target_id, relation),
    FOREIGN KEY(source_id) REFERENCES memory_nodes(node_id) ON DELETE CASCADE,
    FOREIGN KEY(target_id) REFERENCES memory_nodes(node_id) ON DELETE CASCADE
);
"""

_SUPPORT_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_memory_nodes_scope ON memory_nodes(scope);
CREATE INDEX IF NOT EXISTS idx_memory_nodes_project ON memory_nodes(project_path);
CREATE INDEX IF NOT EXISTS idx_memory_nodes_kind ON memory_nodes(kind);
CREATE INDEX IF NOT EXISTS idx_memory_edges_source ON memory_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_memory_edges_target ON memory_edges(target_id);
"""

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS memory_nodes_fts
    USING fts5(content, tags, source, content=memory_nodes, content_rowid=node_id);

CREATE TRIGGER IF NOT EXISTS memory_nodes_ai AFTER INSERT ON memory_nodes BEGIN
    INSERT INTO memory_nodes_fts(rowid, content, tags, source)
        VALUES (new.node_id, new.content, new.tags, new.source);
END;

CREATE TRIGGER IF NOT EXISTS memory_nodes_ad AFTER DELETE ON memory_nodes BEGIN
    INSERT INTO memory_nodes_fts(memory_nodes_fts, rowid, content, tags, source)
        VALUES ('delete', old.node_id, old.content, old.tags, old.source);
END;

CREATE TRIGGER IF NOT EXISTS memory_nodes_au AFTER UPDATE ON memory_nodes BEGIN
    INSERT INTO memory_nodes_fts(memory_nodes_fts, rowid, content, tags, source)
        VALUES ('delete', old.node_id, old.content, old.tags, old.source);
    INSERT INTO memory_nodes_fts(rowid, content, tags, source)
        VALUES (new.node_id, new.content, new.tags, new.source);
END;
"""

_MIGRATIONS = [
    "ALTER TABLE memory_nodes ADD COLUMN source TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE memory_nodes ADD COLUMN confidence TEXT NOT NULL DEFAULT 'medium'",
    "ALTER TABLE memory_nodes ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
]

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class MemoryNode:
    id: int
    content: str
    scope: str
    project_path: str | None
    kind: str
    tags: str
    source: str
    confidence: str
    created_at: str
    updated_at: str

    def as_prompt_line(self) -> str:
        scope = self.scope
        if self.scope == "project" and self.project_path:
            scope = f"project:{self.project_path}"
        tags = f" [{self.tags}]" if self.tags else ""
        return f"- #{self.id} ({scope}, {self.kind}){tags}: {self.content}"


@dataclass(frozen=True)
class MemoryEdge:
    id: int
    source_id: int
    target_id: int
    relation: str
    weight: float
    source: str
    created_at: str


def default_memory_path() -> Path:
    return bb9_home() / MEMORY_DB


class MemoryStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.path = Path(db_path).expanduser() if db_path is not None else default_memory_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._fts_available = False
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_NODE_SCHEMA)
            self._conn.executescript(_EDGE_SCHEMA)
            existing = {row[1] for row in self._conn.execute("PRAGMA table_info(memory_nodes)").fetchall()}
            for statement in _MIGRATIONS:
                column = statement.split("ADD COLUMN", 1)[1].split()[0]
                if column not in existing:
                    self._conn.execute(statement)
                    existing.add(column)
            self._conn.executescript(_SUPPORT_SCHEMA)
            try:
                self._conn.executescript(_FTS_SCHEMA)
                self._conn.execute("INSERT INTO memory_nodes_fts(memory_nodes_fts) VALUES ('rebuild')")
                self._fts_available = True
            except sqlite3.OperationalError:
                self._fts_available = False
            self._conn.commit()

    def add(
        self,
        content: str,
        *,
        scope: str = "global",
        project_path: str | Path | None = None,
        kind: str = "fact",
        category: str | None = None,
        tags: str = "",
        source: str = "",
        confidence: str = "medium",
    ) -> int:
        text = content.strip()
        if not text:
            raise ValueError("memory content must not be empty")
        normalized_scope = _scope(scope)
        project = _project_path(project_path) if normalized_scope == "project" else None
        normalized_kind = (category or kind or "fact").strip() or "fact"
        with self._lock:
            existing = self._conn.execute(
                """
                SELECT node_id FROM memory_nodes
                WHERE content = ? AND scope = ?
                  AND ((project_path IS NULL AND ? IS NULL) OR project_path = ?)
                """,
                (text, normalized_scope, project, project),
            ).fetchone()
            if existing is not None:
                return int(existing["node_id"])
            try:
                cursor = self._conn.execute(
                    """
                    INSERT INTO memory_nodes
                        (content, scope, project_path, kind, tags, source, confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        text,
                        normalized_scope,
                        project,
                        normalized_kind,
                        tags.strip(),
                        source.strip(),
                        confidence.strip() or "medium",
                    ),
                )
                self._conn.commit()
                return int(cursor.lastrowid or 0)
            except sqlite3.IntegrityError:
                row = self._conn.execute(
                    """
                    SELECT node_id FROM memory_nodes
                    WHERE content = ? AND scope = ?
                      AND ((project_path IS NULL AND ? IS NULL) OR project_path = ?)
                    """,
                    (text, normalized_scope, project, project),
                ).fetchone()
                return int(row["node_id"])

    add_node = add

    def add_edge(
        self,
        source_id: int,
        target_id: int,
        relation: str,
        *,
        weight: float = 1.0,
        source: str = "",
    ) -> int:
        label = relation.strip()
        if not label:
            raise ValueError("memory edge relation must not be empty")
        with self._lock:
            try:
                cursor = self._conn.execute(
                    """
                    INSERT INTO memory_edges (source_id, target_id, relation, weight, source)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (source_id, target_id, label, float(weight), source.strip()),
                )
                self._conn.commit()
                return int(cursor.lastrowid or 0)
            except sqlite3.IntegrityError:
                row = self._conn.execute(
                    """
                    SELECT edge_id FROM memory_edges
                    WHERE source_id = ? AND target_id = ? AND relation = ?
                    """,
                    (source_id, target_id, label),
                ).fetchone()
                if row is None:
                    raise
                return int(row["edge_id"])

    def get(self, node_id: int) -> MemoryNode | None:
        with self._lock:
            row = self._conn.execute(_NODE_SELECT + " WHERE node_id = ?", (node_id,)).fetchone()
        return _node(row) if row is not None else None

    def list_nodes(
        self,
        *,
        scope: str | None = None,
        project_path: str | Path | None = None,
        kind: str | None = None,
        category: str | None = None,
        limit: int = 50,
    ) -> list[MemoryNode]:
        clauses: list[str] = []
        params: list[object] = []
        if scope is not None:
            clauses.append("scope = ?")
            params.append(_scope(scope))
        if project_path is not None:
            clauses.append("project_path = ?")
            params.append(_project_path(project_path))
        selected_kind = category or kind
        if selected_kind is not None:
            clauses.append("kind = ?")
            params.append(selected_kind)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(0, limit))
        with self._lock:
            rows = self._conn.execute(
                _NODE_SELECT + f"{where} ORDER BY updated_at DESC, node_id DESC LIMIT ?",
                params,
            ).fetchall()
        return [_node(row) for row in rows]

    def search(
        self,
        query: str,
        *,
        scope: str | None = None,
        project_path: str | Path | None = None,
        kind: str | None = None,
        category: str | None = None,
        limit: int = 10,
    ) -> list[MemoryNode]:
        sanitized = _sanitize_fts_query(query)
        if not sanitized:
            return []
        if not self._fts_available:
            return self._search_like(
                query,
                scope=scope,
                project_path=project_path,
                kind=kind,
                category=category,
                limit=limit,
            )
        clauses: list[str] = []
        params: list[object] = [sanitized]
        if scope is not None:
            clauses.append("n.scope = ?")
            params.append(_scope(scope))
        if project_path is not None:
            clauses.append("n.project_path = ?")
            params.append(_project_path(project_path))
        selected_kind = category or kind
        if selected_kind is not None:
            clauses.append("n.kind = ?")
            params.append(selected_kind)
        filters = " AND " + " AND ".join(clauses) if clauses else ""
        params.append(max(0, limit))
        with self._lock:
            try:
                rows = self._conn.execute(
                    """
                    SELECT n.node_id, n.content, n.scope, n.project_path, n.kind,
                           n.tags, n.source, n.confidence, n.created_at, n.updated_at
                    FROM memory_nodes n
                    JOIN memory_nodes_fts fts ON fts.rowid = n.node_id
                    WHERE memory_nodes_fts MATCH ?
                    """
                    + filters
                    + " ORDER BY fts.rank LIMIT ?",
                    params,
                ).fetchall()
            except sqlite3.OperationalError:
                return self._search_like(
                    query,
                    scope=scope,
                    project_path=project_path,
                    kind=kind,
                    category=category,
                    limit=limit,
                )
        return [_node(row) for row in rows]

    def _search_like(
        self,
        query: str,
        *,
        scope: str | None,
        project_path: str | Path | None,
        kind: str | None,
        category: str | None,
        limit: int,
    ) -> list[MemoryNode]:
        clauses = ["(content LIKE ? OR tags LIKE ? OR source LIKE ?)"]
        needle = f"%{query.strip()}%"
        params: list[object] = [needle, needle, needle]
        if scope is not None:
            clauses.append("scope = ?")
            params.append(_scope(scope))
        if project_path is not None:
            clauses.append("project_path = ?")
            params.append(_project_path(project_path))
        selected_kind = category or kind
        if selected_kind is not None:
            clauses.append("kind = ?")
            params.append(selected_kind)
        params.append(max(0, limit))
        with self._lock:
            rows = self._conn.execute(
                _NODE_SELECT + " WHERE " + " AND ".join(clauses) + " ORDER BY updated_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [_node(row) for row in rows]

    def get_active_context(self, cwd: Path, *, limit: int = 80) -> list[MemoryNode]:
        project = _project_path(cwd)
        with self._lock:
            rows = self._conn.execute(
                _NODE_SELECT
                + """
                WHERE scope = 'global'
                   OR (scope = 'project' AND project_path = ?)
                ORDER BY scope DESC, updated_at DESC, node_id DESC
                LIMIT ?
                """,
                (project, max(0, limit)),
            ).fetchall()
        return [_node(row) for row in rows]

    def edges_for(self, node_id: int) -> list[MemoryEdge]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT edge_id, source_id, target_id, relation, weight, source, created_at
                FROM memory_edges
                WHERE source_id = ? OR target_id = ?
                ORDER BY created_at DESC, edge_id DESC
                """,
                (node_id, node_id),
            ).fetchall()
        return [_edge(row) for row in rows]

    def related(self, node_id: int, *, relation: str | None = None, limit: int = 20) -> list[MemoryNode]:
        params: list[object] = [node_id, node_id]
        relation_filter = ""
        if relation is not None:
            relation_filter = " AND e.relation = ?"
            params.append(relation)
        params.append(max(0, limit))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT n.node_id, n.content, n.scope, n.project_path, n.kind,
                       n.tags, n.source, n.confidence, n.created_at, n.updated_at
                FROM memory_edges e
                JOIN memory_nodes n
                  ON n.node_id = CASE
                    WHEN e.source_id = ? THEN e.target_id
                    ELSE e.source_id
                  END
                WHERE (e.source_id = ? OR e.target_id = ?)
                """
                + relation_filter
                + " ORDER BY e.weight DESC, e.created_at DESC LIMIT ?",
                [node_id, *params],
            ).fetchall()
        return [_node(row) for row in rows]

    def replace(self, old_text: str, new_content: str) -> bool:
        old = old_text.strip()
        new = new_content.strip()
        if not old or not new:
            return False
        with self._lock:
            row = self._conn.execute(
                "SELECT node_id FROM memory_nodes WHERE content LIKE ? LIMIT 1",
                (f"%{old}%",),
            ).fetchone()
            if row is None:
                return False
            self._conn.execute(
                "UPDATE memory_nodes SET content = ?, updated_at = CURRENT_TIMESTAMP WHERE node_id = ?",
                (new, row["node_id"]),
            )
            self._conn.commit()
            return True

    def remove(self, node_id: int) -> bool:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM memory_nodes WHERE node_id = ?", (node_id,))
            self._conn.commit()
            return cursor.rowcount > 0

    def remove_by_text(self, text: str) -> bool:
        needle = text.strip()
        if not needle:
            return False
        with self._lock:
            row = self._conn.execute(
                "SELECT node_id FROM memory_nodes WHERE content LIKE ? LIMIT 1",
                (f"%{needle}%",),
            ).fetchone()
            if row is None:
                return False
            self._conn.execute("DELETE FROM memory_nodes WHERE node_id = ?", (row["node_id"],))
            self._conn.commit()
            return True

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> MemoryStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


_NODE_SELECT = """
SELECT node_id, content, scope, project_path, kind, tags, source, confidence, created_at, updated_at
FROM memory_nodes
"""


def _scope(scope: str) -> str:
    value = scope.strip().lower()
    if value not in {"global", "project"}:
        return "global"
    return value


def _project_path(path: str | Path | None) -> str | None:
    if path is None:
        return None
    return str(Path(path).expanduser().resolve(strict=False))


def _sanitize_fts_query(query: str) -> str:
    tokens = _TOKEN_RE.findall(query)
    return " AND ".join(f'"{token}"' for token in tokens)


def _node(row: sqlite3.Row) -> MemoryNode:
    return MemoryNode(
        id=int(row["node_id"]),
        content=str(row["content"]),
        scope=str(row["scope"]),
        project_path=row["project_path"],
        kind=str(row["kind"]),
        tags=str(row["tags"]),
        source=str(row["source"]),
        confidence=str(row["confidence"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _edge(row: sqlite3.Row) -> MemoryEdge:
    return MemoryEdge(
        id=int(row["edge_id"]),
        source_id=int(row["source_id"]),
        target_id=int(row["target_id"]),
        relation=str(row["relation"]),
        weight=float(row["weight"]),
        source=str(row["source"]),
        created_at=str(row["created_at"]),
    )
