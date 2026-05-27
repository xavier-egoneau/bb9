"""Trusted root loading and path classification."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .paths import bb9_home

PathZone = Literal["workspace", "trusted", "outside", "protected"]

TRUSTED_ROOTS_FILE = bb9_home() / "trusted-roots.md"
PROTECTED_PREFIXES = (
    Path("/bin"),
    Path("/boot"),
    Path("/dev"),
    Path("/etc"),
    Path("/proc"),
    Path("/root"),
    Path("/sbin"),
    Path("/sys"),
    Path("/usr"),
)
PROTECTED_HOME_NAMES = {
    ".aws",
    ".config",
    ".docker",
    ".gnupg",
    ".kube",
    ".local/share/keyrings",
    ".ssh",
}


@dataclass(frozen=True)
class TrustedRoots:
    roots: tuple[Path, ...] = ()

    @staticmethod
    def load(path: Path = TRUSTED_ROOTS_FILE) -> TrustedRoots:
        if not path.exists():
            return TrustedRoots()
        roots: list[Path] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped.startswith(("-", "*")):
                continue
            value = stripped[1:].strip()
            if value:
                roots.append(Path(value).expanduser().resolve())
        return TrustedRoots(tuple(roots))

    def contains(self, path: Path) -> bool:
        resolved = path.expanduser().resolve()
        return any(_is_relative_to(resolved, root) for root in self.roots)

    @staticmethod
    def add(root: Path, path: Path = TRUSTED_ROOTS_FILE) -> Path:
        resolved = root.expanduser().resolve()
        if is_protected_path(resolved):
            raise ValueError(f"protected path cannot become trusted root: {resolved}")

        current = TrustedRoots.load(path)
        if any(resolved == existing for existing in current.roots):
            return resolved

        path.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines()
        if not lines:
            lines = ["# Trusted Roots", ""]
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"- {resolved}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return resolved


def classify_path(path: Path, workspace: Path, trusted_roots: TrustedRoots) -> PathZone:
    expanded = path.expanduser()
    if is_protected_path(expanded):
        return "protected"
    resolved = expanded.resolve()
    workspace_root = workspace.expanduser().resolve()
    if _is_relative_to(resolved, workspace_root):
        return "workspace"
    if trusted_roots.contains(resolved):
        return "trusted"
    return "outside"


def is_protected_path(path: Path) -> bool:
    expanded = path.expanduser()
    resolved = expanded.resolve()
    candidates = [resolved]
    if expanded.is_absolute():
        candidates.append(expanded)
    if any(_is_relative_to(candidate, prefix) for candidate in candidates for prefix in PROTECTED_PREFIXES):
        return True
    home = Path.home().resolve()
    if _is_relative_to(resolved, home):
        relative = resolved.relative_to(home)
        text = str(relative)
        return any(text == name or text.startswith(f"{name}/") for name in PROTECTED_HOME_NAMES)
    return False


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
