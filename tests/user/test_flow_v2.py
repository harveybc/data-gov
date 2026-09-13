"""User-level: an agent downloads a dataset file, reports metrics, checks lineage (04_FLOW_V2)."""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from flask import Flask, Response, jsonify

from app.client import DataGovClient
from tests.conftest import (
    DOIN_KEY,
    HAS_PREDICTOR_DATA,
    LAB_EARLY,
    LAB_HOURLY,
    PREDICTOR_KEY,
    PREDICTOR_RESOURCE,
    lake_spec,
)

METRICS = [
    {"metric": "MAE", "value": 0.0065, "split": "train", "horizon": 24, "std_dev": 0.0007},
    {"metric": "R2", "value": 0.91, "split": "validation", "horizon": 24},
]


def _gov(client, key=PREDICTOR_KEY, experiment="exp-1"):
    return DataGovClient(test_client=client, api_key=key, experiment_key=experiment)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _dataset(info, resource=LAB_EARLY, lake="lab_files", role="x_train_file"):
    return {"lake": lake, "resource": resource, "sha256": info["sha256"], "role": role}


def _gov_rows(runtime, lake_id, table):
    conn = sqlite3.connect(lake_spec(runtime, lake_id)["sqlite_path"])
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
    finally:
        conn.close()


def test_client_download_verifies_saves_and_caches(client, runtime, tmp_path):
    gov = _gov(client)
    dest = tmp_path / "cache"
    status, info = gov.download("lab_files", LAB_HOURLY, dest, start="2024-12-30", end="2024-12-31")
    assert status == 200, info
    path = Path(info["path"])
    assert path.parent == dest and path.name == f"{info['sha256']}.csv"
    assert _sha(path) == info["sha256"]
    assert info["delivery"] == "CUT" and info["time_column"] == "ts" and not info["cached"]
    assert info["source_sha256"] == _sha(Path(lake_spec(runtime, "lab_files")["root_path"]) / LAB_HOURLY)
    assert not list(dest.glob("*.part"))
    status, again = gov.download("lab_files", LAB_HOURLY, dest, start="2024-12-30", end="2024-12-31")
    assert status == 200 and again["cached"] and again["path"] == info["path"]
    usage_status, usage = gov.usage("exp-1")
    downloads = [r for r in usage["events"] if r["verb"] == "download" and r["decision"] == "allow"]
    assert len(downloads) == 2
    assert all(r["sha256"] == info["sha256"] for r in downloads)
    detail = json.loads(downloads[0]["detail"])
    assert detail == {
        "delivery": "CUT",
        "from": "2024-12-30",
        "to": "2024-12-31",
        "source_sha256": info["source_sha256"],
        "time_column": "ts",
    }


def _lying_server(retry_first=False):
    app = Flask("lying")
    body = b"ts,value\n2024-06-01 00:00:00,1\n"
    calls = {"n": 0}

    @app.get("/api/v1/download")
    def download():
        calls["n"] += 1
        if retry_first and calls["n"] == 1:
            response = jsonify({"error": "download slots busy"})
            response.headers["Retry-After"] = "7"
            return response, 503
        response = Response(body, mimetype="application/octet-stream")
        response.headers["Content-Disposition"] = 'attachment; filename="early.csv"'
        response.headers["X-Content-SHA256"] = (
            hashlib.sha256(body).hexdigest() if retry_first else "0" * 64
        )
        return response

    return app, calls


def test_corrupted_body_leaves_no_file(tmp_path):
    app, _ = _lying_server()
    gov = DataGovClient(test_client=app.test_client(), api_key="k", experiment_key="e")
    status, info = gov.download("lake", "early.csv", tmp_path / "dest")
    assert status == 502 and info["error"] == "hash mismatch"
    assert list((tmp_path / "dest").iterdir()) == []


def test_client_retries_on_503_with_retry_after(tmp_path):
    app, calls = _lying_server(retry_first=True)
    gov = DataGovClient(test_client=app.test_client(), api_key="k", experiment_key="e")
    waited = []
    gov._sleep = waited.append
    status, info = gov.download("lake", "early.csv", tmp_path / "dest")
    assert status == 200 and Path(info["path"]).is_file()
    assert waited == [7] and calls["n"] == 2


def test_api_key_from_environment(monkeypatch):
    monkeypatch.setenv("DATA_GOV_API_KEY", "from-env")
    assert DataGovClient().api_key == "from-env"
    assert DataGovClient(api_key="given").api_key == "given"


