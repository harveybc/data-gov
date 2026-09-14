"""User acceptance for the governing Flow v3."""

import hashlib
import json
import sqlite3
from pathlib import Path

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
