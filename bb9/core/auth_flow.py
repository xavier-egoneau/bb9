"""Small OAuth helpers for ChatGPT web auth.

This is adapted from the standalone Marius provider_config flow and kept
stdlib-only.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import time
import webbrowser
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib import parse, request

CHATGPT_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CHATGPT_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
CHATGPT_TOKEN_URL = "https://auth.openai.com/oauth/token"
CHATGPT_REDIRECT_URI = "http://localhost:1455/auth/callback"
CHATGPT_SCOPE = "openid profile email offline_access"


@dataclass(frozen=True)
class OAuthTokenResult:
    access_token: str
    refresh_token: str
    expires: float
    obtained_at: str


class OAuthError(RuntimeError):
    pass


class ChatGPTOAuthFlow:
    def __init__(
        self,
        *,
        redirect_uri: str = CHATGPT_REDIRECT_URI,
        callback_host: str = "127.0.0.1",
        callback_port: int = 1455,
        timeout_seconds: int = 300,
        token_transport: Any = None,
    ) -> None:
        self.redirect_uri = redirect_uri
        self.callback_host = callback_host
        self.callback_port = callback_port
        self.timeout_seconds = timeout_seconds
        self.token_transport = token_transport

    def run(self, *, on_url: Any = None) -> OAuthTokenResult:
        verifier, challenge = generate_pkce()
        state = secrets.token_hex(16)
        auth_url = build_authorize_url(
            code_challenge=challenge,
            state=state,
            redirect_uri=self.redirect_uri,
        )

        _OAuthCallbackHandler.expected_state = state
        _OAuthCallbackHandler.code = None
        server = http.server.HTTPServer((self.callback_host, self.callback_port), _OAuthCallbackHandler)
        server.timeout = 1

        if on_url is not None:
            on_url(auth_url)
        else:
            webbrowser.open(auth_url)

        deadline = time.time() + self.timeout_seconds
        try:
            while time.time() < deadline:
                server.handle_request()
                if _OAuthCallbackHandler.code:
                    raw = exchange_code(
                        _OAuthCallbackHandler.code,
                        verifier,
                        self.redirect_uri,
                        transport=self.token_transport,
                    )
                    return OAuthTokenResult(
                        access_token=str(raw["access_token"]),
                        refresh_token=str(raw.get("refresh_token", "")),
                        expires=time.time() + float(raw.get("expires_in", 0) or 0),
                        obtained_at=datetime.now(UTC).isoformat(),
                    )
        finally:
            server.server_close()

        raise OAuthError("Timeout: aucun callback recu.")


def generate_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorize_url(
    *,
    code_challenge: str,
    state: str,
    redirect_uri: str = CHATGPT_REDIRECT_URI,
) -> str:
    params = {
        "response_type": "code",
        "client_id": CHATGPT_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": CHATGPT_SCOPE,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": "bb9",
    }
    return f"{CHATGPT_AUTHORIZE_URL}?{parse.urlencode(params)}"


def exchange_code(
    code: str,
    verifier: str,
    redirect_uri: str,
    *,
    transport: Any = None,
) -> dict[str, Any]:
    payload = {
        "grant_type": "authorization_code",
        "client_id": CHATGPT_CLIENT_ID,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
    }
    return _token_request(payload, transport=transport)


def refresh_token(token: str, *, transport: Any = None) -> dict[str, Any]:
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": token,
        "client_id": CHATGPT_CLIENT_ID,
    }
    return _token_request(payload, transport=transport)


def _token_request(payload: dict[str, Any], *, transport: Any = None) -> dict[str, Any]:
    if transport is not None:
        return transport(CHATGPT_TOKEN_URL, payload)
    body = parse.urlencode(payload).encode("utf-8")
    req = request.Request(
        CHATGPT_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise OAuthError(f"Token request failed: {exc}") from exc


class _OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    code: str | None = None
    expected_state: str = ""

    def do_GET(self) -> None:
        parsed = parse.urlparse(self.path)
        params = parse.parse_qs(parsed.query)
        if parsed.path != "/auth/callback":
            self._respond(404, b"Not found")
            return
        state = params.get("state", [None])[0]
        if state != self.__class__.expected_state:
            self._respond(400, b"<h1>State mismatch</h1>")
            return
        code = params.get("code", [None])[0]
        if not code:
            self._respond(400, b"<h1>Missing code</h1>")
            return
        self.__class__.code = code
        self._respond(200, _SUCCESS_HTML)

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        pass


_SUCCESS_HTML = (
    b"<!DOCTYPE html><html><body style=\"font-family:sans-serif;text-align:center;padding:60px\">"
    b"<h1>Connexion reussie</h1>"
    b"<p>Vous pouvez fermer cette fenetre et revenir dans BB9.</p>"
    b"</body></html>"
)
