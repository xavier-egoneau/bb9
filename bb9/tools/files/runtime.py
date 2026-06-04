"""Bounded workspace file editing tool."""

from __future__ import annotations

import base64
import json
import shlex
from pathlib import Path
from typing import Any

from bb9.core.models import Action, GuardianDecision, Observation, RunContext
from bb9.core.trust import TrustedRoots, classify_path
from bb9.core.utils import truthy as _truthy

OPS = {"write", "write_many", "replace", "insert_before", "insert_after"}


def action_from_text(text: str) -> Action:
    raw = text.strip()
    json_params = _parse_json_action(raw)
    if json_params is not None:
        return _action_from_params(json_params)
    op, _, rest = raw.partition(" ")
    if op.lower() == "write_many":
        params = {"op": "write_many", "items": _parse_write_many_items(rest)}
        return _action_from_params(params)
    pre_capture = _first_capture_key(rest) if op.lower() == "write" else None
    if pre_capture is not None and pre_capture[0] in {"text", "content", "contents", "body", "b64"}:
        params = _parse_params_relaxed(rest)
        _normalize_text_aliases(params)
        params["op"] = op.lower()
        return _action_from_params(params)
    try:
        argv = shlex.split(raw)
        op = argv[0].lower() if argv else ""
        params = _parse_params(argv[1:])
    except ValueError as exc:
        params = _parse_params_relaxed(rest)
        params["parse_warning"] = str(exc)
    op = op.lower() if op else ""
    _normalize_text_aliases(params)
    params["op"] = op
    return _action_from_params(params)


def _action_from_params(params: dict[str, Any]) -> Action:
    op = str(params.get("op") or "").strip().lower()
    params["op"] = op
    if op == "write_many":
        if not _valid_write_many_items(params.get("items")):
            return Action(name="files", params=params, risk="forbidden")
        return Action(name="files", params=params, risk="medium")
    if op not in OPS or not str(params.get("path") or "").strip():
        return Action(name="files", params=params, risk="forbidden")
    return Action(name="files", params=params, risk="medium")


def review(action: Action, context: RunContext) -> GuardianDecision:
    op = str(action.params.get("op", "")).strip().lower()
    if op not in OPS:
        return GuardianDecision(verdict="block", reason="invalid files action", action=action)
    targets = _target_paths(action, context.workspace.root)
    if not targets:
        return GuardianDecision(verdict="block", reason="invalid files action", action=action)
    trusted_roots = context.trusted_roots or TrustedRoots()
    for target in targets:
        zone = classify_path(target, context.workspace.root, trusted_roots)
        if zone == "protected":
            return GuardianDecision(verdict="block", reason=f"protected path: {target}", action=action)
        if zone == "outside":
            return GuardianDecision(verdict="ask", reason=f"path outside workspace/trusted roots: {target}", action=action)
    if context.permission_profile in {"limited", "power"}:
        return GuardianDecision(verdict="allow", reason=f"workspace file edit allowed by {context.permission_profile} profile", action=action)
    return GuardianDecision(verdict="ask", reason="file edit requires confirmation in safe profile", action=action)


def execute(action: Action, context: RunContext | None = None) -> Observation:
    op = str(action.params.get("op", "")).strip().lower()
    workspace = context.workspace.root if context is not None else Path.cwd()
    path = _target_path(action, workspace)
    try:
        if op == "write":
            return _write(path, _text_param(action, "text"))
        if op == "write_many":
            return _write_many(action, workspace)
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
    return Observation(
        ok=True,
        summary=f"File written: {_display_path(path)}",
        data={"path": str(path), "op": "write"},
    )


def _write_many(action: Action, workspace: Path) -> Observation:
    items = action.params.get("items")
    if not _valid_write_many_items(items):
        return Observation(ok=False, summary="write_many requires items with path and text/content", data={"op": "write_many"})
    written: list[str] = []
    for item in items:
        assert isinstance(item, dict)
        path = _path_from_raw(str(item.get("path") or ""), workspace)
        text = _item_text(item)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(_display_path(path))
    return Observation(
        ok=True,
        summary=f"Files written: {', '.join(written)}",
        data={"paths": written, "op": "write_many"},
    )


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
    return Observation(
        ok=True,
        summary=f"File updated: {_display_path(path)} ({op})",
        data={"path": str(path), "op": op},
    )


def _target_path(action: Action, workspace: Path) -> Path:
    raw = str(action.params.get("path") or "").strip()
    return _path_from_raw(raw, workspace)


def _target_paths(action: Action, workspace: Path) -> list[Path]:
    if str(action.params.get("op") or "").strip().lower() != "write_many":
        raw = str(action.params.get("path") or "").strip()
        return [_path_from_raw(raw, workspace)] if raw else []
    items = action.params.get("items")
    if not _valid_write_many_items(items):
        return []
    return [_path_from_raw(str(item.get("path") or ""), workspace) for item in items if isinstance(item, dict)]


def _path_from_raw(raw: str, workspace: Path) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = workspace / path
    return path


