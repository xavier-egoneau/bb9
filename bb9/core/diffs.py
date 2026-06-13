"""Visible diff artifact helpers."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import Artifact
from .paths import bb9_home

IGNORED_PATHS = {".bb9/.gitignore", ".bb9/context-index.md"}
IGNORED_PREFIXES = (".bb9/artifacts/", ".bb9/uploads/")


@dataclass(frozen=True)
class WorktreeSnapshot:
    root: Path | None
    dirty_hashes: dict[str, str]
    dirty_statuses: dict[str, str]


def capture_worktree_snapshot(workspace: Path | str | None = None) -> WorktreeSnapshot:
    root = _git_root(Path(workspace) if workspace is not None else Path.cwd())
    if root is None:
        return WorktreeSnapshot(root=None, dirty_hashes={}, dirty_statuses={})
    statuses = _dirty_statuses(root)
    return WorktreeSnapshot(
        root=root,
        dirty_hashes={path: _content_hash(root / path) for path in statuses},
        dirty_statuses=statuses,
    )


def diff_artifact_since(
    snapshot: WorktreeSnapshot,
    *,
    workspace: Path | str | None = None,
) -> Artifact | None:
    root = snapshot.root or _git_root(Path(workspace) if workspace is not None else Path.cwd())
    if root is None:
        return None

    statuses = _dirty_statuses(root)
    changed_paths = tuple(
        path
        for path in sorted(statuses)
        if path not in snapshot.dirty_statuses or _content_hash(root / path) != snapshot.dirty_hashes.get(path, "")
    )
    if not changed_paths:
        return None

    file_stats = _file_stats(root, changed_paths, statuses)
    insertions = sum(stat["insertions"] for stat in file_stats)
    deletions = sum(stat["deletions"] for stat in file_stats)
    patch_path = _write_patch(root, changed_paths)
    title = _title(len(file_stats), insertions, deletions)
    return Artifact(
        kind="diff",
        title=title,
        path=str(patch_path) if patch_path is not None else "",
        source="git",
        metadata={
            "workspace": str(root),
            "files_changed": len(file_stats),
            "insertions": insertions,
            "deletions": deletions,
            "files": file_stats,
            "default_collapsed": True,
            "counts_against": "git_worktree_base",
        },
    )


def _git_root(workspace: Path) -> Path | None:
    result = _git(workspace, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        return None
    root = result.stdout.strip()
    return Path(root).resolve(strict=False) if root else None


def _dirty_statuses(root: Path) -> dict[str, str]:
    result = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if result.returncode != 0:
        return {}
    parts = [part for part in result.stdout.split("\0") if part]
    statuses: dict[str, str] = {}
    index = 0
    while index < len(parts):
        entry = parts[index]
        if len(entry) < 4:
            index += 1
            continue
        status = entry[:2]
        path = entry[3:]
        if status[0] in {"R", "C"} or status[1] in {"R", "C"}:
            index += 1
        if not _ignored_path(path):
            statuses[path] = status
        index += 1
    return statuses


def _file_stats(root: Path, paths: tuple[str, ...], statuses: dict[str, str]) -> list[dict[str, Any]]:
    numstat = _numstat(root, paths)
    stats_by_path: dict[str, tuple[int, int]] = dict(numstat)
    files: list[dict[str, object]] = []
    for path in paths:
        status = statuses.get(path, "")
        insertions, deletions = stats_by_path.get(path, (0, 0))
        if path not in stats_by_path and status == "??":
            insertions = _line_count(root / path)
        files.append(
            {
                "path": path,
                "status": status.strip() or status,
                "insertions": insertions,
                "deletions": deletions,
                "hunks_available": path in stats_by_path,
            }
        )
    return files


def _numstat(root: Path, paths: tuple[str, ...]) -> tuple[tuple[str, tuple[int, int]], ...]:
    result = _git(root, "diff", "--numstat", "--", *paths)
    if result.returncode != 0:
        return ()
    rows: list[tuple[str, tuple[int, int]]] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        additions, deletions, path = parts[0], parts[1], parts[2]
        rows.append((path, (_int_or_zero(additions), _int_or_zero(deletions))))
    return tuple(rows)


def _write_patch(root: Path, paths: tuple[str, ...]) -> Path | None:
    result = _git(root, "diff", "--", *paths)
    patch = result.stdout.strip()
    if result.returncode != 0 or not patch:
        return None
    artifact_dir = bb9_home() / "artifacts" / "diffs" / _root_key(root)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}.diff"
    path.write_text(patch + "\n", encoding="utf-8")
    return path


def _content_hash(path: Path) -> str:
    if not path.exists():
        return "<missing>"
    if path.is_symlink():
        try:
            return "symlink:" + str(path.readlink())
        except OSError:
            return "<unreadable-symlink>"
    if not path.is_file():
        return "<not-file>"
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return "<unreadable>"
    return digest.hexdigest()


def _line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        data = path.read_bytes()
    except OSError:
        return 0
    if b"\0" in data:
        return 0
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def _int_or_zero(value: str) -> int:
    return int(value) if value.isdigit() else 0


def _title(file_count: int, insertions: int, deletions: int) -> str:
    suffix = "fichier modifié" if file_count == 1 else "fichiers modifiés"
    return f"{file_count} {suffix} (+{insertions}/-{deletions})"


def _root_key(root: Path) -> str:
    return hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:16]


def _ignored_path(path: str) -> bool:
    return path in IGNORED_PATHS or any(path.startswith(prefix) for prefix in IGNORED_PREFIXES)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
