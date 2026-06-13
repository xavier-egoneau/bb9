"""Standalone Playwright browser tool runtime."""

from __future__ import annotations

import re
import shlex
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bb9.core.models import Action, Artifact, GuardianDecision, Observation, RetryPolicy, Risk, RunContext
from bb9.core.utils import truthy as _truthy

DEFAULT_TIMEOUT_MS = 15000
_SESSION: BrowserSession | None = None
_BROWSER_THREAD: ThreadPoolExecutor | None = None

USAGE = (
    "browser <op> [url] [param=valeur ...] — op: open|check|extract|screenshot|click|type|close. "
    "Params: url=, selector=, text=, path=, screenshot=true|false, full_page=true|false, "
    "viewport=1280x720, timeout_ms=, wait_until=. "
    "Le seul argument positionnel accepté est une URL http(s) ; tout le reste passe en param=valeur. "
    "Exemples : `browser open http://127.0.0.1:8000`, `browser screenshot full_page=true`, "
    '`browser check url=http://127.0.0.1:8000 text="Accueil" screenshot=true`.'
)


def usage() -> str:
    return USAGE


def action_from_text(text: str) -> Action:
    try:
        parts = shlex.split(text)
    except ValueError:
        return Action(name="browser", params={"op": "invalid", "raw": text}, risk="forbidden")
    op = parts[0].lower() if parts else ""
    if _has_unexpected_positional_parts(parts[1:]):
        return Action(name="browser", params={"op": "invalid", "raw": text}, risk="forbidden")
    params = _parse_params(parts[1:])
    if op not in {"open", "extract", "screenshot", "click", "type", "close", "check"}:
        return Action(name="browser", params={"op": "invalid", "raw": text}, risk="forbidden")
    if _has_invalid_bool_params(params):
        return Action(name="browser", params={"op": "invalid", "raw": text}, risk="forbidden")
    params["op"] = op
    risk: Risk = "medium" if op in {"click", "type"} else "low"
    return Action(name="browser", params=params, risk=risk)


def review(action: Action, context: RunContext) -> GuardianDecision:
    op = str(action.params.get("op", ""))
    if op == "invalid":
        return GuardianDecision(verdict="block", reason="invalid browser action", action=action)
    url = str(action.params.get("url", "")).strip()
    if url and urlparse(url).scheme not in {"http", "https"}:
        return GuardianDecision(verdict="block", reason="only http(s) URLs are allowed", action=action)
    if op in {"click", "type"}:
        if context.permission_profile == "safe":
            return GuardianDecision(verdict="ask", reason=f"browser interaction requires confirmation: {op}", action=action)
        return GuardianDecision(verdict="allow", reason=f"browser interaction allowed by {context.permission_profile} profile", action=action)
    if context.permission_profile == "safe":
        return GuardianDecision(verdict="ask", reason="browser execution requires confirmation in safe profile", action=action)
    return GuardianDecision(verdict="allow", reason=f"browser action allowed by {context.permission_profile} profile", action=action)


def execute(action: Action, context: RunContext | None = None) -> Observation:
    workspace = context.workspace.root if context is not None else Path.cwd()
    return _run_in_browser_thread(lambda: _execute_sync(action, workspace))


def _execute_sync(action: Action, workspace: Path) -> Observation:
    op = str(action.params.get("op", "")).strip().lower()
    if op == "check":
        return _run_check(action.params, workspace)
    manager = _session(workspace)
    if op == "open":
        return manager.open(action.params)
    if op == "extract":
        return manager.extract(action.params)
    if op == "screenshot":
        return manager.screenshot(action.params)
    if op == "click":
        return manager.click(action.params)
    if op == "type":
        return manager.type_text(action.params)
    if op == "close":
        return _close_session()
    return Observation(ok=False, summary="Invalid browser tool operation.")


def _run_check(params: dict, workspace: Path) -> Observation:
    return _check_once(params, workspace)


def _check_once(params: dict, workspace: Path) -> Observation:
    manager = BrowserSession(workspace=workspace)
    try:
        return manager.check(params)
    finally:
        manager.close()


def _run_in_browser_thread(callable_) -> Observation:
    global _BROWSER_THREAD
    if _BROWSER_THREAD is None:
        _BROWSER_THREAD = ThreadPoolExecutor(max_workers=1)
    return _BROWSER_THREAD.submit(callable_).result()


