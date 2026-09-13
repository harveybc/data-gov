"""http_lake proxies discover/read/download/write_metrics to a remote lake HTTP API."""

import hashlib
from pathlib import Path

import pytest
from flask import Flask, Response, jsonify, request

from lake_plugins.errors import UnsupportedError
from lake_plugins.http_lake import Plugin

BODY = b"ts,value\n2024-06-01 00:00:00,1\n2024-06-02 00:00:00,2\n"
BODY_SHA = hashlib.sha256(BODY).hexdigest()


def _stub():
    app = Flask("stub-lake")

    @app.get("/api/v1/describe")
    def describe():
        return jsonify(
            {
                "lake_id": "remote",
                "title": "Remote",
                "description": "stub",
                "kind": "http",
                "root_path": "stub",
            }
        )

    @app.get("/api/v1/storage")
    def storage():
        return jsonify(
            {
                "host_total": 100,
                "host_used": 40,
                "host_free": 60,
                "lake_bytes": 10,
            }
        )

    @app.get("/api/v1/discover")
    def discover():
        return jsonify(
            {"resources": [{"resource_id": "a.parquet", "bytes": 10}]}
        )

    @app.get("/api/v1/coverage")
    def coverage():
        return jsonify({"resource_id": request.args["resource"], "t_min": "2020", "t_max": "2021", "rows": 3})

    @app.get("/api/v1/read")
    def read():
        return jsonify({"rows": [{"x": 1}], "sha256": "abc", "bytes": 3})

    @app.get("/api/v1/query")
    def query():
        return jsonify({"rows": [{"n": 1}], "sha256": "def", "bytes": 3})

    @app.get("/api/v1/download")
    def download():
        resource = request.args["resource"]
        if resource == "spans.csv":
            return jsonify({"error": "spans holdout: request a range"}), 403
        if resource == "multi.csv":
            return jsonify({"error": "unsupported csv"}), 422
        response = Response(BODY, mimetype="application/octet-stream")
        response.headers["Content-Disposition"] = 'attachment; filename="early.csv"'
        response.headers["Content-Length"] = str(len(BODY))
        response.headers["X-Content-SHA256"] = "f" * 64 if resource == "lying.csv" else BODY_SHA
        response.headers["X-Source-SHA256"] = BODY_SHA
        response.headers["X-Delivery"] = "AS_IS"
        response.headers["X-Time-Column"] = "ts"
        return response

    @app.post("/api/v1/metrics")
    def metrics():
        report = request.get_json()
        assert request.headers.get("Authorization") == "Bearer lake-token"
        return jsonify({"stored": True, "already_stored": False, "lineage": report["lineage"]}), 201

    return app


def _lake(tmp_path):
    lake = Plugin()
    lake.set_params(
        lake_id="remote",
        title="Remote",
        base_url="http://unused",
        spool_dir=str(tmp_path / "spool"),
        lake_service_token="lake-token",
    )
    lake._opener = _stub().test_client()
    return lake


def test_http_lake_roundtrip(tmp_path):
    lake = _lake(tmp_path)
    assert lake.discover()[0]["resource_id"] == "a.parquet"
    assert lake.coverage("a.parquet")["rows"] == 3
    assert lake.read("a.parquet", start="2020-01-01", end="2020-01-02")["sha256"] == "abc"
    assert lake.query("SELECT 1")["rows"][0]["n"] == 1
    assert lake.storage()["host_free"] == 60
    assert lake.describe()["kind"] == "http"


def test_download_streams_to_spool_and_checks_hash(tmp_path):
    lake = _lake(tmp_path)
    info = lake.download("early.csv")
    part = Path(info["path"])
    assert part.parent == tmp_path / "spool"
    assert part.read_bytes() == BODY
    assert info["sha256"] == BODY_SHA and info["source_sha256"] == BODY_SHA
    assert info["bytes"] == len(BODY)
    assert info["delivery"] == "AS_IS" and info["time_column"] == "ts"
    assert info["filename"] == "early.csv"
    assert info["spool"] is True


def test_download_hash_mismatch_leaves_nothing(tmp_path):
    lake = _lake(tmp_path)
    with pytest.raises(RuntimeError, match="lake hash mismatch"):
        lake.download("lying.csv")
    assert list((tmp_path / "spool").iterdir()) == []


def test_download_maps_lake_errors(tmp_path):
    lake = _lake(tmp_path)
    with pytest.raises(PermissionError, match="spans holdout"):
        lake.download("spans.csv")
    with pytest.raises(UnsupportedError, match="unsupported csv"):
        lake.download("multi.csv")
    assert list((tmp_path / "spool").iterdir()) == []


def test_write_metrics_forwards_report(tmp_path):
    lake = _lake(tmp_path)
    out = lake.write_metrics({"lineage": "VERIFIED", "metrics": []})
    assert out == {"stored": True, "already_stored": False, "lineage": "VERIFIED"}
