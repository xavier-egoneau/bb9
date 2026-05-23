"""CLI extension for the secret tool."""

from __future__ import annotations

from bb9.core.loop import ApprovalDecision

from .input_guard import SecretCandidate, detect_secret_candidate
from .store import SecretStore, normalize_secret_name, secret_ref


def register(cli) -> None:
    cli.add_command("/secret", lambda rest: _cmd_secret(cli, rest), "creer ou lister les secrets")
    cli.add_command("/secrets", lambda rest: _cmd_secrets(cli, rest), "lister les references secrets")
    cli.add_input_interceptor(lambda text: _intercept_secret_input(cli, text))
    cli.add_approval_handler(lambda decision, context: _approve_secret_write(cli, decision, context))
    cli.add_context_line(lambda context: _context_line(cli, context))


def _cmd_secret(cli, value: str) -> bool:
    parts = value.split(maxsplit=1)
    op = parts[0].lower() if parts else "list"
    rest = parts[1].strip() if len(parts) > 1 else ""
    if op in {"list", "ls"}:
        return _cmd_secrets(cli, rest)
    if op in {"add", "set"} and rest:
        _open_secret_capture(cli, normalize_secret_name(rest))
        return True
    print("Usage: /secret list | /secret add <NOM>")
    return True


def _cmd_secrets(cli, _: str) -> bool:
    names = SecretStore().list_names()
    if not names:
        print("No named secrets.")
        return True
    for name in names:
        print(secret_ref(name))
    return True


def _intercept_secret_input(cli, text: str) -> bool:
    candidate = detect_secret_candidate(text)
    if candidate is None:
        return False

    print()
    print(cli.theme.title("Secret detecte"))
    print("BB9 n'enverra pas ce message au provider.")
    print(f"ref...... {secret_ref(candidate.name)}")
    raw_name = input(f"Nom du secret [{candidate.name}] : ").strip()
    name = raw_name or candidate.name
    raw = input("Stocker localement ? [y/N] : ").strip().lower()
    if raw not in {"y", "yes", "o", "oui"}:
        print("Message annule pour eviter d'envoyer un secret.")
        return True
    _store_secret(cli, SecretCandidate(name=name, value=candidate.value))
    return True


def _approve_secret_write(cli, decision, _):
    action = decision.action
    if action is None or action.name != "secret" or action.params.get("op") != "set":
        return None
    name = str(action.params.get("name", "")).strip()
    print(f"op....... {action.params.get('op', '')}")
    print(f"ref...... {secret_ref(name)}")
    raw = input(f"Ouvrir la capture locale pour {secret_ref(name)} ? [y/N] : ").strip().lower()
    if raw not in {"y", "yes", "o", "oui"}:
        return ApprovalDecision(verdict="deny")
    _open_secret_capture(cli, name)
    return ApprovalDecision(verdict="defer", summary=f"Secret capture pending: {secret_ref(name)}")


def _open_secret_capture(cli, name: str) -> None:
    normalized = normalize_secret_name(name)
    cli.open_local_capture(
        prompt="secret>",
        label=secret_ref(normalized),
        on_value=lambda value: _store_secret(cli, SecretCandidate(name=normalized, value=value)),
        cancel_summary=f"Capture annulee: {secret_ref(normalized)}",
    )
    print("Colle maintenant la valeur du secret. Elle ne sera pas envoyee au provider.")
    print("Tape /cancel pour annuler la capture.")


def _store_secret(cli, candidate: SecretCandidate) -> None:
    try:
        stored = SecretStore().set(candidate.name, candidate.value)
    except ValueError as exc:
        print(f"Secret refuse: {exc}")
        return
    summary = f"Secret stored: {secret_ref(stored)}"
    print(summary)
    cli.remember_turn("[secret intercepté]", summary)


def _context_line(cli, _) -> str:
    label = cli.local_capture.label if cli.local_capture else "-"
    return f"sec... {label}"
