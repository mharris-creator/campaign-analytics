"""Thin HubSpot REST client: bearer auth, pagination, rate-limit + 5xx retry."""

from __future__ import annotations

import time

import requests

import config

BASE = "https://api.hubapi.com"


class HubSpotError(RuntimeError):
    pass


class HubSpotClient:
    def __init__(self, token: str | None = None, session: requests.Session | None = None):
        self.token = token or config.HUBSPOT_TOKEN
        if not self.token:
            raise HubSpotError("No HUBSPOT_TOKEN found. Add it to .env (see .env.example).")
        self.s = session or requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {self.token}",
                               "Content-Type": "application/json"})

    def _request(self, method: str, path: str, **kw) -> dict:
        url = path if path.startswith("http") else f"{BASE}{path}"
        for attempt in range(6):
            r = self.s.request(method, url, timeout=30, **kw)
            if r.status_code == 429:                       # rate limited
                time.sleep(float(r.headers.get("Retry-After", 2)))
                continue
            if r.status_code >= 500:                       # transient
                time.sleep(min(2 ** attempt, 30))
                continue
            if r.status_code >= 400:
                raise HubSpotError(f"{method} {url} -> {r.status_code}: {r.text[:400]}")
            return r.json() if r.text else {}
        raise HubSpotError(f"Exhausted retries: {method} {url}")

    def get(self, path: str, params: dict | None = None) -> dict:
        return self._request("GET", path, params=params)

    def post(self, path: str, json: dict | None = None) -> dict:
        return self._request("POST", path, json=json)

    def paginate(self, path: str, params: dict | None = None, page_size: int = 100):
        """Yield every result across CRM v3 list pagination."""
        params = dict(params or {})
        params.setdefault("limit", page_size)
        after = None
        while True:
            if after:
                params["after"] = after
            data = self.get(path, params=params)
            yield from data.get("results", [])
            after = data.get("paging", {}).get("next", {}).get("after")
            if not after:
                break
            time.sleep(0.08)  # gentle pacing under the 10s burst limit