def _parse_write_many_items(text: str) -> Any:
    raw = text.strip()
    for key in ("items=", "files="):
        if raw.startswith(key):
            raw = raw[len(key) :].strip()
            break
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _parse_json_action(text: str) -> dict[str, Any] | None:
    raw = _strip_json_markup(text)
    if not raw.startswith(("{", "[")):
        return None
    try:
        payload, _ = json.JSONDecoder().raw_decode(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, list):
        return {"op": "write_many", "items": payload}
    if not isinstance(payload, dict):
        return None
    params = {str(key).strip().lower().replace("-", "_"): value for key, value in payload.items()}
    if "ops" in params or "operations" in params:
        return _parse_json_write_ops(params.get("ops", params.get("operations")))
    if not params.get("op") and ("items" in params or "files" in params):
        params["op"] = "write_many"
    if str(params.get("op") or "").strip().lower() == "write_many" and "items" not in params:
        params["items"] = params.get("files")
    _normalize_text_aliases(params)
    return params


def _strip_json_markup(text: str) -> str:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.removeprefix("```").strip()
        if raw.startswith(("json", "text", "bb9")):
            _, _, raw = raw.partition("\n")
            raw = raw.strip()
        if raw.endswith("```"):
            raw = raw[:-3].strip()
    if raw.lower().startswith("json\n"):
        _, _, raw = raw.partition("\n")
        raw = raw.strip()
    return raw


def _parse_json_write_ops(ops: Any) -> dict[str, Any]:
    if not isinstance(ops, list):
        return {"op": "write_many", "items": None}
    items: list[dict[str, Any]] = []
    for item in ops:
        if not isinstance(item, dict):
            return {"op": "write_many", "items": None}
        op = str(item.get("op") or "write").strip().lower()
        if op != "write":
            return {"op": "write_many", "items": None}
        normalized = {str(key).strip().lower().replace("-", "_"): value for key, value in item.items()}
        normalized.pop("op", None)
        _normalize_text_aliases(normalized)
        items.append(normalized)
    return {"op": "write_many", "items": items}


def _valid_write_many_items(items: Any) -> bool:
    if not isinstance(items, list) or not items:
        return False
    for item in items:
        if not isinstance(item, dict):
            return False
        if not str(item.get("path") or "").strip():
            return False
        if not _item_text(item):
            return False
    return True


def _item_text(item: dict[str, Any]) -> str:
    for key in ("text", "content", "contents", "body"):
        if key in item:
            return str(item.get(key) or "")
    if item.get("b64"):
        try:
            return base64.b64decode(str(item.get("b64")), validate=True).decode("utf-8")
        except Exception:
            return ""
    return ""


def _parse_params(parts: list[str]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for part in parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        params[key.strip().lower().replace("-", "_")] = value
    return params


def _parse_params_relaxed(text: str) -> dict[str, Any]:
    params: dict[str, Any] = {}
    capture = _first_capture_key(text)
    prefix = text
    if capture is not None:
        key, start = capture
        prefix = text[:start].strip()
        value = text[start + len(key) + 1 :].strip()
        params[key] = _strip_wrapping_quotes(value)
    if prefix:
        try:
            params.update(_parse_params(shlex.split(prefix)))
        except ValueError:
            params.update(_parse_simple_prefix(prefix))
    return params


def _first_capture_key(text: str) -> tuple[str, int] | None:
    candidates: list[tuple[int, str]] = []
    for key in ("text", "content", "contents", "body", "new", "old", "marker", "b64"):
        for needle in (f" {key}=", f"\n{key}=", f"\t{key}="):
            index = text.find(needle)
            if index >= 0:
                candidates.append((index + 1, key))
        if text.startswith(f"{key}="):
            candidates.append((0, key))
    if not candidates:
        return None
    index, key = min(candidates, key=lambda item: item[0])
    return key, index


def _parse_simple_prefix(text: str) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for part in text.split():
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        params[key.strip().lower().replace("-", "_")] = _strip_wrapping_quotes(value.strip())
    return params


def _strip_wrapping_quotes(value: str) -> str:
    text = value.strip()
    for quote in ('"""', "'''"):
        if text.startswith(quote):
            text = text[len(quote) :]
            end = text.find(quote)
            return text[:end] if end >= 0 else text
    if text.startswith(('"', "'")):
        parsed = _quoted_prefix(text)
        if parsed is not None:
            return parsed
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1]
    return text


def _quoted_prefix(text: str) -> str | None:
    quote = text[0]
    line_end = text.find("\n")
    search_end = len(text) if line_end < 0 else line_end
    end = _last_unescaped_quote(text, quote, start=1, end=search_end)
    if end is None:
        return None
    return _unescape_quoted(text[1:end])


def _last_unescaped_quote(text: str, quote: str, *, start: int, end: int) -> int | None:
    escaped = False
    result: int | None = None
    for index in range(start, end):
        char = text[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == quote:
            result = index
    return result


def _unescape_quoted(text: str) -> str:
    chars: list[str] = []
    escaped = False
    for char in text:
        if escaped:
            chars.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        chars.append(char)
    if escaped:
        chars.append("\\")
    return "".join(chars)


def _normalize_text_aliases(params: dict[str, Any]) -> None:
    for alias in ("content", "contents", "body"):
        if "text" not in params and alias in params:
            params["text"] = params[alias]


def _normalized_addition(text: str) -> str:
    if not text:
        return ""
    prefix = "" if text.startswith("\n") else "\n"
    suffix = "" if text.endswith("\n") else "\n"
    return prefix + text + suffix


def _text_param(action: Action, name: str) -> str:
    if name == "text" and action.params.get("b64"):
        try:
            return base64.b64decode(str(action.params.get("b64")), validate=True).decode("utf-8")
        except Exception:
            return ""
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
