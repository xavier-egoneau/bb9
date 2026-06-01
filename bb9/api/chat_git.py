"""Git integration helpers for the chat API."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from bb9.core.diffs import IGNORED_PATHS, IGNORED_PREFIXES


def git_changed_files(root: Path) -> list[dict[str, Any]]:
    result = _git_run(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if result.returncode != 0:
        return []
    parts = [part for part in result.stdout.split("\0") if part]
    statuses: dict[str, str] = {}
    index = 0
    while index < len(parts):
        entry = parts[index]
        if len(entry) >= 4:
            status = entry[:2]
            path = entry[3:]
            if status[0] in {"R", "C"} or status[1] in {"R", "C"}:
                index += 1
            if not _git_ignored_path(path):
                statuses[path] = status
        index += 1
    return git_file_stats(root, tuple(sorted(statuses)), statuses)


def git_file_stats(root: Path, paths: tuple[str, ...], statuses: dict[str, str]) -> list[dict[str, Any]]:
    stats_by_path = dict(_git_numstat(root, paths))
    files: list[dict[str, Any]] = []
    for path in paths:
        status = statuses.get(path, "")
        insertions, deletions = stats_by_path.get(path, (0, 0))
        if path not in stats_by_path and status == "??":
            insertions = _git_line_count(root / path)
        files.append(
            {
                "path": path,
                "status": status.strip() or status,
                "insertions": insertions,
                "deletions": deletions,
            }
        )
    return files


def git_file_diff(root: Path, path: str, status: str) -> str:
    if status == "??":
        return _git_untracked_diff(root, path)
    chunks: list[str] = []
    for args in (("diff", "--"), ("diff", "--cached", "--")):
        result = _git_run(root, *args, path)
        if result.returncode == 0 and result.stdout.strip():
            chunks.append(result.stdout.rstrip())
    return "\n".join(chunks) or "Aucun diff textuel disponible."


def git_commit_message(files: list[dict[str, Any]]) -> str:
    ordered = sorted(files, key=lambda item: str(item.get("path") or ""))
    if len(ordered) == 1:
        subject = f"{_git_commit_verb(str(ordered[0].get('status') or ''))} {ordered[0].get('path') or 'file'}"
    else:
        verbs = {_git_commit_verb(str(file.get("status") or "")) for file in ordered}
        verb = verbs.pop() if len(verbs) == 1 else "Update"
        subject = f"{verb} {len(ordered)} files"
    body = ["Changed files:"]
    body.extend(
        f"- {_git_status_summary(str(file.get('status') or ''))}: {file.get('path') or ''} "
        f"(+{int(file.get('insertions') or 0)} -{int(file.get('deletions') or 0)})"
        for file in ordered[:12]
    )
    if len(ordered) > 12:
        body.append(f"- ... {len(ordered) - 12} more file(s)")
    return f"{subject}\n\n" + "\n".join(body)


def git_commit(root: Path, message: str) -> subprocess.CompletedProcess[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", message) if part.strip()]
    args = ["commit"]
    for paragraph in paragraphs:
        args.extend(("-m", paragraph))
    return _git_run(root, *args, timeout=15)


def clean_git_commit_message(message: str) -> str:
    lines = [line.rstrip() for line in message.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip()


def git_ignored_path(path: str) -> bool:
    return path in IGNORED_PATHS or any(path.startswith(prefix) for prefix in IGNORED_PREFIXES)


def valid_git_relative_path(path: str) -> bool:
    if not path or "\0" in path:
        return False
    candidate = Path(path)
    return not candidate.is_absolute() and ".." not in candidate.parts


def git_branches(root: Path) -> list[dict[str, Any]]:
    result = _git_run(root, "branch", "--format=%(refname:short)")
    if result.returncode != 0:
        return []
    current = git_text(root, "branch", "--show-current")
    branches = []
    seen: set[str] = set()
    for line in result.stdout.splitlines():
        name = line.strip()
        if not name or name in seen:
            continue
        branches.append({"name": name, "current": name == current})
        seen.add(name)
    return branches


def git_text(root: Path, *args: str) -> str:
    result = _git_run(root, *args)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _git_untracked_diff(root: Path, path: str) -> str:
    target = (root / path).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError:
        return "Chemin hors dépôt."
    if not target.is_file():
        return "Aucun diff textuel disponible."
    try:
        data = target.read_bytes()
    except OSError:
        return "Fichier illisible."
    if b"\0" in data:
        return "Fichier binaire non suivi."
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    limit = 400
    shown = lines[:limit]
    patch = [
        f"diff --git a/{path} b/{path}",
        "new file mode 100644",
        "--- /dev/null",
        f"+++ b/{path}",
        f"@@ -0,0 +1,{len(lines)} @@",
    ]
    patch.extend(f"+{line}" for line in shown)
    if len(lines) > limit:
        patch.append(f"+... {len(lines) - limit} ligne(s) masquée(s)")
    return "\n".join(patch)


def _git_numstat(root: Path, paths: tuple[str, ...]) -> tuple[tuple[str, tuple[int, int]], ...]:
    if not paths:
        return ()
    totals: dict[str, tuple[int, int]] = {}
    for args in (("diff", "--numstat"), ("diff", "--cached", "--numstat")):
        result = _git_run(root, *args, "--", *paths)
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            path = parts[2]
            current = totals.get(path, (0, 0))
            totals[path] = (current[0] + _git_int(parts[0]), current[1] + _git_int(parts[1]))
    return tuple(totals.items())


def _git_line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        data = path.read_bytes()
    except OSError:
        return 0
    if b"\0" in data:
        return 0
    return data.count(b"\n") + (0 if not data or data.endswith(b"\n") else 1)


def _git_int(value: str) -> int:
    return int(value) if value.isdigit() else 0


def _git_commit_verb(status: str) -> str:
    value = status.strip()
    if value == "??" or "A" in value:
        return "Add"
    if "D" in value:
        return "Remove"
    if "R" in value:
        return "Rename"
    return "Update"


def _git_status_summary(status: str) -> str:
    value = status.strip()
    if value == "??":
        return "new"
    if "A" in value:
        return "added"
    if "D" in value:
        return "deleted"
    if "R" in value:
        return "renamed"
    return "modified"


def _git_ignored_path(path: str) -> bool:
    return path in IGNORED_PATHS or any(path.startswith(prefix) for prefix in IGNORED_PREFIXES)


def _git_run(root: Path, *args: str, timeout: float = 2.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ("git", *args),
            cwd=str(root),
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(("git", *args), 1, "", str(exc))
