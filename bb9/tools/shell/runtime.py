"""Standalone shell tool runtime."""

from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from bb9.core.models import Action, GuardianDecision, Observation, PermissionProfile, RunContext
from bb9.core.trust import TrustedRoots, classify_path

READ_COMMANDS = {"pwd", "ls", "find", "rg", "sed", "head", "tail", "cat", "grep"}
WORKSPACE_WRITE_COMMANDS = {"mkdir", "touch"}
VERIFICATION_COMMANDS = {"npm", "pnpm", "yarn", "pytest", "python", "python3", "make", "cargo", "go"}
BLOCKED_TOKENS = {
    ">",
    ">>",
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
    if _is_http_server_command(argv):
        return _start_http_server(argv)
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
    if argv and argv[0] == "grep" and completed.returncode == 1 and not stdout and not stderr:
        return Observation(
            ok=True,
            summary="no matches",
            data={
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            },
        )
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
    rewritten = _rewrite_safe_read_pipeline(cmd)
    if _split_shell_pipes(cmd):
        if rewritten is None:
            return GuardianDecision(verdict="ask", reason="compound shell command requires confirmation", action=action)
        params = {**action.params, "cmd": shlex.join(rewritten)}
        action = replace(action, params=params)
        cmd = str(action.params["cmd"])
    if any(token in cmd for token in BLOCKED_TOKENS):
        return GuardianDecision(verdict="ask", reason="compound shell command requires confirmation", action=action)

    try:
        argv = shlex.split(cmd)
    except ValueError as exc:
        return GuardianDecision(verdict="block", reason=f"invalid shell command: {exc}", action=action)

    if not argv:
        return GuardianDecision(verdict="block", reason="empty shell command", action=action)

    command = argv[0]
    is_workspace_write = command in WORKSPACE_WRITE_COMMANDS
    for path in _candidate_paths(
        argv[1:],
        workspace,
        include_plain_names=command in DESTRUCTIVE_COMMANDS or is_workspace_write,
    ):
        zone = classify_path(path, workspace, trusted_roots)
        if zone == "protected":
            return GuardianDecision(verdict="block", reason=f"protected path: {path}", action=action)
        if zone == "outside":
            return GuardianDecision(verdict="ask", reason=f"path outside workspace/trusted roots: {path}", action=action)

    if command in DESTRUCTIVE_COMMANDS:
        return GuardianDecision(verdict="ask", reason=f"destructive or external command requires confirmation: {command}", action=action)

    if is_workspace_write:
        return GuardianDecision(verdict="allow", reason=f"workspace write command allowed by {profile} profile: {command}", action=action)

    if _is_http_server_command(argv):
        directory = _http_server_directory(argv)
        if directory is not None:
            path = Path(directory).expanduser()
            if not path.is_absolute():
                path = workspace / path
            zone = classify_path(path, workspace, trusted_roots)
            if zone == "protected":
                return GuardianDecision(verdict="block", reason=f"protected path: {path}", action=action)
            if zone == "outside":
                return GuardianDecision(verdict="ask", reason=f"path outside workspace/trusted roots: {path}", action=action)
        bind = _http_server_bind(argv)
        if bind not in {"", "127.0.0.1", "localhost", "::1"}:
            return GuardianDecision(verdict="ask", reason=f"http server bind requires confirmation: {bind}", action=action)
        if profile in {"limited", "power"}:
            return GuardianDecision(verdict="allow", reason=f"local http server allowed by {profile} profile", action=action)
        return GuardianDecision(verdict="ask", reason="local http server requires confirmation in safe profile", action=action)

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


def _is_http_server_command(argv: list[str]) -> bool:
    if len(argv) < 3 or argv[0] not in {"python", "python3"} or argv[1:3] != ["-m", "http.server"]:
        return False
    index = 3
    while index < len(argv):
        arg = argv[index]
        if arg in {"--bind", "-b", "--directory", "-d"}:
            index += 2
            continue
        if arg.startswith("-"):
            return False
        if not arg.isdigit():
            return False
        index += 1
    return True


def _start_http_server(argv: list[str]) -> Observation:
    base_argv = _http_server_argv(argv)
    requested_port = _http_server_port(base_argv)
    last_error = "startup failed"
    for port in _candidate_http_ports(requested_port):
        server_argv = _with_http_server_port(base_argv, port)
        url = f"http://127.0.0.1:{port}"
        try:
            process = subprocess.Popen(
                server_argv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                start_new_session=True,
            )
        except FileNotFoundError:
            return Observation(ok=False, summary=f"command not found: {server_argv[0]}", data={"returncode": 127})
        except OSError as exc:
            last_error = str(exc)
            continue
        time.sleep(0.2)
        if process.poll() is not None:
            if _wait_for_http_server(url, attempts=1, delay=0):
                return Observation(
                    ok=True,
                    summary=f"HTTP server already available: {url}",
                    data={"url": url, "cmd": shlex.join(server_argv), "reused": True, "requested_port": requested_port},
                )
            last_error = "port unavailable or startup failed" if process.returncode == 1 else f"exit code {process.returncode}"
            continue
        if not _wait_for_http_server(url):
            process.terminate()
            last_error = f"no HTTP response from {url}"
            continue
        summary = f"HTTP server started: {url}"
        if port != requested_port:
            summary = f"{summary} (port {requested_port} unavailable)"
        return Observation(
            ok=True,
            summary=summary,
            data={"pid": process.pid, "url": url, "cmd": shlex.join(server_argv), "requested_port": requested_port},
        )
    first = requested_port
    last = requested_port + 19
    return Observation(
        ok=False,
        summary=f"http server failed: {last_error}; no available responsive port in {first}-{last}",
        data={"requested_port": requested_port},
    )


def _wait_for_http_server(url: str, *, attempts: int = 10, delay: float = 0.1) -> bool:
    for _ in range(max(1, attempts)):
        try:
            with urlopen(url, timeout=1) as response:
                return 200 <= int(getattr(response, "status", 200)) < 500
        except HTTPError as exc:
            return 200 <= exc.code < 500
        except (OSError, URLError):
            if delay:
                time.sleep(delay)
    return False


def _http_server_argv(argv: list[str]) -> list[str]:
    if _http_server_bind(argv):
        return argv
    return [*argv[:3], "--bind", "127.0.0.1", *argv[3:]]


def _candidate_http_ports(requested_port: int) -> list[int]:
    return [requested_port + offset for offset in range(20)]


def _with_http_server_port(argv: list[str], port: int) -> list[str]:
    result = list(argv)
    skip_next = False
    for index, arg in enumerate(result[3:], start=3):
        if skip_next:
            skip_next = False
            continue
        if arg in {"--bind", "-b", "--directory", "-d"}:
            skip_next = True
            continue
        if arg.isdigit():
            result[index] = str(port)
            return result
    result.append(str(port))
    return result


def _http_server_port(argv: list[str]) -> int:
    skip_next = False
    for arg in argv[3:]:
        if skip_next:
            skip_next = False
            continue
        if arg in {"--bind", "-b", "--directory", "-d"}:
            skip_next = True
            continue
        if arg.isdigit():
            return int(arg)
    return 8000


def _http_server_bind(argv: list[str]) -> str:
    for index, arg in enumerate(argv):
        if arg in {"--bind", "-b"} and index + 1 < len(argv):
            return argv[index + 1]
    return ""


def _http_server_directory(argv: list[str]) -> str | None:
    for index, arg in enumerate(argv):
        if arg in {"--directory", "-d"} and index + 1 < len(argv):
            return argv[index + 1]
    return None


def _looks_like_placeholder_command(cmd: str) -> bool:
    return "<" in cmd or ">" in cmd or "..." in cmd or "`" in cmd


def _argv(action: Action) -> list[str]:
    cmd = str(action.params.get("cmd", "")).strip()
    rewritten = _rewrite_safe_read_pipeline(cmd)
    if rewritten is not None:
        return rewritten
    return shlex.split(cmd)


def _rewrite_safe_read_pipeline(cmd: str) -> list[str] | None:
    parts = _split_shell_pipes(cmd)
    if len(parts) != 2:
        return None
    if any(token in cmd for token in BLOCKED_TOKENS):
        return None
    try:
        left = shlex.split(parts[0])
        right = shlex.split(parts[1])
    except ValueError:
        return None
    if len(left) == 2 and left[0] == "cat" and right and right[0] in {"head", "tail", "grep"}:
        return [*right, left[1]]
    return None


def _split_shell_pipes(cmd: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    for char in cmd:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\" and quote != "'":
            current.append(char)
            escaped = True
            continue
        if quote is not None:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            current.append(char)
            quote = char
            continue
        if char == "|":
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    if parts:
        parts.append("".join(current).strip())
    return parts


def _candidate_paths(args: list[str], workspace: Path, *, include_plain_names: bool = False) -> list[Path]:
    candidates: list[Path] = []
    for arg in args:
        if arg.startswith("-"):
            continue
        if "/" not in arg and not arg.startswith(".") and not include_plain_names:
            continue
        path = Path(arg).expanduser()
        if not path.is_absolute():
            path = workspace / path
        candidates.append(path)
    return candidates
