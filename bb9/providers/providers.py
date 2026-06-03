"""Model provider adapters."""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..core.attachments import ImageAttachment
from .auth_flow import refresh_token
from .config import (
    AUTH_API,
    AUTH_WEB,
    PROVIDER_REGISTRY,
    ProviderEntry,
    normalize_base_url,
    read_web_token,
    resolve_secret_ref,
    update_web_token,
)


class Provider(Protocol):
    def complete(self, prompt: str, *, images: tuple[ImageAttachment, ...] = ()) -> str:
        ...


@dataclass(frozen=True)
class OpenAICompatibleProvider:
    model: str
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    api_key_ref: str = ""
    require_api_key: bool = True
    reasoning_effort: str = ""
    timeout: float = 60.0

    def complete(self, prompt: str, *, images: tuple[ImageAttachment, ...] = ()) -> str:
        api_key = resolve_secret_ref(self.api_key_ref) if self.api_key_ref else os.getenv(self.api_key_env)
        if not api_key and self.require_api_key:
            secret = self.api_key_ref or f"env:{self.api_key_env}"
            raise ProviderError(f"Missing API key: {secret}")

        content: str | list[dict[str, Any]]
        if images:
            content = [{"type": "text", "text": prompt}]
            content.extend({"type": "image_url", "image_url": {"url": image.as_data_url()}} for image in images)
        else:
            content = prompt
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
        }
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        request = Request(
            f"{self.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
            },
            method="POST",
        )
        if api_key:
            request.add_header("Authorization", f"Bearer {api_key}")

        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ProviderError(f"Provider HTTP error {exc.code}: {detail}") from exc
        except URLError as exc:
            raise ProviderError(f"Provider connection error: {exc.reason}") from exc
        except TimeoutError as exc:
            raise ProviderError("Provider request timed out") from exc

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("Provider response did not match chat completions format") from exc

        if not isinstance(content, str):
            raise ProviderError("Provider response content is not text")
        return content


