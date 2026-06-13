"""Shared utilities extracted from duplicated implementations."""

from __future__ import annotations


def workspace_status_summary(text: str) -> tuple[str, ...]:
    wanted = ("Git:", "Package manager:", "Scripts:", "Read state:")
    result: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip().removeprefix("- ").strip()
        if any(line.startswith(prefix) for prefix in wanted):
            result.append(line)
    return tuple(result[:4])


def positive_int(value: object, default: int, *, max_value: int | None = None) -> int:
    try:
        parsed = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    if max_value is not None and parsed > max_value:
        return max_value
    return parsed


def truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "oui", "o"}
