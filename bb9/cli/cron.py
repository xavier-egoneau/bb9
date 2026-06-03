"""CLI handlers for BB9 cron commands."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .agents import AgentNotFoundError
from .cron import (
    CronNotFoundError,
    CronSpec,
    CronStateStore,
    cron_is_due,
    cron_should_notify,
    discover_crons,
    due_crons,
    load_cron,
    next_run_after,
)
from .providers import ProviderError


def handle(cli: Any, value: str) -> bool:
    command = value.strip().lower() or "status"
    if command in {"status", "list", "ls"}:
        print_status(cli)
        return True
    if command == "due":
        print_due(cli)
        return True
    if command == "tick":
        tick(cli)
        return True
    print("Usage: /cron [status|due|tick]")
    return True


def print_status(cli: Any) -> None:
    crons = load_all(cli)
    if not crons:
        print("Aucun cron configure.")
        return
    now = _now_local()
    states = CronStateStore(cli.state.cron_state_path).load()
    for cron in crons:
        state = states.get(cron.name)
        due = cron_is_due(cron, now, state)
        next_run = next_run_after(cron, now, state)
        status = "due" if due else "idle"
        if state is not None and state.locked:
            status = "locked"
        schedule = _schedule_label(cron)
        next_label = next_run.isoformat(timespec="minutes") if next_run else "-"
        print(
            f"cron... {cron.name} "
            f"[{cron.activation}/{cron.mode}/{status}] "
            f"{schedule} next={next_label}"
        )


def print_due(cli: Any) -> None:
    crons = load_all(cli)
    states = CronStateStore(cli.state.cron_state_path).load()
    due = due_crons(crons, _now_local(), states)
    if not due:
        print("Aucun cron du.")
        return
    for cron in due:
        summary = _short_message(cron.summary or cron.intention or "-")
        print(f"due.... {cron.name} -> {summary}")


def tick(cli: Any) -> None:
    crons = load_all(cli)
    store = CronStateStore(cli.state.cron_state_path)
    now = _now_local()
    due = due_crons(crons, now, store.load())
    if not due:
        print("Aucun cron du.")
        return
    for cron in due:
        run_due(cli, cron, store, now)


def run_due(cli: Any, cron: CronSpec, store: CronStateStore, now: datetime) -> None:
    print(f"cron... {cron.name}")
    store.set_locked(cron.name, True)
    try:
        if cron.command.strip():
            ok, summary = run_command(cli, cron.command)
        else:
            agent = cli.load_agent_for_cron(cron.agent)
            context = cli.build_context_with_agent(agent)
            result = cli.run_once_for_cron(agent, cron, context)
            summary = (
                result.observation.summary
                if result.observation is not None
                else result.decision.summary
            )
            ok = result.observation is None or result.observation.ok
        if ok:
            store.record_run(
                cron.name,
                now,
                summary,
                cron.history_policy,
            )
            print("res.... ok: " + _short_message(summary))
        else:
            store.record_error(
                cron.name,
                summary,
                now,
                cron.retry_policy,
                cron.history_policy,
            )
            print("res.... erreur: " + _short_message(summary))
        if cron_should_notify(cron, ok):
            print(f"not.... {cron.notification_policy.channel}")
        cli.remember_turn(f"/cron tick {cron.name}", summary)
    except (AgentNotFoundError, ProviderError, CronNotFoundError) as exc:
        store.record_error(
            cron.name,
            str(exc),
            now,
            cron.retry_policy,
            cron.history_policy,
        )
        print(f"res.... erreur: {exc}")
    finally:
        store.set_locked(cron.name, False)


def run_command(cli: Any, command: str) -> tuple[bool, str]:
    text = command.strip()
    if text.startswith("/dream"):
        return run_dream_command(cli, text)
    return False, f"Commande cron interne non supportée: {text}"


def run_dream_command(cli: Any, text: str) -> tuple[bool, str]:
    _, _, rest = text.partition(" ")
    command_name, _, command_rest = rest.strip().partition(" ")
    if command_name != "run":
        return False, f"Commande cron /dream non supportée: {text}"
    result = cli.run_dream(command_rest.strip(), remember=False)
    if result is None:
        return False, f"Commande échouée: {text}"
    return True, result.summary or f"Commande exécutée: {text}"


def load_all(cli: Any) -> tuple[CronSpec, ...]:
    return tuple(
        load_cron(cli.state.crons_dir, name)
        for name in discover_crons(cli.state.crons_dir)
    )


def _now_local() -> datetime:
    return datetime.now().astimezone()


def _schedule_label(cron: CronSpec) -> str:
    if cron.mode == "once":
        return cron.at or "-"
    days = ",".join(cron.days) if cron.days else "daily"
    return f"{cron.time or '-'} {days}".strip()


def _short_message(text: str, limit: int = 64) -> str:
    plain = " ".join(text.split())
    if len(plain) <= limit:
        return plain
    return plain[: limit - 1] + "..."
