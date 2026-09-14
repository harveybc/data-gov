"""User acceptance for the governing Flow v3."""

import hashlib
import io
import json
import re
import sqlite3
from pathlib import Path

import pytest

from app.client import DataGovClient
from tests.conftest import LAB_EARLY, PREDICTOR_KEY, lake_spec


def _headers(campaign=None, unit=None):
    out = {"Authorization": f"Bearer {PREDICTOR_KEY}"}
    if campaign:
        out["X-Campaign-SHA256"] = campaign
    if unit:
        out["X-Unit-ID"] = unit
    return out


def _campaign(**changes):
    body = {
        "schema": "governed_campaign.v1",
        "campaign_key": "feature-bank-dev-001",
        "classification": "GOVERNING",
        "project": "predictor",
        "code_identity": {"kind": "git_commit", "value": "a" * 40},
        "config_sha256": "b" * 64,
        "input_mode": "DATASETS",
        "synthetic_spec_sha256": None,
        "units": ["u001", "u002"],
        "datasets": [{
            "lake": "lab_files",
            "resource": LAB_EARLY,
            "role": "x",
            "from": None,
            "to": None,
        }],
        "terminal_lake": "olap_strict",
    }
    body.update(changes)
    return body


def _submit(client, body=None):
    response = client.post("/api/v2/campaigns", json=body or _campaign(), headers=_headers())
    assert response.status_code in (200, 201), response.get_json()
    return response.get_json()["campaign_sha256"]


def _download(client, campaign, unit="u001"):
    return client.get(
        "/api/v2/download",
        query_string={"lake": "lab_files", "resource": LAB_EARLY, "role": "x"},
        headers=_headers(campaign, unit),
    )


def test_governing_download_requires_declared_unit(client):
    campaign = _submit(client)
    response = _download(client, campaign, unit=None)
    assert response.status_code == 403
    assert response.get_json()["error"] == "unit is not declared by campaign"


def test_delivery_setup_failure_closes_the_lake_descriptor(
    client, runtime, monkeypatch
):
    campaign = _submit(client, _campaign(campaign_key="descriptor-cleanup"))
    handle = io.BytesIO(b"governed bytes")
    lake = runtime["plugins"]["lakes"]["lab_files"]
    monkeypatch.setattr(lake, "governed_download", lambda *args, **kwargs: {
        "handle": handle,
        "sha256": hashlib.sha256(b"governed bytes").hexdigest(),
        "bytes": len(b"governed bytes"),
        "source_sha256": "a" * 64,
        "delivery": "AS_IS",
        "time_column": "ts",
        "availability_contract_sha256": "b" * 64,
    })
    monkeypatch.setattr(
        runtime["plugins"]["accounting"], "create_delivery",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("accounting unavailable")),
    )

    with pytest.raises(RuntimeError, match="accounting unavailable"):
        _download(client, campaign)
    assert handle.closed


def test_accounting_cannot_create_unitless_governing_delivery(runtime):
    with pytest.raises(ValueError, match="requires unit_id"):
        runtime["plugins"]["accounting"].create_delivery(
            campaign_sha256="a" * 64, unit_id=None, actor="predictor",
            lake_id="lab_files", resource_id=LAB_EARLY, role="x",
            range_from=None, range_to=None, sha256="b" * 64, bytes_count=1,
            source_sha256="c" * 64, delivery_kind="AS_IS", time_column="ts",
            availability_contract_sha256="d" * 64,
        )


def _confirm(client, campaign, delivery_id, digest, size, cached=False):
    return client.post(
        f"/api/v2/deliveries/{delivery_id}/confirm",
        json={"schema": "delivery_confirmation.v1", "sha256": digest,
              "bytes": size, "cached": cached},
        headers=_headers(campaign),
    )


