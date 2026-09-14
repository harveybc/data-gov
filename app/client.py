"""Programmatic client for predictor / doin / heuristic-strategy."""

from __future__ import annotations

import hashlib
import json
import os
import re
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


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


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

    def _headers(self, campaign_sha256=None, unit_id=None):
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self.experiment_key:
            headers["X-Experiment-Key"] = self.experiment_key
        if campaign_sha256:
            headers["X-Campaign-SHA256"] = campaign_sha256
        if unit_id:
            headers["X-Unit-ID"] = unit_id
        return headers

    def _get(self, path, params=None, *, campaign_sha256=None, unit_id=None):
        headers = self._headers(campaign_sha256, unit_id)
        if self.test_client is not None:
            response = self.test_client.get(path, query_string=params, headers=headers)
            body = response.get_json(silent=True) or {}
            return response.status_code, body
        import urllib.error
        import urllib.request

        url = self.base_url + path
        if params:
            url += "?" + urlencode({k: v for k, v in params.items() if v is not None})
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as handle:
                return handle.status, json.loads(handle.read().decode())
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode())
            except Exception:
                body = {"error": str(exc)}
            return exc.code, body

    def _post(self, path, body, *, campaign_sha256=None, unit_id=None):
        headers = self._headers(campaign_sha256, unit_id)
        if self.test_client is not None:
            response = self.test_client.post(path, json=body, headers=headers)
            return response.status_code, response.get_json(silent=True) or {}
        import urllib.error
        import urllib.request

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

    def _stream(self, path, params, *, campaign_sha256=None, unit_id=None):
        headers = self._headers(campaign_sha256, unit_id)
        if self.test_client is not None:
            response = self.test_client.get(path, query_string=params, headers=headers)
            return BufferedResponse(response.status_code, response.headers, response.data)
        return open_stream(self.base_url + path, headers=headers, params=params)

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

    def _save(self, response, dest: Path, *, governing=False, default_ext=""):
        expected = (response.header("X-Content-SHA256") or "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            return 502, {"error": "missing content digest"}
        filename = filename_from_disposition(response.header("Content-Disposition"))
        # governed deliveries carry no disposition: the cache entry keeps the resource's suffix
        ext = Path(filename).suffix if filename else default_ext
        source_sha256 = (response.header("X-Source-SHA256") or "").lower()
        contract_sha256 = (
            response.header("X-Availability-Contract-SHA256") or ""
        ).lower()
        if governing and not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
            return 502, {"error": "missing source digest"}
        if governing and not re.fullmatch(r"[0-9a-f]{64}", contract_sha256):
            return 502, {"error": "missing availability contract digest"}
        info = {
            "sha256": expected,
            "filename": filename,
            "source_sha256": source_sha256 or None,
            "delivery": response.header("X-Delivery"),
            "time_column": response.header("X-Time-Column") or "",
            "availability_contract_sha256": contract_sha256,
            "cached": False,
        }
        target = dest / f"{expected}{ext}"
        if target.exists():
            if not target.is_file() or _sha256_file(target) != expected:
                return 409, {"error": "cache identity conflict", "sha256": expected}
            info.update(path=str(target), bytes=target.stat().st_size, cached=True)
            return 200, info
        # one writer, one part file: parallel downloads of the same bytes never share a partial file
        part = dest / f"{expected}{ext}.{os.getpid()}.{uuid.uuid4().hex}.part"
        digest = hashlib.sha256()
        size = 0
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as out:
                for chunk in response.iter_chunks():
                    digest.update(chunk)
                    out.write(chunk)
                    size += len(chunk)
                out.flush()
                os.fsync(out.fileno())
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        actual = digest.hexdigest()
        if not expected or actual != expected:
            part.unlink(missing_ok=True)
            return 502, {"error": "hash mismatch", "expected": expected, "actual": actual}
        try:
            os.link(part, target)
        except FileExistsError:
            if not target.is_file() or _sha256_file(target) != expected:
                part.unlink(missing_ok=True)
                return 409, {"error": "cache publication conflict", "sha256": expected}
            info["cached"] = True
        finally:
            part.unlink(missing_ok=True)
        _fsync_dir(dest)
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

    # Flow v3: the only API whose outputs may govern experiment decisions.

    def submit_campaign(self, campaign):
        return self._post("/api/v2/campaigns", campaign)

    def governed_download(
        self, campaign_sha256, unit_id, lake, resource, role, dest_dir,
        start=None, end=None,
    ):
        """Download exact bytes and confirm their use only after local verification."""
        params = {
            "lake": lake, "resource": resource, "role": role,
            "from": start, "to": end,
        }
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        attempt = 0
        while True:
            response = self._stream(
                "/api/v2/download", params,
                campaign_sha256=campaign_sha256, unit_id=unit_id,
            )
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
                delivery_id = response.header("X-Delivery-ID")
                if not isinstance(delivery_id, str) or not re.fullmatch(
                    r"[0-9a-f]{32}", delivery_id
                ):
                    return 502, {"error": "missing delivery identity"}
                status, info = self._save(
                    response, dest, governing=True, default_ext=Path(resource).suffix,
                )
            finally:
                response.close()
            if status != 200:
                return status, info
            confirm_status, confirmation = self._post(
                f"/api/v2/deliveries/{delivery_id}/confirm",
                {
                    "schema": "delivery_confirmation.v1",
                    "sha256": info["sha256"],
                    "bytes": info["bytes"],
                    "cached": info["cached"],
                },
                campaign_sha256=campaign_sha256,
            )
            if confirm_status != 200:
                return confirm_status, confirmation
            info.update(
                delivery_id=delivery_id,
                verification_state=confirmation["state"],
                campaign_sha256=campaign_sha256,
                unit_id=unit_id,
                lake=lake,
                resource=resource,
                role=role,
                range_from=start,
                range_to=end,
            )
            return 200, info

    def report_terminal(self, campaign_sha256, unit_id, terminal):
        return self._post(
            f"/api/v2/campaigns/{campaign_sha256}/units/{unit_id}/terminal",
            terminal, campaign_sha256=campaign_sha256, unit_id=unit_id,
        )

    def reconcile_campaign(self, campaign_sha256):
        return self._get(
            f"/api/v2/campaigns/{campaign_sha256}/reconcile",
            campaign_sha256=campaign_sha256,
        )
