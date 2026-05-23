"""Standalone CalDAV tool runtime."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from bb9.core.models import Action, GuardianDecision, Observation, RunContext


DEFAULT_DAYS = 7
DEFAULT_TIMEOUT = 30
VDIRSYNCER_CONFIG = Path.home() / ".config" / "vdirsyncer" / "config"
KHAL_CONFIG = Path.home() / ".config" / "khal" / "config"


def action_from_text(text: str) -> Action:
    argv = shlex.split(text.strip())
    op = argv[0].lower() if argv else "agenda"
    params: dict[str, Any] = {"op": op}
    risk = "medium"

    if op == "doctor":
        risk = "low"
    elif op == "agenda":
        risk = "medium"
        params["days"] = _arg_int(argv[1:], "days", DEFAULT_DAYS)
        params["sync"] = _arg_bool(argv[1:], "sync", True)
    elif op == "maintenance":
        risk = "high"
        params["operation"] = _arg_value(argv[1:], "operation") or (argv[1] if len(argv) > 1 else "refresh")
        params["days"] = _arg_int(argv[1:], "days", DEFAULT_DAYS)
    else:
        risk = "forbidden"

    params["timeout_seconds"] = _arg_int(argv[1:], "timeout", DEFAULT_TIMEOUT)
    return Action(name="caldav", params=params, risk=risk)


def review(action: Action, context: RunContext) -> GuardianDecision:
    op = str(action.params.get("op", "")).strip().lower()
    if op == "doctor":
        return GuardianDecision(verdict="allow", reason="CalDAV doctor is local diagnostic", action=action)
    if op == "agenda":
        if context.permission_profile in {"limited", "power"}:
            return GuardianDecision(verdict="allow", reason=f"CalDAV agenda read allowed by {context.permission_profile} profile", action=action)
        return GuardianDecision(verdict="ask", reason="CalDAV agenda contains personal data", action=action)
    if op == "maintenance":
        return GuardianDecision(verdict="ask", reason="CalDAV maintenance can sync external calendar data", action=action)
    return GuardianDecision(verdict="block", reason="invalid CalDAV action", action=action)


def execute(action: Action) -> Observation:
    op = str(action.params.get("op", "")).strip().lower()
    if op == "doctor":
        return _doctor()
    if op == "agenda":
        return _agenda(action.params)
    if op == "maintenance":
        return _maintenance(action.params)
    return Observation(ok=False, summary=f"Invalid CalDAV operation: {op}")


def _doctor() -> Observation:
    status = _setup_status()
    summary = "CalDAV calendar access looks ready." if status["ready"] else "CalDAV setup incomplete: " + "; ".join(status["missing"])
    return Observation(ok=True, summary=summary, data=status)


def _agenda(params: dict[str, Any]) -> Observation:
    days = _positive_int(params.get("days"), DEFAULT_DAYS)
    timeout = _positive_int(params.get("timeout_seconds"), DEFAULT_TIMEOUT)
    sync = bool(params.get("sync", True))
    status = _setup_status()
    if not status["ready"]:
        return Observation(ok=False, summary="CalDAV setup incomplete: " + "; ".join(status["missing"]), data=status)

    sync_error = ""
    if sync:
        sync_result = _run(["vdirsyncer", "sync"], timeout=timeout)
        if sync_result["returncode"] != 0:
            sync_error = _compact_error(sync_result)

    read = _read_agenda(days=days, timeout=timeout)
    if read["returncode"] != 0:
        return Observation(
            ok=False,
            summary=f"Calendar read failed: {_compact_error(read)}",
            data={"stderr": read["stderr"][:1000], "sync_error": sync_error},
        )
    events = _event_lines(read["stdout"])
    return Observation(
        ok=True,
        summary=_agenda_summary(events, days=days, sync_error=sync_error),
        data={"days": days, "events": events, "sync_error": sync_error},
    )


def _maintenance(params: dict[str, Any]) -> Observation:
    operation = str(params.get("operation") or "refresh").strip().lower()
    if operation not in {"refresh", "discover", "sync", "verify"}:
        return Observation(ok=False, summary=f"Unknown CalDAV maintenance operation: {operation}")
    timeout = _positive_int(params.get("timeout_seconds"), DEFAULT_TIMEOUT)
    days = _positive_int(params.get("days"), DEFAULT_DAYS)
    status = _setup_status()
    if not status["ready"]:
        return Observation(ok=False, summary="CalDAV setup incomplete: " + "; ".join(status["missing"]), data=status)

    steps: list[dict[str, Any]] = []
    if operation in {"refresh", "discover"}:
        steps.append(_step(["vdirsyncer", "discover"], timeout=timeout))
    if operation in {"refresh", "sync"}:
        steps.append(_step(["vdirsyncer", "sync"], timeout=timeout))
    if operation in {"refresh", "sync", "verify"}:
        read = _read_agenda(days=days, timeout=timeout)
        steps.append(
            {
                "command": f"khal list today {days}d",
                "returncode": read["returncode"],
                "ok": read["returncode"] == 0,
                "summary": _agenda_summary(_event_lines(read["stdout"]), days=days, sync_error="")
                if read["returncode"] == 0
                else _compact_error(read),
                "stderr": read["stderr"][:1000],
            }
        )

    failed = [step for step in steps if not step["ok"]]
    summary = ("CalDAV maintenance failed: " if failed else "CalDAV maintenance completed: ")
    summary += "; ".join(step["summary"] for step in (failed or steps))
    return Observation(ok=not failed, summary=summary, data={"operation": operation, "steps": steps})


def _setup_status() -> dict[str, Any]:
    bins = {"vdirsyncer": _command_path("vdirsyncer"), "khal": _command_path("khal")}
    files = {"vdirsyncer_config": VDIRSYNCER_CONFIG.exists(), "khal_config": KHAL_CONFIG.exists()}
    missing: list[str] = []
    for name, path in bins.items():
        if not path:
            missing.append(f"missing binary `{name}`")
    for name, exists in files.items():
        if not exists:
            missing.append(f"missing {name.replace('_', ' ')}")
    if missing:
        missing.append("if credentials are missing, use the `secret` tool before writing local CalDAV config")
    return {"ready": not missing, "missing": missing, "binaries": bins, "files": files}


def _read_agenda(*, days: int, timeout: int) -> dict[str, Any]:
    return _run(["khal", "list", "today", f"{days}d"], timeout=timeout)


def _run(command: list[str], *, timeout: int) -> dict[str, Any]:
    command = _resolve_command(command)
    try:
        completed = subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False, env=_tool_env())
    except FileNotFoundError as exc:
        return {"command": command, "returncode": 127, "stdout": "", "stderr": str(exc)}
    except subprocess.TimeoutExpired as exc:
        return {"command": command, "returncode": 124, "stdout": exc.stdout or "", "stderr": "timeout"}
    return {"command": command, "returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}


def _command_path(name: str) -> str | None:
    managed = _managed_tool_bin_dir() / name
    if managed.exists():
        return str(managed)
    found = shutil.which(name)
    if found:
        return found
    candidate = Path(sys.executable).resolve().parent / name
    if candidate.exists():
        return str(candidate)
    return None


def _managed_tool_bin_dir() -> Path:
    return Path.home() / ".bb9" / "tools" / "bin"


def _managed_tool_python_dir() -> Path:
    return Path.home() / ".bb9" / "tools" / "python"


def _tool_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([p for p in (str(_managed_tool_bin_dir()), env.get("PATH", "")) if p])
    env["PYTHONPATH"] = os.pathsep.join([p for p in (str(_managed_tool_python_dir()), env.get("PYTHONPATH", "")) if p])
    return env


def _resolve_command(command: list[str]) -> list[str]:
    if not command:
        return command
    resolved = _command_path(command[0])
    if resolved:
        return [resolved, *command[1:]]
    return command


def _step(command: list[str], *, timeout: int) -> dict[str, Any]:
    result = _run(command, timeout=timeout)
    return {
        "command": " ".join(command),
        "returncode": result["returncode"],
        "ok": result["returncode"] == 0,
        "summary": "ok" if result["returncode"] == 0 else _compact_error(result),
        "stderr": result["stderr"][:1000],
    }


def _event_lines(stdout: str) -> list[str]:
    return [line.strip() for line in stdout.splitlines() if line.strip()][:50]


def _agenda_summary(events: list[str], *, days: int, sync_error: str) -> str:
    parts = [f"{len(events)} calendar event(s) over {days} day(s)."]
    if events:
        parts.append("; ".join(events[:5]))
    if sync_error:
        parts.append(f"Sync warning: {sync_error}")
    return " ".join(parts)


def _compact_error(result: dict[str, Any]) -> str:
    stderr = str(result.get("stderr") or "").strip()
    stdout = str(result.get("stdout") or "").strip()
    return (stderr or stdout or f"exit {result.get('returncode')}")[:500]


def _arg_value(args: list[str], name: str) -> str:
    prefix = f"{name}="
    for arg in args:
        if arg.startswith(prefix):
            return arg.removeprefix(prefix).strip()
    return ""


def _arg_int(args: list[str], name: str, default: int) -> int:
    return _positive_int(_arg_value(args, name), default)


def _arg_bool(args: list[str], name: str, default: bool) -> bool:
    value = _arg_value(args, name).lower()
    if value in {"1", "true", "yes", "on", "oui"}:
        return True
    if value in {"0", "false", "no", "off", "non"}:
        return False
    return default


def _positive_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
