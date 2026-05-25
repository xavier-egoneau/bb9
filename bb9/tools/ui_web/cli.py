"""CLI extension for the local BB9 web UI."""

from __future__ import annotations

from bb9.core.gateway import execute
from bb9.core.models import Action


def register(cli) -> None:
    cli.add_command("/web", lambda rest: _cmd_web(rest), "ouvrir l'UI locale image/screenshot")


def _cmd_web(rest: str) -> bool:
    port = rest.strip() or "8769"
    observation = execute(Action(name="ui_web", params={"op": "start", "port": port}, risk="low"))
    print(observation.summary)
    return True
