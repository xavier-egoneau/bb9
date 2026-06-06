"""Persistent guardian approval decisions."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .paths import bb9_home

APPROVALS_FILE = "approvals.json"
ARCHIVE_PARAM_PREFIX = "__bb9_"
SECRET_KEYS = ("token", "secret", "password", "api_key", "key", "credential")
SECRET_TEXT_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|"
    r"github_pat_[A-Za-z0-9_]{12,}|glpat-[A-Za-z0-9_-]{12,}|hf_[A-Za-z0-9]{12,}|"
    r"xox[baprs]-[A-Za-z0-9-]{12,}|"
    r"(?i:api[_-]?key|access[_-]?token|auth[_-]?token|bearer)[=/:%][^&\s]{8,})"
)


@dataclass(frozen=True)
class ApprovalRecord:
    id: str
    created_at: str
    fingerprint: str
    tool_name: str
    params: dict[str, Any]
    workspace: str
    reason: str
    risk: str
    approved: bool
    remembered: bool = False
    decided_at: str = ""


def default_approval_store_path() -> Path:
    return bb9_home() / APPROVALS_FILE


def fingerprint_action(tool_name: str, params: dict[str, Any], workspace: Path | str) -> str:
    payload = {
        "tool": str(tool_name),
        "params": public_action_params(params),
        "workspace": _workspace_key(workspace),
    }
    data = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:24]


def public_action_params(params: dict[str, Any]) -> dict[str, Any]:
    public = {
        str(key): value
        for key, value in params.items()
        if not str(key).startswith(ARCHIVE_PARAM_PREFIX)
    }
    return _sanitize(public)


class ApprovalStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_approval_store_path()
        self._lock = threading.Lock()

    def lookup(self, fingerprint: str) -> bool | None:
        with self._lock:
            records = self._load_raw()
        for item in reversed(records):
            if item.get("fingerprint") == fingerprint and bool(item.get("remembered", False)):
                return bool(item.get("approved", False))
        return None

    def record(
        self,
        *,
        fingerprint: str,
        tool_name: str,
        params: dict[str, Any],
        workspace: Path | str,
        reason: str,
        risk: str,
        approved: bool,
        remembered: bool = False,
    ) -> ApprovalRecord:
        now = datetime.now(UTC).isoformat()
        record = ApprovalRecord(
            id=_record_id(now, fingerprint),
            created_at=now,
            fingerprint=fingerprint,
            tool_name=str(tool_name),
            params=public_action_params(params),
            workspace=_workspace_key(workspace),
            reason=str(reason),
            risk=str(risk),
            approved=bool(approved),
            remembered=bool(remembered),
            decided_at=now if remembered else "",
        )
        with self._lock:
            records = self._load_raw()
            records.append(asdict(record))
            self._save_raw(records)
        return record

    def _load_raw(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return data if isinstance(data, list) else []

    def _save_raw(self, records: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _sanitize(value: Any, *, key: str = "") -> Any:
    if _secret_key(key):
        return "<secret-redacted>"
    if isinstance(value, dict):
        return {str(item_key): _sanitize(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        text = str(value) if isinstance(value, str) else value
        if isinstance(text, str) and SECRET_TEXT_RE.search(text):
            return SECRET_TEXT_RE.sub("<secret-redacted>", text)
        return text
    return str(value)


def _secret_key(key: str) -> bool:
    lowered = str(key).lower()
    return any(secret_key in lowered for secret_key in SECRET_KEYS)


def _workspace_key(workspace: Path | str) -> str:
    return str(Path(workspace).expanduser().resolve(strict=False))


def _record_id(timestamp: str, fingerprint: str) -> str:
    compact = timestamp.replace("-", "").replace(":", "").replace("+00:00", "Z").replace(".", "")
    return f"{compact}_{fingerprint}"
