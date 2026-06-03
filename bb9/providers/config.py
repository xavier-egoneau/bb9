"""Local provider configuration.

Inspired by the Marius provider_config brick, kept deliberately small for BB9.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from ..core.tool_runtime import load_tool_module
from .auth_flow import OAuthTokenResult

_secret_store = load_tool_module("secret", "store")
if _secret_store is None:
    raise RuntimeError("secret tool store backend not found")
SECRET_REF_PREFIX = _secret_store.SECRET_REF_PREFIX
resolve_named_secret_ref = _secret_store.resolve_secret_ref


AUTH_API = "api"
AUTH_WEB = "web"

USER_CONFIG_DIR = Path(os.environ.get("BB9_HOME", Path.home() / ".bb9")).expanduser()
LEGACY_USER_CONFIG_DIR = Path.home() / ".config" / "bb9"
DEFAULT_PROVIDER_CONFIG_PATH = USER_CONFIG_DIR / "providers.json"
DEFAULT_SECRET_DIR = USER_CONFIG_DIR / "secrets"
_CODEX_MODELS_CACHE = Path.home() / ".codex" / "models_cache.json"
_CHATGPT_FALLBACK_MODELS: tuple[str, ...] = ()
ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


@dataclass(frozen=True)
class ProviderDefinition:
    kind: str
    label: str
    default_base_url: str
    supported_auth_types: tuple[str, ...]
    default_api_key_env: str = ""
    requires_api_key: bool = True
    models_endpoint: str = "/models"
    models_list_key: str = "data"
    model_name_key: str = "id"
    model_id_prefix_filter: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProviderEntry:
    id: str
    name: str
    provider: str
    auth_type: str
    base_url: str = ""
    api_key_ref: str = ""
    model: str = ""
    added_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new_id(cls) -> str:
        return str(uuid4())[:8]


@dataclass(frozen=True)
class ProviderConfig:
    active_id: str = ""
    entries: tuple[ProviderEntry, ...] = ()

    def active_entry(self) -> ProviderEntry | None:
        if not self.active_id:
            return self.entries[0] if len(self.entries) == 1 else None
        return next((entry for entry in self.entries if entry.id == self.active_id), None)


PROVIDER_REGISTRY: dict[str, ProviderDefinition] = {
    "openai": ProviderDefinition(
        kind="openai",
        label="OpenAI / ChatGPT",
        default_base_url="https://api.openai.com/v1",
        supported_auth_types=(AUTH_API, AUTH_WEB),
        default_api_key_env="OPENAI_API_KEY",
        model_id_prefix_filter=("gpt-", "o1", "o3", "o4"),
    ),
    "openrouter": ProviderDefinition(
        kind="openrouter",
        label="OpenRouter",
        default_base_url="https://openrouter.ai/api/v1",
        supported_auth_types=(AUTH_API,),
        default_api_key_env="OPENROUTER_API_KEY",
        requires_api_key=False,
    ),
    "openai-compatible": ProviderDefinition(
        kind="openai-compatible",
        label="OpenAI-compatible manuel",
        default_base_url="https://api.openai.com/v1",
        supported_auth_types=(AUTH_API,),
        default_api_key_env="OPENAI_API_KEY",
    ),
    "ollama-cloud": ProviderDefinition(
        kind="ollama-cloud",
        label="Ollama Cloud",
        default_base_url="https://ollama.com",
        supported_auth_types=(AUTH_API,),
        default_api_key_env="OLLAMA_API_KEY",
        models_endpoint="/api/tags",
        models_list_key="models",
        model_name_key="name",
    ),
    "ollama": ProviderDefinition(
        kind="ollama",
        label="Ollama local",
        default_base_url="http://localhost:11434/v1",
        supported_auth_types=(AUTH_API,),
        requires_api_key=False,
    ),
}


class ModelFetchError(RuntimeError):
    pass


class ProviderStore:
    def __init__(self, path: Path = DEFAULT_PROVIDER_CONFIG_PATH) -> None:
        self.path = path

    def load(self) -> ProviderConfig:
        if not self.path.exists():
            return ProviderConfig()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            entries = tuple(_entry_from_dict(item) for item in raw if isinstance(item, dict))
            active_id = entries[0].id if entries else ""
            return ProviderConfig(active_id=active_id, entries=entries)
        entries = tuple(_entry_from_dict(item) for item in raw.get("providers", []) if isinstance(item, dict))
        return ProviderConfig(active_id=str(raw.get("active_id", "")), entries=entries)

    def save(self, config: ProviderConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "active_id": config.active_id,
            "providers": [_entry_to_dict(entry) for entry in config.entries],
        }
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def upsert(self, entry: ProviderEntry, *, active: bool = True) -> None:
        config = self.load()
        entries = list(config.entries)
        for index, current in enumerate(entries):
            if current.id == entry.id:
                entries[index] = entry
                break
        else:
            entries.append(entry)
        self.save(ProviderConfig(active_id=entry.id if active else config.active_id, entries=tuple(entries)))

    def set_active(self, provider_id: str) -> bool:
        config = self.load()
        if not any(entry.id == provider_id for entry in config.entries):
            return False
        self.save(ProviderConfig(active_id=provider_id, entries=config.entries))
        return True


def default_provider_config_path() -> Path:
    explicit = os.environ.get("BB9_PROVIDER_CONFIG_PATH")
    if explicit:
        return Path(explicit).expanduser()
    migrate_legacy_user_home()
    return DEFAULT_PROVIDER_CONFIG_PATH


def migrate_legacy_user_home() -> bool:
    if USER_CONFIG_DIR == LEGACY_USER_CONFIG_DIR or not LEGACY_USER_CONFIG_DIR.exists():
        return False

    changed = False
    legacy_config = LEGACY_USER_CONFIG_DIR / "providers.json"
    if legacy_config.exists() and not DEFAULT_PROVIDER_CONFIG_PATH.exists():
        USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        config = _migrated_legacy_config(legacy_config)
        DEFAULT_PROVIDER_CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
        changed = True

    legacy_secrets = LEGACY_USER_CONFIG_DIR / "secrets"
    if legacy_secrets.exists():
        DEFAULT_SECRET_DIR.mkdir(parents=True, exist_ok=True)
        for source in legacy_secrets.iterdir():
            target = DEFAULT_SECRET_DIR / source.name
            if source.is_file() and not target.exists():
                shutil.copy2(source, target)
                changed = True
            elif source.is_dir() and not target.exists():
                shutil.copytree(source, target)
                changed = True
    return changed


def _migrated_legacy_config(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"active_id": "", "providers": []}

    if isinstance(raw, list):
        active_id = str(raw[0].get("id", "")) if raw and isinstance(raw[0], dict) else ""
        entries = [item for item in raw if isinstance(item, dict)]
    elif isinstance(raw, dict):
        active_id = str(raw.get("active_id") or "")
        providers = raw.get("providers")
        entries = providers if isinstance(providers, list) else []
    else:
        return {"active_id": "", "providers": []}

    return {
        "active_id": active_id,
        "providers": [_migrated_legacy_entry(entry) for entry in entries if isinstance(entry, dict)],
    }


def _migrated_legacy_entry(entry: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(entry)
    metadata = migrated.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        migrated["metadata"] = metadata

    token_path = str(metadata.get("token_path") or "").strip()
    if not token_path:
        return migrated

    source_token = Path(token_path).expanduser()
    if not source_token.is_absolute():
        source_token = LEGACY_USER_CONFIG_DIR / source_token
    if not source_token.exists():
        return migrated

    DEFAULT_SECRET_DIR.mkdir(parents=True, exist_ok=True)
    target_token = DEFAULT_SECRET_DIR / source_token.name
    if not target_token.exists():
        shutil.copy2(source_token, target_token)
    metadata["token_path"] = str(target_token)
    return migrated


def normalize_base_url(provider: str, base_url: str) -> str:
    definition = PROVIDER_REGISTRY.get(provider)
    raw = str(base_url or (definition.default_base_url if definition else "")).strip().rstrip("/")
    if not raw:
        return raw
    parsed = urlparse(raw)
    if provider == "ollama" and parsed.netloc in {"localhost:11434", "127.0.0.1:11434"} and parsed.path in {"", "/"}:
        return raw + "/v1"
    if provider == "openai" and parsed.netloc == "api.openai.com" and parsed.path in {"", "/"}:
        return raw + "/v1"
    if provider == "openrouter" and parsed.netloc == "openrouter.ai" and parsed.path in {"", "/"}:
        return raw + "/api/v1"
    return raw


def resolve_secret_ref(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if text.startswith("env:"):
        return os.environ.get(text[4:].strip(), "").strip()
    if text.startswith("file:"):
        path = Path(text[5:].strip()).expanduser()
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    if text.startswith(SECRET_REF_PREFIX):
        return resolve_named_secret_ref(text)
    return text


def public_secret_label(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if text.startswith(("env:", "file:", SECRET_REF_PREFIX)):
        return text
    return "<raw-secret>"


def normalize_api_key_ref_input(
    value: str,
    *,
    default_ref: str = "",
    secret_name: str = "PROVIDER_API_KEY",
    store: Any | None = None,
) -> tuple[str, str]:
    text = (value or "").strip()
    if not text:
        return default_ref, ""
    if text.startswith(("env:", "file:", SECRET_REF_PREFIX)):
        return text, ""
    if ENV_NAME_RE.match(text):
        return f"env:{text}", ""

    secret_store = store or _secret_store.SecretStore()
    stored = secret_store.set(secret_name, text)
    ref = f"{SECRET_REF_PREFIX}{stored}"
    return ref, f"Secret stocke localement: {ref}"


def default_web_token_path(provider_id: str) -> Path:
    return DEFAULT_SECRET_DIR / f"provider-{provider_id}.json"


def write_web_token(path: Path, token: OAuthTokenResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "access_token": token.access_token,
        "refresh_token": token.refresh_token,
        "expires": token.expires,
        "obtained_at": token.obtained_at,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def read_web_token(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def update_web_token(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def fetch_models(entry: ProviderEntry, *, timeout: float = 10.0) -> list[str]:
    if entry.provider == "openai" and entry.auth_type == AUTH_WEB:
        return fetch_codex_models_cache()

    definition = PROVIDER_REGISTRY.get(entry.provider)
    if definition is None:
        raise ModelFetchError(f"Provider inconnu: {entry.provider}")

    api_key = resolve_secret_ref(entry.api_key_ref)
    if definition.requires_api_key and not api_key:
        raise ModelFetchError(f"Secret absent: {entry.api_key_ref or 'aucun secret configure'}")

    url = normalize_base_url(entry.provider, entry.base_url) + definition.models_endpoint
    request = Request(url, headers={"Content-Type": "application/json"})
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")

    try:
        with urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ModelFetchError(f"HTTP {exc.code}: {detail[:240]}") from exc
    except URLError as exc:
        raise ModelFetchError(f"Connexion impossible: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ModelFetchError("Timeout pendant la recuperation des modeles") from exc
    except json.JSONDecodeError as exc:
        raise ModelFetchError("Reponse modeles invalide: JSON attendu") from exc

    raw_models = body.get(definition.models_list_key, [])
    models = [
        str(item.get(definition.model_name_key, "")).strip()
        for item in raw_models
        if isinstance(item, dict)
    ]
    models = [model for model in models if model]
    if definition.model_id_prefix_filter:
        models = [
            model
            for model in models
            if any(model.startswith(prefix) for prefix in definition.model_id_prefix_filter)
        ]
    return sorted(dict.fromkeys(models))


def fetch_codex_models_cache(cache_path: Path = _CODEX_MODELS_CACHE) -> list[str]:
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return list(_CHATGPT_FALLBACK_MODELS)

    rows: list[tuple[int, str]] = []
    for item in data.get("models", []):
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "").strip()
        visibility = str(item.get("visibility") or "list")
        priority = item.get("priority")
        if slug and visibility in {"list", "default", ""}:
            rows.append((priority if isinstance(priority, int) else 999, slug))
    rows.sort()
    models = [slug for _, slug in rows]
    return models or list(_CHATGPT_FALLBACK_MODELS)


def _entry_to_dict(entry: ProviderEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "name": entry.name,
        "provider": entry.provider,
        "auth_type": entry.auth_type,
        "base_url": entry.base_url,
        "api_key_ref": entry.api_key_ref,
        "model": entry.model,
        "added_at": entry.added_at,
        "metadata": entry.metadata,
    }


def _entry_from_dict(data: dict[str, Any]) -> ProviderEntry:
    return ProviderEntry(
        id=str(data.get("id") or ProviderEntry.new_id()),
        name=str(data.get("name") or data.get("provider") or "provider"),
        provider=str(data.get("provider") or "openai-compatible"),
        auth_type=str(data.get("auth_type") or AUTH_API),
        base_url=str(data.get("base_url") or ""),
        api_key_ref=str(data.get("api_key_ref") or data.get("api_key") or ""),
        model=str(data.get("model") or ""),
        added_at=str(data.get("added_at") or ""),
        metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
    )
