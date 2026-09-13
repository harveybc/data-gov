"""User-level: predictor / doin / heuristic-strategy talk to the HTTP API."""

from pathlib import Path

import pytest

from app.client import DataGovClient
from tests.conftest import DOIN_KEY, FINANCIAL_ROOT, HEURISTIC_KEY, PREDICTOR_KEY

RESOURCE = "market_data/crypto/funding_rates/btcusdt/funding_rates.parquet"


pytestmark = pytest.mark.skipif(
    not (FINANCIAL_ROOT / RESOURCE).exists(),
    reason="financial-data parquet not in sibling checkout",
)


def _api(client, key, experiment="exp-predictor-1"):
    return DataGovClient(
        test_client=client,
        api_key=key,
        experiment_key=experiment,
    )


def test_unknown_api_key_is_rejected(client, runtime):
    gov = _api(client, "not-a-real-key")
    status, body = gov.lakes()
    assert status == 401
    logs = runtime["plugins"]["accounting"].logs_all()
    assert any(row["verb"] == "authn" and row["decision"] == "deny" for row in logs)


def test_predictor_lists_lakes_and_resources(client):
    gov = _api(client, PREDICTOR_KEY)
    status, body = gov.lakes()
    assert status == 200
    ids = {item["lake_id"] for item in body["lakes"]}
    assert "financial_files" in ids
    status, body = gov.resources("financial_files")
    assert status == 200
    resources = [item["resource_id"] for item in body["resources"]]
    assert RESOURCE in resources


def test_predictor_reads_in_sample_range_and_records_hash(client, runtime):
    gov = _api(client, PREDICTOR_KEY, experiment="ann_1575_1d")
    status, body = gov.read(
        "financial_files",
        RESOURCE,
        start="2020-01-01",
        end="2020-01-31",
    )
    assert status == 200
    assert body["rows"]
    assert body["sha256"]
    usage_status, usage = gov.usage("ann_1575_1d")
    assert usage_status == 200
    assert any(
        row["sha256"] == body["sha256"] and row["decision"] == "allow"
        for row in usage["events"]
    )


def test_predictor_without_experiment_key_is_forbidden(client, runtime):
    gov = DataGovClient(test_client=client, api_key=PREDICTOR_KEY, experiment_key=None)
    status, body = gov.read(
        "financial_files",
        RESOURCE,
        start="2020-01-01",
        end="2020-01-31",
    )
    assert status == 403
    logs = runtime["plugins"]["accounting"].logs_all()
    assert any(row["verb"] == "read" and row["decision"] == "deny" for row in logs)


def test_holdout_range_is_denied_automatically(client, runtime):
    gov = _api(client, PREDICTOR_KEY, experiment="must-not-see-holdout")
    status, body = gov.read(
        "financial_files",
        RESOURCE,
        start="2025-06-01",
        end="2025-06-30",
    )
    assert status == 403
    assert "holdout" in (body.get("error") or "").lower()
    logs = runtime["plugins"]["accounting"].logs_all()
    assert any(
        row["decision"] == "deny" and "holdout" in (row.get("warning") or "").lower()
        for row in logs
    )


def test_doin_and_heuristic_use_the_same_api(client):
    for key, exp in ((DOIN_KEY, "doin-run-1"), (HEURISTIC_KEY, "hs-run-1")):
        gov = _api(client, key, experiment=exp)
        status, body = gov.coverage("financial_files", RESOURCE)
        assert status == 200, body
        assert body["t_min"]
        assert body["t_max"]


def test_olap_query_in_sample_allowed_holdout_denied(client):
    gov = _api(client, PREDICTOR_KEY, experiment="olap-run")
    status, body = gov.query(
        "olap_lab",
        "SELECT ts, metric, value FROM fact_performance WHERE ts < '2025-01-01'",
    )
    assert status == 200
    assert body["rows"]
    status, body = gov.query(
        "olap_lab",
        "SELECT ts, metric, value FROM fact_performance WHERE ts >= '2025-01-01'",
    )
    assert status == 403
