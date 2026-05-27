"""Standalone secret tool runtime."""

from __future__ import annotations

from bb9.core.models import Action, GuardianDecision, Observation, RunContext

from .store import SecretStore, normalize_secret_name, secret_ref


def action_from_text(text: str) -> Action:
    parts = text.strip().split(maxsplit=1)
    op = parts[0].lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""
    if _looks_like_placeholder_secret(rest):
        return Action(name="secret", params={"op": "invalid", "raw": text}, risk="forbidden")
    if op in {"add", "set"} and rest:
        return Action(name="secret", params={"op": "set", "name": normalize_secret_name(rest)}, risk="high")
    if op == "list":
        return Action(name="secret", params={"op": "list"}, risk="low")
    return Action(name="secret", params={"op": "invalid", "raw": text}, risk="forbidden")


def review(action: Action, _: RunContext) -> GuardianDecision:
    op = str(action.params.get("op", "")).strip().lower()
    if op == "set":
        return GuardianDecision(verdict="ask", reason="secret write requires confirmation", action=action)
    if op == "list":
        return GuardianDecision(verdict="allow", reason="secret names listing is allowed", action=action)
    return GuardianDecision(verdict="block", reason="invalid secret action", action=action)


def execute(action: Action) -> Observation:
    op = str(action.params.get("op", "")).strip().lower()
    store = SecretStore()
    if op == "list":
        names = store.list_names()
        summary = "\n".join(secret_ref(name) for name in names) if names else "No named secrets."
        return Observation(ok=True, summary=summary, data={"names": names})
    if op == "set":
        name = normalize_secret_name(str(action.params.get("name", "")))
        value = str(action.params.get("value", ""))
        if not value:
            return Observation(ok=False, summary=f"Secret value missing for {secret_ref(name)}")
        stored = store.set(name, value)
        return Observation(ok=True, summary=f"Secret stored: {secret_ref(stored)}", data={"ref": secret_ref(stored)})
    return Observation(ok=False, summary="Invalid secret tool operation.")


def _looks_like_placeholder_secret(text: str) -> bool:
    value = text.strip().lower()
    return not value or "<" in value or ">" in value or "..." in value or value in {"nom", "nom_de_variable"}
