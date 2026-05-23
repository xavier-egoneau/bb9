"""Agentic execution trace helpers."""

from __future__ import annotations

from typing import Any

from .models import TraceEvent, TraceType


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
