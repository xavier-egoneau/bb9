"""Agent-scoped notes and todos stored as Markdown in the agent folder.

Each agent owns a private scratch space under its own directory:

- `agents_dir/<agent>/notes/<slug>.md` for free-form notes;
- `agents_dir/<agent>/TODO.md` for a single checkbox todo list.

The store stays deliberately thin and Markdown-first: notes are plain files and
the todo list is a bullet list of `- [ ] task` / `- [x] task` lines, so both are
readable and editable without BB9.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

NOTES_DIRNAME = "notes"
TODO_FILENAME = "TODO.md"
TODO_LINE_RE = re.compile(r"^\s*[-*]\s*\[(?P<mark>[ xX])\]\s?(?P<text>.*)$")
MAX_NOTE_BYTES = 256 * 1024


@dataclass(frozen=True)
class NoteFile:
    slug: str
    title: str
    updated_at: str
    content: str


@dataclass(frozen=True)
class TodoItem:
    index: int
    text: str
    done: bool


def normalize_slug(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    text = folded.strip().lower().replace("_", "-").replace(" ", "-")
    text = re.sub(r"[^a-z0-9-]", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    if not text:
        raise ValueError("empty note name")
    return text


def agent_notes_dir(agents_dir: Path, agent: str) -> Path:
    return Path(agents_dir) / (agent or "default") / NOTES_DIRNAME


def agent_todo_file(agents_dir: Path, agent: str) -> Path:
    return Path(agents_dir) / (agent or "default") / TODO_FILENAME


# --- Notes ---------------------------------------------------------------


def list_notes(agents_dir: Path, agent: str) -> tuple[NoteFile, ...]:
    root = agent_notes_dir(agents_dir, agent)
    if not root.is_dir():
        return ()
    notes = [_read_note_path(path) for path in sorted(root.glob("*.md"))]
    return tuple(note for note in notes if note is not None)


def read_note(agents_dir: Path, agent: str, slug: str) -> NoteFile | None:
    path = agent_notes_dir(agents_dir, agent) / f"{normalize_slug(slug)}.md"
    return _read_note_path(path)


def write_note(agents_dir: Path, agent: str, slug: str, content: str, *, title: str = "") -> NoteFile:
    normalized = normalize_slug(slug)
    body = content if content is not None else ""
    if len(body.encode("utf-8")) > MAX_NOTE_BYTES:
        raise ValueError("note too large")
    heading = title.strip()
    text = body
    if heading and not body.lstrip().startswith("#"):
        text = f"# {heading}\n\n{body}"
    root = agent_notes_dir(agents_dir, agent)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{normalized}.md"
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    note = _read_note_path(path)
    assert note is not None
    return note


def delete_note(agents_dir: Path, agent: str, slug: str) -> bool:
    path = agent_notes_dir(agents_dir, agent) / f"{normalize_slug(slug)}.md"
    if not path.is_file():
        return False
    path.unlink()
    return True


def _read_note_path(path: Path) -> NoteFile | None:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        updated_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()
    except OSError:
        updated_at = ""
    return NoteFile(slug=path.stem, title=_note_title(content, path.stem), updated_at=updated_at, content=content)


def _note_title(content: str, fallback: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or fallback
        if stripped:
            return stripped[:80]
    return fallback


# --- Todos ---------------------------------------------------------------


def read_todos(agents_dir: Path, agent: str) -> tuple[TodoItem, ...]:
    path = agent_todo_file(agents_dir, agent)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ()
    items: list[TodoItem] = []
    for line in text.splitlines():
        match = TODO_LINE_RE.match(line)
        if match is None:
            continue
        items.append(
            TodoItem(
                index=len(items),
                text=match.group("text").strip(),
                done=match.group("mark").lower() == "x",
            )
        )
    return tuple(items)


def add_todo(agents_dir: Path, agent: str, text: str) -> tuple[TodoItem, ...]:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        raise ValueError("empty todo text")
    items = list(read_todos(agents_dir, agent))
    items.append(TodoItem(index=len(items), text=cleaned, done=False))
    return _write_todos(agents_dir, agent, items)


def set_todo_done(agents_dir: Path, agent: str, index: int, done: bool) -> tuple[TodoItem, ...]:
    items = list(read_todos(agents_dir, agent))
    if index < 0 or index >= len(items):
        raise IndexError("todo index out of range")
    items[index] = TodoItem(index=index, text=items[index].text, done=bool(done))
    return _write_todos(agents_dir, agent, items)


def edit_todo(agents_dir: Path, agent: str, index: int, text: str) -> tuple[TodoItem, ...]:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        raise ValueError("empty todo text")
    items = list(read_todos(agents_dir, agent))
    if index < 0 or index >= len(items):
        raise IndexError("todo index out of range")
    items[index] = TodoItem(index=index, text=cleaned, done=items[index].done)
    return _write_todos(agents_dir, agent, items)


def remove_todo(agents_dir: Path, agent: str, index: int) -> tuple[TodoItem, ...]:
    items = list(read_todos(agents_dir, agent))
    if index < 0 or index >= len(items):
        raise IndexError("todo index out of range")
    del items[index]
    return _write_todos(agents_dir, agent, items)


def _write_todos(agents_dir: Path, agent: str, items: list[TodoItem]) -> tuple[TodoItem, ...]:
    path = agent_todo_file(agents_dir, agent)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Todo", ""]
    lines.extend(f"- [{'x' if item.done else ' '}] {item.text}" for item in items)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tuple(TodoItem(index=i, text=item.text, done=item.done) for i, item in enumerate(items))


# --- Context -------------------------------------------------------------


def build_agent_notes_context(agents_dir: Path, agent: str, *, max_notes: int = 20) -> str:
    """Compact prompt block so the agent knows its own notes and todos exist."""
    todos = read_todos(agents_dir, agent)
    notes = list_notes(agents_dir, agent)
    if not todos and not notes:
        return ""
    lines = ["# Notes & Todos de l'agent"]
    open_todos = [item for item in todos if not item.done]
    done_count = len(todos) - len(open_todos)
    if todos:
        lines.append("")
        lines.append(f"## Todo ({len(open_todos)} ouverte(s), {done_count} faite(s))")
        for item in open_todos[:30]:
            lines.append(f"- [ ] {item.text}")
        if done_count:
            lines.append(f"- ({done_count} tâche(s) terminée(s) masquée(s))")
    if notes:
        lines.append("")
        lines.append(f"## Notes ({len(notes)})")
        for note in notes[:max_notes]:
            lines.append(f"- `{note.slug}` : {note.title}")
    lines.append("")
    lines.append(
        "Ces notes et todos vivent dans le dossier de l'agent. "
        "Utilise le tool `notes` pour les lire, créer, modifier ou cocher."
    )
    return "\n".join(lines).strip()