def test_report_with_verified_lineage_is_stored(client, runtime, tmp_path):
    gov = _gov(client)
    status, info = gov.download("lab_files", LAB_EARLY, tmp_path)
    assert status == 200 and info["delivery"] == "AS_IS"
    status, body = gov.report_metrics(
        "exp-1",
        "olap_lab",
        METRICS,
        [_dataset(info)],
        config_sha256="c" * 64,
        code_commit="abc123",
        project="predictor",
        phase="phase_1_daily",
        tags={"plugin": "ann"},
    )
    assert status == 201, body
    assert body["stored"] and not body["already_stored"] and body["lineage"] == "VERIFIED"
    dataset = body["datasets"][0]
    assert dataset["lineage"] == "VERIFIED" and dataset["reason"] is None
    assert isinstance(dataset["event_id"], int)
    reports = _gov_rows(runtime, "olap_lab", "gov_report")
    assert len(reports) == 1 and reports[0]["lineage"] == "VERIFIED"
    assert reports[0]["report_sha256"] == body["report_sha256"]
    assert reports[0]["actor"] == "predictor" and reports[0]["code_commit"] == "abc123"
    rows = _gov_rows(runtime, "olap_lab", "gov_dataset")
    assert rows[0]["event_id"] == dataset["event_id"]
    assert rows[0]["source_sha256"] == info["source_sha256"] and rows[0]["delivery"] == "AS_IS"
    assert rows[0]["time_column"] == "ts"
    assert len(_gov_rows(runtime, "olap_lab", "gov_metric")) == 2
    _, usage = gov.usage("exp-1")
    written = [r for r in usage["events"] if r["verb"] == "write_metrics"]
    assert len(written) == 1 and written[0]["decision"] == "allow"
    assert written[0]["resource_id"] == "experiment/exp-1"
    assert written[0]["sha256"] == body["report_sha256"] and written[0]["warning"] is None
    detail = json.loads(written[0]["detail"])
    assert detail["stored"] is True and detail["lineage"] == "VERIFIED"
    assert detail["datasets"][0]["event_id"] == dataset["event_id"]


def test_no_datasets_is_unverified(client, runtime):
    gov = _gov(client)
    status, body = gov.report_metrics("exp-1", "olap_strict", METRICS, [])
    assert status == 422 and body["error"] == "unverified lineage"
    assert body["lineage"] == "UNVERIFIED" and body["datasets"] == []
    assert _gov_rows(runtime, "olap_strict", "gov_report") == []
    _, usage = gov.usage("exp-1")
    denied = [r for r in usage["events"] if r["verb"] == "write_metrics"]
    assert denied[0]["decision"] == "deny" and denied[0]["warning"] == "unverified lineage"

    status, body = gov.report_metrics("exp-1", "olap_lab", METRICS, [])
    assert status == 201 and body["lineage"] == "UNVERIFIED"
    assert _gov_rows(runtime, "olap_lab", "gov_report")[0]["lineage"] == "UNVERIFIED"
    _, usage = gov.usage("exp-1")
    stored = [r for r in usage["events"] if r["verb"] == "write_metrics" and r["decision"] == "allow"]
    assert stored[0]["warning"] == "unverified dataset lineage"


def test_unverified_reasons(client, runtime, tmp_path):
    predictor = _gov(client, experiment="exp-a")
    status, info = predictor.download("lab_files", LAB_EARLY, tmp_path)
    assert status == 200

    def reason(gov, key, datasets):
        status, body = gov.report_metrics(key, "olap_strict", METRICS, datasets)
        assert status == 422, body
        return body["datasets"][0]["reason"]

    assert reason(_gov(client, DOIN_KEY, "exp-a"), "exp-a", [_dataset(info)]) == "different actor"
    assert (
        reason(_gov(client, experiment="exp-b"), "exp-b", [_dataset(info)])
        == "not served under this key or set"
    )
    assert (
        reason(predictor, "exp-a", [_dataset(info, resource="other.csv")])
        == "hash seen for another resource"
    )
    never = dict(_dataset(info), sha256="9" * 64)
    assert reason(predictor, "exp-a", [never]) == "never served"


def test_set_key_serves_every_experiment_of_the_set(client, runtime, tmp_path):
    under_set = _gov(client, experiment="set-1")
    status, info = under_set.download("lab_files", LAB_EARLY, tmp_path)
    assert status == 200
    status, body = under_set.report_metrics(
        "exp-z", "olap_strict", METRICS, [_dataset(info)], experiment_set_key="set-1"
    )
    assert status == 201 and body["lineage"] == "VERIFIED"
    _, usage = under_set.usage("set-1")
    assert any(r["verb"] == "download" for r in usage["events"])
    assert not any(r["verb"] == "write_metrics" for r in usage["events"])
    _, usage = under_set.usage("exp-z")
    assert any(r["verb"] == "write_metrics" for r in usage["events"])


