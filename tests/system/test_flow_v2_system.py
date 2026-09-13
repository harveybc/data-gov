"""System tests: rules of the governed download / report path (04_FLOW_V2)."""

import hashlib
import json
from pathlib import Path

from flask import Flask, Response

from app.client import DataGovClient
from tests.conftest import LAB_EARLY, LAB_HOURLY, PREDICTOR_KEY, lake_spec


def _gov(client, experiment="sys-1", key=PREDICTOR_KEY):
    return DataGovClient(test_client=client, api_key=key, experiment_key=experiment)


def _rows(runtime, verb):
    return [r for r in runtime["plugins"]["accounting"].logs_all() if r["verb"] == verb]


def test_download_without_experiment_key_is_forbidden(client, runtime, tmp_path):
    gov = _gov(client, experiment=None)
    dest = tmp_path / "dest"
    status, body = gov.download("lab_files", LAB_EARLY, dest)
    assert status == 403 and "experiment_key" in body["error"]
    rows = _rows(runtime, "download")
    assert rows and rows[0]["decision"] == "deny" and rows[0]["warning"] == "missing experiment_key"
    assert list(dest.iterdir()) == []


def test_range_touching_holdout_is_denied(client, runtime, tmp_path):
    gov = _gov(client)
    status, body = gov.download("lab_files", LAB_HOURLY, tmp_path, start="2024-12-31", end="2025-01-01")
    assert status == 403 and "holdout" in body["error"]
    rows = _rows(runtime, "download")
    assert rows[0]["decision"] == "deny" and "holdout" in rows[0]["warning"]
    status, body = gov.download("lab_files", LAB_HOURLY, tmp_path)
    assert status == 403 and body["error"].startswith("spans holdout")


def test_datetime_range_is_rejected(client, runtime, tmp_path):
    gov = _gov(client)
    for start, end in (
        ("2024-12-30", "2024-12-31T00:00:00"),
        ("2024-12-30 00:00:00", "2024-12-31"),
        ("2024-12-31", "2024-12-30"),
        ("2024-12-30", None),
    ):
        status, body = gov.download("lab_files", LAB_HOURLY, tmp_path, start=start, end=end)
        assert status == 400 and body["error"] == "invalid from/to", (start, end)
    status, body = gov.read("lab_files", LAB_HOURLY, start="2024-12-30", end="2024-12-30T12:00")
    assert status == 400 and body["error"] == "invalid from/to"
    status, body = gov.read("lab_files", LAB_HOURLY, start="2024-12-30", end="2024-12-30")
    assert status == 200 and len(body["rows"]) == 24


def test_unknown_lake_and_resource(client, runtime, tmp_path):
    gov = _gov(client)
    status, body = gov.download("lab_files", "does/not/exist.csv", tmp_path)
    assert status == 404 and body["error"] == "unknown resource"
    status, body = gov.download("no_such_lake", LAB_EARLY, tmp_path)
    assert status == 403  # no policy
    assert _rows(runtime, "download")[0]["decision"] == "deny"


def test_actor_comes_from_the_bearer_not_the_body(client, runtime, tmp_path):
    gov = _gov(client)
    _, info = gov.download("lab_files", LAB_EARLY, tmp_path)
    response = client.post(
        "/api/v1/experiments/sys-1/metrics",
        json={
            "lake": "olap_lab",
            "actor": "doin",
            "metrics": [{"metric": "MAE", "value": 0.1}],
            "datasets": [{"lake": "lab_files", "resource": LAB_EARLY, "sha256": info["sha256"]}],
        },
        headers={"Authorization": f"Bearer {PREDICTOR_KEY}"},
    )
    assert response.status_code == 201, response.get_json()
    assert response.get_json()["lineage"] == "VERIFIED"
    import sqlite3

    conn = sqlite3.connect(lake_spec(runtime, "olap_lab")["sqlite_path"])
    assert conn.execute("SELECT actor FROM gov_report").fetchone()[0] == "predictor"
    conn.close()
    assert _rows(runtime, "write_metrics")[0]["actor"] == "predictor"


