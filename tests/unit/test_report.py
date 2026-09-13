"""Canonical report identity (04_FLOW_V2 §3)."""

import pytest

from app.report import canonical_body, canonical_json, report_sha256


def _base():
    return {
        "experiment_key": "exp",
        "actor": "predictor",
        "lake": "olap_lab",
        "datasets": [
            {"lake": "b", "resource": "r", "sha256": "1" * 64, "role": "y", "lineage": "VERIFIED", "event_id": 3},
            {"lake": "a", "resource": "r", "sha256": "1" * 64, "role": None},
        ],
        "metrics": [
            {"metric": "R2", "value": 0.5, "split": "test", "horizon": 2},
            {"metric": "MAE", "value": 0.1, "split": "train", "horizon": 1},
            {"metric": "MAE", "value": 0.2, "split": "train", "horizon": None},
        ],
    }


def test_hash_ignores_order_lineage_and_receipt():
    one = _base()
    two = _base()
    two["datasets"].reverse()
    two["metrics"].reverse()
    two["received_at"] = "2026-09-13T00:00:00Z"
    two["lineage"] = "UNVERIFIED"
    assert report_sha256(one) == report_sha256(two)
    body = canonical_body(one)
    assert [d["lake"] for d in body["datasets"]] == ["a", "b"]
    assert "lineage" not in body["datasets"][1] and "event_id" not in body["datasets"][1]
    assert [(m["metric"], m["horizon"]) for m in body["metrics"]] == [
        ("MAE", None),
        ("MAE", 1),
        ("R2", 2),
    ]
    assert body["tags"] == {} and body["experiment_set_key"] is None
    assert set(body["metrics"][0]) == {
        "metric", "value", "split", "horizon", "std_dev", "min_value", "max_value", "unit", "role"
    }


def test_canonical_json_is_compact_sorted_ascii_and_finite():
    text = canonical_json({"b": 1, "a": "é"})
    assert text == '{"a":"\\u00e9","b":1}'
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})
