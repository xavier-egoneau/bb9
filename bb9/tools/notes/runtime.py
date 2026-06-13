"""Standalone notes & todos tool runtime.

Lets the active agent read and manage its own Markdown notes and a single todo
list, stored under the agent folder (`agents_dir/<agent>/`). The agent does not
need workspace write access for this: the tool resolves the agent directory from
the run context.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from bb9.core import notes as notes_store
from bb9.core.models import Action, GuardianDecision, Observation, RunContext
from bb9.core.paths import default_agents_dir

READ_OPS = {"list", "read"}
WRITE_OPS = {"write", "delete", "todo-add", "todo-done", "todo-undone", "todo-edit", "todo-remove"}

USAGE = (
    "notes <op> ... — op: list | read <slug> | write <slug> text=\"...\" [title=\"...\"] | delete <slug> | "
    "todo-add <texte> | todo-done <n> | todo-undone <n> | todo-edit <n> text=\"...\" | todo-remove <n>. "
    "Les notes et la liste todo vivent dans le dossier de l'agent. "
    "`n` est l'indice (à partir de 0) affiché par `notes list`."
)


def usage() -> str:
    return USAGE


def action_from_text(text: str) -> Action:
    try:
        argv = shlex.split(text.strip())
    except ValueError:
        return Action(name="notes", params={"op": "invalid", "raw": text}, risk="forbidden")
    op = argv[0].lower() if argv else "list"
    positional, options = _split_args(argv[1:])
    params: dict[str, object] = {"op": op, **options}

    if op == "list":
        return Action(name="notes", params={"op": "list"}, risk="low")
    if op == "read":
        if not positional:
            return Action(name="notes", params={"op": "invalid", "raw": text}, risk="forbidden")
        return Action(name="notes", params={"op": "read", "slug": positional[0]}, risk="low")
    if op == "write":
        if not positional:
            return Action(name="notes", params={"op": "invalid", "raw": text}, risk="forbidden")
        params["slug"] = positional[0]
        params.setdefault("text", " ".join(positional[1:]))
        return Action(name="notes", params=params, risk="medium")
    if op == "delete":
        if not positional:
            return Action(name="notes", params={"op": "invalid", "raw": text}, risk="forbidden")
        return Action(name="notes", params={"op": "delete", "slug": positional[0]}, risk="medium")
    if op == "todo-add":
        params["text"] = options.get("text") or " ".join(positional)
        return Action(name="notes", params=params, risk="medium")
    if op in {"todo-done", "todo-undone", "todo-remove"}:
        index = _as_int(positional[0]) if positional else None
        if index is None:
            return Action(name="notes", params={"op": "invalid", "raw": text}, risk="forbidden")
        return Action(name="notes", params={"op": op, "index": index}, risk="medium")
    if op == "todo-edit":
        index = _as_int(positional[0]) if positional else None
        if index is None:
            return Action(name="notes", params={"op": "invalid", "raw": text}, risk="forbidden")
        params["op"] = "todo-edit"
        params["index"] = index
        params.setdefault("text", " ".join(positional[1:]))
        return Action(name="notes", params=params, risk="medium")
    return Action(name="notes", params={"op": "invalid", "raw": text}, risk="forbidden")


def review(action: Action, context: RunContext) -> GuardianDecision:
    op = str(action.params.get("op", "")).strip().lower()
    if op in READ_OPS:
        return GuardianDecision(verdict="allow", reason="reading agent notes is allowed", action=action)
    if op in WRITE_OPS:
        if context.permission_profile == "safe":
            return GuardianDecision(verdict="ask", reason="note write requires confirmation in safe profile", action=action)
        return GuardianDecision(verdict="allow", reason=f"agent notes write allowed by {context.permission_profile} profile", action=action)
    return GuardianDecision(verdict="block", reason="invalid notes action", action=action)


def execute(action: Action, context: RunContext | None = None) -> Observation:
    op = str(action.params.get("op", "")).strip().lower()
    agents_dir, agent = _resolve_agent(context)
    try:
        if op == "list":
            return _observe_state(agents_dir, agent, "Notes & todos de l'agent.")
        if op == "read":
            note = notes_store.read_note(agents_dir, agent, str(action.params.get("slug", "")))
            if note is None:
                return Observation(ok=False, summary="Note introuvable.", retry_policy="block_exact")
            return Observation(ok=True, summary=note.content, data={"slug": note.slug, "title": note.title})
        if op == "write":
            note = notes_store.write_note(
                agents_dir,
                agent,
                str(action.params.get("slug", "")),
                str(action.params.get("text", "")),
                title=str(action.params.get("title", "")),
            )
            return _observe_state(agents_dir, agent, f"Note enregistrée : `{note.slug}`.")
        if op == "delete":
            removed = notes_store.delete_note(agents_dir, agent, str(action.params.get("slug", "")))
            if not removed:
                return Observation(ok=False, summary="Note introuvable.", retry_policy="block_exact")
            return _observe_state(agents_dir, agent, "Note supprimée.")
        if op == "todo-add":
            notes_store.add_todo(agents_dir, agent, str(action.params.get("text", "")))
            return _observe_state(agents_dir, agent, "Tâche ajoutée.")
        if op in {"todo-done", "todo-undone"}:
            notes_store.set_todo_done(agents_dir, agent, int(action.params.get("index", -1)), op == "todo-done")
            return _observe_state(agents_dir, agent, "Tâche mise à jour.")
        if op == "todo-edit":
            notes_store.edit_todo(agents_dir, agent, int(action.params.get("index", -1)), str(action.params.get("text", "")))
            return _observe_state(agents_dir, agent, "Tâche modifiée.")
        if op == "todo-remove":
            notes_store.remove_todo(agents_dir, agent, int(action.params.get("index", -1)))
            return _observe_state(agents_dir, agent, "Tâche supprimée.")
    except (ValueError, IndexError) as exc:
        return Observation(ok=False, summary=f"Opération notes invalide : {exc}", retry_policy="block_exact")
    return Observation(ok=False, summary="Invalid notes tool operation.")


def _observe_state(agents_dir: Path, agent: str, summary: str) -> Observation:
    todos = notes_store.read_todos(agents_dir, agent)
    notes = notes_store.list_notes(agents_dir, agent)
    lines = [summary, ""]
    open_todos = [item for item in todos if not item.done]
    if todos:
        lines.append(f"Todo : {len(open_todos)} ouverte(s) / {len(todos)} total.")
        for item in todos:
            lines.append(f"  [{item.index}] {'x' if item.done else ' '} {item.text}")
    if notes:
        lines.append(f"Notes : {len(notes)}.")
        for note in notes:
            lines.append(f"  - {note.slug} : {note.title}")
    return Observation(
        ok=True,
        summary="\n".join(lines).strip(),
        data={
            "todos": [{"index": item.index, "text": item.text, "done": item.done} for item in todos],
            "notes": [{"slug": note.slug, "title": note.title} for note in notes],
        },
    )


def _resolve_agent(context: RunContext | None) -> tuple[Path, str]:
    if context is not None:
        agents_dir = context.agents_dir or default_agents_dir()
        agent = context.agent.name if context.agent is not None else "default"
        return Path(agents_dir), agent or "default"
    return default_agents_dir(), "default"


def _split_args(argv: list[str]) -> tuple[list[str], dict[str, str]]:
    positional: list[str] = []
    options: dict[str, str] = {}
    for token in argv:
        if "=" in token and not token.startswith("="):
            key, value = token.split("=", 1)
            options[key.strip().replace("-", "_")] = value.strip()
        else:
            positional.append(token)
    return positional, options


def _as_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
