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

READ_COMMANDS = {"pwd", "ls", "find", "rg", "sed", "head", "tail", "cat", "grep", "sort"}
GIT_READ_SUBCOMMANDS = {"branch", "diff", "log", "ls-files", "rev-parse", "show", "status"}
WORKSPACE_WRITE_COMMANDS = {"mkdir", "touch"}
VERIFICATION_COMMANDS = {"npm", "pnpm", "yarn", "pytest", "python", "python3", "make", "cargo", "go"}
LOCAL_STDIN_INTERPRETERS = {"python", "python3"}
BLOCKED_TOKENS = {
    ">",
    ">>",
    "&&",
    "||",
    ";",
    "$(",
    "`",
}
PLACEHOLDER_NAMES = {"commande", "cmd", "nom", "path", "chemin", "texte", "text", "url"}
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


def execute(action: Action, context: RunContext | None = None) -> Observation:
    cmd = str(action.params.get("cmd", "")).strip()
    cwd = context.workspace.root if context is not None else None
    heredoc = _parse_heredoc_command(cmd)
    if heredoc is not None:
        argv, stdin = heredoc
        if not _is_local_stdin_interpreter_command(argv):
            return Observation(
                ok=False,
                summary="unsupported heredoc shell command; shell=True is disabled",
                data={"cmd": cmd, "returncode": 2},
                retry_policy="block_exact",
            )
        return _execute_argv(argv, cmd, cwd=cwd, stdin=stdin)
    if _has_heredoc_operator(cmd):
        return Observation(
            ok=False,
            summary="unterminated heredoc shell command",
            data={"cmd": cmd, "returncode": 2},
            retry_policy="block_exact",
        )
    read_chain = _safe_read_chain_spec(cmd)
    if read_chain is not None:
        chain, tolerate_failure = read_chain
        return _execute_safe_read_chain(chain, cmd, tolerate_failure=tolerate_failure, cwd=cwd)
    if _rewrite_safe_read_pipeline(cmd) is None:
        pipeline = _safe_read_pipeline_argvs(cmd)
        if pipeline is not None:
            return _execute_safe_read_pipeline(pipeline, cmd, cwd=cwd)
        if _split_shell_pipes(cmd):
            return Observation(
                ok=False,
                summary="unsupported compound shell command; shell=True is disabled",
                data={"cmd": cmd, "returncode": 2},
                retry_policy="block_exact",
            )
    try:
        argv = _argv(action)
    except ValueError as exc:
        return Observation(ok=False, summary=f"invalid shell command: {exc}")
    if argv and argv[0] in READ_COMMANDS:
        unsafe = _unsafe_read_command_reason(argv)
        if unsafe:
            return Observation(ok=False, summary=unsafe, data={"cmd": cmd, "returncode": 2}, retry_policy="block_exact")
    if _is_http_server_command(argv):
        return _start_http_server(argv, cwd=cwd)
    invalid_http_server = _invalid_http_server_reason(argv)
    if invalid_http_server:
        return Observation(ok=False, summary=invalid_http_server, retry_policy="recoverable")
    return _execute_argv(argv, cmd, cwd=cwd)


