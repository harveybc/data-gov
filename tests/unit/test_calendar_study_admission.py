"""Offline admission is based on catalog knowledge at T, never on live source bytes."""

import copy
import json
import subprocess
import sys

import pytest

from data_gov.calendar_study_admission import admit_study
from data_gov.resource_registration import ResourceRegistry


def row(registry, resource, at, *, consensus=False, observed=False, coverage=None,
        delivery="OPEN_AVAILABILITY_CONTRACT_INSTALLED", content="a" * 64):
    facts = {
        "what_it_is": "synthetic calendar fixture",
        "physical_location": {"lake": "financial_files", "resource_path_in_root": resource},
        "coverage": coverage or {"status": "MEASURED", "from": "2025-01-01", "to": "2025-02-01"},
        "clock": {"column": "published_at"},
        "publication_instant": {"kind": "OBSERVED_ACTUAL_PUBLICATION" if observed else
                                "ASSUMED_SCHEDULED_PUBLICATION", "observed": observed},
        "carries": {"consensus": consensus, "consensus_non_null_rows": 1 if consensus else 0},
        "absences": [{"code": "UNKNOWN", "why": "fixture"}],
        "governed_delivery": {"state": delivery},
        "measurement_provenance": {"tool": "fixture"},
    }
    return registry.append({"lake": "financial_files", "resource": resource,
                            "content_sha256": content, "facts": facts,
                            "registered_at": at, "registered_by": "test"})[0]


def requirements(*rows):
    return {"resources": [{"lake": r["lake"], "resource": r["resource"],
                            "row_sha256": r["row_sha256"],
                            "content_sha256": r["content_sha256"]} for r in rows],
            "require_governed_delivery": True, "require_consensus_observed_overlap": True}


def codes(result):
    return {r["code"] for r in result["refusals"]}


def test_admits_pinned_available_overlapping_revisions(tmp_path):
    registry = ResourceRegistry(tmp_path / "registry")
    a = row(registry, "consensus", "2025-03-01T00:00:00Z", consensus=True)
    b = row(registry, "observed", "2025-03-01T00:00:00Z", observed=True)
    result = admit_study([a, b], "2025-03-02T00:00:00Z", requirements(a, b))
    assert result["verdict"] == "ADMITTED_OFFLINE"
    assert result["evidence"]["overlap"]["pairs"][0]["overlap"] == ["2025-01-01", "2025-02-01"]
    assert {e["row_sha256"] for e in result["evidence"]["resources"]} == {a["row_sha256"], b["row_sha256"]}


def test_future_row_cannot_cure_a_refusal_and_future_only_row_is_absent(tmp_path):
    registry = ResourceRegistry(tmp_path / "registry")
    old = row(registry, "consensus", "2025-03-01T00:00:00Z", consensus=True,
              delivery="CLOSED_NO_AVAILABILITY_CONTRACT")
    future = row(registry, "consensus", "2025-04-01T00:00:00Z", consensus=True, content="b" * 64)
    observed = row(registry, "observed", "2025-04-01T00:00:00Z", observed=True)
    result = admit_study([old, future, observed], "2025-03-02T00:00:00Z", requirements(future, observed))
    assert "RESOURCE_REVISION_MISMATCH" in codes(result)
    assert "RESOURCE_NOT_KNOWN_AT_T" in codes(result)
    assert "GOVERNED_DELIVERY_UNAVAILABLE" in codes(result)
    assert result["verdict"] == "REFUSED"


def test_unknown_availability_and_nonoverlap_are_named(tmp_path):
    registry = ResourceRegistry(tmp_path / "registry")
    a = row(registry, "consensus", "2025-03-01T00:00:00Z", consensus=True,
            delivery="CLOSED_NO_AVAILABILITY_CONTRACT",
            coverage={"status": "MEASURED", "from": "2025-01-01", "to": "2025-01-31"})
    b = row(registry, "observed", "2025-03-01T00:00:00Z", observed=True,
            coverage={"status": "MEASURED", "from": "2025-02-01", "to": "2025-02-28"})
    result = admit_study([a, b], "2025-03-02T00:00:00Z", requirements(a, b))
    assert {"GOVERNED_DELIVERY_UNAVAILABLE", "NO_CONSENSUS_OBSERVED_OVERLAP"} <= codes(result)
    assert result["evidence"]["overlap"]["pairs"][0]["overlap"] is None


