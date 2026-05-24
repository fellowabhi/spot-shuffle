import json
import secrets
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable

import requests

from spot_shuffle.config import AUTH_URL, SCOPES, TOKEN_URL, Config


class TokenStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text())

    def save(self, tokens: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(tokens, indent=2))

    def get_refresh_token(self) -> str | None:
        return self.load().get("refresh_token")

    def get_access_token(self) -> str | None:
        return self.load().get("access_token")


def build_auth_url(config: Config, state: str) -> str:
    params = {
        "client_id": config.client_id,
        "response_type": "code",
        "redirect_uri": config.redirect_uri,
        "scope": SCOPES,
        "state": state,
        "show_dialog": "true",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(config: Config, code: str) -> dict:
    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config.redirect_uri,
            "client_id": config.client_id,
            "client_secret": config.client_secret,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def refresh_access_token(config: Config, refresh_token: str) -> dict:
    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": config.client_id,
            "client_secret": config.client_secret,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def run_auth_flow(config: Config, store: TokenStore) -> None:
    state = secrets.token_urlsafe(16)
    auth_code: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != urllib.parse.urlparse(config.redirect_uri).path:
                self.send_error(404)
                return

            params = urllib.parse.parse_qs(parsed.query)
            if params.get("state", [None])[0] != state:
                self.send_error(400, "State mismatch")
                return

            error = params.get("error", [None])[0]
            if error:
                self.send_error(400, error)
                return

            code = params.get("code", [None])[0]
            if not code:
                self.send_error(400, "Missing authorization code")
                return

            auth_code["code"] = code
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h1>Authorized!</h1>"
                b"<p>You can close this tab and return to the terminal.</p>"
                b"</body></html>"
            )

        def log_message(self, format: str, *args) -> None:
            return

    redirect = urllib.parse.urlparse(config.redirect_uri)
    port = redirect.port or (443 if redirect.scheme == "https" else 80)
    server = HTTPServer((redirect.hostname or "127.0.0.1", port), CallbackHandler)

    auth_url = build_auth_url(config, state)
    print(f"Opening browser for Spotify authorization...\n{auth_url}")
    webbrowser.open(auth_url)
    print(f"Waiting for callback on {config.redirect_uri} ...")
    server.handle_request()

    if "code" not in auth_code:
        raise SystemExit("Authorization failed: no code received.")

    tokens = exchange_code(config, auth_code["code"])
    existing = store.load()
    if "refresh_token" not in tokens and existing.get("refresh_token"):
        tokens["refresh_token"] = existing["refresh_token"]
    store.save(tokens)
    print(f"Tokens saved to {store.path}")


def get_valid_access_token(
    config: Config,
    store: TokenStore,
    on_refresh: Callable[[dict], None] | None = None,
) -> str:
    tokens = store.load()
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise SystemExit(
            "No refresh token found. Run: python -m spot_shuffle.cli auth"
        )

    if tokens.get("access_token"):
        return tokens["access_token"]

    refreshed = refresh_access_token(config, refresh_token)
    if "refresh_token" not in refreshed:
        refreshed["refresh_token"] = refresh_token
    store.save(refreshed)
    if on_refresh:
        on_refresh(refreshed)
    return refreshed["access_token"]
