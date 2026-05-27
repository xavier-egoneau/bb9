"""Interactive goal commands."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .goals import GoalCommandHandler, GoalLoopRunner


def handle(cli: Any, value: str, *, write: Callable[[str], None] = print) -> bool:
    return build_handler(cli, write=write).handle(value)


def status(cli: Any, *, write: Callable[[str], None] = print) -> str:
    text = build_handler(cli, write=write).status()
    write(text)
    return text


def build_handler(cli: Any, *, write: Callable[[str], None] = print) -> GoalCommandHandler:
    return GoalCommandHandler(cli.goal_manager, build_runner(cli, write=write), write=write)


def build_runner(cli: Any, *, write: Callable[[str], None] = print) -> GoalLoopRunner:
    return GoalLoopRunner(
        cli.goal_manager,
        build_context=cli.build_goal_context,
        build_provider=cli.build_goal_provider,
        ask_user=cli.ask_guardian,
        remember_turn=cli.remember_turn,
        write=write,
    )