def _terminal(status="COMPLETED", deliveries=None, metrics=None, **changes):
    body = {
        "schema": "governed_terminal.v1",
        "generation": 1,
        "status": status,
        "reason": None if status == "COMPLETED" else "typed test outcome",
        "started_at": "2026-09-13T20:00:00Z",
        "finished_at": "2026-09-13T20:00:01Z",
        "costs": {"wall_seconds": 1.0, "cpu_seconds": 0.5},
        "deliveries": deliveries or [],
        "artifacts": [],
        "metrics": metrics if metrics is not None else [],
        "tags": {"purpose": "acceptance"},
    }
    body.update(changes)
    return body


def _rows(runtime, table):
    path = lake_spec(runtime, "olap_strict")["sqlite_path"]
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
    finally:
        conn.close()


def test_campaign_is_required_and_fixes_membership(client):
    assert _download(client, "0" * 64).status_code == 404
    campaign = _submit(client)
    assert _download(client, campaign, "not-declared").status_code == 403
    response = client.get(
        "/api/v2/download",
        query_string={"lake": "lab_files", "resource": "static.csv", "role": "x"},
        headers=_headers(campaign, "u001"),
    )
    assert response.status_code == 403


def test_governing_campaign_refuses_unidentified_code(client):
    for value in ("a" * 40 + "-dirty", "abc123", "A" * 40):
        body = _campaign(code_identity={"kind": "git_commit", "value": value},
                         campaign_key=f"bad-{len(value)}-{value[:1]}")
        response = client.post("/api/v2/campaigns", json=body, headers=_headers())
        assert response.status_code == 400


def test_delivery_needs_client_confirmation_before_terminal(client, runtime):
    campaign = _submit(client)
    response = _download(client, campaign)
    assert response.status_code == 200
    delivery_id = response.headers["X-Delivery-ID"]
    digest = response.headers["X-Content-SHA256"]
    contract_digest = response.headers["X-Availability-Contract-SHA256"]
    assert re.fullmatch(r"[0-9a-f]{64}", contract_digest)
    body = response.data

    terminal = _terminal(
        deliveries=[delivery_id],
        metrics=[{"metric": "MAE", "split": "test", "horizon": 1,
                  "unit": None, "value": 0.1, "std_dev": None,
                  "min_value": None, "max_value": None}],
    )
    refused = client.post(
        f"/api/v2/campaigns/{campaign}/units/u001/terminal",
        json=terminal, headers=_headers(campaign, "u001"),
    )
    assert refused.status_code == 422

    wrong = _confirm(client, campaign, delivery_id, "0" * 64, len(body))
    assert wrong.status_code == 409
    confirmed = _confirm(client, campaign, delivery_id, digest, len(body))
    assert confirmed.status_code == 200
    assert confirmed.get_json()["state"] == "VERIFIED_TRANSFER"

    accepted = client.post(
        f"/api/v2/campaigns/{campaign}/units/u001/terminal",
        json=terminal, headers=_headers(campaign, "u001"),
    )
    assert accepted.status_code == 201, accepted.get_json()
    assert _rows(runtime, "gov_terminal")[0]["status"] == "COMPLETED"
    dataset_row = _rows(runtime, "gov_terminal_dataset")[0]
    assert dataset_row["availability_contract_sha256"] == contract_digest


def test_cache_confirmation_is_distinct(client):
    campaign = _submit(client)
    response = _download(client, campaign)
    delivery_id = response.headers["X-Delivery-ID"]
    confirmed = _confirm(
        client, campaign, delivery_id, response.headers["X-Content-SHA256"],
        len(response.data), cached=True,
    )
    assert confirmed.status_code == 200
    assert confirmed.get_json()["state"] == "VERIFIED_CACHE"


