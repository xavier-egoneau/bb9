"""Interactive guardian approval for the CLI surface."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..core.loop import ApprovalDecision, ApprovalResult
from ..core.models import GuardianDecision, RunContext
from ..core.trust import TrustedRoots


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

        tool_name = action.name if action is not None else ""
        if tool_name and tool_name in getattr(cli, "session_allowed_tools", set()):
            return "allow"

        trust_root = _trusted_root_candidate(decision.reason)
        if trust_root is not None:
            print(f"trust.... {trust_root}")
            raw = input("Autoriser ? [y] une fois, [s] cette session, [t] ajouter trusted root, [N] refuser : ").strip().lower()
            if raw == "s":
                cli.session_allowed_tools.add(tool_name)
                return "allow"
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

        raw = input("Autoriser ? [y] une fois, [s] cette session, [N] refuser : ").strip().lower()
        if raw == "s":
            cli.session_allowed_tools.add(tool_name)
            return "allow"
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
