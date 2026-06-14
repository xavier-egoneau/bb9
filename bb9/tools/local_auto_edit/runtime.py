"""Local runtime auto-edit tool wrapper."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

from bb9.core.models import Action, GuardianDecision, Observation, RunContext
from bb9.core.trust import TrustedRoots, classify_path
from bb9.core.utils import truthy

REPEATED_KEYS = {"file", "test_command", "backend_arg", "vllm_arg", "sglang_arg", "llama_arg"}
OPTION_KEYS = {
    "workload": "--workload",
    "model_alias": "--model-alias",
    "backend": "--backend",
    "profile": "--profile",
    "host": "--host",
    "port": "--port",
    "dtype": "--dtype",
    "quantization": "--quantization",
    "tokenizer": "--tokenizer",
    "model_id": "--model-id",
    "served_name": "--served-name",
    "max_model_len": "--max-model-len",
    "gpu_memory_utilization": "--gpu-memory-utilization",
    "max_tokens": "--max-tokens",
    "temperature": "--temperature",
    "context_window": "--context-window",
    "timeout": "--timeout",
    "startup_timeout": "--startup-timeout",
    "extra_body_json": "--extra-body-json",
}
REPEATED_OPTION_KEYS = {
    "test_command": "--test-command",
    "backend_arg": "--backend-arg",
    "vllm_arg": "--vllm-arg",
    "sglang_arg": "--sglang-arg",
    "llama_arg": "--llama-arg",
}
RUNTIME_ROOT_ENV = "BB9_LOCAL_RUNTIME_ROOT"


def action_from_text(text: str) -> Action:
    raw = text.strip()
    try:
        argv = shlex.split(raw)
    except ValueError as exc:
        return Action(name="local_auto_edit", params={"op": "invalid", "parse_error": str(exc)}, risk="forbidden")
    op = argv[0].lower() if argv else "run"
    params = _parse_params(argv[1:])
    params["op"] = op
    if op != "run" or not str(params.get("prompt") or "").strip() or not _as_list(params.get("file")):
        return Action(name="local_auto_edit", params=params, risk="forbidden")
    return Action(name="local_auto_edit", params=params, risk="high" if truthy(params.get("apply")) else "medium")


def review(action: Action, context: RunContext) -> GuardianDecision:
    if str(action.params.get("op") or "").strip().lower() != "run":
        return GuardianDecision(verdict="block", reason="invalid local_auto_edit action", action=action)
    if not str(action.params.get("prompt") or "").strip():
        return GuardianDecision(verdict="block", reason="local_auto_edit missing prompt", action=action)
    if not _as_list(action.params.get("file")):
        return GuardianDecision(verdict="block", reason="local_auto_edit missing file", action=action)

    workspace = _workspace_from_action(action, context)
    trusted_roots = context.trusted_roots or TrustedRoots()
    zone = classify_path(workspace, context.workspace.root, trusted_roots)
    if zone == "protected":
        return GuardianDecision(verdict="block", reason=f"protected workspace: {workspace}", action=action)
    if zone == "outside":
        return GuardianDecision(verdict="ask", reason=f"workspace outside active workspace/trusted roots: {workspace}", action=action)
    for file_path in _file_paths(action, workspace):
        file_zone = classify_path(file_path, context.workspace.root, trusted_roots)
        if file_zone == "protected":
            return GuardianDecision(verdict="block", reason=f"protected file: {file_path}", action=action)
        if file_zone == "outside":
            return GuardianDecision(verdict="ask", reason=f"file outside active workspace/trusted roots: {file_path}", action=action)

    if truthy(action.params.get("apply")):
        if context.permission_profile in {"limited", "power"}:
            return GuardianDecision(verdict="allow", reason=f"local auto-edit apply allowed by {context.permission_profile} profile", action=action)
        return GuardianDecision(verdict="ask", reason="local auto-edit apply can modify files", action=action)
    return GuardianDecision(verdict="allow", reason="local auto-edit dry-run is bounded", action=action)


def execute(action: Action, context: RunContext | None = None) -> Observation:
    workspace = _workspace_from_action(action, context)
    runtime_root = _runtime_root(action, workspace)
    if not runtime_root.is_dir():
        return Observation(
            ok=False,
            summary=f"local runtime root not found: {runtime_root}",
            data={"runtime_root": str(runtime_root)},
            retry_policy="recoverable",
        )
    python = _runtime_python(runtime_root)
    if not python.exists() and python.name != "python3":
        return Observation(
            ok=False,
            summary=f"local runtime python not found: {python}",
            data={"runtime_root": str(runtime_root), "python": str(python)},
            retry_policy="recoverable",
        )

    argv = _command(action, workspace, runtime_root, python)
    env = _runtime_env(runtime_root)
    timeout = _positive_float(action.params.get("process_timeout"), default=1800.0)
    try:
        completed = subprocess.run(
            argv,
            cwd=runtime_root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return Observation(
            ok=False,
            summary=f"local auto-edit timed out after {timeout:g}s",
            data={"cmd": shlex.join(argv), "stdout": exc.stdout or "", "stderr": exc.stderr or ""},
            retry_policy="recoverable",
        )
    except OSError as exc:
        return Observation(ok=False, summary=f"local auto-edit failed: {exc}", data={"cmd": shlex.join(argv)})

    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    summary = _summary(completed.returncode, stdout, stderr)
    return Observation(
        ok=completed.returncode == 0,
        summary=summary,
        data={
            "cmd": shlex.join(argv),
            "returncode": completed.returncode,
            "workspace": str(workspace),
            "runtime_root": str(runtime_root),
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "applied": truthy(action.params.get("apply")),
        },
        retry_policy="allow" if completed.returncode == 0 else "recoverable",
    )


def _parse_params(parts: list[str]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    positional: list[str] = []
    for part in parts:
        if "=" not in part:
            positional.append(part)
            continue
        key, value = part.split("=", 1)
        key = key.strip().lower().replace("-", "_")
        value = value.strip()
        if key in REPEATED_KEYS:
            params.setdefault(key, []).append(value)
        else:
            params[key] = value
    if positional and "prompt" not in params:
        params["prompt"] = " ".join(positional)
    return params


def _command(action: Action, workspace: Path, runtime_root: Path, python: Path) -> list[str]:
    params = action.params
    argv = [str(python), "-m", "local_runtime.cli", "auto-edit", "--workspace", str(workspace)]
    for key, flag in OPTION_KEYS.items():
        value = str(params.get(key) or "").strip()
        if value:
            argv.extend([flag, value])
    argv.extend(["--prompt", str(params.get("prompt") or "").strip()])
    for file_name in _as_list(params.get("file")):
        argv.extend(["--file", file_name])
    for key, flag in REPEATED_OPTION_KEYS.items():
        for value in _as_list(params.get(key)):
            argv.extend([flag, value])
    if truthy(params.get("apply")):
        argv.append("--apply")
    if truthy(params.get("disable_thinking")):
        argv.append("--disable-thinking")
    return argv


def _workspace_from_action(action: Action, context: RunContext | None) -> Path:
    raw = str(action.params.get("workspace") or "").strip()
    base = context.workspace.root if context is not None else Path.cwd()
    path = Path(raw).expanduser() if raw else base
    if not path.is_absolute():
        path = base / path
    return path.resolve(strict=False)


def _file_paths(action: Action, workspace: Path) -> list[Path]:
    paths: list[Path] = []
    for raw in _as_list(action.params.get("file")):
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = workspace / path
        paths.append(path.resolve(strict=False))
    return paths


def _runtime_root(action: Action, workspace: Path) -> Path:
    raw = str(action.params.get("runtime_root") or os.environ.get(RUNTIME_ROOT_ENV) or "").strip()
    if raw:
        return Path(raw).expanduser().resolve(strict=False)
    sibling = workspace.parent / "runtime"
    if sibling.is_dir():
        return sibling.resolve(strict=False)
    return (Path.cwd().parent / "runtime").resolve(strict=False)


def _runtime_python(runtime_root: Path) -> Path:
    for relative in (".venv-sglang/bin/python", ".venv/bin/python"):
        candidate = runtime_root / relative
        if candidate.exists():
            return candidate
    return Path("python3")


def _runtime_env(runtime_root: Path) -> dict[str, str]:
    env = dict(os.environ)
    src = str(runtime_root / "src")
    current = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(part for part in (src, current) if part)
    return env


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _positive_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _summary(returncode: int, stdout: str, stderr: str) -> str:
    title = "local auto-edit completed" if returncode == 0 else f"local auto-edit failed ({returncode})"
    output = stdout or stderr
    if not output:
        return title
    return f"{title}\n\n{_truncate(output, 6000)}"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 80].rstrip() + "\n... [truncated]"
