"""Agentic execution trace helpers."""

from __future__ import annotations

from typing import Any

from .models import Artifact, TraceEvent, TraceType


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
    ) -> None:
        self._events.append(
            TraceEvent(
                event_type=event_type,
                summary=summary,
                session_id=self._session_id,
                data=data or {},
            )
        )


def tool_trace_artifact(events: tuple[TraceEvent, ...]) -> Artifact | None:
    entries: list[dict[str, object]] = []
    pending_tool = ""
    for event in events:
        if event.event_type == "action":
            pending_tool = str(event.data.get("tool") or event.summary or "").strip()
            continue
        if event.event_type != "observation" or not pending_tool:
            continue
        entries.append(
            {
                "tool": pending_tool,
                "ok": bool(event.data.get("ok", False)),
                "summary": event.summary.strip(),
            }
        )
        pending_tool = ""
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


def _tool_trace_title(count: int, failures: int) -> str:
    suffix = "outil utilisé" if count == 1 else "outils utilisés"
    if failures:
        return f"{count} {suffix}, {failures} échec(s)"
    return f"{count} {suffix}"