def test_unknown_coverage_and_missing_role_fail_closed(tmp_path):
    registry = ResourceRegistry(tmp_path / "registry")
    a = row(registry, "consensus", "2025-03-01T00:00:00Z", consensus=True,
            coverage={"status": "UNKNOWN", "why": "unmeasured"})
    b = row(registry, "actual", "2025-03-01T00:00:00Z")
    result = admit_study([a, b], "2025-03-02T00:00:00Z", requirements(a, b))
    assert "OBSERVED_PUBLICATION_SOURCE_MISSING" in codes(result)
    b2 = row(registry, "observed", "2025-03-01T00:00:00Z", observed=True)
    result = admit_study([a, b2], "2025-03-02T00:00:00Z", requirements(a, b2))
    assert "COVERAGE_UNKNOWN" in codes(result)


def test_tampered_snapshot_cannot_admit(tmp_path):
    registry = ResourceRegistry(tmp_path / "registry")
    a = row(registry, "consensus", "2025-03-01T00:00:00Z", consensus=True)
    b = row(registry, "observed", "2025-03-01T00:00:00Z", observed=True)
    altered = copy.deepcopy(a)
    altered["facts"]["governed_delivery"]["state"] = "CLOSED_NO_AVAILABILITY_CONTRACT"
    result = admit_study([altered, b], "2025-03-02T00:00:00Z", requirements(a, b))
    assert "SNAPSHOT_ROW_INVALID" in codes(result)
    assert result["verdict"] == "REFUSED"


def test_unknown_delivery_state_is_not_treated_as_open(tmp_path):
    registry = ResourceRegistry(tmp_path / "registry")
    a = row(registry, "consensus", "2025-03-01T00:00:00Z", consensus=True)
    b = row(registry, "observed", "2025-03-01T00:00:00Z", observed=True)
    changed = copy.deepcopy(a)
    changed["facts"]["governed_delivery"]["state"] = "UNKNOWN"
    from data_gov.resource_registration import digest, SCHEMA
    changed["facts_sha256"] = digest({"schema": SCHEMA, "facts": changed["facts"]})
    changed["row_sha256"] = digest({k: v for k, v in changed.items() if k != "row_sha256"})
    result = admit_study([changed, b], "2025-03-02T00:00:00Z", requirements(changed, b))
    assert "AVAILABILITY_UNKNOWN" in codes(result) or "SNAPSHOT_ROW_INVALID" in codes(result)


def test_cli_reads_snapshot_without_writing_it(tmp_path):
    registry = ResourceRegistry(tmp_path / "registry")
    a = row(registry, "consensus", "2025-03-01T00:00:00Z", consensus=True)
    b = row(registry, "observed", "2025-03-01T00:00:00Z", observed=True)
    snapshot = tmp_path / "snapshot.json"
    req = tmp_path / "requirements.json"
    snapshot.write_text(json.dumps({"registrations": [a, b]}), encoding="utf-8")
    req.write_text(json.dumps(requirements(a, b)), encoding="utf-8")
    before = snapshot.read_bytes()
    run = subprocess.run([sys.executable, "-m", "data_gov.calendar_study_admission",
                          "--snapshot", str(snapshot), "--as-of", "2025-03-02T00:00:00Z",
                          "--requirements", str(req)], capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["verdict"] == "ADMITTED_OFFLINE"
    assert snapshot.read_bytes() == before
    req_body = requirements(a, b)
    req_body["resources"][0]["row_sha256"] = "0" * 64
    req.write_text(json.dumps(req_body), encoding="utf-8")
    refused = subprocess.run([sys.executable, "-m", "data_gov.calendar_study_admission",
                              "--snapshot", str(snapshot), "--as-of", "2025-03-02T00:00:00Z",
                              "--requirements", str(req)], capture_output=True, text=True, check=False)
    assert refused.returncode == 2
    assert "RESOURCE_REVISION_MISMATCH" in {r["code"] for r in json.loads(refused.stdout)["refusals"]}


def test_malformed_snapshot_row_is_a_named_refusal(tmp_path):
    registry = ResourceRegistry(tmp_path / "registry")
    a = row(registry, "consensus", "2025-03-01T00:00:00Z", consensus=True)
    b = row(registry, "observed", "2025-03-01T00:00:00Z", observed=True)
    result = admit_study([a, b, {"row_id": ["not", "a", "key"]}],
                         "2025-03-02T00:00:00Z", requirements(a, b))
    assert result["verdict"] == "REFUSED"
    assert "SNAPSHOT_ROW_INVALID" in codes(result)


def test_requires_explicit_pins_and_offset():
    with pytest.raises(ValueError, match="REQUIREMENTS_INVALID"):
        admit_study([], "2025-03-02T00:00:00Z", {"resources": []})
    with pytest.raises(ValueError, match="AMBIGUOUS_LOCAL_TIME"):
        admit_study([], "2025-03-02T00:00:00", requirements())
