"""Generic Markdown archive discovery and loading."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class MarkdownArchive:
    name: str
    kind_file: str
    root: Path
    path: Path
    body: str
    metadata: dict[str, str] = field(default_factory=dict)


def discover_archives(root: Path, kind_file: str) -> list[str]:
    return discover_archives_any(root, (kind_file,))


def discover_archives_any(root: Path, kind_files: tuple[str, ...]) -> list[str]:
    if not root.exists():
        return []
    names: list[str] = []
    for item in sorted(root.iterdir()):
        if item.is_dir() and _valid_archive_name(item.name) and any(
            (item / kind_file).is_file()
            for kind_file in kind_files
        ):
            names.append(item.name)
    return names


def load_archive(root: Path, name: str, kind_file: str) -> MarkdownArchive:
    if not _valid_archive_name(name):
        raise ArchiveNotFoundError(f"Invalid archive name: {name}")
    path = root / name / kind_file
    if not path.is_file():
        raise ArchiveNotFoundError(f"Archive not found: {name}")
    raw = path.read_text(encoding="utf-8")
    metadata, body = parse_frontmatter(raw)
    return MarkdownArchive(
        name=name,
        kind_file=kind_file,
        root=root,
        path=path,
        body=body,
        metadata=metadata,
    )


def load_enabled_archives(
    root: Path,
    kind_file: str,
    disabled: tuple[str, ...] = (),
) -> tuple[MarkdownArchive, ...]:
    disabled_set = set(disabled)
    archives: list[MarkdownArchive] = []
    for name in discover_archives(root, kind_file):
        if name in disabled_set:
            continue
        archives.append(load_archive(root, name, kind_file))
    return tuple(archives)


def read_archive_text(root: Path, name: str, filename: str) -> str:
    if not _valid_archive_name(name):
        return ""
    return read_optional_text(root / name / filename)


def read_optional_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    lines = text.splitlines(keepends=True)
    end_index = None
    for index, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end_index = index
            break
    if end_index is None:
        return {}, text

    metadata: dict[str, str] = {}
    for line in lines[1:end_index]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        if not key:
            continue
        metadata[key] = _unquote(value.strip())
    return metadata, "".join(lines[end_index + 1 :]).lstrip("\n")


def parse_markdown_name_list(text: str) -> tuple[str, ...]:
    names: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(("-", "*")):
            continue
        value = stripped[1:].strip()
        if not value:
            continue
        name = value.split()[0].strip("`")
        if name and _valid_archive_name(name):
            names.append(name)
    return tuple(names)


def valid_archive_name(name: str) -> bool:
    return _valid_archive_name(name)


class ArchiveNotFoundError(RuntimeError):
    pass


def _valid_archive_name(name: str) -> bool:
    return bool(name) and all(char.isalnum() or char in {"-", "_"} for char in name)


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value