@dataclass(frozen=True)
class OllamaProvider:
    model: str
    base_url: str = "https://ollama.com"
    api_key_ref: str = "env:OLLAMA_API_KEY"
    timeout: float = 120.0

    def complete(self, prompt: str, *, images: tuple[ImageAttachment, ...] = ()) -> str:
        api_key = resolve_secret_ref(self.api_key_ref)
        if not api_key:
            raise ProviderError(f"Missing API key: {self.api_key_ref or 'env:OLLAMA_API_KEY'}")
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        if images:
            payload["messages"][0]["images"] = [base64.b64encode(image.path.read_bytes()).decode("ascii") for image in images]
        request = Request(
            f"{self.base_url.rstrip('/')}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ProviderError(f"Ollama HTTP error {exc.code}: {detail}") from exc
        except URLError as exc:
            raise ProviderError(f"Ollama connection error: {exc.reason}") from exc
        except TimeoutError as exc:
            raise ProviderError("Ollama request timed out") from exc
        except json.JSONDecodeError as exc:
            raise ProviderError("Ollama response did not contain JSON") from exc

        message = body.get("message")
        content = message.get("content") if isinstance(message, dict) else body.get("response")
        if not isinstance(content, str):
            raise ProviderError("Ollama response did not contain text content")
        return content


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChatGPTWebProvider:
    model: str
    token_path: Path
    reasoning_effort: str = ""
    timeout: float = 120.0

    def complete(self, prompt: str, *, images: tuple[ImageAttachment, ...] = ()) -> str:
        token = self._fresh_token()
        content = [{"type": "input_text", "text": prompt}]
        content.extend({"type": "input_image", "image_url": image.as_data_url()} for image in images)
        payload = {
            "model": self.model,
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": content,
                }
            ],
            "instructions": "You are BB9, a concise helpful assistant.",
            "stream": True,
            "store": False,
        }
        if self.reasoning_effort:
            payload["reasoning"] = {"effort": self.reasoning_effort}
        request = Request(
            "https://chatgpt.com/backend-api/codex/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers=_chatgpt_headers(token),
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return _read_chatgpt_stream(response)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ProviderError(f"ChatGPT web HTTP error {exc.code}: {detail}") from exc
        except URLError as exc:
            raise ProviderError(f"ChatGPT web connection error: {exc.reason}") from exc
        except TimeoutError as exc:
            raise ProviderError("ChatGPT web request timed out") from exc

    def _fresh_token(self) -> str:
        data = read_web_token(self.token_path)
        access_token = str(data.get("access_token") or "").strip()
        if not access_token:
            raise ProviderError(f"Missing ChatGPT web token: {self.token_path}")

        try:
            expires = float(data.get("expires") or 0)
        except (TypeError, ValueError):
            expires = 0

        if not expires or expires > time.time() + 60:
            return access_token

        refresh = str(data.get("refresh_token") or "").strip()
        if not refresh:
            return access_token

        try:
            raw = refresh_token(refresh)
        except Exception as exc:
            raise ProviderError(f"ChatGPT web token refresh failed: {exc}") from exc

        new_access = str(raw.get("access_token") or "").strip()
        if not new_access:
            raise ProviderError("ChatGPT web token refresh returned no access token")

        new_data = dict(data)
        new_data["access_token"] = new_access
        if raw.get("refresh_token"):
            new_data["refresh_token"] = str(raw["refresh_token"])
        try:
            expires_in = float(raw.get("expires_in") or 0)
        except (TypeError, ValueError):
            expires_in = 0
        if expires_in:
            new_data["expires"] = time.time() + expires_in
        new_data["obtained_at"] = datetime.now(UTC).isoformat()
        update_web_token(self.token_path, new_data)
        return new_access


def provider_from_entry(entry: ProviderEntry) -> Provider:
    if entry.auth_type == AUTH_WEB:
        token_path = str(entry.metadata.get("token_path") or "").strip()
        if not token_path:
            raise ProviderError(f"Missing token_path for web provider: {entry.name}")
        if not entry.model:
            raise ProviderError(f"Missing model for provider: {entry.name}")
        return ChatGPTWebProvider(
            model=entry.model,
            token_path=Path(token_path),
            reasoning_effort=str(entry.metadata.get("reasoning_effort") or "").strip(),
        )
    if entry.auth_type != AUTH_API:
        raise ProviderError(f"Unsupported auth type: {entry.auth_type}")
    if not entry.model:
        raise ProviderError(f"Missing model for provider: {entry.name}")
    if entry.provider == "ollama-cloud":
        return OllamaProvider(
            model=entry.model,
            base_url=normalize_base_url(entry.provider, entry.base_url),
            api_key_ref=entry.api_key_ref or "env:OLLAMA_API_KEY",
        )
    definition = PROVIDER_REGISTRY.get(entry.provider)
    return OpenAICompatibleProvider(
        model=entry.model,
        base_url=normalize_base_url(entry.provider, entry.base_url),
        api_key_ref=entry.api_key_ref,
        require_api_key=definition.requires_api_key if definition is not None else True,
        reasoning_effort=str(entry.metadata.get("reasoning_effort") or "").strip(),
    )


def _chatgpt_headers(token: str) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "OpenAI-Beta": "responses=experimental",
        "originator": "codex_cli_rs",
    }
    account_id = _chatgpt_account_id(token)
    if account_id:
        headers["chatgpt-account-id"] = account_id
    return headers


def _chatgpt_account_id(token: str) -> str:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        auth_claim = data.get("https://api.openai.com/auth", {})
        return str(auth_claim.get("chatgpt_account_id") or auth_claim.get("user_id") or "")
    except Exception:
        return ""


def _read_chatgpt_stream(response: Any) -> str:
    parts: list[str] = []
    completed_text = ""
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        event_type = event.get("type")
        if event_type == "response.output_text.delta":
            delta = event.get("delta") or ""
            if isinstance(delta, str):
                parts.append(delta)
        elif event_type == "response.output_text.done":
            text = event.get("text") or ""
            if isinstance(text, str):
                completed_text = text
        elif event_type == "response.completed":
            text = _chatgpt_text_from_response(event.get("response") or {})
            if text:
                completed_text = text
        elif event_type == "response.failed":
            error = event.get("error") or {}
            raise ProviderError(f"ChatGPT web error: {error.get('message') or 'provider_error'}")

    if parts:
        return "".join(parts)
    return completed_text


def _chatgpt_text_from_response(response: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in response.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            if content.get("type") in {"output_text", "text"}:
                text = content.get("text") or ""
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts)
