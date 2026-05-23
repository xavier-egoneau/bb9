"""Standalone shell tool runtime."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from bb9.core.models import Action, GuardianDecision, Observation, PermissionProfile, RunContext
from bb9.core.trust import TrustedRoots, classify_path


READ_COMMANDS = {"pwd", "ls", "find", "rg", "sed", "head", "tail", "cat"}
VERIFICATION_COMMANDS = {"npm", "pnpm", "yarn", "pytest", "python", "python3", "make", "cargo", "go"}
BLOCKED_TOKENS = {
    ">",
    ">>",
    "|",
    "&&",
    "||",
    ";",
    "$(",
    "`",
}
DESTRUCTIVE_COMMANDS = {
    "chmod",
    "chown",
    "curl",
    "dd",
    "mkfs",
    "mount",
    "mv",
    "rm",
    "rsync",
    "scp",
    "sudo",
    "umount",
    "wget",
}


def action_from_text(text: str) -> Action:
    return Action(name="shell", params={"cmd": text}, risk="medium")


def review(action: Action, context: RunContext) -> GuardianDecision:
    trusted_roots = context.trusted_roots or TrustedRoots()
    return _review_shell_action(action, context.workspace.root, trusted_roots, context.permission_profile)


def execute(action: Action) -> Observation:
    try:
        argv = _argv(action)
    except ValueError as exc:
        return Observation(ok=False, summary=f"invalid shell command: {exc}")
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        command = argv[0] if argv else ""
        return Observation(
            ok=False,
            summary=f"command not found: {command}",
            data={"cmd": str(action.params.get("cmd", "")), "returncode": 127},
        )
    except subprocess.TimeoutExpired:
        return Observation(
            ok=False,
            summary="command timed out",
            data={"cmd": str(action.params.get("cmd", "")), "returncode": 124},
        )
    except OSError as exc:
        return Observation(
            ok=False,
            summary=f"shell execution error: {exc}",
            data={"cmd": str(action.params.get("cmd", ""))},
        )
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    summary = stdout or stderr or f"exit code {completed.returncode}"
    return Observation(
        ok=completed.returncode == 0,
        summary=summary,
        data={
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        },
    )


def _review_shell_action(
    action: Action,
    workspace: Path,
    trusted_roots: TrustedRoots,
    profile: PermissionProfile,
) -> GuardianDecision:
    cmd = str(action.params.get("cmd", "")).strip()
    if not cmd:
        return GuardianDecision(verdict="block", reason="empty shell command", action=action)
    if _looks_like_placeholder_command(cmd):
        return GuardianDecision(verdict="block", reason="placeholder shell command", action=action)
    if any(token in cmd for token in BLOCKED_TOKENS):
        return GuardianDecision(verdict="ask", reason="compound shell command requires confirmation", action=action)

    try:
        argv = shlex.split(cmd)
    except ValueError as exc:
        return GuardianDecision(verdict="block", reason=f"invalid shell command: {exc}", action=action)

    if not argv:
        return GuardianDecision(verdict="block", reason="empty shell command", action=action)

    command = argv[0]
    if command in DESTRUCTIVE_COMMANDS:
        return GuardianDecision(verdict="ask", reason=f"destructive or external command requires confirmation: {command}", action=action)

    for path in _candidate_paths(argv[1:], workspace):
        zone = classify_path(path, workspace, trusted_roots)
        if zone == "protected":
            return GuardianDecision(verdict="block", reason=f"protected path: {path}", action=action)
        if zone == "outside":
            return GuardianDecision(verdict="ask", reason=f"path outside workspace/trusted roots: {path}", action=action)

    if command in VERIFICATION_COMMANDS and _is_verification_command(argv):
        if profile in {"limited", "power"}:
            return GuardianDecision(verdict="allow", reason=f"verification command allowed by {profile} profile", action=action)
        return GuardianDecision(verdict="ask", reason=f"verification command requires confirmation: {command}", action=action)

    if command not in READ_COMMANDS:
        return GuardianDecision(verdict="ask", reason=f"unknown shell command requires confirmation: {command}", action=action)

    return GuardianDecision(verdict="allow", reason=f"read-only shell command allowed by {profile} profile", action=action)


def _is_verification_command(argv: list[str]) -> bool:
    command = argv[0]
    if command in {"npm", "pnpm", "yarn"}:
        return len(argv) >= 2 and argv[1] in {"test", "build", "lint", "typecheck", "run"}
    if command == "pytest":
        return True
    if command in {"python", "python3"}:
        return len(argv) >= 3 and argv[1] == "-m" and argv[2] in {"unittest", "pytest"}
    if command == "make":
        return len(argv) >= 2 and argv[1] in {"test", "tests", "check", "lint", "build"}
    if command == "cargo":
        return len(argv) >= 2 and argv[1] in {"test", "check", "build"}
    if command == "go":
        return len(argv) >= 2 and argv[1] == "test"
    return False


def _looks_like_placeholder_command(cmd: str) -> bool:
    return "<" in cmd or ">" in cmd or "..." in cmd or "`" in cmd


def _argv(action: Action) -> list[str]:
    cmd = str(action.params.get("cmd", "")).strip()
    return shlex.split(cmd)


def _candidate_paths(args: list[str], workspace: Path) -> list[Path]:
    candidates: list[Path] = []
    for arg in args:
        if arg.startswith("-"):
            continue
        if "/" not in arg and not arg.startswith("."):
            continue
        path = Path(arg).expanduser()
        if not path.is_absolute():
            path = workspace / path
        candidates.append(path)
    return candidates
