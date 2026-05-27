"""Bounded workspace file editing tool."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from bb9.core.models import Action, GuardianDecision, Observation, RunContext
from bb9.core.trust import TrustedRoots, classify_path

OPS = {"write", "replace", "insert_before", "insert_after"}


def action_from_text(text: str) -> Action:
    try:
        argv = shlex.split(text.strip())
    except ValueError as exc:
        return Action(name="files", params={"op": "invalid", "error": str(exc), "raw": text}, risk="forbidden")
    op = argv[0].lower() if argv else ""
    params = _parse_params(argv[1:])
    params["op"] = op
    if op not in OPS or not str(params.get("path") or "").strip():
        return Action(name="files", params=params, risk="forbidden")
    return Action(name="files", params=params, risk="medium")


def review(action: Action, context: RunContext) -> GuardianDecision:
    op = str(action.params.get("op", "")).strip().lower()
    if op not in OPS:
        return GuardianDecision(verdict="block", reason="invalid files action", action=action)
    target = _target_path(action, context.workspace.root)
    trusted_roots = context.trusted_roots or TrustedRoots()
    zone = classify_path(target, context.workspace.root, trusted_roots)
    if zone == "protected":
        return GuardianDecision(verdict="block", reason=f"protected path: {target}", action=action)
    if zone == "outside":
        return GuardianDecision(verdict="ask", reason=f"path outside workspace/trusted roots: {target}", action=action)
    if context.permission_profile in {"limited", "power"}:
        return GuardianDecision(verdict="allow", reason=f"workspace file edit allowed by {context.permission_profile} profile", action=action)
    return GuardianDecision(verdict="ask", reason="file edit requires confirmation in safe profile", action=action)


def execute(action: Action) -> Observation:
    op = str(action.params.get("op", "")).strip().lower()
    path = _target_path(action, Path.cwd())
    try:
        if op == "write":
            return _write(path, _text_param(action, "text"))
        if op == "replace":
            return _replace(
                path,
                old=_text_param(action, "old"),
                new=_text_param(action, "new"),
                replace_all=_truthy(action.params.get("all")),
            )
        if op == "insert_before":
            return _insert(path, marker=_text_param(action, "marker"), text=_text_param(action, "text"), before=True)
        if op == "insert_after":
            return _insert(path, marker=_text_param(action, "marker"), text=_text_param(action, "text"), before=False)
    except OSError as exc:
        return Observation(ok=False, summary=f"file edit failed: {exc}", data={"path": str(path)})
    return Observation(ok=False, summary="Invalid files tool operation.")


def _write(path: Path, text: str) -> Observation:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return Observation(ok=True, summary=f"File written: {_display_path(path)}", data={"path": str(path), "op": "write"})


def _replace(path: Path, *, old: str, new: str, replace_all: bool) -> Observation:
    if not old:
        return Observation(ok=False, summary="replace requires old text", data={"path": str(path)})
    content = path.read_text(encoding="utf-8")
    if old not in content:
        return Observation(ok=False, summary=f"replace text not found: {_display_path(path)}", data={"path": str(path)})
    count = content.count(old) if replace_all else 1
    updated = content.replace(old, new, -1 if replace_all else 1)
    path.write_text(updated, encoding="utf-8")
    return Observation(
        ok=True,
        summary=f"File updated: {_display_path(path)} ({count} replacement{'s' if count != 1 else ''})",
        data={"path": str(path), "op": "replace", "count": count},
    )


def _insert(path: Path, *, marker: str, text: str, before: bool) -> Observation:
    if not marker:
        return Observation(ok=False, summary="insert requires marker text", data={"path": str(path)})
    content = path.read_text(encoding="utf-8")
    index = content.find(marker)
    if index < 0:
        return Observation(ok=False, summary=f"insert marker not found: {_display_path(path)}", data={"path": str(path)})
    insert_at = index if before else index + len(marker)
    addition = _normalized_addition(text)
    updated = content[:insert_at] + addition + content[insert_at:]
    path.write_text(updated, encoding="utf-8")
    op = "insert_before" if before else "insert_after"
    return Observation(ok=True, summary=f"File updated: {_display_path(path)} ({op})", data={"path": str(path), "op": op})


def _target_path(action: Action, workspace: Path) -> Path:
    raw = str(action.params.get("path") or "").strip()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = workspace / path
    return path


def _parse_params(parts: list[str]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for part in parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        params[key.strip().lower().replace("-", "_")] = value
    return params


def _normalized_addition(text: str) -> str:
    if not text:
        return ""
    prefix = "" if text.startswith("\n") else "\n"
    suffix = "" if text.endswith("\n") else "\n"
    return prefix + text + suffix


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "oui", "o"}


def _text_param(action: Action, name: str) -> str:
    text = str(action.params.get(name) or "")
    return (
        text.replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\'", "'")
    )


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)
