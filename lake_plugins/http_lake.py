"""Proxy a remote lake HTTP API. AAA stays in data-gov; the lake serves bytes."""

from __future__ import annotations

import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


class Plugin:
    plugin_params = {
        "lake_id": "remote",
        "title": "Remote lake",
        "description": "HTTP lake adapter",
        "kind": "http",
        "base_url": "http://127.0.0.1:5056",
        "root_path": "",
    }

    def __init__(self):
        self.params = dict(self.plugin_params)
        self._opener = None

    def set_params(self, **kwargs):
        self.params.update(kwargs)

    def _get(self, path: str, params=None):
        if self._opener is not None:
            response = self._opener.get(path, query_string=params or {})
            body = response.get_json(silent=True) or {}
            self._raise_http(response.status_code, body)
            return body
        url = self.params.get("base_url", "").rstrip("/") + path
        if params:
            url += "?" + urlencode({k: v for k, v in params.items() if v is not None})
        try:
            with urlopen(Request(url), timeout=60) as handle:
                return json.loads(handle.read().decode())
        except HTTPError as exc:
            try:
                body = json.loads(exc.read().decode())
            except Exception:
                body = {}
            self._raise_http(exc.code, body)
            raise
        except URLError as exc:
            raise RuntimeError(f"lake unreachable: {exc}") from exc

    def _raise_http(self, status, body):
        err = (body or {}).get("error") or f"http {status}"
        if status == 403:
            raise PermissionError(err)
        if status == 404:
            raise FileNotFoundError(err)
        if status >= 400:
            raise RuntimeError(err)

    def describe(self):
        try:
            remote = self._get("/api/v1/describe")
        except RuntimeError:
            remote = {}
        return {
            "lake_id": self.params.get("lake_id"),
            "title": self.params.get("title") or remote.get("title"),
            "description": self.params.get("description") or remote.get("description"),
            "kind": "http",
            "root_path": remote.get("root_path") or self.params.get("base_url"),
        }

    def storage(self):
        try:
            return self._get("/api/v1/storage")
        except RuntimeError:
            return {
                "host_total": 0,
                "host_used": 0,
                "host_free": 0,
                "lake_bytes": 0,
                "root": self.params.get("base_url"),
            }

    def discover(self):
        try:
            payload = self._get("/api/v1/discover")
        except RuntimeError:
            return []
        return payload.get("resources") or payload.get("items") or []

    def list_resources(self):
        return self.discover()

    def coverage(self, resource_id: str):
        return self._get("/api/v1/coverage", {"resource": resource_id})

    def read(self, resource_id: str, start=None, end=None):
        return self._get(
            "/api/v1/read",
            {"resource": resource_id, "from": start, "to": end},
        )

    def query(self, sql: str):
        return self._get("/api/v1/query", {"sql": sql})
