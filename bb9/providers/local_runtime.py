"""Autostart helpers for the experimental local runtime providers."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

LOCAL_RUNTIME_PROVIDERS = {
    "runbb9": "",
    "local-runtime-sglang": "qwen3-14b-awq",
    "local-runtime-llamacpp": "gemma4-e4b-gguf-q4km",
}
RUNTIME_ROOT_ENV = "BB9_LOCAL_RUNTIME_ROOT"
AUTOSTART_ENV = "BB9_LOCAL_RUNTIME_AUTOSTART"
STARTUP_TIMEOUT_ENV = "BB9_LOCAL_RUNTIME_STARTUP_TIMEOUT"


class LocalRuntimeStartError(RuntimeError):
    pass


def is_local_runtime_provider(provider: str) -> bool:
    return provider in LOCAL_RUNTIME_PROVIDERS


def ensure_local_runtime(provider: str, base_url: str, model: str = "", *, startup_timeout: float | None = None) -> None:
    if not is_local_runtime_provider(provider):
        return
    if not _autostart_enabled():
        raise LocalRuntimeStartError(f"local runtime autostart disabled by {AUTOSTART_ENV}")
    if _is_models_endpoint_live(base_url):
        return

    runtime_root = _runtime_root()
    if not runtime_root.is_dir():
        raise LocalRuntimeStartError(f"local runtime root not found: {runtime_root}")

    timeout = startup_timeout if startup_timeout is not None else _startup_timeout()
    command = _serve_command(provider, base_url, model, runtime_root)
    log_path = _log_path(provider, model or LOCAL_RUNTIME_PROVIDERS[provider])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {' '.join(command)}\n")
            log.flush()
            subprocess.Popen(
                command,
                cwd=runtime_root,
                env=_runtime_env(runtime_root),
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except OSError as exc:
        raise LocalRuntimeStartError(f"local runtime start failed: {exc}") from exc

    deadline = time.monotonic() + max(1.0, timeout)
    while time.monotonic() < deadline:
        if _is_models_endpoint_live(base_url):
            return
        time.sleep(1.0)
    raise LocalRuntimeStartError(f"local runtime did not become ready after {timeout:g}s; log: {log_path}")


def _serve_command(provider: str, base_url: str, model: str, runtime_root: Path) -> list[str]:
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port
    if provider == "runbb9":
        command = [
            str(_runtime_python(runtime_root)),
            "-m",
            "runbb9",
            "serve",
            "--host",
            host,
        ]
        if port is not None:
            command.extend(["--port", str(port)])
        return command
    command = [
        str(_runtime_python(runtime_root)),
        "-m",
        "local_runtime.cli",
        "serve",
        "--model",
        model.strip() or LOCAL_RUNTIME_PROVIDERS[provider],
        "--host",
        host,
    ]
    if port is not None:
        command.extend(["--port", str(port)])
    return command


def _is_models_endpoint_live(base_url: str) -> bool:
    try:
        with urlopen(f"{base_url.rstrip('/')}/models", timeout=1.0) as response:
            return 200 <= int(getattr(response, "status", 200)) < 300
    except OSError:
        return False


def _runtime_root() -> Path:
    explicit = os.environ.get(RUNTIME_ROOT_ENV, "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve(strict=False)
    project_root = Path(__file__).resolve().parents[2]
    sibling = project_root.parent / "runtime"
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


def _autostart_enabled() -> bool:
    value = os.environ.get(AUTOSTART_ENV, "").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _startup_timeout() -> float:
    try:
        value = float(os.environ.get(STARTUP_TIMEOUT_ENV, "") or 180)
    except ValueError:
        return 180
    return value if value > 0 else 180


def _log_path(provider: str, model: str) -> Path:
    safe = "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in f"{provider}-{model}")
    return Path(os.environ.get("BB9_HOME", Path.home() / ".bb9")).expanduser() / "local-runtime" / f"{safe}.log"
