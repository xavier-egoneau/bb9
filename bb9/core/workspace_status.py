"""Small volatile workspace status for runtime context."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .context_index import CONTEXT_INDEX_FILE, IMPORTANT_FILES

PACKAGE_MANAGERS = (
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("bun.lockb", "bun"),
    ("bun.lock", "bun"),
    ("package-lock.json", "npm"),
)
MAX_SCRIPTS = 12


def build_workspace_status(workspace: Path, *, context_index: str = "") -> str:
    root = workspace.expanduser().resolve(strict=False)
    lines = [
        "# Workspace Status",
        "",
        "Etat technique volatil du workspace. Il aide a cadrer le prochain geste mais ne remplace pas la lecture ciblee.",
        "",
        f"- Root: `{root}`",
        f"- Git: {_git_status(root)}",
        f"- Package manager: {_package_manager(root)}",
        f"- Scripts: {_package_scripts(root)}",
        f"- Governance: {_governance_files(root)}",
        f"- Context index: {_context_index_status(root, context_index)}",
        "- Read state: aucun fichier source n'est considere comme lu durablement par cet inventaire.",
    ]
    return "\n".join(lines) + "\n"


def _git_status(root: Path) -> str:
    branch = _git(root, "branch", "--show-current")
    inside = _git(root, "rev-parse", "--is-inside-work-tree")
    if inside != "true":
        return "not a git worktree"
    if not branch:
        branch = _git(root, "rev-parse", "--short", "HEAD") or "detached"
    porcelain = _git(root, "status", "--short")
    dirty_count = len([line for line in porcelain.splitlines() if line.strip()])
    dirty = "clean" if dirty_count == 0 else f"dirty ({dirty_count} file(s))"
    return f"branch `{branch}`, {dirty}"


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(root),
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _package_manager(root: Path) -> str:
    if not (root / "package.json").is_file():
        return "none detected"
    for filename, name in PACKAGE_MANAGERS:
        if (root / filename).is_file():
            return name
    return "npm assumed from `package.json`"


def _package_scripts(root: Path) -> str:
    path = root / "package.json"
    if not path.is_file():
        return "none"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return "unreadable `package.json`"
    scripts = data.get("scripts")
    if not isinstance(scripts, dict) or not scripts:
        return "none"
    names = sorted(str(name) for name in scripts if str(name).strip())
    shown = names[:MAX_SCRIPTS]
    suffix = f", ... {len(names) - MAX_SCRIPTS} more" if len(names) > MAX_SCRIPTS else ""
    return ", ".join(f"`{name}`" for name in shown) + suffix


def _governance_files(root: Path) -> str:
    found = [name for name in IMPORTANT_FILES if (root / name).is_file()]
    if not found:
        return "none detected"
    return ", ".join(f"`{name}`" for name in found)


def _context_index_status(root: Path, context_index: str) -> str:
    generated = _context_index_generated(context_index)
    index_path = root / CONTEXT_INDEX_FILE
    if index_path.is_file():
        line_count = _line_count(index_path)
        details = f"{line_count} line(s)"
    elif context_index.strip():
        details = f"{len(context_index.splitlines())} line(s), not persisted"
    else:
        details = "absent"
    if generated:
        return f"{details}, generated {generated}"
    return details


def _context_index_generated(context_index: str) -> str:
    for line in context_index.splitlines():
        if line.startswith("Generated:"):
            return line.removeprefix("Generated:").strip()
    return ""


def _line_count(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8").splitlines())
    except (OSError, UnicodeDecodeError):
        return 0
