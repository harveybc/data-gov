"""Programmatic client for predictor / doin / heuristic-strategy."""

from __future__ import annotations

from urllib.parse import urlencode


class DataGovClient:
    def __init__(self, base_url=None, api_key=None, experiment_key=None, test_client=None):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.experiment_key = experiment_key
        self.test_client = test_client

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
        import json
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

    def usage(self, experiment_key):
        return self._get(f"/api/v1/experiments/{experiment_key}/usage")
