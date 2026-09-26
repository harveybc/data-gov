"""Measure the FRED release-actuals resources under `economic_calendar/release_actuals/`, one record each.

The 2026-09-26 calendar registration registered `cpi_yoy` "as representative of" its siblings and left the
rest out, because naming them the same without measuring each one would be the assumption that work exists
to remove. This tool makes the measurement, in the record shape `tools/register_calendar_resources.py`
already consumes, so the registration reuses that registrar rather than a second one.

What is MEASURED here, per file: the bytes and their digest, the row count, every column's non-null and
null counts and value types, the pandas dtypes, the clock column's span and whether its values are
timezone-aware, the unit column's distinct values, the vintage key ladder and its verdict, the series and
event identity in the rows, and whether the provenance sidecar's declared digest is these bytes.

What is DECLARED here, and nowhere else: which column name plays which catalog role (ROLE_COLUMNS), and
that a role's blocked-case list may be carried over from the sibling the upstream producer already
inventoried. The carry-over is licensed by a measurement, not by a resemblance: a file whose column set is
not identical to that sibling's is recorded `REFUSED_COLUMN_SET_DIFFERS_FROM_THE_MEASURED_SIBLING` and the
registrar then refuses it by name (`INVENTORY_STATUS_...`) instead of registering guessed roles.

    python3 tools/inventory_fred_release_actuals.py \
        --financial-data-root <root> \
        --reference-inventory docs/audits/evidence/CALENDAR_REGISTRATION_2026_09_26/calendar_inventory.v1.redacted.json \
        --out <artifact.json>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

SCHEMA = "data_gov.fred_release_actuals_inventory.v1"
TOOL = "data_gov.tools.inventory_fred_release_actuals.v1"

#: the sibling the upstream `m5phet.calendar_inventory.v1` artifact already measured, and whose record this
#: tool reproduces as a parity check before trusting its own reading of the others
REFERENCE_RESOURCE = "fred_cpi_yoy_actuals"
REFERENCE_SLUG = "cpi_yoy"

RELEASE_ACTUALS = "economic_calendar/release_actuals"

#: DECLARED: which column name, if present, plays which catalog role. A role with no candidate column in a
#: file is absent, and an absence is what it is -- never filled in from a sibling.
ROLE_COLUMNS = {
    "actual": ("actual",),
    "consensus": ("consensus_estimate",),
    "previous": ("previous",),
    "reference_period": ("date",),
    "unit": ("transform",),
    "publication_instant": (),
    "receipt_instant": (),
    "schedule_instant": (),
    "revision_marker": (),
    "vintage_version": (),
    "cancellation_state": (),
    "observed_sequence": (),
    "historical_availability": (),
}

CLOCK_COLUMN = "date"
VALUE_COLUMN = "actual"
SERIES_COLUMN = "fred_series"
EVENT_COLUMN = "event_name"


def digest_file(path: Path) -> str:
    sha = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            sha.update(block)
    return sha.hexdigest()


def _column_stats(frame: pd.DataFrame) -> dict:
    out = {}
    for column in frame.columns:
        values = frame[column]
        null = values.isna()
        types: dict[str, int] = {}
        for value in values[~null]:
            types[type(value).__name__] = types.get(type(value).__name__, 0) + 1
        out[str(column)] = {"non_null": int((~null).sum()), "null_or_empty": int(null.sum()),
                            "types": dict(sorted(types.items()))}
    return out


def _clock(frame: pd.DataFrame) -> dict:
    values = frame[CLOCK_COLUMN] if CLOCK_COLUMN in frame.columns else None
    if values is None:
        return {"column": None, "measured_timezone_aware": False, "declared_timezone_aware": False,
                "reading": "this file has no column named 'date'; no clock was measured"}
    aware = bool(getattr(values.dtype, "tz", None) is not None)
    present = values.dropna()
    first_last = [str(present.iloc[0]), str(present.iloc[-1])] if len(present) else [None, None]
    span = [str(present.min()), str(present.max())] if len(present) else [None, None]
    return {
        "column": CLOCK_COLUMN,
        "declared_timezone_aware": False,
        "measured_timezone_aware": aware,
        "first_and_last_values_in_file_order": first_last,
        "span": span,
        "span_comparison": "chronological" if first_last == span else "not_in_chronological_file_order",
        "values_missing": int(values.isna().sum()),
        "reading": ("NAIVE_WALL_CLOCK: these values name no instant on their own. This column is the REFERENCE "
                    "PERIOD of the value, not a release wall clock, so there is no publication convention to "
                    "measure it against and no era measurement was made for it"
                    if not aware else
                    "the values carry an offset, so each one names an instant without any declaration by this "
                    "catalog"),
    }


def _roles(frame: pd.DataFrame, reference_roles: dict) -> dict:
    out = {}
    for role, candidates in ROLE_COLUMNS.items():
        column = next((c for c in candidates if c in frame.columns), None)
        non_null = int(frame[column].notna().sum()) if column else 0
        role_out = {"column": column, "non_null_rows": non_null, "present": bool(column) and non_null > 0,
                    "cases_it_blocks": list((reference_roles.get(role) or {}).get("cases_it_blocks") or [])}
        if column and non_null == 0:
            role_out["reading"] = ("COLUMN_PRESENT_BUT_EMPTY: the column exists and every row is null, which is "
                                   "the same absence as a missing column for any case that needs a value")
        out[role] = role_out
    return out


def _vintages(frame: pd.DataFrame) -> dict:
    """Can an earlier version of a value be recovered from these bytes? Measured on two keys."""
    ladders = []
    keys = [[SERIES_COLUMN, CLOCK_COLUMN]] if {SERIES_COLUMN, CLOCK_COLUMN} <= set(frame.columns) else []
    keys.append([c for c in frame.columns if c != VALUE_COLUMN])
    for key in keys:
        grouped = frame.groupby([str(k) for k in key], dropna=False)[VALUE_COLUMN]
        distinct = grouped.nunique(dropna=False)
        sizes = grouped.size()
        ladders.append({"key": [str(k) for k in key], "keys": int(len(sizes)),
                        "keys_with_more_than_one_row": int((sizes > 1).sum()),
                        "keys_whose_rows_disagree_about_the_value": int((distinct > 1).sum()),
                        "most_distinct_values_on_one_key": int(distinct.max()) if len(distinct) else 0,
                        "examples": []})
    finest = ladders[-1]
    verdict = "NO_VINTAGES" if finest["keys_whose_rows_disagree_about_the_value"] == 0 else "VINTAGE_UNDECIDABLE"
    return {
        "value_column": VALUE_COLUMN,
        "has_version_field": False,
        "has_observation_clock": False,
        "key_ladder": ladders,
        "verdict": verdict,
        "reading": (f"at the finest key this resource offers ({', '.join(finest['key'])}) every key carries one "
                    "value, so no earlier version of any field survives here and a point-in-time view cannot be "
                    "reconstructed from these bytes"
                    if verdict == "NO_VINTAGES" else
                    "some key carries rows that disagree about the value, and nothing in these bytes says which "
                    "reading came first, so whether they are vintages cannot be decided here"),
    }


def _units(frame: pd.DataFrame) -> dict:
    column = next((c for c in ROLE_COLUMNS["unit"] if c in frame.columns), None)
    if column is None:
        return {"column": None, "distinct": 0, "most_common": [],
                "reading": "no column of these bytes says what its numbers are measured in"}
    counts = frame[column].value_counts(dropna=True)
    return {"column": column, "distinct": int(counts.size),
            "most_common": [[str(value), int(count)] for value, count in counts.head(5).items()]}


def _provenance(directory: Path, measured_sha: str) -> dict:
    sidecar = directory / "provenance.json"
    if not sidecar.is_file():
        return {"present": False, "reading": "no provenance.json accompanies these bytes"}
    body = json.loads(sidecar.read_text(encoding="utf-8"))
    declared = next((f.get("sha256") for f in body.get("files") or []
                     if str(f.get("path", "")).endswith(directory.name + "/actuals.parquet")), None)
    return {
        "present": True,
        "source": body.get("source"),
        "declared_sha256": declared,
        "declared_digest_matches_these_bytes": declared == measured_sha,
        "file_grain_receipt_instant": body.get("acquired_at"),
        "description": body.get("description"),
        "reading": ("`acquired_at` is a FILE-grain receipt clock: it dates the download, not any single release, "
                    "so it cannot order two releases inside the file and is never read as `receipt_instant`"),
    }


def _identity(frame: pd.DataFrame) -> dict:
    def values(column):
        if column not in frame.columns:
            return []
        return sorted(str(v) for v in frame[column].dropna().unique())
    return {"series_column": SERIES_COLUMN, "series": values(SERIES_COLUMN),
            "event_column": EVENT_COLUMN, "events": values(EVENT_COLUMN)}


def measure(root: Path, slug: str, reference_roles: dict, reference_columns: list | None) -> dict:
    path = root / RELEASE_ACTUALS / slug / "actuals.parquet"
    name = f"fred_{slug}_actuals"
    if not path.is_file():
        return {"resource": name, "slug": slug, "status": "REFUSED_BYTES_ABSENT",
                "path_in_root": str(path.relative_to(root)),
                "reading": "no actuals.parquet under this directory, so nothing about it was measured"}
    measured_sha = digest_file(path)
    frame = pd.read_parquet(path)
    # the upstream artifact records `columns` as a mapping, so its order is not retained: the comparison is on
    # the SET of column names, sorted, which is the same basis on both sides
    columns = sorted(str(c) for c in frame.columns)
    record = {
        "resource": name, "slug": slug, "status": "MEASURED",
        "path_in_root": str(path.relative_to(root)),
        "bytes": path.stat().st_size, "sha256": measured_sha, "rows": int(len(frame)),
        "format": "parquet",
        "columns": _column_stats(frame),
        "pandas_dtypes": {str(c): str(frame[c].dtype) for c in frame.columns},
        "identity": _identity(frame),
        "clock": _clock(frame),
        "field_roles": _roles(frame, reference_roles),
        "units": _units(frame),
        "vintages": _vintages(frame),
        "provenance": _provenance(path.parent, measured_sha),
        "reading": ("measured from these bytes by " + TOOL),
    }
    if reference_columns is not None and columns != reference_columns:
        record["status"] = "REFUSED_COLUMN_SET_DIFFERS_FROM_THE_MEASURED_SIBLING"
        record["reading"] = (
            f"this file's columns {columns} are not the columns of {REFERENCE_RESOURCE} ({reference_columns}), so "
            "the role assignment measured for that sibling cannot be carried over to it and no role is guessed here")
    if record["provenance"].get("present") and not record["provenance"]["declared_digest_matches_these_bytes"]:
        record["status"] = "REFUSED_PROVENANCE_DIGEST_MISMATCH"
        record["reading"] = (
            "the provenance sidecar declares a digest these bytes do not have, so the sidecar's acquisition clock "
            "and source cannot be registered as facts about this file")
    return record


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--financial-data-root", required=True, help="root the financial lake serves")
    parser.add_argument("--reference-inventory", required=True,
                        help=f"an inventory artifact carrying the measured record of {REFERENCE_RESOURCE}")
    parser.add_argument("--out", required=True, help="where to write the artifact")
    parser.add_argument("--include-reference", action="store_true",
                        help=f"also emit a record for {REFERENCE_SLUG} (it is already registered; this is the parity check)")
    args = parser.parse_args(argv)

    root = Path(args.financial_data_root)
    reference_body = json.loads(Path(args.reference_inventory).read_text(encoding="utf-8"))
    reference = next((r for r in reference_body.get("resources", []) if r.get("resource") == REFERENCE_RESOURCE), None)
    if reference is None:
        print(f"REFUSED: {args.reference_inventory} carries no record of {REFERENCE_RESOURCE}, so neither the role "
              "case lists nor the column set they were measured on is available", file=sys.stderr)
        return 2
    reference_roles = reference.get("field_roles") or {}
    reference_columns = sorted(reference.get("columns") or {}) or None

    slugs = sorted(p.name for p in (root / RELEASE_ACTUALS).iterdir()
                   if p.is_dir() and (p / "actuals.parquet").is_file())
    if not args.include_reference:
        slugs = [s for s in slugs if s != REFERENCE_SLUG]
    records = []
    for slug in slugs:
        records.append(measure(root, slug, reference_roles, reference_columns))
    artifact = {
        "schema": SCHEMA, "tool": TOOL,
        "root_kind": "financial_data_root",
        "family": "fred_release_actuals",
        "reference": {"resource": REFERENCE_RESOURCE,
                      "artifact": {"file": Path(args.reference_inventory).name,
                                   "sha256": digest_file(Path(args.reference_inventory)),
                                   "schema": reference_body.get("schema")},
                      "role_case_lists_carried_over": sorted(reference_roles),
                      "column_set_compared": reference_columns,
                      "why": ("the blocked-case list of each role is the upstream producer's, carried over only to "
                              "files whose column set is measured identical to the sibling it was measured on")},
        "declared": {"role_columns": {k: list(v) for k, v in ROLE_COLUMNS.items()},
                     "clock_column": CLOCK_COLUMN, "value_column": VALUE_COLUMN},
        "resources": records,
        "reading": ("one record per FRED release-actuals resource, measured from its own bytes. The absolute root is "
                    "deliberately not recorded: it is host configuration, not a fact about a resource"),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    measured = [r for r in records if r["status"] == "MEASURED"]
    print(f"wrote {out} ({len(measured)} measured, {len(records) - len(measured)} refused, "
          f"digest {digest_file(out)})")
    for record in records:
        print(f"  {record['status']:<12} {record['resource']:<34} rows={record.get('rows')} "
              f"series={(record.get('identity') or {}).get('series')} sha={str(record.get('sha256'))[:12]}")
    return 0 if measured else 1


if __name__ == "__main__":                                       # pragma: no cover - a CLI
    sys.exit(main())
