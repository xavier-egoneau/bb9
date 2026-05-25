"""Standalone web tool runtime."""

from __future__ import annotations

import html
import ipaddress
import json
import os
import re
import shlex
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from bb9.core.models import Action, GuardianDecision, Observation, RunContext


USER_AGENT = "BB9/0.1 (+https://local.bb9)"
DEFAULT_SEARCH_URL = "http://localhost:19080"
DEFAULT_TIMEOUT = 15
DEFAULT_MAX_CHARS = 16000
SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|"
    r"(?i:api[_-]?key|access[_-]?token|auth[_-]?token|bearer)[=/:%][^&\s]{8,})"
)


def action_from_text(text: str) -> Action:
    parts = shlex.split(text)
    op = parts[0].lower() if parts else ""
    params = _parse_params(parts[1:])
    if op not in {"fetch", "search"}:
        return Action(name="web", params={"op": "invalid", "raw": text}, risk="forbidden")
    params["op"] = op
    return Action(name="web", params=params, risk="low")


def review(action: Action, _: RunContext) -> GuardianDecision:
    op = str(action.params.get("op", ""))
    if op == "fetch":
        url = str(action.params.get("url", "")).strip()
        error = _validate_public_url(url)
        if error:
            return GuardianDecision(verdict="block", reason=error, action=action)
        return GuardianDecision(verdict="allow", reason="public web fetch", action=action)
    if op == "search":
        query = str(action.params.get("query", "")).strip()
        if not query:
            return GuardianDecision(verdict="block", reason="missing search query", action=action)
        return GuardianDecision(verdict="allow", reason="public web search", action=action)
    return GuardianDecision(verdict="block", reason="invalid web action", action=action)


def execute(action: Action) -> Observation:
    op = str(action.params.get("op", "")).strip().lower()
    if op == "fetch":
        return _fetch(action)
    if op == "search":
        return _search(action)
    return Observation(ok=False, summary="Invalid web tool operation.")


def _fetch(action: Action) -> Observation:
    url = str(action.params.get("url", "")).strip()
    error = _validate_public_url(url)
    if error:
        return Observation(ok=False, summary=error)
    max_chars = _bounded_int(action.params.get("max_chars"), DEFAULT_MAX_CHARS, 500, 100000)
    timeout = _bounded_int(action.params.get("timeout"), DEFAULT_TIMEOUT, 1, 60)
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain,*/*;q=0.6"})
    try:
        with urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("content-type", "")
            raw = response.read(1_000_000 + 1)
            status = getattr(response, "status", None) or getattr(response, "code", None)
    except HTTPError as exc:
        return Observation(ok=False, summary=f"HTTP {exc.code} on {url}", data={"url": url, "status": exc.code})
    except URLError as exc:
        return Observation(ok=False, summary=f"Web fetch failed: {exc.reason}", data={"url": url})
    except OSError as exc:
        return Observation(ok=False, summary=f"Web fetch failed: {exc}", data={"url": url})

    encoding = _encoding_from_content_type(content_type) or "utf-8"
    text = raw.decode(encoding, errors="replace")
    extracted = _extract_html(text, url) if "html" in content_type.lower() or "<html" in text[:500].lower() else text
    truncated = len(extracted) > max_chars
    if truncated:
        extracted = extracted[:max_chars]
    summary = f"Fetched {url}: {len(extracted)} chars"
    if truncated:
        summary += " (truncated)"
    return Observation(
        ok=True,
        summary=summary,
        data={"url": url, "status": status, "content_type": content_type, "text": extracted, "truncated": truncated},
    )


def _search(action: Action) -> Observation:
    query = str(action.params.get("query", "")).strip()
    if not query:
        return Observation(ok=False, summary="Missing search query.")
    limit = _bounded_int(action.params.get("limit"), 5, 1, 10)
    base_url = str(action.params.get("base_url") or os.environ.get("BB9_SEARCH_URL") or DEFAULT_SEARCH_URL).rstrip("/")
    search_url = f"{base_url}/search?{urlencode({'q': query, 'format': 'json'})}"
    request = Request(search_url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
            payload = json.loads(response.read(1_000_000).decode("utf-8", errors="replace"))
    except (HTTPError, URLError, OSError, json.JSONDecodeError) as exc:
        return Observation(ok=False, summary=f"Web search backend unavailable: {exc}", data={"query": query, "url": search_url})
    results = []
    for item in payload.get("results", [])[:limit]:
        url = str(item.get("url") or "")
        if _validate_public_url(url):
            continue
        results.append({
            "title": str(item.get("title") or url),
            "url": url,
            "content": str(item.get("content") or item.get("snippet") or ""),
        })
    if not results:
        return Observation(ok=False, summary=f"No web results for: {query}", data={"query": query, "results": []})
    lines = [f"{idx}. {item['title']} — {item['url']}" for idx, item in enumerate(results, start=1)]
    return Observation(ok=True, summary="\n".join(lines), data={"query": query, "results": results})


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "section", "article", "main"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1
        if tag in {"p", "div", "li", "h1", "h2", "h3", "section", "article", "main"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth and data.strip():
            self.parts.append(data)

    def text(self) -> str:
        value = html.unescape(" ".join(self.parts))
        lines = [" ".join(line.split()) for line in value.splitlines()]
        return "\n".join(line for line in lines if line).strip()


def _extract_html(document: str, url: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(document)
    except Exception:
        return document
    return parser.text() or document


def _parse_params(parts: list[str]) -> dict[str, str]:
    params: dict[str, str] = {}
    positional: list[str] = []
    for part in parts:
        if "=" in part:
            key, value = part.split("=", 1)
            params[key.strip().replace("-", "_")] = value.strip()
        else:
            positional.append(part)
    if positional:
        if "url" not in params and _looks_like_url(positional[0]):
            params["url"] = positional[0]
        elif "query" not in params:
            params["query"] = " ".join(positional)
    return params


def _validate_public_url(url: str) -> str:
    if not url:
        return "missing url"
    if SECRET_RE.search(url):
        return "secret-looking value in URL"
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return "only http(s) URLs are allowed"
    host = parsed.hostname or ""
    if not host:
        return "missing URL host"
    if host in {"localhost"} or host.endswith(".local"):
        return "local/private URLs are blocked for web tool"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return ""
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast:
        return "local/private URLs are blocked for web tool"
    return ""


def _looks_like_url(value: str) -> bool:
    return urlparse(value).scheme in {"http", "https"}


def _encoding_from_content_type(content_type: str) -> str:
    match = re.search(r"charset=([^;\s]+)", content_type, re.I)
    return match.group(1) if match else ""


def _bounded_int(raw, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))
