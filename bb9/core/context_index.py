"""Workspace context index generation."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

MAX_FILES = 160
MAX_DIRS = 80
IMPORTANT_FILES = (
    "AGENTS.md",
    "README.md",
    "ROADMAP.md",
    "DECISIONS.md",
    "MEMORY.md",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "docker-compose.yml",
)
IGNORED_DIRS = {
    ".bb9",
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "dist",
    "node_modules",
}
CONTEXT_INDEX_FILE = Path(".bb9/context-index.md")
WORKSPACE_GITIGNORE = "*\n"


def refresh_context_index(workspace: Path, path: Path = CONTEXT_INDEX_FILE) -> str:
    text = build_context_index(workspace)
    index_path = _index_path(workspace, path)
    _ensure_metadata_dir(index_path.parent)
    index_path.write_text(text, encoding="utf-8")
    return text


def load_context_index(workspace: Path, path: Path = CONTEXT_INDEX_FILE) -> str:
    index_path = _index_path(workspace, path)
    if not index_path.exists():
        return refresh_context_index(workspace, path)
    return index_path.read_text(encoding="utf-8")


def load_or_refresh_context_index(workspace: Path, path: Path = CONTEXT_INDEX_FILE, *, max_age_seconds: float = 30.0) -> str:
    index_path = _index_path(workspace, path)
    if index_path.exists() and max_age_seconds > 0:
        try:
            if time.time() - index_path.stat().st_mtime <= max_age_seconds:
                return index_path.read_text(encoding="utf-8")
        except OSError:
            pass
    return refresh_context_index(workspace, path)


def build_context_index(workspace: Path) -> str:
    root = workspace.expanduser().resolve()
    files = _files(root)
    dirs = _dirs(root)
    important = [name for name in IMPORTANT_FILES if (root / name).is_file()]

    lines = [
        "# Context Index",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        f"Workspace: {root}",
        "",
        "## Role",
        "",
        "Carte locale regenerable du workspace. Elle aide a s'orienter mais ne remplace pas la lecture des fichiers sources.",
        "",
        "## Governance",
        "",
    ]
    lines.extend(f"- `{name}`" for name in important)
    if not important:
        lines.append("- Aucun fichier de gouvernance connu detecte.")

    lines.extend(["", "## Directories", ""])
    lines.extend(f"- `{directory}`" for directory in dirs[:MAX_DIRS])
    if len(dirs) > MAX_DIRS:
        lines.append(f"- ... {len(dirs) - MAX_DIRS} autre(s)")
    if not dirs:
        lines.append("- Aucun dossier notable detecte.")

    lines.extend(["", "## Files", ""])
    lines.extend(f"- `{file}`" for file in files[:MAX_FILES])
    if len(files) > MAX_FILES:
        lines.append(f"- ... {len(files) - MAX_FILES} autre(s)")
    if not files:
        lines.append("- Aucun fichier detecte.")

    return "\n".join(lines) + "\n"


def _files(root: Path) -> list[str]:
    result: list[str] = []
    for path in root.rglob("*"):
        if _ignored(path, root) or not path.is_file():
            continue
        result.append(path.relative_to(root).as_posix())
    return sorted(result)


def _dirs(root: Path) -> list[str]:
    result: list[str] = []
    for path in root.rglob("*"):
        if _ignored(path, root) or not path.is_dir():
            continue
        relative = path.relative_to(root)
        if len(relative.parts) <= 2:
            result.append(relative.as_posix())
    return sorted(result)


def _ignored(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    return any(part in IGNORED_DIRS for part in relative.parts)


def _index_path(workspace: Path, path: Path) -> Path:
    if path.is_absolute():
        return path
    return workspace / path


def _ensure_metadata_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    gitignore = path / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(WORKSPACE_GITIGNORE, encoding="utf-8")