class BrowserSession:
    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self.artifact_root = self.workspace / ".bb9" / "artifacts" / "screenshots"
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None

    def check(self, params: dict) -> Observation:
        opened = self.open(params)
        if not opened.ok:
            return opened
        failures: list[str] = []
        passes: list[str] = []
        page = self._page
        assert page is not None
        expected_text = str(params.get("text") or "").strip()
        selector = str(params.get("selector") or "").strip()
        if expected_text:
            try:
                page.get_by_text(expected_text, exact=False).first.wait_for(timeout=3000)
                passes.append(f"text:{expected_text}")
            except Exception:
                failures.append(f"missing text:{expected_text}")
        if selector:
            try:
                page.locator(selector).first.wait_for(timeout=3000)
                passes.append(f"selector:{selector}")
            except Exception:
                failures.append(f"missing selector:{selector}")
        try:
            body_text = page.locator("body").inner_text(timeout=3000)
        except Exception:
            body_text = ""
        if body_text.strip():
            passes.append("not_blank")
        else:
            failures.append("blank page")
        screenshot_path = ""
        screenshot_artifacts: tuple[Artifact, ...] = ()
        if _truthy(params.get("screenshot")):
            shot = self.screenshot(params)
            if shot.ok:
                screenshot_path = str(shot.data.get("path", ""))
                screenshot_artifacts = shot.artifacts
                passes.append("screenshot")
            else:
                failures.append(f"screenshot failed:{shot.summary}")
        ok = not failures
        summary_lines = [f"Browser check {'passed' if ok else 'failed'}: {page.url}"]
        if passes:
            summary_lines.append("PASS " + ", ".join(passes))
        if failures:
            summary_lines.append("FAIL " + ", ".join(failures))
        if screenshot_path:
            summary_lines.append(f"Screenshot: {screenshot_path}")
        return Observation(
            ok=ok,
            summary="\n".join(summary_lines),
            data={"url": page.url, "title": page.title(), "passes": passes, "failures": failures, "screenshot": screenshot_path, "text": body_text[:4000]},
            artifacts=screenshot_artifacts,
        )

    def open(self, params: dict) -> Observation:
        url = str(params.get("url") or "").strip()
        if not url:
            return Observation(ok=False, summary="missing url")
        if urlparse(url).scheme not in {"http", "https"}:
            return Observation(ok=False, summary="only http(s) URLs are allowed")
        page_or_error = self._ensure_page(params)
        if isinstance(page_or_error, Observation):
            return page_or_error
        page = page_or_error
        try:
            response = page.goto(url, wait_until=str(params.get("wait_until") or "domcontentloaded"), timeout=_bounded_int(params.get("timeout_ms"), DEFAULT_TIMEOUT_MS, 1000, 120000))
        except Exception as exc:
            summary = f"browser navigation failed: {exc}"
            data = {"url": url}
            retry: RetryPolicy = "block_exact"
            if _is_local_http_url(url) and _looks_like_local_server_failure(str(exc)):
                hint = (
                    "Local server did not return a valid HTTP response. "
                    "Start a responsive preview server with `BB9_ACTION shell python3 -m http.server <port>` "
                    "and use the URL returned by shell."
                )
                summary = f"{summary}\nHint: {hint}"
                data["hint"] = hint
                retry = "recoverable"
            return Observation(ok=False, summary=summary, data=data, retry_policy=retry)
        status = getattr(response, "status", None) if response is not None else None
        return Observation(ok=True, summary=f"Opened {page.url}", data={"url": page.url, "title": page.title(), "status": status})

    def extract(self, params: dict) -> Observation:
        page = self._require_page()
        if isinstance(page, Observation):
            return page
        selector = str(params.get("selector") or "body")
        try:
            text = page.locator(selector).inner_text(timeout=3000)
        except Exception as exc:
            return Observation(ok=False, summary=f"browser extract failed: {exc}")
        return Observation(ok=True, summary=text[:4000], data={"url": page.url, "selector": selector, "text": text})

    def screenshot(self, params: dict) -> Observation:
        page = self._require_page()
        if isinstance(page, Observation):
            return page
        path = self._screenshot_path(str(params.get("path") or ""))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(path), full_page=_truthy(params.get("full_page")))
        except Exception as exc:
            return Observation(ok=False, summary=f"browser screenshot failed: {exc}")
        return Observation(
            ok=True,
            summary=f"Screenshot saved: {path}",
            data={"url": page.url, "path": str(path)},
            artifacts=(Artifact(kind="screenshot", title=path.name, path=str(path), source="browser"),),
        )

    def click(self, params: dict) -> Observation:
        page = self._require_page()
        if isinstance(page, Observation):
            return page
        selector = str(params.get("selector") or "").strip()
        text = str(params.get("text") or "").strip()
        if not selector and not text:
            return Observation(ok=False, summary="missing selector or text")
        try:
            target = page.locator(selector).first if selector else page.get_by_text(text, exact=False).first
            target.click(timeout=5000)
        except Exception as exc:
            return Observation(ok=False, summary=f"browser click failed: {exc}")
        return Observation(ok=True, summary=f"Clicked {selector or text}", data={"url": page.url})

    def type_text(self, params: dict) -> Observation:
        page = self._require_page()
        if isinstance(page, Observation):
            return page
        selector = str(params.get("selector") or "").strip()
        text = str(params.get("text") or "")
        if not selector:
            return Observation(ok=False, summary="missing selector")
        try:
            page.locator(selector).first.fill(text, timeout=5000)
        except Exception as exc:
            return Observation(ok=False, summary=f"browser type failed: {exc}")
        return Observation(ok=True, summary=f"Typed into {selector}", data={"url": page.url})

    def close(self) -> Observation:
        try:
            if self._browser is not None:
                self._browser.close()
            if self._playwright is not None:
                self._playwright.stop()
        finally:
            self._page = None
            self._browser = None
            self._playwright = None
        return Observation(ok=True, summary="Browser closed.")

    def _ensure_page(self, params: dict) -> Any | Observation:
        if self._page is not None:
            return self._page
        try:
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError:
            return Observation(ok=False, summary="Playwright missing. Install with: python3 -m pip install playwright && python3 -m playwright install chromium", retry_policy="block_tool")
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
            self._page = self._browser.new_page(viewport=_viewport(params.get("viewport")))
            return self._page
        except Exception as exc:
            return Observation(ok=False, summary=f"Could not start Playwright Chromium: {exc}", retry_policy="block_tool")

    def _require_page(self) -> Any | Observation:
        if self._page is None:
            return Observation(ok=False, summary="No page open. Use browser open or browser check with url.", retry_policy="allow")
        return self._page

    def _screenshot_path(self, raw: str) -> Path:
        name = Path(raw).name if raw else f"screenshot-{time.strftime('%Y%m%d-%H%M%S')}.png"
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-._") or f"screenshot-{time.strftime('%Y%m%d-%H%M%S')}.png"
        if not re.search(r"\.(png|jpe?g|webp)$", safe, re.I):
            safe = f"{safe}.png"
        return self.artifact_root / safe


