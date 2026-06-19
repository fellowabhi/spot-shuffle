import time
from typing import Any

import requests

from spot_shuffle.auth import TokenStore, refresh_access_token
from spot_shuffle.config import API_BASE, Config

PAGE_SLEEP_SECONDS = 0.1
MAX_RATE_LIMIT_RETRIES = 5
DEFAULT_RETRY_AFTER_SECONDS = 5.0


def retry_after_seconds(response: requests.Response) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after is None:
        return DEFAULT_RETRY_AFTER_SECONDS
    try:
        return max(float(retry_after), 1.0)
    except ValueError:
        return DEFAULT_RETRY_AFTER_SECONDS


class SpotifyClient:
    def __init__(self, config: Config, store: TokenStore):
        self.config = config
        self.store = store
        self._access_token = self._load_access_token()

    def _load_access_token(self) -> str:
        tokens = self.store.load()
        if tokens.get("access_token"):
            return tokens["access_token"]
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            raise SystemExit(
                "No refresh token found. Run: python -m spot_shuffle.cli auth"
            )
        return self._refresh(refresh_token)

    def _refresh(self, refresh_token: str | None = None) -> str:
        refresh_token = refresh_token or self.store.load().get("refresh_token")
        if not refresh_token:
            raise SystemExit(
                "No refresh token found. Run: python -m spot_shuffle.cli auth"
            )
        refreshed = refresh_access_token(self.config, refresh_token)
        if "refresh_token" not in refreshed:
            refreshed["refresh_token"] = refresh_token
        self.store.save(refreshed)
        self._access_token = refreshed["access_token"]
        return self._access_token

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    def _send_once(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
    ) -> requests.Response:
        return requests.request(
            method,
            url,
            headers=self._auth_headers(),
            params=params,
            json=json,
            timeout=30,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
    ) -> requests.Response:
        return self._request_with_retries(
            method,
            f"{API_BASE}{path}",
            params=params,
            json=json,
        )

    def _request_with_retries(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
    ) -> requests.Response:
        refreshed_for_auth = False
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            response = self._send_once(method, url, params=params, json=json)

            if response.status_code == 401 and not refreshed_for_auth:
                self._refresh()
                refreshed_for_auth = True
                response = self._send_once(method, url, params=params, json=json)

            if response.status_code == 429:
                if attempt >= MAX_RATE_LIMIT_RETRIES:
                    response.raise_for_status()
                time.sleep(retry_after_seconds(response))
                refreshed_for_auth = False
                continue

            response.raise_for_status()
            return response

        raise RuntimeError("unreachable")

    def get_json(self, path: str, *, params: dict | None = None) -> dict[str, Any]:
        response = self.request("GET", path, params=params)
        return response.json()

    def get_paginated(
        self,
        path: str,
        *,
        params: dict | None = None,
        items_key: str = "items",
    ) -> list[Any]:
        params = dict(params or {})
        params.setdefault("limit", 50)
        items: list[Any] = []
        data = self.get_json(path, params=params)

        while True:
            items.extend(data.get(items_key, []))
            next_url = data.get("next")
            if not next_url:
                break
            time.sleep(PAGE_SLEEP_SECONDS)
            response = self._request_with_retries("GET", next_url)
            data = response.json()

        return items

    def put(self, path: str, *, json: dict | None = None) -> None:
        self.request("PUT", path, json=json)

    def post(self, path: str, *, json: dict | None = None) -> dict[str, Any] | None:
        response = self.request("POST", path, json=json)
        if response.content:
            return response.json()
        return None
