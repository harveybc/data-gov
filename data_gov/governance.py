"""Strict, canonical objects for governing campaigns and run terminals."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone

KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")

CAMPAIGN_KEYS = {
    "schema", "campaign_key", "classification", "project", "code_identity",
    "config_sha256", "input_mode", "synthetic_spec_sha256", "units", "datasets",
    "terminal_lake",
}
DATASET_KEYS = {"lake", "resource", "role", "from", "to"}
TERMINAL_KEYS = {
    "schema", "generation", "status", "reason", "started_at", "finished_at",
    "costs", "deliveries", "artifacts", "metrics", "tags",
}
METRIC_KEYS = {
    "metric", "split", "horizon", "unit", "value", "std_dev", "min_value",
    "max_value",
}
ARTIFACT_KEYS = {"role", "sha256", "bytes"}
TERMINAL_STATES = {"COMPLETED", "FAILED", "INCONCLUSIVE", "REFUSED", "QUARANTINED"}


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False)


def object_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("ascii")).hexdigest()


def _exact(obj, keys, name):
    if not isinstance(obj, dict) or set(obj) != keys:
        raise ValueError(f"invalid {name} schema")


def _key(value, name):
    if not isinstance(value, str) or not KEY_RE.fullmatch(value):
        raise ValueError(f"invalid {name}")
    return value


def _hex(value, regex, name):
    if not isinstance(value, str) or not regex.fullmatch(value):
        raise ValueError(f"invalid {name}")
    return value


def _number(value, name, *, nullable=True, integer=False):
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid {name}")
    out = float(value)
    if not math.isfinite(out):
        raise ValueError(f"invalid {name}")
    if integer:
        if out != int(out):
            raise ValueError(f"invalid {name}")
        return int(out)
    return out


def _optional_text(value, name):
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError(f"invalid {name}")
    return value


def _day(value, name):
    if value is None:
        return None
    if not isinstance(value, str) or not DAY_RE.fullmatch(value):
        raise ValueError(f"invalid {name}")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"invalid {name}") from exc
    return value


def _utc(value, name):
    if not isinstance(value, str) or not UTC_RE.fullmatch(value):
        raise ValueError(f"invalid {name}")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"invalid {name}") from exc
    if parsed.tzinfo != timezone.utc:
        raise ValueError(f"invalid {name}")
    return value, parsed


def normalise_campaign(payload, actor, lake_ids):
    _exact(payload, CAMPAIGN_KEYS, "campaign")
    if payload["schema"] != "governed_campaign.v1":
        raise ValueError("invalid campaign schema")
    campaign_key = _key(payload["campaign_key"], "campaign_key")
    project = _key(payload["project"], "project")
    classification = payload["classification"]
    if classification not in {"GOVERNING", "NON_GOVERNING"}:
        raise ValueError("invalid classification")
    identity = payload["code_identity"]
    _exact(identity, {"kind", "value"}, "code_identity")
    kind = identity["kind"]
    value = identity["value"]
    if kind == "git_commit":
        if classification == "GOVERNING":
            _hex(value, HEX40_RE, "code_identity")
        elif not isinstance(value, str) or not value:
            raise ValueError("invalid code_identity")
    elif kind == "file_manifest":
        _hex(value, HEX64_RE, "code_identity")
    else:
        raise ValueError("invalid code_identity")
    config_sha = _hex(payload["config_sha256"], HEX64_RE, "config_sha256")
    terminal_lake = payload["terminal_lake"]
    if not isinstance(terminal_lake, str) or terminal_lake not in lake_ids:
        raise ValueError("unknown terminal_lake")

    raw_units = payload["units"]
    if not isinstance(raw_units, list) or not raw_units or len(raw_units) > 10000:
        raise ValueError("invalid units")
    units = [_key(unit, "unit_id") for unit in raw_units]
    if len(units) != len(set(units)):
        raise ValueError("duplicate unit_id")

    raw_datasets = payload["datasets"]
    if not isinstance(raw_datasets, list):
        raise ValueError("invalid datasets")
    datasets = []
    seen = set()
    for raw in raw_datasets:
        _exact(raw, DATASET_KEYS, "campaign dataset")
        lake = raw["lake"]
        if not isinstance(lake, str) or lake not in lake_ids:
            raise ValueError("unknown dataset lake")
        resource = _key(raw["resource"], "resource") if "/" not in str(raw["resource"]) else raw["resource"]
        if not isinstance(resource, str) or not resource or resource.startswith("/") or ".." in resource.split("/"):
            raise ValueError("invalid resource")
        role = _key(raw["role"], "role")
        start, end = _day(raw["from"], "from"), _day(raw["to"], "to")
        if (start is None) != (end is None) or (start and start > end):
            raise ValueError("invalid from/to")
        item = {"lake": lake, "resource": resource, "role": role, "from": start, "to": end}
        marker = (lake, resource, role, start, end)
        if marker in seen:
            raise ValueError("duplicate campaign dataset")
        seen.add(marker)
        datasets.append(item)

    input_mode = payload["input_mode"]
    synthetic = payload["synthetic_spec_sha256"]
    if input_mode == "DATASETS":
        if not datasets or synthetic is not None:
            raise ValueError("invalid DATASETS input")
    elif input_mode == "SYNTHETIC":
        if datasets:
            raise ValueError("invalid SYNTHETIC input")
        synthetic = _hex(synthetic, HEX64_RE, "synthetic_spec_sha256")
    else:
        raise ValueError("invalid input_mode")

    body = {
        "schema": "governed_campaign.v1",
        "campaign_key": campaign_key,
        "actor": actor,
        "classification": classification,
        "project": project,
        "code_identity": {"kind": kind, "value": value},
        "config_sha256": config_sha,
        "input_mode": input_mode,
        "synthetic_spec_sha256": synthetic,
        "units": sorted(units),
        "datasets": sorted(datasets, key=lambda d: (
            d["lake"], d["resource"], d["role"], d["from"] or "", d["to"] or ""
        )),
        "terminal_lake": terminal_lake,
    }
    body["campaign_sha256"] = object_sha256(body)
    return body


def normalise_confirmation(payload):
    _exact(payload, {"schema", "sha256", "bytes", "cached"}, "confirmation")
    if payload["schema"] != "delivery_confirmation.v1":
        raise ValueError("invalid confirmation schema")
    if not isinstance(payload["cached"], bool):
        raise ValueError("invalid cached")
    return {
        "schema": "delivery_confirmation.v1",
        "sha256": _hex(payload["sha256"], HEX64_RE, "sha256"),
        "bytes": _number(payload["bytes"], "bytes", nullable=False, integer=True),
        "cached": payload["cached"],
    }


def _normalise_metric(raw):
    _exact(raw, METRIC_KEYS, "metric")
    name = _key(raw["metric"], "metric")
    split = _optional_text(raw["split"], "split")
    unit = _optional_text(raw["unit"], "unit")
    horizon = _number(raw["horizon"], "horizon", integer=True)
    return {
        "metric": name,
        "split": split,
        "horizon": horizon,
        "unit": unit,
        "value": _number(raw["value"], "value"),
        "std_dev": _number(raw["std_dev"], "std_dev"),
        "min_value": _number(raw["min_value"], "min_value"),
        "max_value": _number(raw["max_value"], "max_value"),
    }


def normalise_terminal(payload, campaign, unit_id, actor):
    _exact(payload, TERMINAL_KEYS, "terminal")
    if payload["schema"] != "governed_terminal.v1":
        raise ValueError("invalid terminal schema")
    generation = _number(payload["generation"], "generation", nullable=False, integer=True)
    if generation < 1:
        raise ValueError("invalid generation")
    status = payload["status"]
    if status not in TERMINAL_STATES:
        raise ValueError("invalid status")
    reason = _optional_text(payload["reason"], "reason")
    if status != "COMPLETED" and reason is None:
        raise ValueError("reason required")
    started_at, started = _utc(payload["started_at"], "started_at")
    finished_at, finished = _utc(payload["finished_at"], "finished_at")
    if finished < started:
        raise ValueError("finished_at before started_at")

    costs = payload["costs"]
    if not isinstance(costs, dict) or not costs:
        raise ValueError("invalid costs")
    clean_costs = {}
    for key, value in costs.items():
        name = _key(key, "cost key")
        number = _number(value, f"costs.{name}", nullable=False)
        if number < 0:
            raise ValueError(f"invalid costs.{name}")
        clean_costs[name] = number

    deliveries = payload["deliveries"]
    if not isinstance(deliveries, list) or not all(
        isinstance(item, str) and re.fullmatch(r"[0-9a-f]{32}", item) for item in deliveries
    ) or len(deliveries) != len(set(deliveries)):
        raise ValueError("invalid deliveries")

    artifacts = payload["artifacts"]
    if not isinstance(artifacts, list):
        raise ValueError("invalid artifacts")
    clean_artifacts = []
    artifact_ids = set()
    for raw in artifacts:
        _exact(raw, ARTIFACT_KEYS, "artifact")
        item = {
            "role": _key(raw["role"], "artifact role"),
            "sha256": _hex(raw["sha256"], HEX64_RE, "artifact sha256"),
            "bytes": _number(raw["bytes"], "artifact bytes", nullable=False, integer=True),
        }
        marker = (item["role"], item["sha256"])
        if marker in artifact_ids:
            raise ValueError("duplicate artifact")
        artifact_ids.add(marker)
        clean_artifacts.append(item)

    metrics = payload["metrics"]
    if not isinstance(metrics, list):
        raise ValueError("invalid metrics")
    clean_metrics = [_normalise_metric(raw) for raw in metrics]
    metric_ids = [(m["metric"], m["split"], m["horizon"], m["unit"]) for m in clean_metrics]
    if len(metric_ids) != len(set(metric_ids)):
        raise ValueError("duplicate metric identity")

    tags = payload["tags"]
    if not isinstance(tags, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in tags.items()
    ):
        raise ValueError("invalid tags")

    body = {
        "schema": "governed_terminal.v1",
        "campaign_sha256": campaign["campaign_sha256"],
        "campaign_key": campaign["campaign_key"],
        "classification": campaign["classification"],
        "project": campaign["project"],
        "actor": actor,
        "unit_id": unit_id,
        "generation": generation,
        "status": status,
        "reason": reason,
        "started_at": started_at,
        "finished_at": finished_at,
        "costs": {key: clean_costs[key] for key in sorted(clean_costs)},
        "deliveries": sorted(deliveries),
        "artifacts": sorted(clean_artifacts, key=lambda item: (item["role"], item["sha256"])),
        "metrics": sorted(clean_metrics, key=lambda item: (
            item["metric"], item["split"] or "", item["horizon"] if item["horizon"] is not None else -1,
            item["unit"] or "",
        )),
        "tags": {key: tags[key] for key in sorted(tags)},
        "terminal_lake": campaign["terminal_lake"],
        "config_sha256": campaign["config_sha256"],
        "code_identity": campaign["code_identity"],
        "synthetic_spec_sha256": campaign["synthetic_spec_sha256"],
    }
    return body
