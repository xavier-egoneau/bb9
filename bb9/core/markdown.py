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


def extract_command_lines(markdown: str) -> tuple[str, ...]:
    commands: list[str] = []
    for command in _command_bullet_lines(extract_section(markdown, "Commandes")):
        _append_command(commands, command)
    for command in _repl_command_lines(extract_section(markdown, "Commandes REPL")):
        _append_command(commands, command)
    return tuple(commands)


def command_aliases(commands: tuple[str, ...]) -> tuple[str, ...]:
    aliases: list[str] = []
    for command in commands:
        alias = _first_slash_command(command)
        if alias and alias not in aliases:
            aliases.append(alias)
    return tuple(aliases)


def _append_command(commands: list[str], command: str) -> None:
    if command and command not in commands:
        commands.append(command)


def _command_bullet_lines(section: str) -> tuple[str, ...]:
    commands: list[str] = []
    seen_command = False
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        command = _command_bullet_line(line)
        if command:
            commands.append(command)
            seen_command = True
            continue
        if seen_command:
            break
    return tuple(commands)


def _repl_command_lines(section: str) -> tuple[str, ...]:
    commands: list[str] = []
    seen_command = False
    in_fence = False
    for line in section.splitlines():
        stripped = line.strip()
        if in_fence:
            if stripped.startswith("```"):
                break
            command = _command_fence_line(line)
            if command:
                commands.append(command)
                seen_command = True
            continue

        if not stripped:
            continue
        command = _command_bullet_line(line)
        if command:
            commands.append(command)
            seen_command = True
            continue
        if stripped.startswith("```") and not seen_command:
            in_fence = True
            continue
        if seen_command:
            break
        break
    return tuple(commands)


def _command_bullet_line(line: str) -> str:
    stripped = line.strip()
    if not stripped.startswith(("-", "*")):
        return ""
    value = stripped[1:].strip()
    if not value.startswith(("`/", "/")):
        return ""
    return value


def _command_fence_line(line: str) -> str:
    stripped = line.strip()
    if not stripped.startswith("/"):
        return ""
    return stripped


def _first_slash_command(command: str) -> str:
    text = command.strip()
    if text.startswith("`"):
        text = text[1:]
    if not text.startswith("/"):
        return ""
    alias = text.split(maxsplit=1)[0].strip("`")
    if not alias.startswith("/") or len(alias) <= 1:
        return ""
    return alias
