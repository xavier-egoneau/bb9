"""Project/workspace resolution primitives shared by channels."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .sessions import SessionStore, default_session_store_path
from .settings import SettingsStore, default_settings_path

PROJECT_MARKERS = (
    ".git",
    "AGENTS.md",
    "README.md",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
)

_SLASH_SWITCH_RE = re.compile(r"^\s*/(?:project|workspace|cd)\s+(.+?)\s*$", re.IGNORECASE)
_NATURAL_SWITCH_PATTERNS = (
    re.compile(
        r"^\s*(?:mets?|met)\s*-?\s*toi\s+(?:sur|dans|vers)\s+"
        r"(?:(?:le|la)\s+)?(?:projet|workspace|dossier|repo)\s+(.+?)\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:passe|bascule|switch|ouvre|va)\s+(?:sur|dans|vers)\s+"
        r"(?:(?:le|la)\s+)?(?:projet|workspace|dossier|repo)\s+(.+?)\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*change\s+(?:de\s+)?(?:projet|workspace|dossier|repo)\s+"
        r"(?:(?:vers|sur|dans)\s+)?(.+?)\s*$",
        re.IGNORECASE,
    ),
)
_REST_SPLIT_RE = re.compile(r"\s+(?:et|puis)\s+", re.IGNORECASE)


@dataclass(frozen=True)
class WorkspaceSwitchRequest:
    target: str
    remainder: str = ""


@dataclass(frozen=True)
class ProjectCandidate:
    path: Path
    source: str = ""
    updated_at: str = ""

    @property
    def label(self) -> str:
        return self.path.name or str(self.path)


@dataclass(frozen=True)
class ProjectResolution:
    ok: bool
    target: str
    path: Path | None = None
    error: str = ""
    message: str = ""
    candidates: tuple[ProjectCandidate, ...] = ()


def workspace_switch_from_text(text: str) -> WorkspaceSwitchRequest | None:
    value = str(text or "").strip()
    if not value:
        return None
    slash = _SLASH_SWITCH_RE.match(value)
    if slash:
        target, remainder = _split_target_and_remainder(slash.group(1), split_natural=False)
        return WorkspaceSwitchRequest(target=target, remainder=remainder) if target else None
    for pattern in _NATURAL_SWITCH_PATTERNS:
        match = pattern.match(value)
        if not match:
            continue
        target, remainder = _split_target_and_remainder(match.group(1), split_natural=True)
        return WorkspaceSwitchRequest(target=target, remainder=remainder) if target else None
    return None


def resolve_project_target(
    target: str,
    *,
    session_store_path: Path | None = None,
    settings_path: Path | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
) -> ProjectResolution:
    raw = _clean_target(target)
    if not raw:
        return ProjectResolution(ok=False, target="", error="missing_project", message="Projet manquant.")
    base = (cwd or Path.cwd()).expanduser().resolve(strict=False)
    user_home = (home or Path.home()).expanduser().resolve(strict=False)

    direct = _direct_path_candidate(raw, base)
    if direct is not None:
        if direct.is_dir():
            return ProjectResolution(ok=True, target=raw, path=direct.resolve(strict=False))
        return ProjectResolution(ok=False, target=raw, error="project_not_found", message=f"Projet introuvable: {raw}")

    candidates = known_project_candidates(
        session_store_path=session_store_path,
        settings_path=settings_path,
    )

    exact = _matching_candidates(raw, candidates, exact=True)
    if len(exact) == 1:
        return ProjectResolution(ok=True, target=raw, path=exact[0].path.resolve(strict=False), candidates=exact)
    if len(exact) > 1:
        return _ambiguous_resolution(raw, exact)

    immediate = _immediate_candidates(raw, cwd=base, home=user_home)
    if len(immediate) == 1:
        return ProjectResolution(ok=True, target=raw, path=immediate[0].path.resolve(strict=False), candidates=immediate)
    if len(immediate) > 1:
        return _ambiguous_resolution(raw, immediate)

    fuzzy = _matching_candidates(raw, candidates, exact=False)
    if len(fuzzy) == 1:
        return ProjectResolution(ok=True, target=raw, path=fuzzy[0].path.resolve(strict=False), candidates=fuzzy)
    if len(fuzzy) > 1:
        return _ambiguous_resolution(raw, fuzzy)

    return ProjectResolution(ok=False, target=raw, error="project_not_found", message=f"Projet introuvable: {raw}")


def known_project_candidates(
    *,
    session_store_path: Path | None = None,
    settings_path: Path | None = None,
) -> tuple[ProjectCandidate, ...]:
    candidates: list[ProjectCandidate] = []
    seen: set[str] = set()
    settings = SettingsStore(settings_path or default_settings_path()).load()
    hidden = set(settings.hidden_projects)

    def add(path: str | Path, *, source: str, updated_at: str = "") -> None:
        resolved = Path(path).expanduser().resolve(strict=False)
        key = str(resolved)
        # A registered path is always kept; a hidden one is dropped unless re-registered.
        if key in seen or not resolved.is_dir():
            return
        if key in hidden and source != "registry":
            return
        seen.add(key)
        candidates.append(ProjectCandidate(path=resolved, source=source, updated_at=updated_at))

    for registered in settings.projects:
        add(registered, source="registry")
    if settings.web_project_path:
        add(settings.web_project_path, source="settings")

    store = SessionStore(session_store_path or default_session_store_path())
    try:
        for project in store.projects(limit=200, filter_existing=True):
            add(
                str(project.get("path") or ""),
                source="session",
                updated_at=str(project.get("updated_at") or ""),
            )
    finally:
        store.close()

    return tuple(candidates)


def switch_process_workspace(path: Path | str) -> Path:
    target = Path(path).expanduser().resolve(strict=False)
    if not target.is_dir():
        raise FileNotFoundError(str(target))
    os.chdir(target)
    return target


def workspace_safety_warning(
    path: Path | str | None = None,
    *,
    home: Path | None = None,
) -> str:
    current = Path(path or Path.cwd()).expanduser().resolve(strict=False)
    user_home = (home or Path.home()).expanduser().resolve(strict=False)
    if current == user_home:
        return (
            "Alerte workspace: BB9 est lance depuis le dossier utilisateur. "
            "Demande `mets-toi sur projet <nom>` ou relance depuis un dossier projet."
        )
    if current.parent == current:
        return (
            "Alerte workspace: BB9 est lance depuis la racine du systeme. "
            "Demande `mets-toi sur projet <nom>` ou relance depuis un dossier projet."
        )
    if not _looks_project_like(current) and _is_broad_home_child(current, user_home):
        return (
            "Alerte workspace: ce dossier ne ressemble pas a un projet BB9. "
            "Verifie le workspace avec `/context` ou demande `mets-toi sur projet <nom>`."
        )
    return ""


def _split_target_and_remainder(value: str, *, split_natural: bool) -> tuple[str, str]:
    tail = str(value or "").strip()
    if not tail:
        return "", ""
    if tail[0] in {"'", '"'}:
        quote = tail[0]
        end = tail.find(quote, 1)
        if end > 0:
            target = tail[1:end]
            remainder = _clean_remainder(tail[end + 1 :])
            return _clean_target(target), remainder
    if split_natural:
        match = _REST_SPLIT_RE.search(tail)
        if match:
            return _clean_target(tail[: match.start()]), _clean_remainder(tail[match.end() :])
        semi = tail.find(";")
        if semi >= 0:
            return _clean_target(tail[:semi]), _clean_remainder(tail[semi + 1 :])
    return _clean_target(tail), ""


def _clean_target(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    if text not in {".", ".."}:
        text = text.rstrip(" .,:;")
    return text.strip()


def _clean_remainder(value: str) -> str:
    return str(value or "").strip(" \t\n\r,;:-")


def _direct_path_candidate(raw: str, cwd: Path) -> Path | None:
    if raw.startswith(("~", ".", "/")) or "/" in raw:
        path = Path(raw).expanduser()
        return path if path.is_absolute() else cwd / path
    direct = cwd / raw
    if direct.is_dir():
        return direct
    return None


def _matching_candidates(
    raw: str,
    candidates: tuple[ProjectCandidate, ...],
    *,
    exact: bool,
) -> tuple[ProjectCandidate, ...]:
    wanted = _norm(raw)
    matches: list[ProjectCandidate] = []
    for candidate in candidates:
        name = _norm(candidate.path.name)
        suffix = _norm(_path_suffix(candidate.path))
        full = _norm(str(candidate.path))
        if exact:
            if wanted in {name, suffix, full}:
                matches.append(candidate)
        elif wanted and (wanted in name or wanted in suffix or wanted in full):
            matches.append(candidate)
    return tuple(matches)


def _immediate_candidates(raw: str, *, cwd: Path, home: Path) -> tuple[ProjectCandidate, ...]:
    if "/" in raw:
        return ()
    roots = (cwd, cwd.parent, home, home / "Documents" / "projets", home / "Documents", home / "projets", home / "projects", home / "dev", home / "code")
    seen: set[str] = set()
    candidates: list[ProjectCandidate] = []
    for root in roots:
        if not root.is_dir():
            continue
        path = (root / raw).resolve(strict=False)
        key = str(path)
        if key in seen or not path.is_dir():
            continue
        seen.add(key)
        candidates.append(ProjectCandidate(path=path, source="filesystem"))
    return tuple(candidates)


def _ambiguous_resolution(target: str, candidates: tuple[ProjectCandidate, ...]) -> ProjectResolution:
    labels = ", ".join(str(candidate.path) for candidate in candidates[:5])
    extra = "" if len(candidates) <= 5 else f", +{len(candidates) - 5}"
    return ProjectResolution(
        ok=False,
        target=target,
        error="ambiguous_project",
        message=f"Projet ambigu: {labels}{extra}",
        candidates=candidates,
    )


def _path_suffix(path: Path) -> str:
    name = path.name
    parent = path.parent.name
    return f"{parent}/{name}" if parent else name


def _norm(value: str) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _looks_project_like(path: Path) -> bool:
    return any((path / marker).exists() for marker in PROJECT_MARKERS)


def _is_broad_home_child(path: Path, home: Path) -> bool:
    if path.parent != home:
        return False
    broad_names = {"documents", "downloads", "desktop", "bureau", "telechargements", "téléchargements"}
    return path.name.casefold() in broad_names
