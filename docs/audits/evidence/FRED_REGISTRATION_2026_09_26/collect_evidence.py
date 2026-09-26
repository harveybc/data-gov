"""Collect the FRED sibling registration evidence from the live registry, and redact the run reports.

Reproduce from the repository root:

    python3 docs/audits/evidence/FRED_REGISTRATION_2026_09_26/collect_evidence.py \
        --registry <repo>/var/registry --as-of 2026-09-26T23:59:59+00:00

It writes `registrations.v1.json` (the nine rows in force, as the registry serves them) and a redacted copy of
every `*_run.v1.json` / `reread_*.v1.json` beside it, recording each original's digest. It also CHECKS, and
refuses to write if either fails: that no row grants execution, and that the five calendar slots registered on
2026-09-26 still carry exactly the facts digests the retained calendar evidence recorded -- this action appended
nine slots and revised none.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
sys.path.insert(0, str(REPO))

from data_gov.resource_registration import ResourceRegistry  # noqa: E402

CALENDAR_EVIDENCE = REPO / "docs/audits/evidence/CALENDAR_REGISTRATION_2026_09_26/registrations.v1.json"
FRED_PREFIX = "economic_calendar/release_actuals/"
#: the two release_actuals resources that are NOT the nine siblings this action registered. Compared as whole
#: paths, never by suffix: `core_cpi_yoy/actuals.parquet` ends with `cpi_yoy/actuals.parquet`.
NOT_A_SIBLING = (FRED_PREFIX + "fxmacrodata/announcements.parquet",
                 FRED_PREFIX + "cpi_yoy/actuals.parquet")


def digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def redact(value, replacements):
    if isinstance(value, str):
        for needle, token in replacements:
            value = value.replace(needle, token)
        return value
    if isinstance(value, dict):
        return {k: redact(v, replacements) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, replacements) for v in value]
    return value


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registry", required=True)
    parser.add_argument("--as-of", required=True, help="the instant to read the catalog at")
    args = parser.parse_args(argv)

    registry = ResourceRegistry(args.registry)
    in_force = registry.known_at(args.as_of)
    siblings = [row for row in in_force
                if row["resource"].startswith(FRED_PREFIX) and row["resource"] not in NOT_A_SIBLING]

    granted = [row["resource"] for row in in_force if row.get("execution_authorized")]
    if granted:
        print(f"REFUSED: these rows grant execution: {granted}", file=sys.stderr)
        return 2

    calendar = json.loads(CALENDAR_EVIDENCE.read_text(encoding="utf-8"))
    expected = {row["resource"]: row["facts_sha256"] for row in calendar["in_force"]}
    now = {row["resource"]: row["facts_sha256"] for row in in_force}
    moved = {resource: (expected[resource], now.get(resource))
             for resource in expected if now.get(resource) != expected[resource]}
    if moved:
        print(f"REFUSED: a calendar slot's facts moved since 2026-09-26: {moved}", file=sys.stderr)
        return 2

    replacements = [(str(Path.home() / "Documents/GitHub"), "<repos>"), (str(Path.home()), "<home>")]
    out = {
        "schema": "data_gov.fred_sibling_registration_evidence.v1",
        "registry": "the local data-gov instance's var/registry (gitignored); these are its bytes",
        "rows_in_force_at": args.as_of,
        "rows_in_force": len(siblings),
        "catalog_rows_in_force": len(in_force),
        "calendar_slots_unchanged": sorted(expected),
        "calendar_slots_unchanged_reading": (
            "each of the five calendar slots still carries the facts digest the 2026-09-26 calendar evidence "
            "recorded: this action appended nine slots and revised none of theirs"),
        "every_row_grants_nothing": True,
        "replay": redact(registry.replay(), replacements),
        "in_force": redact(siblings, replacements),
        "reading": ("the nine sibling FRED actuals registrations in force. `cpi_yoy` was registered on 2026-09-26 as "
                    "representative of them; representative is not measured, so each of these was measured from its "
                    "own bytes. Every one is CLOSED_NO_AVAILABILITY_CONTRACT and authorizes nothing"),
    }
    (HERE / "registrations.v1.json").write_text(
        json.dumps(out, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")

    redacted = []
    for report in sorted(HERE.glob("*run.v1.json")) + sorted(HERE.glob("reread_*.v1.json")):
        body = json.loads(report.read_text(encoding="utf-8"))
        body = redact(body, replacements)
        body["_redaction"] = {
            "schema": "data_gov.evidence_redaction.v1",
            "what_was_replaced": "absolute filesystem roots, replaced by <repos> and <home>",
            "why": "this repository carries no private paths or account identifiers",
            "original_sha256": digest_file(report),
            "original_file": report.name,
        }
        target = HERE / (report.stem + ".redacted.json")
        target.write_text(json.dumps(body, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        redacted.append((report.name, target.name))

    print(f"wrote registrations.v1.json ({len(siblings)} rows in force of {len(in_force)} in the catalog)")
    for original, target in redacted:
        print(f"  redacted {original} -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