def test_repost_is_already_stored(client, runtime, tmp_path):
    gov = _gov(client)
    _, info = gov.download("lab_files", LAB_EARLY, tmp_path)
    first = gov.report_metrics("exp-1", "olap_lab", METRICS, [_dataset(info)])
    second = gov.report_metrics("exp-1", "olap_lab", list(reversed(METRICS)), [_dataset(info)])
    assert first[0] == 201 and second[0] == 200
    assert second[1]["already_stored"] and not second[1]["stored"]
    assert second[1]["report_sha256"] == first[1]["report_sha256"]
    assert second[1]["lineage"] == "VERIFIED"
    assert len(_gov_rows(runtime, "olap_lab", "gov_metric")) == 2


def test_usage_and_dataset_usage(client, runtime, tmp_path):
    gov = _gov(client)
    _, info = gov.download("lab_files", LAB_EARLY, tmp_path)
    _, info2 = gov.download("lab_files", LAB_EARLY, tmp_path)
    assert info2["cached"]
    gov.report_metrics("exp-1", "olap_lab", METRICS, [_dataset(info)])
    status, body = gov.dataset_usage(info["sha256"])
    assert status == 200
    rows = body["events"]
    assert len(rows) == 2 and all(r["verb"] == "download" and r["decision"] == "allow" for r in rows)
    status, page = gov.dataset_usage(info["sha256"], limit=1)
    assert [r["id"] for r in page["events"]] == [rows[0]["id"]]
    status, page = gov.dataset_usage(info["sha256"], limit=1, before_id=rows[0]["id"])
    assert [r["id"] for r in page["events"]] == [rows[1]["id"]]
    status, usage = gov.usage("exp-1", limit=1)
    assert status == 200 and len(usage["events"]) == 1 and usage["events"][0]["verb"] == "write_metrics"
    status, body = gov.dataset_usage("not-a-hash")
    assert status == 400
    status, body = gov.usage("bad key!")
    assert status == 400


def test_report_validation(client, runtime, tmp_path):
    gov = _gov(client)
    bad = [
        ("olap_lab", [{"metric": "MAE", "value": float("nan")}], [], "non-finite value"),
        ("olap_lab", [], [], "metrics required"),
        ("olap_lab", [{"metric": "MAE", "value": "x"}], [], "invalid value"),
        ("olap_lab", [{"metric": "MAE", "value": 1, "horizon": 1.5}], [], "invalid horizon"),
        ("nowhere", METRICS, [], "unknown lake"),
        ("olap_lab", METRICS, [{"lake": "l", "resource": "r", "sha256": "zz"}], "invalid dataset sha256"),
    ]
    for lake, metrics, datasets, error in bad:
        payload = {"lake": lake, "metrics": metrics, "datasets": datasets}
        response = client.post(
            "/api/v1/experiments/exp-1/metrics",
            data=json.dumps(payload),
            content_type="application/json",
            headers={"Authorization": f"Bearer {PREDICTOR_KEY}"},
        )
        assert response.status_code == 400, (error, response.get_json())
        assert response.get_json()["error"] == error
    response = client.post(
        "/api/v1/experiments/bad key/metrics",
        json={"lake": "olap_lab", "metrics": METRICS},
        headers={"Authorization": f"Bearer {PREDICTOR_KEY}"},
    )
    assert response.status_code == 400 and response.get_json()["error"] == "invalid key"
    status, body = gov.report_metrics("exp-1", "lab_files", METRICS, [])
    assert status == 403
    assert _gov_rows(runtime, "olap_lab", "gov_report") == []


def test_duplicate_datasets_collapse_and_numbers_normalise(client, runtime, tmp_path):
    gov = _gov(client)
    _, info = gov.download("lab_files", LAB_EARLY, tmp_path)
    metrics = [{"metric": "MAE", "value": "0.5", "horizon": "24", "split": "train"}]
    status, body = gov.report_metrics(
        "exp-1", "olap_lab", metrics, [_dataset(info), _dataset(info)]
    )
    assert status == 201 and len(body["datasets"]) == 1
    row = _gov_rows(runtime, "olap_lab", "gov_metric")[0]
    assert row["value"] == 0.5 and row["horizon"] == 24


@pytest.mark.skipif(not HAS_PREDICTOR_DATA, reason="predictor sample data not in sibling checkout")
def test_predictor_examples_lake_serves_sample_as_is(client, runtime, tmp_path):
    gov = _gov(client, experiment="ann_1575_1d")
    status, info = gov.download("predictor_examples", PREDICTOR_RESOURCE, tmp_path)
    assert status == 200, info
    assert info["delivery"] == "AS_IS" and info["time_column"] == "DATE_TIME"
    assert Path(info["path"]).read_bytes().startswith(b"DATE_TIME,")
    status, body = gov.report_metrics(
        "ann_1575_1d", "olap_strict", METRICS, [_dataset(info, PREDICTOR_RESOURCE, "predictor_examples")]
    )
    assert status == 201 and body["lineage"] == "VERIFIED"
