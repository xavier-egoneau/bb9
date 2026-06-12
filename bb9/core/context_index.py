"""Workspace context index generation."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from os import walk
from pathlib import Path

MAX_FILES = 160
MAX_DIRS = 80
MAX_SCAN_ENTRIES = 5000
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
    ".cache",
    ".cargo",
    ".config",
    ".git",
    ".hg",
    ".local",
    ".mypy_cache",
    ".npm",
    ".pnpm-store",
    ".pytest_cache",
    ".ruff_cache",
    ".rustup",
    ".tox",
    ".venv",
    ".var",
    "__pycache__",
    "dist",
    "node_modules",
    "snap",
}
CONTEXT_INDEX_FILE = Path(".bb9/context-index.md")
WORKSPACE_GITIGNORE = "*\n"


@dataclass(frozen=True)
class ContextScan:
    files: list[str]
    dirs: list[str]
    visited_entries: int
    truncated: bool


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
    scan = _scan(root)
    files = scan.files
    dirs = scan.dirs
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

    if scan.truncated:
        lines.extend(
            [
                "",
                "## Limits",
                "",
                f"- Scan borne apres {scan.visited_entries} entree(s) pour garder BB9 reactif.",
            ]
        )

    return "\n".join(lines) + "\n"


def _scan(root: Path) -> ContextScan:
    files: list[str] = []
    dirs: list[str] = []
    visited = 0
    truncated = False

    for current, dir_names, file_names in walk(root, topdown=True):
        current_path = Path(current)
        try:
            current_relative = current_path.relative_to(root)
        except ValueError:
            dir_names[:] = []
            continue

        if _ignored_relative(current_relative):
            dir_names[:] = []
            continue

        dir_names[:] = sorted(name for name in dir_names if name not in IGNORED_DIRS)

        for directory_name in dir_names:
            visited += 1
            relative = current_relative / directory_name
            if len(relative.parts) <= 2 and len(dirs) < MAX_DIRS:
                dirs.append(relative.as_posix())
            if visited >= MAX_SCAN_ENTRIES or (len(files) >= MAX_FILES and len(dirs) >= MAX_DIRS):
                truncated = True
                dir_names[:] = []
                break
        if truncated:
            break

        for file_name in sorted(file_names):
            visited += 1
            relative = current_relative / file_name
            if len(files) < MAX_FILES:
                files.append(relative.as_posix())
            if visited >= MAX_SCAN_ENTRIES or (len(files) >= MAX_FILES and len(dirs) >= MAX_DIRS):
                truncated = True
                break
        if truncated:
            break

    return ContextScan(
        files=sorted(files),
        dirs=sorted(dirs),
        visited_entries=visited,
        truncated=truncated,
    )


def _ignored_relative(relative: Path) -> bool:
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
