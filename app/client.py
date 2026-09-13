"""Programmatic client for predictor / doin / heuristic-strategy."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

from app.httpstream import (
    CHUNK,
    BufferedResponse,
    filename_from_disposition,
    open_stream,
)

MAX_RETRIES = 5
DEFAULT_RETRY_AFTER = 30
MAX_RETRY_AFTER = 120


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _retry_after(value):
    try:
        return max(0, min(int(value), MAX_RETRY_AFTER))
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER


class DataGovClient:
    def __init__(
        self,
        base_url=None,
        api_key=None,
        experiment_key=None,
        test_client=None,
        api_key_file=None,
    ):
        if api_key is None and api_key_file:
            api_key = Path(api_key_file).read_text(encoding="utf-8").strip() or None
        if api_key is None:
            api_key = os.environ.get("DATA_GOV_API_KEY")
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.experiment_key = experiment_key
        self.test_client = test_client
        self._sleep = time.sleep

    def _headers(self):
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self.experiment_key:
            headers["X-Experiment-Key"] = self.experiment_key
        return headers

    def _get(self, path, params=None):
        if self.test_client is not None:
            response = self.test_client.get(path, query_string=params, headers=self._headers())
            body = response.get_json(silent=True) or {}
            return response.status_code, body
        import urllib.error
        import urllib.request

        url = self.base_url + path
        if params:
            url += "?" + urlencode({k: v for k, v in params.items() if v is not None})
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=30) as handle:
                return handle.status, json.loads(handle.read().decode())
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode())
            except Exception:
                body = {"error": str(exc)}
            return exc.code, body

    def _post(self, path, body):
        if self.test_client is not None:
            response = self.test_client.post(path, json=body, headers=self._headers())
            return response.status_code, response.get_json(silent=True) or {}
        import urllib.error
        import urllib.request

        headers = self._headers()
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(body, allow_nan=False).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as handle:
                return handle.status, json.loads(handle.read().decode())
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode())
            except Exception:
                payload = {"error": str(exc)}
            return exc.code, payload

    def _stream(self, path, params):
        if self.test_client is not None:
            response = self.test_client.get(path, query_string=params, headers=self._headers())
            return BufferedResponse(response.status_code, response.headers, response.data)
        return open_stream(self.base_url + path, headers=self._headers(), params=params)

    def lakes(self):
        return self._get("/api/v1/lakes")

    def resources(self, lake_id):
        return self._get("/api/v1/resources", {"lake": lake_id})

    def coverage(self, lake_id, resource_id):
        return self._get(
            "/api/v1/coverage", {"lake": lake_id, "resource": resource_id}
        )

    def read(self, lake_id, resource_id, start=None, end=None):
        return self._get(
            "/api/v1/read",
            {
                "lake": lake_id,
                "resource": resource_id,
                "from": start,
                "to": end,
            },
        )

    def query(self, lake_id, sql):
        return self._get("/api/v1/query", {"lake": lake_id, "sql": sql})

    def usage(self, experiment_key, limit=1000, before_id=None):
        return self._get(
            f"/api/v1/experiments/{experiment_key}/usage",
            {"limit": limit, "before_id": before_id},
        )

    def dataset_usage(self, sha256, limit=1000, before_id=None):
        return self._get(
            f"/api/v1/datasets/{sha256}/usage", {"limit": limit, "before_id": before_id}
        )

    def download(self, lake, resource, dest_dir, start=None, end=None):
        """(status, info). On 200 `info["path"]` is dest_dir/<sha256><ext>, verified against
        X-Content-SHA256 (a cache hit is re-hashed). A body that does not match is deleted and
        answered (502, {"error": "hash mismatch"}). 503 with Retry-After is retried, bounded."""
        params = {"lake": lake, "resource": resource, "from": start, "to": end}
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        attempt = 0
        while True:
            response = self._stream("/api/v1/download", params)
            try:
                if (
                    response.status == 503
                    and response.header("Retry-After") is not None
                    and attempt < MAX_RETRIES
                ):
                    attempt += 1
                    wait = _retry_after(response.header("Retry-After"))
                    response.close()
                    self._sleep(wait)
                    continue
                if response.status != 200:
                    return response.status, response.read_json()
                return self._save(response, dest)
            finally:
                response.close()

    def _save(self, response, dest: Path):
        expected = (response.header("X-Content-SHA256") or "").lower()
        filename = filename_from_disposition(response.header("Content-Disposition"))
        ext = Path(filename).suffix if filename else ""
        info = {
            "sha256": expected,
            "filename": filename,
            "source_sha256": response.header("X-Source-SHA256"),
            "delivery": response.header("X-Delivery"),
            "time_column": response.header("X-Time-Column") or "",
            "cached": False,
        }
        target = dest / f"{expected}{ext}"
        if expected and target.is_file() and _sha256_file(target) == expected:
            info.update(path=str(target), bytes=target.stat().st_size, cached=True)
            return 200, info
        # one writer, one part file: parallel downloads of the same bytes never share a partial file
        part = dest / f"{expected}{ext}.{os.getpid()}.{uuid.uuid4().hex}.part"
        digest = hashlib.sha256()
        size = 0
        try:
            with open(part, "wb") as out:
                for chunk in response.iter_chunks():
                    digest.update(chunk)
                    out.write(chunk)
                    size += len(chunk)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        actual = digest.hexdigest()
        if not expected or actual != expected:
            part.unlink(missing_ok=True)
            return 502, {"error": "hash mismatch", "expected": expected, "actual": actual}
        os.replace(part, target)
        info.update(path=str(target), bytes=size)
        return 200, info

    def report_metrics(
        self,
        experiment_key,
        lake,
        metrics,
        datasets,
        experiment_set_key=None,
        config_sha256=None,
        code_commit=None,
        project=None,
        phase=None,
        tags=None,
    ):
        body = {
            "lake": lake,
            "metrics": list(metrics or []),
            "datasets": list(datasets or []),
        }
        for name, value in (
            ("experiment_set_key", experiment_set_key),
            ("config_sha256", config_sha256),
            ("code_commit", code_commit),
            ("project", project),
            ("phase", phase),
            ("tags", tags),
        ):
            if value is not None:
                body[name] = value
        return self._post(f"/api/v1/experiments/{experiment_key}/metrics", body)
