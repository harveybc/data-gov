"""http_lake proxies discover/read to a remote lake HTTP API."""

from flask import Flask, jsonify, request

from lake_plugins.http_lake import Plugin


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

    return app


def test_http_lake_roundtrip():
    stub = _stub()
    lake = Plugin()
    lake.set_params(lake_id="remote", title="Remote", base_url="http://unused")
    lake._opener = stub.test_client()
    assert lake.discover()[0]["resource_id"] == "a.parquet"
    assert lake.coverage("a.parquet")["rows"] == 3
    assert lake.read("a.parquet", start="2020-01-01", end="2020-01-02")["sha256"] == "abc"
    assert lake.query("SELECT 1")["rows"][0]["n"] == 1
    assert lake.storage()["host_free"] == 60
    assert lake.describe()["kind"] == "http"