def test_source_changed_event_fires_on_next_download(client, runtime, tmp_path):
    gov = _gov(client)
    role = runtime["plugins"]["role"]
    root = Path(lake_spec(runtime, "lab_files")["root_path"])
    status, first = gov.download("lab_files", LAB_EARLY, tmp_path)
    assert status == 200 and role.events == []
    (root / LAB_EARLY).write_text("ts,value\n2024-06-01 00:00:00,1\n2024-06-02 00:00:00,7\n")
    status, second = gov.download("lab_files", LAB_EARLY, tmp_path)
    assert status == 200 and second["source_sha256"] != first["source_sha256"]
    assert [e["kind"] for e in role.events] == ["source_changed"]
    event = role.events[0]
    assert event["previous_source_sha256"] == first["source_sha256"]
    assert event["source_sha256"] == second["source_sha256"]
    rows = _rows(runtime, "event")
    assert len(rows) == 1 and rows[0]["warning"] == "source_changed"
    assert json.loads(rows[0]["detail"])["resource_id"] == LAB_EARLY
    status, third = gov.download("lab_files", LAB_EARLY, tmp_path)
    assert status == 200 and third["cached"] and len(role.events) == 1


def test_download_slots_answer_503_with_retry_after(client, runtime, tmp_path):
    slots = runtime["plugins"]["web"]._slots
    assert slots.acquire(blocking=False) and slots.acquire(blocking=False)
    try:
        response = client.get(
            "/api/v1/download",
            query_string={"lake": "lab_files", "resource": LAB_EARLY},
            headers={"Authorization": f"Bearer {PREDICTOR_KEY}", "X-Experiment-Key": "sys-1"},
        )
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "30"
    finally:
        slots.release()
        slots.release()
    response = client.get(
        "/api/v1/download",
        query_string={"lake": "lab_files", "resource": LAB_EARLY},
        headers={"Authorization": f"Bearer {PREDICTOR_KEY}", "X-Experiment-Key": "sys-1"},
    )
    assert response.status_code == 200
    assert response.headers["X-Content-SHA256"] == hashlib.sha256(response.data).hexdigest()
    assert response.headers["Content-Length"] == str(len(response.data))
    assert response.headers["X-Delivery"] == "AS_IS"
    assert response.headers["Content-Disposition"] == f'attachment; filename="{LAB_EARLY}"'
    # both slots are free again once the body has been sent
    assert slots.acquire(blocking=False) and slots.acquire(blocking=False)
    slots.release()
    slots.release()


def test_http_lake_spool_file_is_unlinked_after_open(client, runtime, tmp_path):
    from lake_plugins.http_lake import Plugin as HttpLake

    body = b"ts,value\n2024-06-01 00:00:00,1\n"
    stub = Flask("remote-stub")

    @stub.get("/api/v1/download")
    def download():
        if stub.config.get("lie"):
            response = Response(body + b"tampered\n", mimetype="application/octet-stream")
        else:
            response = Response(body, mimetype="application/octet-stream")
        response.headers["Content-Disposition"] = 'attachment; filename="early.csv"'
        response.headers["X-Content-SHA256"] = hashlib.sha256(body).hexdigest()
        response.headers["X-Source-SHA256"] = hashlib.sha256(body).hexdigest()
        response.headers["X-Delivery"] = "AS_IS"
        response.headers["X-Time-Column"] = "ts"
        return response

    spool = tmp_path / "spool"
    remote = HttpLake()
    remote.set_params(lake_id="remote_files", base_url="http://unused", spool_dir=str(spool), lake_service_token="t")
    remote._opener = stub.test_client()
    runtime["plugins"]["lakes"]["remote_files"] = remote
    runtime["plugins"]["access"].params["policies"].append(
        {"principal": "*", "lake": "remote_files", "verbs": ["download"]}
    )
    gov = _gov(client)
    status, info = gov.download("remote_files", "early.csv", tmp_path / "dest")
    assert status == 200, info
    assert Path(info["path"]).read_bytes() == body
    assert list(spool.iterdir()) == []
    stub.config["lie"] = True
    status, info = gov.download("remote_files", "early.csv", tmp_path / "dest2")
    assert status == 503 and info["error"] == "lake hash mismatch"
    assert list(spool.iterdir()) == []
    assert _rows(runtime, "download")[0]["warning"] == "lake hash mismatch"
