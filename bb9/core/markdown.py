"""Small Markdown helpers for local contracts."""

from __future__ import annotations


def extract_section(markdown: str, heading: str) -> str:
    target = heading.strip().casefold()
    lines = markdown.splitlines()
    start: int | None = None
    level = 0

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        marker, _, title = stripped.partition(" ")
        if not marker or any(ch != "#" for ch in marker):
            continue
        if title.strip().casefold() == target:
            start = index + 1
            level = len(marker)
            break

    if start is None:
        return ""

    body: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("#"):
            marker, _, _ = stripped.partition(" ")
            if marker and all(ch == "#" for ch in marker) and len(marker) <= level:
                break
        body.append(line)
    return "\n".join(body).strip()
