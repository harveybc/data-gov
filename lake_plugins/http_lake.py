"""Proxy a remote store API through the shared governance kernel."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from app.httpstream import BufferedResponse, filename_from_disposition, open_stream
from app.store_metadata import store_metadata
from lake_plugins.errors import UnsupportedError


class Plugin:
    plugin_params = {
        "lake_id": "remote",
        "title": "Remote store",
        "description": "HTTP store adapter",
        "kind": None,
        "base_url": "http://127.0.0.1:5056",
        "root_path": "",
        "spool_dir": "./var/spool",
        "max_downloads": 2,
    }

    def __init__(self):
        self.params = dict(self.plugin_params)
        self._opener = None

    def set_params(self, **kwargs):
        store_metadata(dict(self.params, **kwargs))
        self.params.update(kwargs)

    def _headers(self):
        token = self.params.get("lake_service_token")
        if not token:
            try:
                from app.lake_auth import load_token

                token = load_token()
            except Exception:
                token = None
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}"}

    def _get(self, path: str, params=None):
        headers = self._headers()
        if self._opener is not None:
            response = self._opener.get(
                path, query_string=params or {}, headers=headers
            )
            body = response.get_json(silent=True) or {}
            self._raise_http(response.status_code, body)
            return body
        url = self.params.get("base_url", "").rstrip("/") + path
        if params:
            url += "?" + urlencode({k: v for k, v in params.items() if v is not None})
        try:
            with urlopen(Request(url, headers=headers), timeout=60) as handle:
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

    def _open(self, path: str, params=None, method="GET", json_body=None):
        """Streamed request: a connect timeout only; the test hook buffers the same surface."""
        headers = self._headers()
        if self._opener is not None:
            if method == "POST":
                response = self._opener.post(path, json=json_body, headers=headers)
            else:
                response = self._opener.get(path, query_string=params or {}, headers=headers)
            return BufferedResponse(response.status_code, response.headers, response.data)
        url = self.params.get("base_url", "").rstrip("/") + path
        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode()
            headers["Content-Type"] = "application/json"
        try:
            return open_stream(url, headers=headers, params=params, method=method, body=body)
        except OSError as exc:
            raise RuntimeError(f"lake unreachable: {exc}") from exc

    def _raise_http(self, status, body):
        err = (body or {}).get("error") or f"http {status}"
        if status == 401:
            raise RuntimeError("unauthenticated")
        if status == 403:
            raise PermissionError(err)
        if status == 404:
            raise FileNotFoundError(err)
        if status == 400:
            raise ValueError(err)
        if status == 422:
            raise UnsupportedError(err)
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
            **store_metadata(self.params, remote=remote),
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

    def download(self, resource_id: str, start=None, end=None):
        """Stream the lake's body into a spool file, hashing as it passes; the lake's hash is
        checked, never trusted. The caller unlinks the spool file right after opening it."""
        response = self._open(
            "/api/v1/download", {"resource": resource_id, "from": start, "to": end}
        )
        spool = Path(self.params.get("spool_dir") or "./var/spool").resolve()
        spool.mkdir(parents=True, exist_ok=True)
        part = spool / f"{uuid.uuid4().hex}.part"
        digest = hashlib.sha256()
        size = 0
        try:
            if response.status != 200:
                self._raise_http(response.status, response.read_json())
            with open(part, "wb") as out:
                for chunk in response.iter_chunks():
                    digest.update(chunk)
                    out.write(chunk)
                    size += len(chunk)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        finally:
            response.close()
        claimed = (response.header("X-Content-SHA256") or "").lower()
        if digest.hexdigest() != claimed:
            part.unlink(missing_ok=True)
            raise RuntimeError("lake hash mismatch")
        filename = filename_from_disposition(response.header("Content-Disposition"))
        return {
            "path": str(part),
            "filename": filename or Path(resource_id).name,
            "sha256": claimed,
            "bytes": size,
            "source_sha256": response.header("X-Source-SHA256"),
            "delivery": response.header("X-Delivery"),
            "time_column": response.header("X-Time-Column") or "",
            "spool": True,
        }

    def governed_download(self, resource_id: str, start=None, end=None):
        """Retain the exact remote bytes after the lake applies its availability contract."""
        response = self._open(
            "/api/v2/download", {"resource": resource_id, "from": start, "to": end}
        )
        spool = Path(self.params.get("spool_dir") or "./var/spool").resolve()
        spool.mkdir(parents=True, exist_ok=True)
        part = spool / f"{uuid.uuid4().hex}.part"
        digest = hashlib.sha256()
        size = 0
        try:
            if response.status != 200:
                self._raise_http(response.status, response.read_json())
            with open(part, "xb") as out:
                for chunk in response.iter_chunks():
                    digest.update(chunk)
                    out.write(chunk)
                    size += len(chunk)
                out.flush()
                import os

                os.fsync(out.fileno())
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        finally:
            response.close()
        claimed = (response.header("X-Content-SHA256") or "").lower()
        if digest.hexdigest() != claimed:
            part.unlink(missing_ok=True)
            raise RuntimeError("lake hash mismatch")
        contract_sha = (response.header("X-Availability-Contract-SHA256") or "").lower()
        if len(contract_sha) != 64 or any(char not in "0123456789abcdef" for char in contract_sha):
            part.unlink(missing_ok=True)
            raise RuntimeError("lake availability contract identity missing")
        handle = open(part, "rb")
        part.unlink()
        filename = filename_from_disposition(response.header("Content-Disposition"))
        return {
            "handle": handle,
            "filename": filename or Path(resource_id).name,
            "sha256": claimed,
            "bytes": size,
            "source_sha256": response.header("X-Source-SHA256"),
            "delivery": response.header("X-Delivery"),
            "time_column": response.header("X-Time-Column") or "",
            "availability_contract_sha256": contract_sha,
            "spool": True,
        }

    def write_metrics(self, report: dict):
        response = self._open("/api/v1/metrics", method="POST", json_body=report)
        try:
            body = response.read_json()
        finally:
            response.close()
        if response.status not in (200, 201):
            self._raise_http(response.status, body)
        return {
            "stored": bool(body.get("stored")),
            "already_stored": bool(body.get("already_stored")),
            "lineage": body.get("lineage"),
        }

    def write_terminal(self, terminal: dict):
        response = self._open("/api/v2/terminals", method="POST", json_body=terminal)
        try:
            body = response.read_json()
        finally:
            response.close()
        if response.status not in (200, 201):
            self._raise_http(response.status, body)
        return {
            "stored": bool(body.get("stored")),
            "already_stored": bool(body.get("already_stored")),
            "terminal_sha256": body.get("terminal_sha256"),
        }

    def terminal_digests(self, campaign_sha256):
        payload = self._get(
            "/api/v2/terminals", {"campaign_sha256": campaign_sha256}
        )
        rows = payload.get("terminals")
        if not isinstance(rows, list):
            raise RuntimeError("remote lake returned invalid terminal inventory")
        return rows