def test_all_terminal_outcomes_are_stored_without_fake_metrics(client, runtime):
    units = ["u001", "u002", "u003", "u004", "u005"]
    campaign = _submit(client, _campaign(
        campaign_key="all-outcomes", classification="NON_GOVERNING", units=units,
        datasets=[], input_mode="SYNTHETIC", synthetic_spec_sha256="c" * 64,
    ))
    states = ["COMPLETED", "FAILED", "INCONCLUSIVE", "REFUSED", "QUARANTINED"]
    for unit, state in zip(units, states):
        response = client.post(
            f"/api/v2/campaigns/{campaign}/units/{unit}/terminal",
            json=_terminal(state), headers=_headers(campaign, unit),
        )
        assert response.status_code == 201, response.get_json()
    assert {row["status"] for row in _rows(runtime, "gov_terminal")} == set(states)
    assert _rows(runtime, "gov_terminal_metric") == []


def test_terminal_metric_identity_is_unique_and_order_independent(client):
    campaign = _submit(client, _campaign(
        campaign_key="metric-identity", classification="NON_GOVERNING",
        datasets=[], input_mode="SYNTHETIC", synthetic_spec_sha256="d" * 64,
    ))
    metric = {"metric": "MAE", "split": "test", "horizon": 1, "unit": None,
              "value": 0.1, "std_dev": None, "min_value": None, "max_value": None}
    duplicate = _terminal(metrics=[metric, dict(metric)])
    response = client.post(
        f"/api/v2/campaigns/{campaign}/units/u001/terminal",
        json=duplicate, headers=_headers(campaign, "u001"),
    )
    assert response.status_code == 400


def test_terminal_is_idempotent_but_conflict_is_not_last_writer_wins(client):
    campaign = _submit(client, _campaign(
        campaign_key="terminal-cas", classification="NON_GOVERNING",
        datasets=[], input_mode="SYNTHETIC", synthetic_spec_sha256="e" * 64,
    ))
    path = f"/api/v2/campaigns/{campaign}/units/u001/terminal"
    terminal = _terminal("FAILED")
    first = client.post(path, json=terminal, headers=_headers(campaign, "u001"))
    second = client.post(path, json=terminal, headers=_headers(campaign, "u001"))
    conflict = client.post(
        path, json=_terminal("INCONCLUSIVE"), headers=_headers(campaign, "u001")
    )
    assert first.status_code == 201
    assert second.status_code == 200 and second.get_json()["already_stored"]
    assert conflict.status_code == 409


def test_reconciliation_names_missing_units_without_repair(client):
    campaign = _submit(client, _campaign(
        campaign_key="reconcile", classification="NON_GOVERNING",
        datasets=[], input_mode="SYNTHETIC", synthetic_spec_sha256="f" * 64,
    ))
    client.post(
        f"/api/v2/campaigns/{campaign}/units/u001/terminal",
        json=_terminal("FAILED"), headers=_headers(campaign, "u001"),
    )
    response = client.get(
        f"/api/v2/campaigns/{campaign}/reconcile", headers=_headers(campaign)
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["missing_units"] == ["u002"]
    assert body["accounting_only"] == [] and body["lake_only"] == []


def test_official_client_executes_the_complete_governed_protocol(client, tmp_path):
    gov = DataGovClient(test_client=client, api_key=PREDICTOR_KEY)
    status, registered = gov.submit_campaign(_campaign(campaign_key="client-flow"))
    assert status == 201
    campaign = registered["campaign_sha256"]
    status, dataset = gov.governed_download(
        campaign, "u001", "lab_files", LAB_EARLY, "x", tmp_path / "cache"
    )
    assert status == 200
    assert Path(dataset["path"]).read_bytes()
    assert dataset["verification_state"] == "VERIFIED_TRANSFER"
    terminal = _terminal("COMPLETED", deliveries=[dataset["delivery_id"]])
    status, receipt = gov.report_terminal(campaign, "u001", terminal)
    assert status == 201 and receipt["terminal_sha256"]
    status, reconciliation = gov.reconcile_campaign(campaign)
    assert status == 200
    assert reconciliation["missing_units"] == ["u002"]
