"""Pure, offline calendar-study admission against immutable registration snapshots.

This is a catalog decision, not delivery authorization or scientific identification.
No registry directory, source file, network service, or mutable clock is consulted.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from data_gov.resource_registration import ResourceRegistry, instant

SCHEMA = "data_gov.calendar_study_admission.v1"
_READ_DECORATIONS = {"integrity", "integrity_problems", "recomputed_sha256", "derived_row_id", "quarantined"}


def _refuse(refusals, code, **evidence):
    refusals.append({"code": code, **evidence})


def _requirements(requirements):
    if not isinstance(requirements, dict) or not isinstance(requirements.get("resources"), list) or not requirements["resources"]:
        raise ValueError("REQUIREMENTS_INVALID: nonempty resources list required")
    for flag in ("require_governed_delivery", "require_consensus_observed_overlap"):
        if type(requirements.get(flag)) is not bool:
            raise ValueError(f"REQUIREMENTS_INVALID: explicit boolean {flag} required")
    seen = set()
    for item in requirements["resources"]:
        if not isinstance(item, dict) or not all(isinstance(item.get(k), str) and item[k] for k in
                                                 ("lake", "resource", "row_sha256", "content_sha256")):
            raise ValueError("REQUIREMENTS_INVALID: each resource needs lake, resource, row_sha256 and content_sha256")
        slot = (item["lake"], item["resource"])
        if slot in seen:
            raise ValueError("REQUIREMENTS_INVALID: duplicate resource slot")
        seen.add(slot)


def _window(row):
    coverage = row["facts"].get("coverage") or {}
    if coverage.get("status") != "MEASURED":
        return None
    try:
        start = date.fromisoformat(coverage["from"][:10])
        end = date.fromisoformat(coverage["to"][:10])
        return (start, end) if start <= end else None
    except (KeyError, TypeError, ValueError):
        return None


def admit_study(snapshot, as_of, requirements):
    """Return a structured verdict using only full registry rows and explicit pins.

    A snapshot is a list of full rows (not summaries). The caller must preserve all
    revisions: a sliced snapshot cannot establish what revision was known at T.
    """
    cutoff = instant(as_of, "as_of").isoformat()
    _requirements(requirements)
    if not isinstance(snapshot, list):
        raise ValueError("SNAPSHOT_INVALID: expected a list of full registration rows")
    refusals, selected, evidence = [], {}, []
    for raw in snapshot:
        if not isinstance(raw, dict):
            _refuse(refusals, "SNAPSHOT_ROW_INVALID", reason="row is not a mapping")
            continue
        clean = {k: v for k, v in raw.items() if k not in _READ_DECORATIONS}
        try:
            checked = ResourceRegistry._checked(clean, expected_key=clean.get("row_id"))
        except (TypeError, ValueError, KeyError):
            _refuse(refusals, "SNAPSHOT_ROW_INVALID", row_id=raw.get("row_id"),
                    problems=["ROW_NOT_CHECKABLE"])
            continue
        if checked["integrity"] != "OK":
            _refuse(refusals, "SNAPSHOT_ROW_INVALID", row_id=raw.get("row_id"),
                    problems=checked["integrity_problems"])
            continue
        slot = (clean.get("lake"), clean.get("resource"))
        try:
            registered = instant(clean.get("registered_at"), "registered_at").isoformat()
        except ValueError:
            _refuse(refusals, "SNAPSHOT_ROW_INVALID", row_id=clean.get("row_id"), problems=["INVALID_REGISTERED_AT"])
            continue
        if registered <= cutoff:
            previous = selected.get(slot)
            if previous is None or (registered, clean["row_sha256"]) > (previous["registered_at"], previous["row_sha256"]):
                selected[slot] = clean

    chosen = []
    for pin in requirements["resources"]:
        slot = (pin["lake"], pin["resource"])
        row = selected.get(slot)
        if row is None:
            _refuse(refusals, "RESOURCE_NOT_KNOWN_AT_T", lake=slot[0], resource=slot[1])
            continue
        chosen.append(row)
        delivery = (row["facts"].get("governed_delivery") or {}).get("state")
        resource_evidence = {"lake": slot[0], "resource": slot[1], "row_sha256": row["row_sha256"],
                             "revision_index": row.get("revision_index"), "registered_at": row["registered_at"],
                             "content_sha256": row.get("content_sha256"), "delivery_state": delivery,
                             "coverage": row["facts"].get("coverage")}
        evidence.append(resource_evidence)
        if row["row_sha256"] != pin["row_sha256"] or row.get("content_sha256") != pin["content_sha256"]:
            _refuse(refusals, "RESOURCE_REVISION_MISMATCH", **resource_evidence,
                    required_row_sha256=pin["row_sha256"], required_content_sha256=pin["content_sha256"])
        if requirements["require_governed_delivery"]:
            if delivery not in ("CLOSED_NO_AVAILABILITY_CONTRACT", "CLOSED_NOT_IN_ANY_LAKE_ROOT",
                                "OPEN_AVAILABILITY_CONTRACT_INSTALLED"):
                _refuse(refusals, "AVAILABILITY_UNKNOWN", **resource_evidence)
            elif delivery != "OPEN_AVAILABILITY_CONTRACT_INSTALLED":
                _refuse(refusals, "GOVERNED_DELIVERY_UNAVAILABLE", **resource_evidence)

    overlap = {"pairs": []}
    if requirements["require_consensus_observed_overlap"]:
        consensus = [r for r in chosen if (r["facts"].get("carries") or {}).get("consensus") is True
                     and (r["facts"].get("carries") or {}).get("consensus_non_null_rows", 0) > 0]
        observed = [r for r in chosen if (r["facts"].get("publication_instant") or {}).get("kind") ==
                    "OBSERVED_ACTUAL_PUBLICATION" and
                    (r["facts"].get("publication_instant") or {}).get("observed") is True]
        if not consensus:
            _refuse(refusals, "CONSENSUS_SOURCE_MISSING")
        if not observed:
            _refuse(refusals, "OBSERVED_PUBLICATION_SOURCE_MISSING")
        any_overlap = False
        for left in consensus:
            for right in observed:
                lw, rw = _window(left), _window(right)
                pair = {"consensus_row_sha256": left["row_sha256"], "observed_row_sha256": right["row_sha256"],
                        "consensus_window": [x.isoformat() for x in lw] if lw else None,
                        "observed_window": [x.isoformat() for x in rw] if rw else None, "overlap": None}
                if lw is None or rw is None:
                    _refuse(refusals, "COVERAGE_UNKNOWN", **pair)
                else:
                    start, end = max(lw[0], rw[0]), min(lw[1], rw[1])
                    if start <= end:
                        pair["overlap"] = [start.isoformat(), end.isoformat()]
                        any_overlap = True
                overlap["pairs"].append(pair)
        if consensus and observed and not any_overlap and not any(r["code"] == "COVERAGE_UNKNOWN" for r in refusals):
            _refuse(refusals, "NO_CONSENSUS_OBSERVED_OVERLAP", pairs=overlap["pairs"])
    return {"schema": SCHEMA, "as_of": cutoff, "verdict": "REFUSED" if refusals else "ADMITTED_OFFLINE",
            "requirements": requirements, "refusals": refusals,
            "evidence": {"resources": evidence, "overlap": overlap},
            "scope": "Catalog-only; no delivery authorization or scientific causal identification"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline calendar-study admission from a full-row registry snapshot")
    parser.add_argument("--snapshot", required=True, help="JSON from resource_registration --json (all revisions)")
    parser.add_argument("--as-of", required=True, help="catalog decision instant with UTC offset")
    parser.add_argument("--requirements", required=True, help="JSON file with pinned resources and explicit requirements")
    args = parser.parse_args(argv)
    try:
        body = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
        snapshot = body.get("registrations") if isinstance(body, dict) else body
        requirements = json.loads(Path(args.requirements).read_text(encoding="utf-8"))
        result = admit_study(snapshot, args.as_of, requirements)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["verdict"] == "ADMITTED_OFFLINE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
