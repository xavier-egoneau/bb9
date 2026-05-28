"""Agentic execution trace helpers."""

from __future__ import annotations

from typing import Any

from .models import Artifact, TraceEvent, TraceType
from .sessions import redact_session_text


class Trace:
    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        self._events: list[TraceEvent] = []

    @property
    def events(self) -> tuple[TraceEvent, ...]:
        return tuple(self._events)

    def add(
        self,
        event_type: TraceType,
        summary: str,
        data: dict[str, Any] | None = None,
    ) -> TraceEvent:
        event = TraceEvent(
            event_type=event_type,
            summary=summary,
            session_id=self._session_id,
            data=data or {},
        )
        self._events.append(event)
        return event


def tool_trace_artifact(events: tuple[TraceEvent, ...]) -> Artifact | None:
    entries: list[dict[str, object]] = []
    pending: dict[str, object] = {}
    for event in events:
        if event.event_type == "action":
            tool = str(event.data.get("tool") or event.summary or "").strip()
            pending = {"tool": tool}
            cmd = redact_session_text(str(event.data.get("cmd") or "").strip())
            if cmd:
                pending["cmd"] = cmd
            continue
        if event.event_type != "observation" or not pending:
            continue
        entries.append(
            {
                **pending,
                "ok": bool(event.data.get("ok", False)),
                "summary": redact_session_text(event.summary.strip()),
            }
        )
        pending = {}
    if not entries:
        return None
    failures = sum(1 for entry in entries if not entry.get("ok"))
    return Artifact(
        kind="tool_trace",
        title=_tool_trace_title(len(entries), failures),
        source="loop",
        metadata={
            "entries": entries,
            "count": len(entries),
            "failures": failures,
            "default_collapsed": True,
        },
    )


def decision_trace_artifact(events: tuple[TraceEvent, ...]) -> Artifact | None:
    entries: list[dict[str, object]] = []
    for event in events:
        if event.event_type not in {"decision", "guardian", "action", "observation", "stop"}:
            continue
        entries.append(
            {
                "type": event.event_type,
                "summary": redact_session_text(event.summary.strip()),
                "data": _redacted_data(event.data),
                "time": event.time,
            }
        )
    if not entries:
        return None
    return Artifact(
        kind="report",
        title="Trace de décision",
        source="loop",
        metadata={
            "entries": entries,
            "count": len(entries),
            "default_hidden": True,
            "note": "Trace observable sans raisonnement privé du modèle.",
        },
    )


def _tool_trace_title(count: int, failures: int) -> str:
    suffix = "outil utilisé" if count == 1 else "outils utilisés"
    if failures:
        return f"{count} {suffix}, {failures} échec(s)"
    return f"{count} {suffix}"


def _redacted_data(data: dict[str, Any]) -> dict[str, object]:
    redacted: dict[str, object] = {}
    for key, value in data.items():
        if isinstance(value, str):
            redacted[key] = redact_session_text(value)
        elif isinstance(value, bool | int | float) or value is None:
            redacted[key] = value
        else:
            redacted[key] = redact_session_text(str(value))
    return redacted
