"""Canonical metrics report: one JSON text and one sha256 on both sides of a lake (04_FLOW_V2 §3)."""

from __future__ import annotations

import hashlib
import json

METRIC_FIELDS = (
    "metric",
    "value",
    "split",
    "horizon",
    "std_dev",
    "min_value",
    "max_value",
    "unit",
    "role",
)
DATASET_FIELDS = ("lake", "resource", "sha256", "role")


def canonical_body(report: dict) -> dict:
    datasets = [{k: d.get(k) for k in DATASET_FIELDS} for d in report.get("datasets") or []]
    datasets.sort(key=lambda d: (d["lake"], d["resource"], d["sha256"], d["role"] or ""))
    metrics = [{k: m.get(k) for k in METRIC_FIELDS} for m in report.get("metrics") or []]
    # explicit None test: `horizon or -1` would fold a horizon of 0 into the unset case
    metrics.sort(
        key=lambda m: (
            m["metric"],
            m["split"] or "",
            -1 if m["horizon"] is None else m["horizon"],
        )
    )
    return {
        "experiment_key": report["experiment_key"],
        "experiment_set_key": report.get("experiment_set_key"),
        "actor": report["actor"],
        "lake": report["lake"],
        "config_sha256": report.get("config_sha256"),
        "code_commit": report.get("code_commit"),
        "project": report.get("project"),
        "phase": report.get("phase"),
        "tags": report.get("tags") or {},
        "datasets": datasets,
        "metrics": metrics,
    }


def canonical_json(body) -> str:
    return json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def report_sha256(report: dict) -> str:
    text = canonical_json(canonical_body(report))
    return hashlib.sha256(text.encode("ascii")).hexdigest()