def _parse_params(parts: list[str]) -> dict[str, str]:
    params: dict[str, str] = {}
    positional: list[str] = []
    for part in parts:
        if "=" in part:
            key, value = part.split("=", 1)
            params[key.strip().replace("-", "_")] = value.strip()
        else:
            positional.append(part)
    if positional and "url" not in params and urlparse(positional[0]).scheme in {"http", "https"}:
        params["url"] = positional[0]
    return params


def _has_unexpected_positional_parts(parts: list[str]) -> bool:
    positional = [part for part in parts if "=" not in part]
    if not positional:
        return False
    return not (len(positional) == 1 and urlparse(positional[0]).scheme in {"http", "https"})


def _has_invalid_bool_params(params: dict[str, str]) -> bool:
    for key in ("screenshot", "full_page"):
        if key not in params:
            continue
        if str(params[key]).strip().lower() not in {"1", "true", "yes", "y", "on", "oui", "o", "0", "false", "no", "n", "off", "non"}:
            return True
    return False


def _is_local_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _looks_like_local_server_failure(message: str) -> bool:
    normalized = message.lower()
    return any(
        marker in normalized
        for marker in (
            "err_empty_response",
            "err_connection_refused",
            "err_connection_reset",
            "err_address_unreachable",
        )
    )


def _session(workspace: Path) -> BrowserSession:
    global _SESSION
    if _SESSION is None:
        _SESSION = BrowserSession(workspace=workspace)
    elif _SESSION.workspace.resolve(strict=False) != Path(workspace).resolve(strict=False):
        _SESSION.close()
        _SESSION = BrowserSession(workspace=workspace)
    return _SESSION


def _close_session() -> Observation:
    global _SESSION
    if _SESSION is None:
        return Observation(ok=True, summary="Browser already closed.")
    observation = _SESSION.close()
    _SESSION = None
    return observation


def _viewport(raw) -> dict[str, int] | None:
    text = str(raw or "").lower().strip()
    if not text:
        return None
    match = re.match(r"^(\d{3,5})x(\d{3,5})$", text)
    if not match:
        return None
    return {"width": int(match.group(1)), "height": int(match.group(2))}


def _bounded_int(raw, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))
