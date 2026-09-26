"""The nine sibling FRED actuals resources: measured one by one, absences and all, granting nothing.

`cpi_yoy` was registered on 2026-09-26 "as representative of the twelve sibling FRED actual resources". There are
ten FRED directories under `economic_calendar/release_actuals/`, not twelve; one of them is `cpi_yoy` itself, so
nine siblings were left out, and representative is not measured. These tests exercise the measurement and the
registration of those nine on synthetic bytes, end to end, and they exercise every refusal that keeps a row from
being written from something nobody measured.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rcr = _load("register_calendar_resources")
inv = _load("inventory_fred_release_actuals")

from data_gov.resource_registration import ResourceRegistry  # noqa: E402

#: the columns every FRED actuals resource carries, in the producer's order
COLUMNS = ["date", "actual", "event_name", "fred_series", "transform", "transformed_actual",
           "consensus_estimate", "surprise", "source_note"]

#: what a sibling row must declare missing. Seven codes, the same seven the registered `cpi_yoy` row carries.
EXPECTED_ABSENCES = {
    "NO_PUBLICATION_CLOCK", "CONSENSUS_COLUMN_PRESENT_BUT_EMPTY", "NO_REVISION_HISTORY",
    "NO_PER_ROW_RECEIPT_CLOCK", "NO_CANCELLATION_STATE", "NO_TIMEZONE_DECLARED_BY_THE_SOURCE",
    "NO_AVAILABILITY_CONTRACT",
}

CALENDAR_EVIDENCE = ROOT / "docs/audits/evidence/CALENDAR_REGISTRATION_2026_09_26"
FRED_EVIDENCE = ROOT / "docs/audits/evidence/FRED_REGISTRATION_2026_09_26"
FINANCIAL_DATA = Path.home() / "Documents/GitHub/financial-data"


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_sibling(root: Path, slug: str, event: str, series: str, transform: str, *, rows: int = 6,
                   readme: str | None = None, consensus=None) -> Path:
    directory = root / inv.RELEASE_ACTUALS / slug
    directory.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame({
        "date": pd.to_datetime([f"199{i // 12}-{i % 12 + 1:02d}-01" for i in range(rows)]),
        "actual": [float(i) for i in range(rows)],
        "event_name": [event] * rows,
        "fred_series": [series] * rows,
        "transform": [transform] * rows,
        "transformed_actual": [float(i) / 2 for i in range(rows)],
        "consensus_estimate": [consensus] * rows,
        "surprise": [None] * rows,
        "source_note": ["FRED"] * rows,
    })[COLUMNS]
    path = directory / "actuals.parquet"
    frame.to_parquet(path, index=False)
    quote = (f"Economic release actuals for {event} from FRED; consensus fields are intentionally blank until a "
             "free scheduled-events source is validated.")
    (directory / "README.md").write_text(f"# {slug}\n\n{readme if readme is not None else quote}\n", encoding="utf-8")
    (directory / "provenance.json").write_text(json.dumps({
        "source": "FRED", "description": quote, "acquired_at": "2026-05-01T16:42:56.811471+00:00",
        "files": [{"path": f"{inv.RELEASE_ACTUALS}/{slug}/actuals.parquet", "sha256": _sha(path)}]}), encoding="utf-8")
    return path


def _reference_inventory(tmp_path: Path) -> Path:
    """A reference artifact carrying the one record the producer carries role case lists over from."""
    record = {
        "resource": inv.REFERENCE_RESOURCE, "status": "MEASURED",
        "columns": {column: {"non_null": 1, "null_or_empty": 0, "types": {}} for column in COLUMNS},
        "field_roles": {
            "actual": {"present": True, "column": "actual", "non_null_rows": 1, "cases_it_blocks": []},
            "consensus": {"present": False, "column": "consensus_estimate", "non_null_rows": 0,
                          "cases_it_blocks": ["CAL03", "CAL06"]},
            "previous": {"present": False, "column": None, "non_null_rows": 0, "cases_it_blocks": []},
            "reference_period": {"present": True, "column": "date", "non_null_rows": 1, "cases_it_blocks": []},
            "unit": {"present": True, "column": "transform", "non_null_rows": 1, "cases_it_blocks": []},
            "publication_instant": {"present": False, "column": None, "non_null_rows": 0,
                                    "cases_it_blocks": ["CAL02", "CAL03", "CAL10"]},
            "receipt_instant": {"present": False, "column": None, "non_null_rows": 0,
                                "cases_it_blocks": ["CAL02", "CAL07", "CAL08", "CAL12"]},
            "schedule_instant": {"present": False, "column": None, "non_null_rows": 0,
                                 "cases_it_blocks": ["CAL01", "CAL11"]},
            "revision_marker": {"present": False, "column": None, "non_null_rows": 0,
                                "cases_it_blocks": ["CAL04", "CAL10"]},
            "vintage_version": {"present": False, "column": None, "non_null_rows": 0,
                                "cases_it_blocks": ["CAL04", "CAL07", "CAL08"]},
            "cancellation_state": {"present": False, "column": None, "non_null_rows": 0, "cases_it_blocks": ["CAL11"]},
            "observed_sequence": {"present": False, "column": None, "non_null_rows": 0, "cases_it_blocks": ["CAL10"]},
            "historical_availability": {"present": False, "column": None, "non_null_rows": 0,
                                        "cases_it_blocks": ["CAL09"]},
        },
    }
    path = tmp_path / "reference_inventory.json"
    path.write_text(json.dumps({"schema": "m5phet.calendar_inventory.v1", "resources": [record]}), encoding="utf-8")
    return path


@pytest.fixture()
def world(tmp_path):
    """A synthetic financial-data root holding all nine siblings, measured into an inventory artifact."""
    root = tmp_path / "financial-data"
    for slug, (event, series) in rcr.FRED_SIBLINGS.items():
        _write_sibling(root, slug, event, series, "level")
    reference = _reference_inventory(tmp_path)
    artifact = tmp_path / "fred_inventory.json"
    assert inv.main(["--financial-data-root", str(root), "--reference-inventory", str(reference),
                     "--out", str(artifact)]) == 0
    return {"root": root, "reference": reference, "artifact": artifact, "tmp": tmp_path,
            "clock": CALENDAR_EVIDENCE / "wp22_measured_calendar_clock.redacted.json",
            "registry": tmp_path / "registry"}


def _run(world, extra=(), family="fred_release_actuals"):
    out = world["tmp"] / f"run_{len(list(world['tmp'].glob('run_*.json')))}.json"
    code = rcr.main(["--family", family,
                     "--inventory", str(world["artifact"]), "--clock", str(world["clock"]),
                     "--registry", str(world["registry"]),
                     "--financial-data-root", str(world["root"]),
                     "--actor", "satoshi-iii", "--registered-at", "2026-09-26T00:00:00+00:00",
                     "--evidence-out", str(out), *extra])
    return code, json.loads(out.read_text(encoding="utf-8"))


# --- the registration ------------------------------------------------------------------------------------------

def test_all_nine_siblings_are_registered_from_their_own_bytes_and_none_is_opened(world):
    code, report = _run(world)
    assert code == 0 and report["refused"] == []
    assert report["registered"] == 9 and len(rcr.FRED_SIBLINGS) == 9
    for result in report["results"]:
        row = result["row"]
        assert result["disposition"] == "STORED"
        assert row["execution_authorized"] is False
        assert row["facts"]["governed_delivery"]["state"] == "CLOSED_NO_AVAILABILITY_CONTRACT"
        assert row["content_sha256"] == _sha(world["root"] / row["resource"])
    assert "authorizes no delivery" in report["grants"]


def test_every_sibling_declares_the_same_seven_absences_and_each_one_says_why(world):
    _, report = _run(world)
    for result in report["results"]:
        absences = result["row"]["facts"]["absences"]
        assert {a["code"] for a in absences} == EXPECTED_ABSENCES
        assert all(a.get("why") for a in absences), "an absence without a reason is refused by the registry"


def test_the_series_of_every_row_is_the_series_its_rows_carry(world):
    _, report = _run(world)
    declared = {f"economic_calendar/release_actuals/{slug}/actuals.parquet": series
                for slug, (_, series) in rcr.FRED_SIBLINGS.items()}
    for result in report["results"]:
        row = result["row"]
        frame = pd.read_parquet(world["root"] / row["resource"])
        assert sorted(frame["fred_series"].unique()) == [declared[row["resource"]]]
        assert declared[row["resource"]] in row["facts"]["what_it_is"]


def test_the_reference_period_clock_stays_unknown_and_is_never_read_as_utc(world):
    _, report = _run(world)
    for result in report["results"]:
        clock = result["row"]["facts"]["clock"]
        assert clock["evidence"] == "UNKNOWN" and clock["eras_status"] == "NOT_MEASURED"
        assert clock["timezone_aware_in_the_bytes"] is False and clock["eras"] == []
        assert "rather than being read as UTC" in clock["reading"]
        assert result["row"]["facts"]["measurement_provenance"]["clock_artifact"] is None


def test_the_coverage_window_of_every_row_is_measured_from_the_bytes(world):
    _, report = _run(world)
    for result in report["results"]:
        coverage = result["row"]["facts"]["coverage"]
        assert coverage["status"] == "MEASURED" and coverage["column"] == "date"
        assert coverage["from"] and coverage["to"] and coverage["from"] <= coverage["to"]
        assert coverage["unit"] == "naive_wall_clock"


def test_without_a_lake_inventory_snapshot_being_inventoried_is_unknown_with_the_reason(world):
    _, report = _run(world)
    for result in report["results"]:
        evidence = result["row"]["facts"]["governed_delivery"]["inventoried_evidence"]
        assert evidence["inventoried"] == "UNKNOWN"
        assert evidence["evidence"] == "NO_LAKE_INVENTORY_SNAPSHOT_GIVEN" and evidence["why"]


def test_a_run_with_no_pair_to_compare_says_so_instead_of_claiming_the_windows_do_not_touch(world):
    _, report = _run(world)
    overlap = report["consensus_overlap"]
    assert overlap["verdict"] == "NO_PAIR_TO_COMPARE" and overlap["pairs"] == []
    assert "no window comparison was made" in overlap["reading"]
    assert "the windows do not touch" not in overlap["reading"]


def test_re_reading_the_same_nine_resources_is_nine_duplicates_and_writes_nothing(world):
    _run(world)
    before = sorted(p.name for p in world["registry"].rglob("*.json"))
    code, report = _run(world, extra=["--registered-at", "2026-09-27T12:00:00+00:00"])
    assert code == 0 and report["duplicates"] == 9 and report["registered"] == 0
    assert sorted(p.name for p in world["registry"].rglob("*.json")) == before
    assert len(ResourceRegistry(world["registry"]).known_at("2026-09-27T23:59:59+00:00")) == 9


def test_the_default_family_registers_no_fred_sibling(world):
    """The default must keep meaning what it meant: adding declarations never changes an existing invocation."""
    code, report = _run(world, family="calendar")
    assert report["registered"] == 0
    assert {r["resource"] for r in report["results"]} == set()
    assert {r["code"] for r in report["refused"]} == {"NOT_IN_THE_INVENTORY"}
    assert len(report["refused"]) == 5
    assert code == 1, "a declared resource this artifact does not measure is refused by name, not skipped"


# --- the refusals ----------------------------------------------------------------------------------------------

def test_a_sibling_the_inventory_does_not_carry_is_refused_not_registered(world):
    body = json.loads(world["artifact"].read_text(encoding="utf-8"))
    body["resources"] = [r for r in body["resources"] if r["resource"] != "fred_treasury_10y_actuals"]
    world["artifact"].write_text(json.dumps(body), encoding="utf-8")
    code, report = _run(world)
    assert code == 1 and report["registered"] == 8
    assert [r["code"] for r in report["refused"]] == ["NOT_IN_THE_INVENTORY"]
    assert "treasury_10y" not in " ".join(r["resource"] for r in report["results"])


def test_bytes_that_moved_since_the_inventory_are_refused_rather_than_registered_from_stale_facts(world):
    path = world["root"] / inv.RELEASE_ACTUALS / "fed_funds/actuals.parquet"
    frame = pd.read_parquet(path)
    frame.loc[0, "actual"] = 999.0
    frame.to_parquet(path, index=False)
    code, report = _run(world)
    assert code == 1 and report["registered"] == 8
    refused = [r for r in report["refused"] if r["code"] == "BYTES_MOVED_SINCE_THE_INVENTORY"]
    assert len(refused) == 1 and "false registration" in refused[0]["why"]


def test_a_quote_the_documentation_does_not_contain_is_refused(world):
    readme = world["root"] / inv.RELEASE_ACTUALS / "initial_claims/README.md"
    readme.write_text("# initial_claims\n\nsomething the producer never wrote\n", encoding="utf-8")
    code, report = _run(world)
    assert code == 1 and report["registered"] == 8
    assert [r["code"] for r in report["refused"]] == ["QUOTE_NOT_FOUND_IN_THE_DOCUMENTATION"]


def test_a_declared_series_that_is_not_the_series_in_the_rows_is_refused(world, tmp_path):
    root = tmp_path / "wrong-series"
    for slug, (event, _) in rcr.FRED_SIBLINGS.items():
        _write_sibling(root, slug, event, "NOT_THE_DECLARED_SERIES", "level")
    artifact = tmp_path / "wrong_series_inventory.json"
    assert inv.main(["--financial-data-root", str(root), "--reference-inventory", str(world["reference"]),
                     "--out", str(artifact)]) == 0
    world["root"], world["artifact"] = root, artifact
    code, report = _run(world)
    assert code == 1 and report["registered"] == 0
    assert {r["code"] for r in report["refused"]} == {"DECLARED_SERIES_DOES_NOT_MATCH_THE_BYTES"}
    assert all("worse than" in r["why"] for r in report["refused"])


def test_a_file_whose_columns_differ_from_the_measured_sibling_is_never_given_its_roles(world):
    """The producer refuses to carry roles over, and the registrar then refuses the record by name."""
    path = world["root"] / inv.RELEASE_ACTUALS / "retail_sales_mom/actuals.parquet"
    frame = pd.read_parquet(path).drop(columns=["surprise"])
    frame.to_parquet(path, index=False)
    # keep the sidecar true of the new bytes, so the ONLY thing wrong is the column set
    sidecar = path.parent / "provenance.json"
    body = json.loads(sidecar.read_text())
    body["files"][0]["sha256"] = _sha(path)
    sidecar.write_text(json.dumps(body), encoding="utf-8")
    artifact = world["tmp"] / "after_schema_change.json"
    assert inv.main(["--financial-data-root", str(world["root"]), "--reference-inventory", str(world["reference"]),
                     "--out", str(artifact)]) == 0
    record = next(r for r in json.loads(artifact.read_text())["resources"]
                  if r["resource"] == "fred_retail_sales_mom_actuals")
    assert record["status"] == "REFUSED_COLUMN_SET_DIFFERS_FROM_THE_MEASURED_SIBLING"
    assert "no role is guessed here" in record["reading"]
    world["artifact"] = artifact
    code, report = _run(world)
    assert code == 1
    assert [r["code"] for r in report["refused"]] == ["INVENTORY_STATUS_REFUSED_COLUMN_SET_DIFFERS_FROM_THE_MEASURED_SIBLING"]


def test_a_provenance_sidecar_that_declares_other_bytes_is_refused_by_the_producer(world, tmp_path):
    root = tmp_path / "bad-provenance"
    _write_sibling(root, "fed_funds", "Fed Funds", "FEDFUNDS", "level")
    sidecar = root / inv.RELEASE_ACTUALS / "fed_funds/provenance.json"
    body = json.loads(sidecar.read_text())
    body["files"][0]["sha256"] = "0" * 64
    sidecar.write_text(json.dumps(body), encoding="utf-8")
    artifact = tmp_path / "bad_provenance_inventory.json"
    assert inv.main(["--financial-data-root", str(root), "--reference-inventory", str(world["reference"]),
                     "--out", str(artifact)]) == 1
    record = json.loads(artifact.read_text())["resources"][0]
    assert record["status"] == "REFUSED_PROVENANCE_DIGEST_MISMATCH"
    assert record["provenance"]["declared_digest_matches_these_bytes"] is False


# --- what the live run produced, and what it did not disturb ---------------------------------------------------

def test_the_retained_evidence_records_nine_rows_in_force_and_five_calendar_slots_untouched():
    evidence = json.loads((FRED_EVIDENCE / "registrations.v1.json").read_text(encoding="utf-8"))
    assert evidence["rows_in_force"] == 9 and evidence["catalog_rows_in_force"] == 14
    assert evidence["every_row_grants_nothing"] is True
    assert len(evidence["calendar_slots_unchanged"]) == 5
    assert evidence["replay"]["verdict"] == "ALL_ROWS_RE_DERIVED" and evidence["replay"]["quarantined"] == 0
    for row in evidence["in_force"]:
        assert row["execution_authorized"] is False
        assert {a["code"] for a in row["facts"]["absences"]} == EXPECTED_ABSENCES
        assert str(Path.home()) not in json.dumps(row), "no host path reaches this repository"


def test_the_committed_inventory_artifact_measured_nine_resources():
    body = json.loads((FRED_EVIDENCE / "fred_release_actuals_inventory.v1.json").read_text(encoding="utf-8"))
    assert body["schema"] == inv.SCHEMA and len(body["resources"]) == 9
    assert {r["status"] for r in body["resources"]} == {"MEASURED"}
    assert set(r["resource"] for r in body["resources"]) == {f"fred_{slug}_actuals" for slug in rcr.FRED_SIBLINGS}


def test_the_producer_reproduces_the_upstream_record_of_the_sibling_it_carries_roles_from(tmp_path):
    """Parity, on the real bytes: the same reading of cpi_yoy as the upstream producer's artifact."""
    reference_artifact = CALENDAR_EVIDENCE / "calendar_inventory.v1.redacted.json"
    path = FINANCIAL_DATA / inv.RELEASE_ACTUALS / "cpi_yoy/actuals.parquet"
    if not path.is_file():
        pytest.skip("the financial-data root is not present here")
    out = tmp_path / "parity.json"
    assert inv.main(["--financial-data-root", str(FINANCIAL_DATA), "--reference-inventory", str(reference_artifact),
                     "--out", str(out), "--include-reference"]) == 0
    mine = next(r for r in json.loads(out.read_text())["resources"] if r["resource"] == inv.REFERENCE_RESOURCE)
    upstream = next(r for r in json.loads(reference_artifact.read_text())["resources"]
                    if r["resource"] == inv.REFERENCE_RESOURCE)
    for key in ("sha256", "bytes", "rows", "columns", "pandas_dtypes"):
        assert mine[key] == upstream[key], key
    assert mine["vintages"]["key_ladder"] == upstream["vintages"]["key_ladder"]
    assert mine["vintages"]["verdict"] == upstream["vintages"]["verdict"]
    assert mine["units"] == upstream["units"]
    for role, measured in upstream["field_roles"].items():
        for field in ("present", "column", "non_null_rows", "cases_it_blocks"):
            assert mine["field_roles"][role][field] == measured[field], (role, field)
