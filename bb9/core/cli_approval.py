"""Interactive guardian approval for the CLI surface."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .loop import ApprovalDecision, ApprovalResult
from .models import GuardianDecision, RunContext
from .trust import TrustedRoots


def ask_guardian(cli: Any, decision: GuardianDecision, context: RunContext) -> ApprovalResult | ApprovalDecision:
    with paused_activity(cli):
        action = decision.action
        theme = cli.theme
        print()
        print(theme.title("Validation requise"))
        print(f"raison... {decision.reason}")
        if action is not None:
            print(f"tool..... {action.name}")
            if action.name == "shell":
                print(f"cmd...... {action.params.get('cmd', '')}")

        for handler in cli.approval_handlers:
            handled = handler(decision, context)
            if handled is not None:
                return handled

        trust_root = _trusted_root_candidate(decision.reason)
        if trust_root is not None:
            print(f"trust.... {trust_root}")
            raw = input("Autoriser ? [y] une fois, [t] ajouter trusted root, [N] refuser : ").strip().lower()
            if raw == "t":
                try:
                    added = TrustedRoots.add(trust_root)
                except ValueError as exc:
                    print(f"Trusted root refuse: {exc}")
                    return "deny"
                print(f"Trusted root ajoute: {added}")
                return "allow"
            if raw in {"y", "yes", "o", "oui"}:
                return "allow"
            return "deny"

        raw = input("Autoriser une fois ? [y/N] : ").strip().lower()
        if raw in {"y", "yes", "o", "oui"}:
            return "allow"
        return "deny"


@contextmanager
def paused_activity(cli: Any) -> Iterator[None]:
    if cli.activity is None:
        yield
        return
    with cli.activity.paused():
        yield


def _trusted_root_candidate(reason: str) -> Path | None:
    prefix = "path outside workspace/trusted roots:"
    if not reason.startswith(prefix):
        return None
    raw = reason.removeprefix(prefix).strip()
    if not raw:
        return None
    path = Path(raw).expanduser().resolve()
    if path.exists() and path.is_dir():
        return path
    if path.suffix:
        return path.parent
    return path
