"""Detect likely secrets in user text before provider calls."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .store import normalize_secret_name


SECRET_NAME_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASS", "PWD", "CREDENTIAL")
ENV_ASSIGNMENT_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]{2,})\s*=\s*['\"]?([^'\"\s]{12,})['\"]?"
)
TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bsk-or-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bsk-proj-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
)


@dataclass(frozen=True)
class SecretCandidate:
    name: str
    value: str


def detect_secret_candidate(text: str) -> SecretCandidate | None:
    env_match = ENV_ASSIGNMENT_RE.search(text)
    if env_match:
        name = normalize_secret_name(env_match.group(1))
        value = env_match.group(2).strip()
        if _looks_like_secret_name(name) or _looks_like_token(value):
            return SecretCandidate(name=name, value=value)

    for pattern in TOKEN_PATTERNS:
        match = pattern.search(text)
        if match:
            value = match.group(0)
            return SecretCandidate(name=_infer_name(text, value), value=value)
    return None


def _looks_like_secret_name(name: str) -> bool:
    return any(word in name for word in SECRET_NAME_WORDS)


def _looks_like_token(value: str) -> bool:
    return any(pattern.search(value) for pattern in TOKEN_PATTERNS)


def _infer_name(text: str, value: str) -> str:
    low = text.lower()
    if value.startswith("sk-ant-") or "anthropic" in low or "claude" in low:
        return "ANTHROPIC_API_KEY"
    if value.startswith("sk-or-") or "openrouter" in low:
        return "OPENROUTER_API_KEY"
    if value.startswith(("github_pat_", "ghp_", "gho_", "ghu_", "ghs_", "ghr_")) or "github" in low:
        return "GITHUB_TOKEN"
    if value.startswith("glpat-") or "gitlab" in low:
        return "GITLAB_TOKEN"
    if value.startswith("hf_") or "huggingface" in low or "hugging face" in low:
        return "HUGGINGFACE_TOKEN"
    if value.startswith("xox") or "slack" in low:
        return "SLACK_TOKEN"
    if value.startswith("sk-") or "openai" in low or "chatgpt" in low:
        return "OPENAI_API_KEY"
    return "SECRET_VALUE"
