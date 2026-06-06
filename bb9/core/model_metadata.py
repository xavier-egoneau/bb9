"""Model metadata lookup for context budgeting."""

from __future__ import annotations

import json
import re
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from html import unescape
from pathlib import Path

from .paths import bb9_home

DEFAULT_CONTEXT_WINDOW = 250_000
DEFAULT_AUTO_COMPACT_AT = 0.80
CACHE_TTL = timedelta(days=30)
CACHE_FILE = "model-metadata.json"


@dataclass(frozen=True)
class ModelMetadata:
    model: str
    context_window_tokens: int = DEFAULT_CONTEXT_WINDOW
    soft_input_limit_tokens: int = 0
    source: str = "fallback"
    fetched_at: str = ""


def resolve_model_metadata(
    model: str,
    *,
    cache_path: Path | None = None,
) -> ModelMetadata:
    name = model.strip()
    if not name:
        return ModelMetadata(model="")
    lookup_name = _canonical_model_name(name)

    path = cache_path or default_cache_path()
    cached = _cache_get(path, name) or _cache_get(path, lookup_name)
    if cached is not None and not _is_stale(cached.fetched_at):
        return replace(cached, model=name)

    fallback = replace(_known_metadata(lookup_name), model=name)
    with suppress(OSError):
        _cache_set(path, fallback)
    return fallback


def compaction_window_for_model(model: str) -> int:
    return resolve_model_metadata(model).context_window_tokens


def default_cache_path() -> Path:
    return bb9_home() / CACHE_FILE


def _metadata_from_openai_doc(model: str, body: str, *, source: str) -> ModelMetadata:
    plain = unescape(re.sub(r"<[^>]+>", " ", body))
    plain = re.sub(r"\s+", " ", plain)
    context_window = _first_int_before(plain, "context window") or DEFAULT_CONTEXT_WINDOW
    soft_limit = _first_int_after(plain, "prompts with >") or 0
    return ModelMetadata(
        model=model,
        context_window_tokens=context_window,
        soft_input_limit_tokens=soft_limit,
        source=source,
        fetched_at=_now(),
    )


def _first_int_before(text: str, marker: str) -> int:
    match = re.search(re.escape(marker), text, flags=re.IGNORECASE)
    if not match:
        return 0
    prefix = text[max(0, match.start() - 80):match.start()]
    numbers = re.findall(r"[0-9]+(?:[,.][0-9]+)*(?:[KkMm])?", prefix)
    if not numbers:
        return 0
    return _parse_number(numbers[-1])


def _first_int_after(text: str, marker: str) -> int:
    pattern = rf"{re.escape(marker)}\s*([0-9][0-9,. ]*[KkMm]?)"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return 0
    return _parse_number(match.group(1))


def _parse_number(raw: str) -> int:
    text = raw.strip().replace(",", "").replace(" ", "")
    multiplier = 1
    if text.lower().endswith("k"):
        multiplier = 1_000
        text = text[:-1]
    elif text.lower().endswith("m"):
        multiplier = 1_000_000
        text = text[:-1]
    try:
        return int(float(text) * multiplier)
    except ValueError:
        return 0


def _known_metadata(model: str) -> ModelMetadata:
    normalized = model.strip().lower()
    known: dict[str, tuple[int, int]] = {
        "gpt-5.5": (1_050_000, 272_000),
        "gpt-5.5-pro": (1_050_000, 272_000),
        "gpt-5.4": (1_050_000, 272_000),
        "gpt-5.4-pro": (1_050_000, 272_000),
        "gpt-5.4-mini": (1_050_000, 272_000),
        "gpt-5.4-nano": (400_000, 0),
        "gpt-5-chat-latest": (128_000, 0),
        "chatgpt-4o-latest": (128_000, 0),
        "o3": (200_000, 0),
    }
    context_window, soft_limit = known.get(normalized, (DEFAULT_CONTEXT_WINDOW, 0))
    return ModelMetadata(
        model=model,
        context_window_tokens=context_window,
        soft_input_limit_tokens=soft_limit,
        source="known" if normalized in known else "fallback",
        fetched_at=_now(),
    )


def _canonical_model_name(model: str) -> str:
    normalized = model.strip().lower()
    if "/" in normalized:
        normalized = normalized.rsplit("/", 1)[-1]
    return normalized


def _cache_get(path: Path, model: str) -> ModelMetadata | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    item = raw.get(model)
    if not isinstance(item, dict):
        item = raw.get(model.lower())
    if not isinstance(item, dict):
        return None
    return ModelMetadata(
        model=str(item.get("model") or model),
        context_window_tokens=int(item.get("context_window_tokens") or DEFAULT_CONTEXT_WINDOW),
        soft_input_limit_tokens=int(item.get("soft_input_limit_tokens") or 0),
        source=str(item.get("source") or "cache"),
        fetched_at=str(item.get("fetched_at") or ""),
    )


def _cache_set(path: Path, metadata: ModelMetadata) -> None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    raw[metadata.model] = {
        "model": metadata.model,
        "context_window_tokens": metadata.context_window_tokens,
        "soft_input_limit_tokens": metadata.soft_input_limit_tokens,
        "source": metadata.source,
        "fetched_at": metadata.fetched_at or _now(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _is_stale(fetched_at: str) -> bool:
    if not fetched_at:
        return True
    try:
        then = datetime.fromisoformat(fetched_at)
    except ValueError:
        return True
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    return datetime.now(UTC) - then > CACHE_TTL


def _now() -> str:
    return datetime.now(UTC).isoformat()