def _execute_argv(argv: list[str], cmd: str, *, cwd: Path | None = None, stdin: str | None = None) -> Observation:
    try:
        completed = subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
            cwd=cwd,
        )
    except FileNotFoundError:
        command = argv[0] if argv else ""
        return Observation(
            ok=False,
            summary=f"command not found: {command}",
            data={"cmd": cmd, "returncode": 127},
        )
    except subprocess.TimeoutExpired:
        return Observation(
            ok=False,
            summary="command timed out",
            data={"cmd": cmd, "returncode": 124},
        )
    except OSError as exc:
        return Observation(
            ok=False,
            summary=f"shell execution error: {exc}",
            data={"cmd": cmd},
        )
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    if _is_no_match_exit(argv, completed):
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
    heredoc = _parse_heredoc_command(cmd)
    if heredoc is not None:
        argv, _stdin = heredoc
        if not _is_local_stdin_interpreter_command(argv):
            return GuardianDecision(verdict="block", reason="unsupported heredoc shell command; shell=True is disabled", action=action)
        if profile in {"limited", "power"}:
            return GuardianDecision(verdict="allow", reason=f"local interpreter heredoc allowed by {profile} profile", action=action)
        return GuardianDecision(verdict="ask", reason=f"local interpreter heredoc requires confirmation: {argv[0]}", action=action)
    if _has_heredoc_operator(cmd):
        return GuardianDecision(verdict="block", reason="unterminated heredoc shell command", action=action)
    pipeline = None
    read_chain = _safe_read_chain_spec(cmd)
    if read_chain is not None:
        chain, _tolerate_failure = read_chain
        path_args = [arg for item in chain for arg in item[1:]]
        for path in _candidate_paths(path_args, workspace):
            zone = classify_path(path, workspace, trusted_roots)
            if zone == "protected":
                return GuardianDecision(verdict="block", reason=f"protected path: {path}", action=action)
            if zone == "outside":
                return GuardianDecision(verdict="ask", reason=f"path outside workspace/trusted roots: {path}", action=action)
        return GuardianDecision(verdict="allow", reason=f"read-only shell chain allowed by {profile} profile", action=action)
    rewritten = _rewrite_safe_read_pipeline(cmd)
    if _split_shell_pipes(cmd):
        if rewritten is None:
            pipeline = _safe_read_pipeline_argvs(cmd)
            if pipeline is None:
                return GuardianDecision(verdict="block", reason="unsupported compound shell command; shell=True is disabled", action=action)
        else:
            params = {**action.params, "cmd": shlex.join(rewritten)}
            action = replace(action, params=params)
            cmd = str(action.params["cmd"])
    try:
        argv = pipeline[0] if pipeline is not None else shlex.split(cmd)
    except ValueError as exc:
        return GuardianDecision(verdict="block", reason=f"invalid shell command: {exc}", action=action)

    if not argv:
        return GuardianDecision(verdict="block", reason="empty shell command", action=action)

    invalid_http_server = _invalid_http_server_reason(argv)
    if invalid_http_server:
        return GuardianDecision(verdict="block", reason=invalid_http_server, action=action)

    if _has_blocked_shell_syntax(cmd):
        return GuardianDecision(verdict="ask", reason="compound shell command requires confirmation", action=action)

    command = argv[0]
    if command in READ_COMMANDS:
        unsafe = _unsafe_read_command_reason(argv)
        if unsafe:
            return GuardianDecision(verdict="block", reason=unsafe, action=action)
    is_workspace_write = command in WORKSPACE_WRITE_COMMANDS
    path_args = [arg for item in pipeline for arg in item[1:]] if pipeline is not None else argv[1:]
    for path in _candidate_paths(
        path_args,
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
        if profile == "safe":
            return GuardianDecision(verdict="ask", reason=f"workspace write requires confirmation in safe profile: {command}", action=action)
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

    if _is_git_read_command(argv):
        return GuardianDecision(verdict="allow", reason=f"read-only git command allowed by {profile} profile", action=action)

    if command not in READ_COMMANDS:
        return GuardianDecision(verdict="ask", reason=f"unknown shell command requires confirmation: {command}", action=action)

    if pipeline is not None:
        return GuardianDecision(verdict="allow", reason=f"read-only shell pipeline allowed by {profile} profile", action=action)

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


def _parse_heredoc_command(cmd: str) -> tuple[list[str], str] | None:
    first_line, separator, body = cmd.partition("\n")
    if not separator:
        return None
    try:
        first_argv = shlex.split(first_line)
    except ValueError:
        return None
    heredoc = _heredoc_from_argv(first_argv)
    if heredoc is None:
        return None
    start, end, delimiter, strip_tabs = heredoc
    if not delimiter:
        return None
    argv = [*first_argv[:start], *first_argv[end:]]
    lines = body.splitlines()
    for index, line in enumerate(lines):
        closing = line.lstrip("\t") if strip_tabs else line
        if closing == delimiter:
            stdin = "\n".join(lines[:index])
            if stdin:
                stdin += "\n"
            return argv, stdin
    return None


def _heredoc_from_argv(argv: list[str]) -> tuple[int, int, str, bool] | None:
    for index, arg in enumerate(argv):
        if arg in {"<<", "<<-"}:
            if index + 1 >= len(argv):
                return None
            return index, index + 2, argv[index + 1], arg == "<<-"
        if arg.startswith("<<-") and len(arg) > 3:
            return index, index + 1, arg[3:], True
        if arg.startswith("<<") and len(arg) > 2:
            return index, index + 1, arg[2:], False
    return None


def _has_heredoc_operator(cmd: str) -> bool:
    first_line = cmd.splitlines()[0] if cmd else ""
    try:
        argv = shlex.split(first_line)
    except ValueError:
        return "<<" in first_line
    return _heredoc_from_argv(argv) is not None


def _is_local_stdin_interpreter_command(argv: list[str]) -> bool:
    return len(argv) == 2 and argv[0] in LOCAL_STDIN_INTERPRETERS and argv[1] == "-"


def _is_git_read_command(argv: list[str]) -> bool:
    return len(argv) >= 2 and argv[0] == "git" and argv[1] in GIT_READ_SUBCOMMANDS


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


def _invalid_http_server_reason(argv: list[str]) -> str:
    if len(argv) < 3 or argv[0] not in {"python", "python3"} or argv[1:3] != ["-m", "http.server"]:
        return ""
    index = 3
    while index < len(argv):
        arg = argv[index]
        if arg in {"--bind", "-b", "--directory", "-d"}:
            if index + 1 >= len(argv):
                return f"invalid http server command: missing value for {arg}"
            index += 2
            continue
        if arg.startswith("-"):
            return f"invalid http server command: unsupported option {arg}"
        if not arg.isdigit():
            return f"invalid http server command: port must be numeric, got {arg!r}"
        index += 1
    return ""


def _start_http_server(argv: list[str], *, cwd: Path | None = None) -> Observation:
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
                cwd=cwd,
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
    return _contains_protocol_placeholder(cmd) or _has_unquoted_ellipsis(cmd)


def _argv(action: Action) -> list[str]:
    cmd = str(action.params.get("cmd", "")).strip()
    rewritten = _rewrite_safe_read_pipeline(cmd)
    if rewritten is not None:
        return rewritten
    return shlex.split(cmd)


def _execute_safe_read_pipeline(pipeline: list[list[str]], cmd: str, *, cwd: Path | None = None) -> Observation:
    if len(pipeline) < 2:
        return Observation(ok=False, summary="unsupported compound shell command; shell=True is disabled", data={"cmd": cmd}, retry_policy="block_exact")
    last_stdout = ""
    stderr_parts: list[str] = []
    try:
        for index, argv in enumerate(pipeline):
            completed = subprocess.run(
                argv,
                input=last_stdout if index else None,
                capture_output=True,
                check=False,
                text=True,
                timeout=30,
                cwd=cwd,
            )
            last_stdout = completed.stdout
            if completed.stderr.strip():
                stderr_parts.append(completed.stderr.strip())
            if completed.returncode != 0:
                if _is_no_match_exit(argv, completed):
                    return Observation(
                        ok=True,
                        summary="no matches",
                        data={"cmd": cmd, "returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr},
                    )
                summary = completed.stdout.strip() or completed.stderr.strip() or f"exit code {completed.returncode}"
                return Observation(
                    ok=False,
                    summary=summary,
                    data={"cmd": cmd, "returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr},
                )
    except FileNotFoundError as exc:
        command = exc.filename or ""
        return Observation(ok=False, summary=f"command not found: {command}", data={"cmd": cmd, "returncode": 127})
    except subprocess.TimeoutExpired:
        return Observation(ok=False, summary="command timed out", data={"cmd": cmd, "returncode": 124})
    except OSError as exc:
        return Observation(ok=False, summary=f"shell execution error: {exc}", data={"cmd": cmd})
    stderr = "\n".join(stderr_parts)
    summary = last_stdout.strip() or stderr.strip() or "exit code 0"
    return Observation(ok=True, summary=summary, data={"cmd": cmd, "returncode": 0, "stdout": last_stdout, "stderr": stderr})


def _execute_safe_read_chain(chain: list[list[str]], cmd: str, *, tolerate_failure: bool = False, cwd: Path | None = None) -> Observation:
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    try:
        for argv in chain:
            completed = subprocess.run(
                argv,
                capture_output=True,
                check=False,
                text=True,
                timeout=30,
                cwd=cwd,
            )
            stdout = completed.stdout.strip()
            stderr = completed.stderr.strip()
            if stdout:
                stdout_parts.append(stdout)
            if stderr:
                stderr_parts.append(stderr)
            if completed.returncode != 0:
                if tolerate_failure:
                    summary = "\n".join((*stdout_parts, *stderr_parts)).strip() or "exit code 0"
                    return Observation(
                        ok=True,
                        summary=summary,
                        data={
                            "cmd": cmd,
                            "returncode": 0,
                            "stdout": "\n".join(stdout_parts),
                            "stderr": "\n".join(stderr_parts),
                        },
                    )
                summary = stdout or stderr or f"exit code {completed.returncode}"
                return Observation(
                    ok=False,
                    summary=summary,
                    data={"cmd": cmd, "returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr},
                )
    except FileNotFoundError as exc:
        command = exc.filename or ""
        return Observation(ok=False, summary=f"command not found: {command}", data={"cmd": cmd, "returncode": 127})
    except subprocess.TimeoutExpired:
        return Observation(ok=False, summary="command timed out", data={"cmd": cmd, "returncode": 124})
    except OSError as exc:
        return Observation(ok=False, summary=f"shell execution error: {exc}", data={"cmd": cmd})
    summary = "\n".join((*stdout_parts, *stderr_parts)).strip() or "exit code 0"
    return Observation(ok=True, summary=summary, data={"cmd": cmd, "returncode": 0, "stdout": "\n".join(stdout_parts), "stderr": "\n".join(stderr_parts)})


def _rewrite_safe_read_pipeline(cmd: str) -> list[str] | None:
    parts = _split_shell_pipes(cmd)
    if len(parts) != 2:
        return None
    if _has_blocked_shell_syntax(cmd):
        return None
    try:
        left = shlex.split(parts[0])
        right = shlex.split(parts[1])
    except ValueError:
        return None
    if len(left) == 2 and left[0] == "cat" and right and right[0] in {"head", "tail", "grep"}:
        return [*right, left[1]]
    return None


def _safe_read_pipeline_argvs(cmd: str) -> list[list[str]] | None:
    parts = _split_shell_pipes(cmd)
    if len(parts) < 2 or len(parts) > 4:
        return None
    if _has_blocked_shell_syntax(cmd):
        return None
    try:
        pipeline = [shlex.split(part) for part in parts]
    except ValueError:
        return None
    if not pipeline or any(not argv for argv in pipeline):
        return None
    if all(_is_safe_pipeline_command(argv) for argv in pipeline):
        return pipeline
    return None


def _is_safe_pipeline_command(argv: list[str]) -> bool:
    if not argv:
        return False
    if _is_git_read_command(argv):
        return True
    return argv[0] in READ_COMMANDS and not _unsafe_read_command_reason(argv)


def _unsafe_read_command_reason(argv: list[str]) -> str:
    command = argv[0] if argv else ""
    args = argv[1:]
    if command == "sed" and any(arg == "-i" or arg.startswith("-i") or arg == "--in-place" or arg.startswith("--in-place=") for arg in args):
        return "mutating sed option is not read-only"
    if command == "find" and any(arg in {"-delete", "-exec", "-execdir", "-ok", "-okdir"} for arg in args):
        return "mutating find option is not read-only"
    if command == "sort" and any(arg == "-o" or arg.startswith("-o") or arg == "--output" or arg.startswith("--output=") for arg in args):
        return "mutating sort output option is not read-only"
    return ""


def _is_no_match_exit(argv: list[str], completed: subprocess.CompletedProcess[str]) -> bool:
    return bool(argv) and argv[0] in {"grep", "rg"} and completed.returncode == 1 and not completed.stdout.strip() and not completed.stderr.strip()


def _safe_read_chain_argvs(cmd: str) -> list[list[str]] | None:
    spec = _safe_read_chain_spec(cmd)
    return spec[0] if spec is not None else None


def _safe_read_chain_spec(cmd: str) -> tuple[list[list[str]], bool] | None:
    base_cmd, tolerate_failure = _strip_trailing_or_true(cmd)
    parts = _split_shell_and(cmd)
    if tolerate_failure:
        parts = _split_shell_and(base_cmd)
    if len(parts) < 2 and not tolerate_failure:
        return None
    if _split_shell_pipes(base_cmd):
        return None
    if _has_blocked_shell_syntax(base_cmd, allowed={"&&"}):
        return None
    chain: list[list[str]] = []
    for part in parts:
        try:
            argv = shlex.split(part)
        except ValueError:
            return None
        if not argv:
            return None
        if argv[0] not in READ_COMMANDS and not _is_git_read_command(argv):
            return None
        if argv[0] in READ_COMMANDS and _unsafe_read_command_reason(argv):
            return None
        chain.append(argv)
    return chain, tolerate_failure


def _strip_trailing_or_true(cmd: str) -> tuple[str, bool]:
    parts = _split_shell_or(cmd)
    if len(parts) == 2 and parts[1].strip() == "true":
        return parts[0].strip(), True
    return cmd, False


def _has_blocked_shell_syntax(cmd: str, *, allowed: set[str] | None = None) -> bool:
    allowed = allowed or set()
    for token in sorted(BLOCKED_TOKENS - allowed, key=len, reverse=True):
        if _has_unquoted_token(cmd, token):
            return True
    return False


def _has_unquoted_ellipsis(cmd: str) -> bool:
    return _has_unquoted_token(cmd, "...")


def _has_unquoted_token(cmd: str, token: str) -> bool:
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(cmd):
        char = cmd[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            escaped = True
            index += 1
            continue
        if quote is not None:
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if cmd.startswith(token, index):
            return True
        index += 1
    return False


def _contains_protocol_placeholder(text: str) -> bool:
    index = 0
    while True:
        start = text.find("<", index)
        if start < 0:
            return False
        end = text.find(">", start + 1)
        if end < 0:
            return False
        value = "_".join(text[start + 1 : end].strip().lower().split())
        if value in PLACEHOLDER_NAMES:
            return True
        index = end + 1


def _split_shell_on(cmd: str, delimiter: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    delim_len = len(delimiter)
    index = 0
    while index < len(cmd):
        char = cmd[index]
        if escaped:
            current.append(char)
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            current.append(char)
            escaped = True
            index += 1
            continue
        if quote is not None:
            current.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            current.append(char)
            quote = char
            index += 1
            continue
        if cmd[index : index + delim_len] == delimiter:
            parts.append("".join(current).strip())
            current = []
            index += delim_len
            continue
        current.append(char)
        index += 1
    if parts:
        parts.append("".join(current).strip())
    return parts


def _split_shell_or(cmd: str) -> list[str]:
    return _split_shell_on(cmd, "||")


def _split_shell_and(cmd: str) -> list[str]:
    return _split_shell_on(cmd, "&&")


def _split_shell_pipes(cmd: str) -> list[str]:
    return _split_shell_on(cmd, "|")


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
