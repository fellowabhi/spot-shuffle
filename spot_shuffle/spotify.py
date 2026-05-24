import time
from typing import Any

import requests

from spot_shuffle.auth import TokenStore, refresh_access_token
from spot_shuffle.config import API_BASE, Config

PAGE_SLEEP_SECONDS = 0.1


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

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
    ) -> requests.Response:
        url = f"{API_BASE}{path}"
        headers = {"Authorization": f"Bearer {self._access_token}"}

        response = requests.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json,
            timeout=30,
        )

        if response.status_code == 401:
            self._refresh()
            headers["Authorization"] = f"Bearer {self._access_token}"
            response = requests.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
                timeout=30,
            )

        response.raise_for_status()
        if response.status_code == 204 or not response.content:
            return response
        return response

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
            response = requests.get(
                next_url,
                headers={"Authorization": f"Bearer {self._access_token}"},
                timeout=30,
            )
            if response.status_code == 401:
                self._refresh()
                response = requests.get(
                    next_url,
                    headers={"Authorization": f"Bearer {self._access_token}"},
                    timeout=30,
                )
            response.raise_for_status()
            data = response.json()

        return items

    def put(self, path: str, *, json: dict | None = None) -> None:
        self.request("PUT", path, json=json)

    def post(self, path: str, *, json: dict | None = None) -> dict[str, Any] | None:
        response = self.request("POST", path, json=json)
        if response.content:
            return response.json()
        return None
