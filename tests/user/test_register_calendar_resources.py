"""The five economic-calendar registrations: derived from measurements, absences and all, granting nothing.

The fixtures here are small synthetic files whose digests the synthetic inventory declares, so the derivation is
exercised end to end without reading the real archives (121,658 rows) and without depending on a data root existing.
What the real run produced is recorded in `docs/audits/evidence/CALENDAR_REGISTRATION_2026_09_26/`.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("register_calendar_resources",
                                               ROOT / "tools" / "register_calendar_resources.py")
rcr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rcr)

from data_gov.resource_registration import NOT_IN_ANY_LAKE_ROOT, ResourceRegistry  # noqa: E402

ARCHIVE = "archive_2011_2021"
ANNOUNCEMENTS = "fxmacrodata_announcements"
CALENDAR = "fxmacrodata_release_calendar"
PROXY = "fred_release_date_proxy"
CPI = "fred_cpi_yoy_actuals"

ALL_ROLES = ("schedule_instant", "consensus", "actual", "previous", "revision_marker", "publication_instant",
             "receipt_instant", "unit", "reference_period", "observed_sequence", "historical_availability",
             "vintage_version", "cancellation_state")


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _roles(**present):
    return {role: {"present": role in present,
                   "column": present[role][0] if role in present else None,
                   "non_null_rows": present[role][1] if role in present else 0,
                   "cases_it_blocks": [] if role in present else ["CAL0X"]}
            for role in ALL_ROLES}


@pytest.fixture()
def world(tmp_path):
    """Two roots holding the five resources, and the two measurement artifacts that describe them."""
    fin = tmp_path / "financial-data"
    fen = tmp_path / "feature-eng"
    paths = {}

    archive = fen / "tests/data/economic_calendar_2011_2021.csv"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text("2011/01/01,4:00:00,United States,3,CPI,better,%,1.5,1.4,1.3\n"
                       "2021/04/26,9:00:00,United States,3,CPI,worse,%,2.5,2.6,2.4\n", encoding="utf-8")
    paths[ARCHIVE] = archive

    def parquet(relative, frame):
        target = fin / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(target, index=False)
        (target.parent / "provenance.json").write_text(json.dumps(
            {"source": "fixture", "acquired_at": "2026-05-01T23:38:43+00:00",
             "files": [{"path": target.name, "sha256": _sha(target)}]}), encoding="utf-8")
        return target

    paths[ANNOUNCEMENTS] = parquet(
        "economic_calendar/release_actuals/fxmacrodata/announcements.parquet",
        pd.DataFrame({"currency": ["USD", "USD"], "indicator": ["cpi", "cpi"],
                      "date": ["2024-12-01", "2026-04-01"], "val": [1.0, 2.0],
                      "announcement_datetime_utc": pd.to_datetime(
                          ["2024-12-12T08:30:00Z", "2026-05-01T23:36:47Z"], utc=True)}))
    paths[CALENDAR] = parquet(
        "economic_calendar/scheduled_events/fxmacrodata/release_calendar.parquet",
        pd.DataFrame({"currency": ["USD"], "release": ["cpi"],
                      "announcement_datetime_utc": pd.to_datetime(["2027-07-14T18:00:00Z"], utc=True)}))
    paths[PROXY] = parquet(
        "economic_calendar/scheduled_events/fred_release_date_proxy/scheduled_events.parquet",
        pd.DataFrame({"event_slug": ["cpi"], "scheduled_date_proxy": ["1996-01-01"],
                      "actual": [1.0], "consensus_estimate": [None]}))
    paths[CPI] = parquet(
        "economic_calendar/release_actuals/cpi_yoy/actuals.parquet",
        pd.DataFrame({"date": pd.to_datetime(["1990-01-01"]), "actual": [1.0],
                      "transform": ["yoy_pct_change"], "consensus_estimate": [None]}))

    inventory = {"schema": "m5phet.calendar_inventory.v1", "resources": [
        {"resource": ARCHIVE, "status": "MEASURED", "sha256": _sha(paths[ARCHIVE]), "rows": 2, "bytes": 1,
         "header_row": False, "kind": "csv_no_header",
         "clock": {"column": "event_date+event_time", "measured_timezone_aware": False},
         "provenance": {"present": False, "reading": "no provenance.json beside the archive"},
         "units": {"column": "data_format", "most_common": [["%", 2]]},
         "vintages": {"verdict": "VINTAGE_UNDECIDABLE", "value_column": "actual",
                      "reading": "two values at the finest key and nothing to date them"},
         "field_roles": _roles(schedule_instant=("event_date+event_time", 2), consensus=("forecast", 2),
                               actual=("actual", 2), previous=("previous", 2), unit=("data_format", 2))},
        {"resource": ANNOUNCEMENTS, "status": "MEASURED", "sha256": _sha(paths[ANNOUNCEMENTS]), "rows": 2, "bytes": 1,
         "kind": "parquet", "clock": {"column": "announcement_datetime_utc", "measured_timezone_aware": True},
         "provenance": {"present": True, "file_grain_receipt_instant": "2026-05-01T23:38:43+00:00"},
         "units": {"column": None, "reading": "NO_UNIT_COLUMN"},
         "vintages": {"verdict": "VINTAGE_UNDECIDABLE", "value_column": "val", "reading": "undecidable"},
         "field_roles": _roles(actual=("val", 2), publication_instant=("announcement_datetime_utc", 2),
                               reference_period=("date", 2))},
        {"resource": CALENDAR, "status": "MEASURED", "sha256": _sha(paths[CALENDAR]), "rows": 1, "bytes": 1,
         "kind": "parquet", "clock": {"column": "announcement_datetime_utc", "measured_timezone_aware": True},
         "provenance": {"present": True, "file_grain_receipt_instant": "2026-05-01T23:38:43+00:00"},
         "units": {"column": None, "reading": "NO_UNIT_COLUMN"},
         "vintages": {"verdict": "NOT_APPLICABLE_NO_VALUE_COLUMN", "value_column": None,
                      "reading": "schedules, not values"},
         "field_roles": _roles(schedule_instant=("announcement_datetime_utc", 1))},
        {"resource": PROXY, "status": "MEASURED", "sha256": _sha(paths[PROXY]), "rows": 1, "bytes": 1,
         "kind": "parquet", "clock": {"column": "scheduled_date_proxy", "measured_timezone_aware": False},
         "provenance": {"present": True, "file_grain_receipt_instant": "2026-05-01T21:19:26+00:00"},
         "units": {"column": None, "reading": "NO_UNIT_COLUMN"},
         "vintages": {"verdict": "NO_VINTAGES", "value_column": "actual", "reading": "one value per key"},
         "field_roles": dict(_roles(schedule_instant=("scheduled_date_proxy", 1), actual=("actual", 1)),
                             consensus={"present": False, "column": "consensus_estimate", "non_null_rows": 0,
                                        "cases_it_blocks": ["CAL03", "CAL06"]})},
        {"resource": CPI, "status": "MEASURED", "sha256": _sha(paths[CPI]), "rows": 1, "bytes": 1,
         "kind": "parquet", "clock": {"column": "date", "measured_timezone_aware": False},
         "provenance": {"present": True, "file_grain_receipt_instant": "2026-05-01T16:42:56+00:00"},
         "units": {"column": "transform", "most_common": [["yoy_pct_change", 1]]},
         "vintages": {"verdict": "NO_VINTAGES", "value_column": "actual", "reading": "one value per key"},
         "field_roles": dict(_roles(actual=("actual", 1), reference_period=("date", 1), unit=("transform", 1)),
                             consensus={"present": False, "column": "consensus_estimate", "non_null_rows": 0,
                                        "cases_it_blocks": ["CAL03", "CAL06"]})},
    ]}
    clock = {"schema": "m5phet.calendar_clock.v1",
             "provenance": "MEASURED_FROM_THE_ARCHIVE_AGAINST_OBSERVED_PUBLICATION_CONVENTIONS",
             "periods": [
                 {"start_date": "2011-01-01", "end_date": "2012-04-30", "status": "UNDETERMINED",
                  "utc_offset": None, "utc_offset_seconds": None, "reason": "ESTIMATES_DISAGREE", "n": 261,
                  "confidence": 0.78},
                 {"start_date": "2012-05-01", "end_date": "2018-01-31", "status": "DETERMINED",
                  "utc_offset": "UTC-05:00", "utc_offset_seconds": -18000, "reason": None, "n": 1183,
                  "confidence": 1.0},
                 {"start_date": "2018-03-01", "end_date": "2018-08-31", "status": "DETERMINED",
                  "utc_offset": "UTC-04:00", "utc_offset_seconds": -14400, "reason": None, "n": 108,
                  "confidence": 1.0}]}
    inventory_path = tmp_path / "inventory.json"
    clock_path = tmp_path / "clock.json"
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
    clock_path.write_text(json.dumps(clock), encoding="utf-8")
    return {"fin": fin, "fen": fen, "inventory": inventory_path, "clock": clock_path, "paths": paths,
            "registry": tmp_path / "registry"}


def _run(world, extra=()):
    return rcr.main(["--inventory", str(world["inventory"]), "--clock", str(world["clock"]),
                     "--registry", str(world["registry"]),
                     "--financial-data-root", str(world["fin"]),
                     "--feature-eng-root", str(world["fen"]),
                     "--actor", "satoshi-iii", "--registered-at", "2026-09-26T00:00:00+00:00", *extra])


def _rows(world):
    return {row["resource"]: row for row in ResourceRegistry(world["registry"]).rows()}


def test_all_five_are_registered_and_none_is_opened(world):
    assert _run(world) == 0
    rows = _rows(world)
    assert len(rows) == 5
    states = {row["facts"]["governed_delivery"]["state"] for row in rows.values()}
    assert states == {"CLOSED_NO_AVAILABILITY_CONTRACT", "CLOSED_NOT_IN_ANY_LAKE_ROOT"}
    assert all(row["execution_authorized"] is False for row in rows.values())


def test_the_archive_is_registered_outside_every_lake_root(world):
    _run(world)
    archive = _rows(world)["tests/data/economic_calendar_2011_2021.csv"]
    assert archive["lake"] == NOT_IN_ANY_LAKE_ROOT
    assert archive["facts"]["governed_delivery"]["state"] == "CLOSED_NOT_IN_ANY_LAKE_ROOT"
    # the registration must not name the absolute root: that is host configuration, not a fact about the resource
    assert str(world["fen"]) not in json.dumps(archive)


def test_the_measured_clock_travels_into_the_registration_with_its_eras(world):
    _run(world)
    clock = _rows(world)["tests/data/economic_calendar_2011_2021.csv"]["facts"]["clock"]
    assert clock["evidence"] == "MEASURED_FROM_DATA_NOT_DOCUMENTED_BY_THE_SOURCE"
    assert clock["documented_by_the_source"] is False
    assert clock["eras_determined"] == 2 and clock["eras_undetermined"] == 1
    assert clock["distinct_offsets"] == ["UTC-04:00", "UTC-05:00"]
    assert [e["utc_offset"] for e in clock["eras"]] == [None, "UTC-05:00", "UTC-04:00"]


def test_a_measured_clock_is_still_not_a_producer_statement(world):
    """Measuring a zone is evidence. It is not documentation, and both absences must be findable by code."""
    _run(world)
    codes = {a["code"] for a in _rows(world)["tests/data/economic_calendar_2011_2021.csv"]["facts"]["absences"]}
    assert "CLOCK_MEASURED_NOT_DOCUMENTED" in codes
    assert "NO_TIMEZONE_DECLARED_BY_THE_SOURCE" in codes
    assert "CLOCK_ERA_UNDETERMINED" in codes
    assert "AVAILABILITY_VOCABULARY_CANNOT_EXPRESS_THIS_CLOCK" in codes


def test_exactly_one_resource_carries_a_consensus_and_it_has_no_publication_clock(world):
    _run(world)
    rows = _rows(world)
    with_consensus = [r for r in rows.values() if r["facts"]["carries"]["consensus"]]
    assert [r["resource"] for r in with_consensus] == ["tests/data/economic_calendar_2011_2021.csv"]
    codes = {a["code"] for a in with_consensus[0]["facts"]["absences"]}
    assert {"NO_PUBLICATION_CLOCK", "NO_CONSENSUS_PUBLICATION_CLOCK"} <= codes
    assert with_consensus[0]["facts"]["study_refusal"]["code"] == "CONSENSUS_WITHOUT_OBSERVED_PUBLICATION_CLOCK"


def test_exactly_one_resource_observed_a_publication_instant_and_it_has_no_consensus(world):
    _run(world)
    rows = _rows(world)
    observed = [r for r in rows.values() if r["facts"]["publication_instant"]["observed"]]
    assert [r["resource"] for r in observed] == [
        "economic_calendar/release_actuals/fxmacrodata/announcements.parquet"]
    assert observed[0]["facts"]["study_refusal"]["code"] == "OBSERVED_PUBLICATION_WITHOUT_CONSENSUS"
    assert {a["code"] for a in observed[0]["facts"]["absences"]} >= {"NO_CONSENSUS_AT_ALL"}


def test_no_consensus_source_overlaps_an_observed_clock_source_and_the_verdict_is_on_every_row(world):
    _run(world)
    for row in _rows(world).values():
        overlap = row["facts"]["carries"]["consensus_overlap_with_an_observed_clock_source"]
        assert overlap["verdict"] == "NO_CONSENSUS_SOURCE_OVERLAPS_AN_OBSERVED_CLOCK_SOURCE"
        assert overlap["pairs"] and all(pair["overlap"] == "NONE" for pair in overlap["pairs"])
        assert overlap["pairs"][0]["gap_days"] > 0


def test_an_empty_consensus_column_is_an_absence_of_its_own(world):
    _run(world)
    for resource in ("economic_calendar/release_actuals/cpi_yoy/actuals.parquet",
                     "economic_calendar/scheduled_events/fred_release_date_proxy/scheduled_events.parquet"):
        codes = {a["code"] for a in _rows(world)[resource]["facts"]["absences"]}
        assert "CONSENSUS_COLUMN_PRESENT_BUT_EMPTY" in codes
        assert "NO_CONSENSUS_AT_ALL" not in codes                # the schema looks complete; say which absence it is


def test_the_file_grain_acquisition_clock_is_never_a_per_release_receipt(world):
    _run(world)
    for row in _rows(world).values():
        assert "NO_PER_ROW_RECEIPT_CLOCK" in {a["code"] for a in row["facts"]["absences"]}
    archive = _rows(world)["tests/data/economic_calendar_2011_2021.csv"]
    assert "NO_PROVENANCE_SIDECAR" in {a["code"] for a in archive["facts"]["absences"]}


def test_every_registration_carries_a_coverage_window_measured_from_the_bytes(world):
    _run(world)
    rows = _rows(world)
    assert rows["tests/data/economic_calendar_2011_2021.csv"]["facts"]["coverage"]["from"] == "2011-01-01"
    assert rows["tests/data/economic_calendar_2011_2021.csv"]["facts"]["coverage"]["to"] == "2021-04-26"
    announcements = rows["economic_calendar/release_actuals/fxmacrodata/announcements.parquet"]["facts"]["coverage"]
    assert announcements["unit"] == "instant_utc"
    assert announcements["from"].startswith("2024-12-12")


def test_re_registering_the_same_resources_is_five_duplicates_and_writes_nothing(world):
    _run(world)
    before = {r["row_sha256"] for r in _rows(world).values()}
    assert _run(world, ["--registered-at", "2026-10-05T00:00:00+00:00"]) == 0
    after = _rows(world)
    assert len(after) == 5
    assert {r["row_sha256"] for r in after.values()} == before
    assert all(r["registered_at"] == "2026-09-26T00:00:00+00:00" for r in after.values())


def test_bytes_that_moved_since_the_inventory_are_refused_not_registered(world):
    world["paths"][CPI].write_bytes(world["paths"][CPI].read_bytes() + b"\x00")
    assert _run(world) == 1
    rows = _rows(world)
    assert "economic_calendar/release_actuals/cpi_yoy/actuals.parquet" not in rows
    assert len(rows) == 4


def test_a_missing_root_refuses_that_resource_and_registers_the_others(world):
    assert rcr.main(["--inventory", str(world["inventory"]), "--clock", str(world["clock"]),
                     "--registry", str(world["registry"]),
                     "--financial-data-root", str(world["fin"]),
                     "--actor", "satoshi-iii",
                     "--registered-at", "2026-09-26T00:00:00+00:00"]) == 1
    rows = _rows(world)
    assert "tests/data/economic_calendar_2011_2021.csv" not in rows
    assert len(rows) == 4


def test_known_at_answers_with_the_catalog_that_could_have_refused_a_study(world):
    _run(world)
    registry = ResourceRegistry(world["registry"])
    assert registry.known_at("2026-09-25T23:59:59+00:00") == []
    assert len(registry.known_at("2026-09-26T00:00:01+00:00")) == 5


def test_the_governance_absences_are_findable_as_codes_too(world):
    """A consumer enumerating `absences` must not have to know to look in `governed_delivery` as well."""
    _run(world)
    rows = _rows(world)
    archive = rows["tests/data/economic_calendar_2011_2021.csv"]
    assert "NOT_IN_ANY_LAKE_ROOT" in {a["code"] for a in archive["facts"]["absences"]}
    for resource, row in rows.items():
        if resource == "tests/data/economic_calendar_2011_2021.csv":
            continue
        assert "NO_AVAILABILITY_CONTRACT" in {a["code"] for a in row["facts"]["absences"]}


def test_without_a_lake_inventory_snapshot_being_inventoried_is_registered_unknown(world):
    """A glob says what WOULD be walked. Whether the deployed lake walked it is a different question."""
    _run(world)
    row = _rows(world)["economic_calendar/release_actuals/cpi_yoy/actuals.parquet"]
    delivery = row["facts"]["governed_delivery"]
    assert delivery["inventoried"] == "UNKNOWN"
    assert delivery["inventoried_evidence"]["evidence"] == "NO_LAKE_INVENTORY_SNAPSHOT_GIVEN"


def test_a_lake_inventory_snapshot_makes_being_inventoried_a_measurement(world, tmp_path):
    snapshot = tmp_path / "lake_inventory.json"
    snapshot.write_text(json.dumps({"resources": [
        {"resource_id": "economic_calendar/release_actuals/cpi_yoy/actuals.parquet"}]}), encoding="utf-8")
    _run(world, ["--lake-inventory", str(snapshot)])
    rows = _rows(world)
    listed = rows["economic_calendar/release_actuals/cpi_yoy/actuals.parquet"]["facts"]["governed_delivery"]
    assert listed["inventoried"] is True
    assert listed["inventoried_evidence"]["evidence"] == "MEASURED_FROM_A_LAKE_INVENTORY_SNAPSHOT"
    assert listed["inventoried_evidence"]["snapshot"]["file"] == "lake_inventory.json"
    unlisted = rows["economic_calendar/release_actuals/fxmacrodata/announcements.parquet"]["facts"]
    assert unlisted["governed_delivery"]["inventoried"] is False
