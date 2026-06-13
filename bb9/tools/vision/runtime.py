"""Vision tool runtime — Ollama-based image description."""

from __future__ import annotations

import base64
import json
import shlex
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from bb9.core.models import Action, GuardianDecision, Observation, Risk, RunContext

DEFAULT_MODEL = "gemma4:latest"
DEFAULT_URL = "http://localhost:11434"
DEFAULT_TIMEOUT = 120
DEFAULT_NUM_PREDICT = 512
SUPPORTED_FORMATS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def action_from_text(text: str) -> Action:
    argv = shlex.split(text.strip())
    op = argv[0].lower() if argv else "describe"
    params: dict[str, Any] = {"op": op}
    for arg in argv[1:]:
        if "=" in arg:
            key, _, value = arg.partition("=")
            params[key.strip()] = value.strip()
        elif not str(params.get("path") or ""):
            params["path"] = arg
    risk: Risk = "low"
    return Action(name="vision", params=params, risk=risk)


def review(action: Action, context: RunContext) -> GuardianDecision:
    return GuardianDecision(verdict="allow", reason="vision tool is read-only", action=action)


def execute(action: Action) -> Observation:
    op = str(action.params.get("op", "describe")).strip().lower()
    if op != "describe":
        return Observation(ok=False, summary=f"unsupported vision operation: {op}")

    path = str(action.params.get("path", "")).strip()
    if not path:
        return Observation(ok=False, summary="missing image path")

    image = Path(path).expanduser()
    if not image.is_absolute():
        image = Path.cwd() / image
    image = image.resolve()

    if not image.is_file():
        return Observation(ok=False, summary=f"image not found: {path}")
    if image.suffix.lower() not in SUPPORTED_FORMATS:
        return Observation(ok=False, summary=f"unsupported image format: {image.suffix}")

    read_started = time.perf_counter()
    try:
        image_bytes = image.read_bytes()
    except OSError as exc:
        return Observation(ok=False, summary=f"cannot read image: {exc}")
    read_elapsed = time.perf_counter() - read_started

    prompt = str(action.params.get("prompt", "")).strip()
    if not prompt:
        prompt = (
            "Reponds directement, sans raisonnement. Decris cette image de facon factuelle : "
            "contenu, elements visuels, texte present, disposition, couleurs et style."
        )

    config = _vision_config()
    model = config["model"]
    base_url = str(config["url"])
    timeout = int(config["timeout"])
    num_predict = int(config["num_predict"])
    b64 = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [b64],
            }
        ],
        "stream": False,
        "think": False,
        "options": {
            "num_predict": num_predict,
            "temperature": 0,
        },
    }

    request_elapsed = 0.0
    try:
        request = Request(
            f"{base_url.rstrip('/')}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        request_started = time.perf_counter()
        with urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        request_elapsed = time.perf_counter() - request_started
    except HTTPError as exc:
        request_elapsed = time.perf_counter() - request_started if "request_started" in locals() else 0.0
        error_body = _read_http_error(exc)
        return Observation(
            ok=False,
            summary=f"Ollama vision HTTP {exc.code} at {base_url}: {error_body}",
            data={
                "cmd": f"vision describe {path}",
                "returncode": exc.code,
                "model": model,
                "path": str(image),
                "elapsed_s": round(request_elapsed, 3),
            },
        )
    except URLError as exc:
        return Observation(
            ok=False,
            summary=f"Ollama vision unreachable at {base_url}: {exc.reason}. Lance `ollama serve`.",
            data={"cmd": f"vision describe {path}", "returncode": 1},
        )
    except (json.JSONDecodeError, OSError) as exc:
        request_elapsed = time.perf_counter() - request_started if "request_started" in locals() else 0.0
        return Observation(
            ok=False,
            summary=f"Ollama vision error: {exc}",
            data={
                "cmd": f"vision describe {path}",
                "returncode": 1,
                "model": model,
                "path": str(image),
                "elapsed_s": round(request_elapsed, 3),
            },
        )

    message = body.get("message")
    content = message.get("content") if isinstance(message, dict) else body.get("response")
    if not isinstance(content, str) or not content.strip():
        thinking = message.get("thinking") if isinstance(message, dict) else ""
        hint = ""
        if isinstance(thinking, str) and thinking.strip():
            hint = " Le modele a produit du raisonnement interne mais pas de description finale."
        return Observation(
            ok=False,
            summary=(
                f"Le modele vision {model} a retourne une reponse vide."
                f"{hint} Verifie le modele configure dans ~/.bb9/settings.json ou relance l'action."
            ),
            data={
                "model": model,
                "path": str(image),
                "image_bytes": len(image_bytes),
                "read_elapsed_s": round(read_elapsed, 3),
                "ollama_elapsed_s": round(request_elapsed, 3),
                "done_reason": body.get("done_reason"),
            },
        )

    return Observation(
        ok=True,
        summary=content.strip(),
        data={
            "model": model,
            "path": str(image),
            "image_bytes": len(image_bytes),
            "read_elapsed_s": round(read_elapsed, 3),
            "ollama_elapsed_s": round(request_elapsed, 3),
            "done_reason": body.get("done_reason"),
        },
    )


def _vision_config() -> dict[str, str | int]:
    settings_path = Path.home() / ".bb9" / "settings.json"
    model = DEFAULT_MODEL
    base_url = DEFAULT_URL
    timeout = DEFAULT_TIMEOUT
    num_predict = DEFAULT_NUM_PREDICT
    try:
        if settings_path.is_file():
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            vision = data.get("vision", {})
            if isinstance(vision, dict):
                model = str(vision.get("model") or DEFAULT_MODEL)
                base_url = str(vision.get("url") or DEFAULT_URL)
                timeout = _positive_int(vision.get("timeout"), DEFAULT_TIMEOUT)
                num_predict = _positive_int(vision.get("num_predict"), DEFAULT_NUM_PREDICT)
    except (json.JSONDecodeError, OSError):
        pass
    return {"model": model, "url": base_url, "timeout": timeout, "num_predict": num_predict}


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _read_http_error(exc: HTTPError) -> str:
    try:
        raw = exc.read().decode("utf-8", errors="replace").strip()
    except OSError:
        raw = ""
    if not raw:
        return exc.reason or "HTTP error"
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return raw[:1000]
    error = body.get("error") if isinstance(body, dict) else None
    return str(error or raw)[:1000]
