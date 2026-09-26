#!/usr/bin/env python3
"""Register the five economic-calendar resources in the local data-gov resource registry.

The event-study work reads five files. None of them was in the catalog, so every study that read them carried an
ungoverned input: nothing in data-gov said what they are, what clock their timestamps are on, or -- the fact that
governs all of it -- which halves of a surprise they are missing. This tool writes that down.

It is a DERIVATION, not a declaration. Every number in a registration comes from one of two measurement artifacts,
both named by digest in the row:

* `m5phet.calendar_inventory.v1` -- rows, columns, which CAL field roles each resource carries, its unit column, and
  its three-valued vintage verdict, measured on the bytes;
* `m5phet.calendar_clock.v1` -- the per-era UTC offsets of the 2011-2021 archive's naive wall clock, MEASURED against
  publication conventions read from an independent archive of observed instants.

What this tool declares rather than measures is exactly four things per resource, and each is recorded as a
declaration with its source: the lake the bytes belong to, the resource path inside that lake's root, the sentence the
producer's own README uses to say what the resource is, and the publication-clock kind -- in the vocabulary
`feature_eng_m5phet.events.publication_clock_block` already writes into every study artifact, so a registration and a
study cannot disagree about what "observed" means.

Everything else is refused rather than guessed. If the bytes have moved since the inventory was taken, the tool
refuses that resource instead of registering the inventory's facts against different bytes. If a coverage window
cannot be parsed from the resource's own clock column, it is registered UNKNOWN with the reason.

Registering a resource GRANTS NOTHING. Four of the five sit under a lake root with no availability contract, so a
governed download of them is refused today; the fifth is under no lake root at all. The registration says so, per
resource, with the one fact that would change it.

Deterministic, CPU only, no network, no credential.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_gov.resource_registration import (  # noqa: E402  (path set above: this tool runs from a checkout)
    NOT_IN_ANY_LAKE_ROOT, RegistrationRefusal, ResourceRegistry, digest, instant)

TOOL = "data_gov.tools.register_calendar_resources.v1"

#: The four declarations per resource, and nothing else. `lake` is the data-gov `lake_id` whose root holds the bytes,
#: `root` names which CLI root the path is relative to, and `documented_by` is the producer file the `what_it_is`
#: sentence is quoted from -- so a reader can check the quote instead of trusting it.
DECLARED = {
    "archive_2011_2021": {
        "family": "calendar",
        "lake": NOT_IN_ANY_LAKE_ROOT,
        "root": "feature_eng_root",
        "resource": "tests/data/economic_calendar_2011_2021.csv",
        "what_it_is": ("A retrospective economic-calendar archive: one row per scheduled release of one economy, with "
                       "the consensus, the actual and the previous value, and a scheduled wall clock with no zone. It "
                       "is the only resource on this machine that carries a consensus."),
        "documented_by": None,
        "documented_by_reading": ("nothing accompanies these bytes: no README, no data dictionary and no "
                                  "provenance.json. The column names are declared by "
                                  "feature_eng_m5phet.calendar_join.DEFAULT_ARCHIVE_COLUMNS, not by a producer"),
        "publication_kind": "ASSUMED_SCHEDULED_PUBLICATION_LOCALIZED",
        "publication_why": ("the only instant in the file is the SCHEDULED one. Reading it as the publication instant "
                            "is an operator's assumption; the wall clock it is read on is no longer UTC but the "
                            "per-era offsets measured in the clock artifact named here"),
        "clock_is_measured": True,
    },
    "fxmacrodata_announcements": {
        "family": "calendar",
        "lake": "financial_files",
        "root": "financial_data_root",
        "resource": "economic_calendar/release_actuals/fxmacrodata/announcements.parquet",
        "what_it_is": ("Historical macro announcement values with no-lookahead announcement timestamps. It is the only "
                       "resource on this machine whose publication instants somebody observed."),
        "documented_by": "economic_calendar/release_actuals/fxmacrodata/README.md",
        "documented_by_reading": ("the producer's README states: 'Historical macro announcement values with "
                                 "no-lookahead announcement timestamps. Consensus/forecast fields are not present "
                                 "unless supplied by the provider payload.'"),
        "publication_kind": "OBSERVED_ACTUAL_PUBLICATION",
        "publication_why": ("`announcement_datetime_utc` is timezone-aware in the bytes and is the instant the source "
                            "says the actual was announced; it is the observed publication clock the archive's own "
                            "clock was measured against"),
        "clock_is_measured": False,
    },
    "fxmacrodata_release_calendar": {
        "family": "calendar",
        "lake": "financial_files",
        "root": "financial_data_root",
        "resource": "economic_calendar/scheduled_events/fxmacrodata/release_calendar.parquet",
        "what_it_is": ("A forward-looking release schedule: when supported currencies' macro releases are scheduled to "
                       "be announced. It carries no value of any kind -- neither a consensus nor an actual."),
        "documented_by": "economic_calendar/scheduled_events/fxmacrodata/README.md",
        "documented_by_reading": ("the producer's README states: 'Upcoming macro release calendar with announcement "
                                 "timestamps for supported currencies.'"),
        "publication_kind": "ASSUMED_SCHEDULED_PUBLICATION",
        "publication_why": ("every instant here is a SCHEDULE for a release that had not happened when the file was "
                            "acquired; nothing in it is an observation of a publication"),
        "clock_is_measured": False,
    },
    "fred_release_date_proxy": {
        "family": "calendar",
        "lake": "financial_files",
        "root": "financial_data_root",
        "resource": "economic_calendar/scheduled_events/fred_release_date_proxy/scheduled_events.parquet",
        "what_it_is": ("A derived stand-in for a release calendar: FRED actuals re-labelled with the observation date "
                       "of the value as if it were the release date. It is not a schedule anybody published."),
        "documented_by": "economic_calendar/scheduled_events/fred_release_date_proxy/README.md",
        "publication_kind": "ASSUMED_SCHEDULED_PUBLICATION",
        "publication_why": ("`scheduled_date_proxy` is a DATE, not an instant, and the resource's own `source_note` "
                            "says it is 'generated from FRED actual observation dates'. It is a proxy derived from "
                            "the value's reference date, so it is weaker than a schedule: a release published weeks "
                            "after its reference period carries the reference period's date here"),
        "clock_is_measured": False,
    },
    "fred_cpi_yoy_actuals": {
        "family": "calendar",
        "lake": "financial_files",
        "root": "financial_data_root",
        "resource": "economic_calendar/release_actuals/cpi_yoy/actuals.parquet",
        "what_it_is": ("US CPI year-on-year actuals from FRED, one row per reference month, with a consensus column "
                       "the producer left empty on purpose. Representative of the twelve sibling FRED actual "
                       "resources under `economic_calendar/release_actuals/`."),
        "documented_by": "economic_calendar/release_actuals/cpi_yoy/README.md",
        "documented_by_reading": ("the producer's README states: 'Economic release actuals for CPI YoY from FRED; "
                                 "consensus fields are intentionally blank until a free scheduled-events source is "
                                 "validated.'"),
        "publication_kind": "NO_PUBLICATION_INSTANT_OF_ANY_KIND",
        "publication_why": ("the only time column is `date`, the REFERENCE PERIOD of the value. Nothing in the file "
                            "says when the number was released, and the reference month is not a release date"),
        "clock_is_measured": False,
    },
}


#: The nine sibling FRED actuals resources the 2026-09-26 calendar registration left out. `cpi_yoy` was
#: registered "as representative" of them; representative is not measured, so each one is registered from its own
#: bytes here. Two things per sibling are typed in -- the event name its own README uses and the FRED series id --
#: and BOTH are checked rather than trusted: the quote must appear in the README file, and the series must be the
#: series the rows carry, or the resource is refused by name instead of registered.
FRED_SIBLINGS = {
    "core_cpi_yoy": ("Core CPI YoY", "CPILFESL"),
    "core_pce_yoy": ("PCE Core YoY", "PCEPILFE"),
    "fed_funds": ("Fed Funds", "FEDFUNDS"),
    "gdp_qoq_annualized": ("GDP QoQ Annualized", "A191RL1Q225SBEA"),
    "initial_claims": ("Initial Claims", "ICSA"),
    "nonfarm_payrolls_mom": ("Nonfarm Payrolls MoM", "PAYEMS"),
    "retail_sales_mom": ("Retail Sales", "RSAFS"),
    "treasury_10y": ("10Y Treasury", "DGS10"),
    "unemployment_rate": ("Unemployment Rate", "UNRATE"),
}


def _fred_sibling(slug: str, event: str, series: str) -> dict:
    quote = (f"Economic release actuals for {event} from FRED; consensus fields are intentionally blank until a "
             "free scheduled-events source is validated.")
    return {
        "family": "fred_release_actuals",
        "lake": "financial_files",
        "root": "financial_data_root",
        "resource": f"economic_calendar/release_actuals/{slug}/actuals.parquet",
        "series": series,
        "what_it_is": (f"{event} release actuals from FRED (series {series}), one row per reference period of the "
                       "value, with a consensus column the producer left empty on purpose. One of the nine sibling "
                       "FRED actual resources under `economic_calendar/release_actuals/` that the 2026-09-26 "
                       "calendar registration left unregistered, having registered `cpi_yoy` as representative of "
                       "them; this registration measures this one instead of inheriting that reading"),
        "documented_by": f"economic_calendar/release_actuals/{slug}/README.md",
        "quote": quote,
        "documented_by_reading": f"the producer's README states: '{quote}'",
        "publication_kind": "NO_PUBLICATION_INSTANT_OF_ANY_KIND",
        "publication_why": ("the only time column is `date`, the REFERENCE PERIOD of the value. Nothing in the file "
                            "says when the number was released, and the reference period is not a release date"),
        "clock_is_measured": False,
    }


DECLARED.update({f"fred_{slug}_actuals": _fred_sibling(slug, event, series)
                 for slug, (event, series) in FRED_SIBLINGS.items()})

#: `--family` selects which declarations a run is about. It defaults to the five calendar resources so a repeat of
#: the 2026-09-26 run is the same run: adding declarations to this file must never change what an existing
#: invocation registers.
FAMILIES = ("calendar", "fred_release_actuals", "all")


def _file_digest(path):
    sha = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            sha.update(block)
    return sha.hexdigest()


def _artifact(path):
    body = json.loads(Path(path).read_text(encoding="utf-8"))
    return {"path": Path(path).name, "sha256": _file_digest(path), "schema": body.get("schema")}, body


# --- coverage: measured here, from the resource's own clock column ----------------------------------------------------

def _coverage(path, column, kind):
    """The window the rows span, on the resource's own clock column. Unparseable is UNKNOWN with the reason: a
    coverage window that was guessed is worse than one that is missing, because a consumer trusts it."""
    try:
        if kind == "csv_no_header":
            import csv
            values = []
            with Path(path).open("r", newline="", encoding="utf-8", errors="replace") as handle:
                for row in csv.reader(handle):
                    if not row or not row[0].strip():
                        continue
                    values.append(datetime.strptime(row[0].strip(), "%Y/%m/%d").date().isoformat())
            if not values:
                return {"status": "UNKNOWN", "why": "no rows carried a parseable date"}
            return {"status": "MEASURED", "column": column, "unit": "calendar_date",
                    "from": min(values), "to": max(values), "rows_with_a_value": len(values)}
        import pandas as pd
        frame = pd.read_parquet(path)
        parsed = pd.to_datetime(frame[column], errors="coerce")
        present = parsed.dropna()
        if present.empty:
            return {"status": "UNKNOWN", "column": column,
                    "why": f"no value of {column!r} parsed as a timestamp"}
        aware = present.dt.tz is not None if hasattr(present.dt, "tz") else False
        return {"status": "MEASURED", "column": column,
                "unit": "instant_utc" if aware else "naive_wall_clock",
                "from": present.min().isoformat(), "to": present.max().isoformat(),
                "rows_with_a_value": int(len(present)),
                "rows_without_a_value": int(len(frame) - len(present))}
    except Exception as exc:                                     # a refusal to state a window, never a guessed one
        return {"status": "UNKNOWN", "column": column,
                "why": f"{exc.__class__.__name__} while reading {column!r}: {exc}"}


# --- the derivations -------------------------------------------------------------------------------------------------

def _role(record, name):
    return (record.get("field_roles") or {}).get(name) or {}


def _clock_block(record, declared, clock_artifact, clock_ref):
    """What this resource's timestamps MEAN, and whether anybody documented it."""
    measured_aware = bool(record.get("clock", {}).get("measured_timezone_aware"))
    column = record.get("clock", {}).get("column")
    if declared["clock_is_measured"]:
        periods = clock_artifact.get("periods") or []
        eras = [{"from": p.get("start_date"), "to": p.get("end_date"), "status": p.get("status"),
                 "utc_offset": p.get("utc_offset"), "utc_offset_seconds": p.get("utc_offset_seconds"),
                 "reason": p.get("reason"), "estimates": p.get("n"), "confidence": p.get("confidence")}
                for p in periods]
        determined = [e for e in eras if e["status"] == "DETERMINED"]
        return {
            "column": column,
            "timezone_aware_in_the_bytes": measured_aware,
            "evidence": "MEASURED_FROM_DATA_NOT_DOCUMENTED_BY_THE_SOURCE",
            "documented_by_the_source": False,
            "measured_by": clock_ref,
            "measurement_method": (clock_artifact.get("provenance") or "").strip() or None,
            "eras": eras,
            "eras_determined": len(determined),
            "eras_undetermined": len(eras) - len(determined),
            "distinct_offsets": sorted({e["utc_offset"] for e in determined if e["utc_offset"]}),
            "reading": (
                "this resource's wall clock is NOT UTC. It was measured as a fixed UTC-05:00 all year round up to "
                "2018-01 -- the archive did not move with daylight saving -- and as America/New_York local time from "
                "2018-03, whose seasonal alternation between UTC-04:00 and UTC-05:00 is what the eras show. The "
                "source documents none of this: it is a measurement against an independent archive of observed "
                "instants, it can be re-run, and it can be wrong. Reading the column as UTC -- which every run "
                "before 2026-09-25 did -- places every US release four to five hours before it happened. An era "
                "whose offset could not be determined is excluded CLOCK_PERIOD_UNDETERMINED, never given a "
                "neighbour's offset"),
        }
    if measured_aware:
        return {"column": column, "timezone_aware_in_the_bytes": True,
                "evidence": "TIMEZONE_AWARE_IN_THE_BYTES_AND_NAMED_UTC_BY_THE_PRODUCER",
                "documented_by_the_source": True, "eras": [], "eras_determined": 0, "eras_undetermined": 0,
                "reading": ("every value of this column carries an offset, so it names an instant without any "
                            "declaration by this catalog. No era measurement was needed and none was made")}
    return {"column": column, "timezone_aware_in_the_bytes": False,
            "evidence": "UNKNOWN", "documented_by_the_source": False, "eras": [],
            "eras_determined": 0, "eras_undetermined": 0,
            "eras_status": "NOT_MEASURED",
            "reading": ("a naive column with no producer statement of zone. No era measurement was made for this "
                        "resource: the clock job measures an archive against observed publication conventions, and "
                        "this column is a reference period or a derived date rather than a release wall clock, so "
                        "there is no convention to measure it against. It stays UNKNOWN rather than being read as UTC")}


def _carries_block(record, declared):
    consensus = _role(record, "consensus")
    actual = _role(record, "actual")
    previous = _role(record, "previous")
    schedule = _role(record, "schedule_instant")
    vintages = record.get("vintages") or {}
    return {
        "consensus": bool(consensus.get("present")),
        "consensus_column": consensus.get("column"),
        "consensus_non_null_rows": int(consensus.get("non_null_rows") or 0),
        "actual": bool(actual.get("present")),
        "actual_non_null_rows": int(actual.get("non_null_rows") or 0),
        "previous_non_null_rows": int(previous.get("non_null_rows") or 0),
        "schedule_only": bool(schedule.get("present")) and not actual.get("present"),
        "revision_history": vintages.get("verdict"),
        "revision_history_reading": vintages.get("reading"),
        "unit_column": (record.get("units") or {}).get("column"),
        "unit_values": [u for u, _ in ((record.get("units") or {}).get("most_common") or [])],
    }


def _governance_absences(declared, delivery):
    """The catalog's own absences about this resource: not facts of the bytes, facts of the governance around them.

    They are also in `governed_delivery.state`, and they are repeated here as codes on purpose: a consumer that
    enumerates `absences` to decide whether an input is governed must find them without knowing to look in a second
    place.
    """
    if declared["lake"] == NOT_IN_ANY_LAKE_ROOT:
        return [{"code": "NOT_IN_ANY_LAKE_ROOT", "field": "lake", "why": delivery["why"]}]
    return [{"code": "NO_AVAILABILITY_CONTRACT", "field": "resource_contracts", "why": delivery["why"]}]


def _absences(record, declared, clock, carries):
    """Every absence this resource has, from the measurement. Nothing is added that the inventory does not show, and
    nothing the inventory shows is left out because it is inconvenient."""
    out = []
    roles = record.get("field_roles") or {}

    def blocks(name):
        return (roles.get(name) or {}).get("cases_it_blocks") or []

    if not (roles.get("publication_instant") or {}).get("present"):
        out.append({"code": "NO_PUBLICATION_CLOCK", "field": "publication_instant",
                    "blocks": blocks("publication_instant"),
                    "why": ("no column of these bytes is an instant at which a value became public; the inventory "
                            f"found the role absent over all {record.get('rows')} rows. "
                            + declared["publication_why"])})
        if carries["consensus"]:
            out.append({"code": "NO_CONSENSUS_PUBLICATION_CLOCK", "field": "consensus",
                        "blocks": sorted(set(blocks("publication_instant")) | {"EVENT_STUDY"}),
                        "why": (f"{carries['consensus_non_null_rows']} rows carry a consensus in column "
                                f"{carries['consensus_column']!r} and no row carries any publication instant, so a "
                                "surprise computed here cannot be shown to have been pre-release information")})
    if not carries["consensus"]:
        if carries["consensus_column"]:
            out.append({"code": "CONSENSUS_COLUMN_PRESENT_BUT_EMPTY", "field": carries["consensus_column"],
                        "blocks": blocks("consensus"),
                        "why": (f"the column {carries['consensus_column']!r} exists and is null in every one of "
                                f"{record.get('rows')} rows")})
        else:
            out.append({"code": "NO_CONSENSUS_AT_ALL", "field": "consensus", "blocks": blocks("consensus"),
                        "why": "no column of these bytes carries an expectation of any release"})
    verdict = carries["revision_history"]
    if verdict == "NO_VINTAGES":
        out.append({"code": "NO_REVISION_HISTORY", "field": (record.get("vintages") or {}).get("value_column"),
                    "why": carries["revision_history_reading"]})
    elif verdict == "VINTAGE_UNDECIDABLE":
        out.append({"code": "REVISION_HISTORY_UNDECIDABLE",
                    "field": (record.get("vintages") or {}).get("value_column"),
                    "why": carries["revision_history_reading"]})
    if not (roles.get("receipt_instant") or {}).get("present"):
        provenance = record.get("provenance") or {}
        acquired = provenance.get("file_grain_receipt_instant")
        out.append({"code": "NO_PER_ROW_RECEIPT_CLOCK", "field": "receipt_instant",
                    "blocks": blocks("receipt_instant"),
                    "why": (f"the only receipt clock is the download's `acquired_at` ({acquired}), which is at FILE "
                            "grain: it dates the whole acquisition, not any single release, so it cannot order two "
                            "releases inside the file"
                            if acquired else
                            "nothing records when any value reached this system, and no provenance sidecar supplies "
                            "even a file-grain acquisition clock")})
    if not (record.get("provenance") or {}).get("present"):
        out.append({"code": "NO_PROVENANCE_SIDECAR", "field": "provenance.json",
                    "why": (record.get("provenance") or {}).get("reading")
                           or "no provenance.json accompanies these bytes"})
    if not (roles.get("reference_period") or {}).get("present"):
        out.append({"code": "NO_REFERENCE_PERIOD", "field": "reference_period",
                    "blocks": blocks("reference_period"),
                    "why": "no column says which period a value refers to, so comparability cannot be checked"})
    if not (roles.get("unit") or {}).get("present"):
        out.append({"code": "NO_UNIT", "field": "unit", "blocks": blocks("unit"),
                    "why": (record.get("units") or {}).get("reading")
                           or "no column of these bytes says what its numbers are measured in"})
    if not (roles.get("cancellation_state") or {}).get("present"):
        out.append({"code": "NO_CANCELLATION_STATE", "field": "cancellation_state",
                    "blocks": blocks("cancellation_state"),
                    "why": "nothing distinguishes a release that did not happen from one this resource omits"})
    if not clock["timezone_aware_in_the_bytes"] and not clock["documented_by_the_source"]:
        # declared for the measured archive too, not only for the unmeasured columns: measuring a clock ourselves does
        # not turn it into a producer statement, and a consumer matching on this code must still find it here.
        out.append({"code": "NO_TIMEZONE_DECLARED_BY_THE_SOURCE", "field": clock["column"],
                    "why": (f"the column {clock['column']!r} is naive in the bytes and the producer states no zone "
                            "for it. Whatever zone is used downstream is a declaration of this catalog, never a fact "
                            "of the source"
                            + ("; this registration's zone comes from a measurement, which is evidence but not "
                               "documentation" if clock["evidence"].startswith("MEASURED") else
                               "; no measurement was made for this column either, so the catalog declares no zone "
                               "rather than reading it as UTC"))})
    if clock["evidence"] == "MEASURED_FROM_DATA_NOT_DOCUMENTED_BY_THE_SOURCE":
        out.append({"code": "CLOCK_MEASURED_NOT_DOCUMENTED", "field": clock["column"],
                    "why": ("the per-era offsets this registration declares were measured from the bytes against an "
                            "independent archive of observed instants; the source states no zone at all")})
        undetermined = [e for e in clock["eras"] if e["status"] != "DETERMINED"]
        if undetermined:
            out.append({"code": "CLOCK_ERA_UNDETERMINED", "field": clock["column"],
                        "why": ("the offset of "
                                + "; ".join(f"{e['from']}..{e['to']} ({e['reason']}, {e['estimates']} estimates)"
                                            for e in undetermined)
                                + " could not be determined. Rows inside these eras are excluded "
                                  "CLOCK_PERIOD_UNDETERMINED and never given a neighbouring era's offset")})
        out.append({"code": "AVAILABILITY_VOCABULARY_CANNOT_EXPRESS_THIS_CLOCK", "field": "timezone_evidence",
                    "why": ("the installed availability-contract vocabulary "
                            "(`financial_data_store.inventory.TIMEZONE_EVIDENCE`) offers only PRODUCER_STATEMENT and "
                            "UNKNOWN. This clock is neither: it is measured from data. No honest availability "
                            "contract can be written for this resource until that vocabulary carries a "
                            "measured-from-data value, and declaring PRODUCER_STATEMENT would assert a producer "
                            "statement that does not exist")})
    return out


def _delivery(declared, inventoried):
    if declared["lake"] == NOT_IN_ANY_LAKE_ROOT:
        return {"state": "CLOSED_NOT_IN_ANY_LAKE_ROOT", "lake": None, "inventoried": False,
                "why": ("these bytes live inside a code checkout's test fixtures, under no configured lake root. "
                        "data-gov cannot discover, hash, cut or account for a delivery of them, so every study that "
                        "reads them reads them off the filesystem outside governance"),
                "what_would_open_it": ("move or publish the file under a lake root that a provider inventories, then "
                                       "install an availability contract for it -- which today additionally needs a "
                                       "measured-from-data value in the timezone-evidence vocabulary")}
    return {"state": "CLOSED_NO_AVAILABILITY_CONTRACT", "lake": declared["lake"],
            "inventoried": inventoried["inventoried"],
            "inventoried_evidence": inventoried,
            "why": ("the provider's include globs cover `economic_calendar/**`, so the resource is discoverable "
                    f"(inventoried: {inventoried['inventoried']}, {inventoried['evidence']}), and "
                    "`resource_contracts` carries no entry for it, so a governed download is refused "
                    "`resource availability contract required` (fail-closed, docs/00_CONTRATO.md §5)"),
            "what_would_open_it": ("an availability contract derived from producer evidence: what the available-time "
                                   "label denotes, the completion bound, the time-zone evidence and the use class, "
                                   "validated against these bytes")}


def _inventoried(resource, lake_inventory):
    """Whether a lake actually inventories this path, from a lake inventory snapshot -- or UNKNOWN with the reason.

    It is deliberately not assumed from the include globs: a glob says what WOULD be walked, and whether the deployed
    lake walked it is a different question that only its own inventory can answer.
    """
    if lake_inventory is None:
        return {"inventoried": "UNKNOWN",
                "evidence": "NO_LAKE_INVENTORY_SNAPSHOT_GIVEN",
                "why": ("--lake-inventory was not supplied, so nothing here asserts whether the deployed lake walked "
                        "this path; the include globs of the provider say only what would be walked")}
    snapshot, paths = lake_inventory
    return {"inventoried": resource in paths,
            "evidence": "MEASURED_FROM_A_LAKE_INVENTORY_SNAPSHOT",
            "snapshot": snapshot,
            "why": (f"the snapshot lists {len(paths)} resources and this path is "
                    f"{'among them' if resource in paths else 'NOT among them'}")}


def _read_lake_inventory(path):
    """A lake inventory snapshot reduced to the set of resource paths it lists, plus its digest."""
    if path is None:
        return None

    def walk(value):
        if isinstance(value, dict):
            for item in value.values():
                yield from walk(item)
        elif isinstance(value, list):
            for item in value:
                yield from walk(item)
        elif isinstance(value, str):
            yield value

    body = json.loads(Path(path).read_text(encoding="utf-8"))
    return ({"file": Path(path).name, "sha256": _file_digest(path)},
            {text for text in walk(body.get("resources", body)) if text and not text.startswith("/")})


def _study_refusal(carries, publication_kind):
    if carries["consensus"] and publication_kind != "OBSERVED_ACTUAL_PUBLICATION":
        return {"code": "CONSENSUS_WITHOUT_OBSERVED_PUBLICATION_CLOCK",
                "reading": ("this resource carries the consensus an event study needs and no observed publication "
                            "instant, so any response measured from it is NOT_IDENTIFIED by construction. The "
                            "catalog refuses the study here; it is not something to discover in an analysis")}
    if publication_kind == "OBSERVED_ACTUAL_PUBLICATION" and not carries["consensus"]:
        return {"code": "OBSERVED_PUBLICATION_WITHOUT_CONSENSUS",
                "reading": ("this resource observes when actuals were announced and carries no expectation, so no "
                            "surprise can be formed from it alone; it can only date a surprise computed elsewhere")}
    return {"code": "NEITHER_CONSENSUS_NOR_OBSERVED_PUBLICATION",
            "reading": ("this resource carries neither half of a surprise: no expectation, and no observed instant "
                        "at which anything became public")}


def build_registrations(inventory, clock_artifact, inventory_ref, clock_ref, roots, actor, registered_at,
                        lake_inventory=None, family="calendar"):
    """The declared family's registrations, derived. A resource the inventory could not measure, or whose bytes have
    moved since it was measured, is REFUSED by name and not registered from stale facts."""
    records = {r["resource"]: r for r in inventory.get("resources", [])}
    out, refused = [], []
    for name, declared in DECLARED.items():
        if family != "all" and declared.get("family", "calendar") != family:
            continue
        record = records.get(name)
        if record is None:
            refused.append({"resource": name, "code": "NOT_IN_THE_INVENTORY",
                            "why": "the inventory artifact carries no record of this resource"})
            continue
        if record.get("status") != "MEASURED":
            refused.append({"resource": name, "code": f"INVENTORY_STATUS_{record.get('status')}",
                            "why": record.get("reading")})
            continue
        root = roots.get(declared["root"])
        if not root:
            refused.append({"resource": name, "code": "ROOT_NOT_GIVEN",
                            "why": f"--{declared['root'].replace('_', '-')} was not supplied, so the bytes this "
                                   f"registration would be about cannot be located"})
            continue
        path = Path(root) / declared["resource"]
        if not path.is_file():
            refused.append({"resource": name, "code": "BYTES_ABSENT",
                            "why": f"{declared['resource']} is not under the root given for it"})
            continue
        quote = declared.get("quote")
        if quote:
            documentation = Path(root) / declared["documented_by"]
            text = documentation.read_text(encoding="utf-8") if documentation.is_file() else ""
            if quote not in text:
                refused.append({"resource": name, "code": "QUOTE_NOT_FOUND_IN_THE_DOCUMENTATION",
                                "why": (f"the sentence this registration attributes to {declared['documented_by']} "
                                        "is not in that file, so `source_documentation` would quote something the "
                                        "producer did not write")})
                continue
        series = declared.get("series")
        if series:
            measured_series = ((record.get("identity") or {}).get("series")) or []
            if measured_series != [series]:
                refused.append({"resource": name, "code": "DECLARED_SERIES_DOES_NOT_MATCH_THE_BYTES",
                                "why": (f"this registration declares FRED series {series!r} and the rows carry "
                                        f"{measured_series!r}; a catalog row naming the wrong series is worse than "
                                        "no row")})
                continue
        measured_sha = _file_digest(path)
        if measured_sha != record.get("sha256"):
            refused.append({"resource": name, "code": "BYTES_MOVED_SINCE_THE_INVENTORY",
                            "why": (f"the file now digests to {measured_sha[:16]}… and the inventory measured "
                                    f"{str(record.get('sha256'))[:16]}…; registering the inventory's facts against "
                                    "different bytes would be a false registration")})
            continue

        clock = _clock_block(record, declared, clock_artifact, clock_ref)
        carries = _carries_block(record, declared)
        coverage = _coverage(path, record.get("clock", {}).get("column"), record.get("kind") or (
            "csv_no_header" if record.get("header_row") is False else "parquet"))
        delivery = _delivery(declared, _inventoried(declared["resource"], lake_inventory))
        facts = {
            "what_it_is": declared["what_it_is"],
            "source_documentation": {"file": declared.get("documented_by"),
                                     "reading": declared.get("documented_by_reading")},
            "physical_location": {
                "lake": None if declared["lake"] == NOT_IN_ANY_LAKE_ROOT else declared["lake"],
                "resource_path_in_root": declared["resource"],
                "root_kind": declared["root"],
                "bytes": record.get("bytes"), "rows": record.get("rows"),
                "format": "csv_without_a_header_row" if record.get("header_row") is False else "parquet",
                "reading": ("the path is relative to the root it belongs to; the absolute path of the root is "
                            "deliberately not registered -- it is host configuration, not a fact about the resource")},
            "coverage": coverage,
            "clock": clock,
            "publication_instant": {"kind": declared["publication_kind"],
                                    "observed": declared["publication_kind"] == "OBSERVED_ACTUAL_PUBLICATION",
                                    "column": (record.get("field_roles", {}).get("publication_instant") or {}
                                               ).get("column") or clock["column"],
                                    "why": declared["publication_why"]},
            "carries": carries,
            "absences": (_absences(record, declared, clock, carries)
                         + _governance_absences(declared, delivery)),
            "study_refusal": _study_refusal(carries, declared["publication_kind"]),
            "governed_delivery": delivery,
            "measurement_provenance": {
                "tool": TOOL,
                "inventory_artifact": inventory_ref,
                "clock_artifact": clock_ref if declared["clock_is_measured"] else None,
                "bytes_digest_reverified_at_registration": measured_sha,
                "reading": ("every count, role, unit and vintage verdict above comes from the inventory artifact; the "
                            "era offsets come from the clock artifact; the coverage window and the bytes digest were "
                            "re-measured by this tool on the file itself")},
        }
        out.append({"lake": declared["lake"], "resource": declared["resource"],
                    "content_sha256": measured_sha, "facts": facts,
                    "registered_at": registered_at, "registered_by": actor,
                    "_inventory_name": name})
    return out, refused


def consensus_overlap(registrations):
    """Measured, not asserted: does any consensus-carrying resource's window overlap any observed-clock resource's?

    This is the fact that makes every event study on this machine NOT_IDENTIFIED, and it is a comparison of windows
    rather than an opinion. It is computed here and attached to every registration so a study is refused by the
    catalog on whichever resource it opens first.
    """
    def window(reg):
        coverage = reg["facts"]["coverage"]
        return (coverage.get("from"), coverage.get("to")) if coverage.get("status") == "MEASURED" else (None, None)

    consensus = [(r, *window(r)) for r in registrations if r["facts"]["carries"]["consensus"]]
    observed = [(r, *window(r)) for r in registrations
                if r["facts"]["publication_instant"]["observed"]]
    pairs = []
    for creg, cfrom, cto in consensus:
        for oreg, ofrom, oto in observed:
            if None in (cfrom, cto, ofrom, oto):
                pairs.append({"consensus_resource": creg["resource"], "observed_resource": oreg["resource"],
                              "overlap": "UNKNOWN", "why": "one of the two windows could not be measured"})
                continue
            lo, hi = max(cfrom[:10], ofrom[:10]), min(cto[:10], oto[:10])
            pairs.append({"consensus_resource": creg["resource"], "observed_resource": oreg["resource"],
                          "consensus_window": [cfrom, cto], "observed_window": [ofrom, oto],
                          "overlap": "NONE" if lo > hi else f"{lo}..{hi}",
                          "gap_days": (datetime.fromisoformat(lo).date()
                                       - datetime.fromisoformat(hi).date()).days if lo > hi else 0})
    verdict = ("NO_CONSENSUS_SOURCE_OVERLAPS_AN_OBSERVED_CLOCK_SOURCE"
               if pairs and all(p.get("overlap") == "NONE" for p in pairs)
               else ("NO_PAIR_TO_COMPARE" if not pairs else "AN_OVERLAP_EXISTS"))
    if verdict == "NO_PAIR_TO_COMPARE":
        # this run registered no consensus source, or no observed-clock source, or neither. Saying "the windows do
        # not touch" here would assert a comparison that was not made, so the row says what it compared: nothing.
        return {"verdict": verdict, "pairs": pairs,
                "consensus_resources": [r["resource"] for r, _, _ in consensus],
                "observed_clock_resources": [r["resource"] for r, _, _ in observed],
                "reading": ("no window comparison was made for this registration: the resources registered in this "
                            "run carry no consensus, or no observed publication instant, or neither, so there was no "
                            "pair to compare. This is NOT a finding that some other pair overlaps or does not -- read "
                            "the rows of the resources that carry those halves for that")}
    return {"verdict": verdict, "pairs": pairs,
            "consensus_resources": [r["resource"] for r, _, _ in consensus],
            "observed_clock_resources": [r["resource"] for r, _, _ in observed],
            "reading": ("a surprise needs an expectation and an observed publication instant. Every consensus on this "
                        "machine is in one window and every observed instant is in another, and the windows do not "
                        "touch, so no row anywhere can carry both. That is why the event studies are NOT_IDENTIFIED, "
                        "and it is a measured comparison of coverage windows rather than a judgement")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--inventory", required=True, help="the m5phet.calendar_inventory.v1 artifact")
    parser.add_argument("--clock", required=True, help="the m5phet.calendar_clock.v1 artifact")
    parser.add_argument("--registry", required=True, help="the data-gov resource registry root to append to")
    parser.add_argument("--financial-data-root", default=None, help="root the financial lake serves")
    parser.add_argument("--feature-eng-root", default=None, help="root the 2011-2021 archive sits under")
    parser.add_argument("--lake-inventory", default=None,
                        help="a lake inventory snapshot; without it, `inventoried` is registered UNKNOWN")
    parser.add_argument("--actor", required=True, help="who is making these registrations")
    parser.add_argument("--registered-at", default=None,
                        help="the receipt clock, with an offset (default: now). It is outside the key")
    parser.add_argument("--evidence-out", default=None, help="write the canonical rows here for review")
    parser.add_argument("--family", default="calendar", choices=FAMILIES,
                        help="which declared family to register (default: the five calendar resources, so an "
                             "existing invocation keeps registering exactly what it did)")
    parser.add_argument("--dry-run", action="store_true", help="derive and print; write nothing")
    args = parser.parse_args(argv)

    inventory_ref, inventory = _artifact(args.inventory)
    clock_ref, clock_artifact = _artifact(args.clock)
    registered_at = instant(args.registered_at or datetime.now(timezone.utc), "registered_at").isoformat()
    roots = {"financial_data_root": args.financial_data_root, "feature_eng_root": args.feature_eng_root}

    registrations, refused = build_registrations(inventory, clock_artifact, inventory_ref, clock_ref,
                                                 roots, args.actor, registered_at,
                                                 _read_lake_inventory(args.lake_inventory), args.family)
    overlap = consensus_overlap(registrations)
    for registration in registrations:
        registration["facts"]["carries"]["consensus_overlap_with_an_observed_clock_source"] = overlap

    registry = ResourceRegistry(args.registry)
    results = []
    for registration in registrations:
        name = registration.pop("_inventory_name")
        if args.dry_run:
            results.append({"resource": registration["resource"], "inventory_name": name,
                            "disposition": "DRY_RUN", "row": registration})
            continue
        try:
            row, disposition = registry.append(registration)
        except RegistrationRefusal as exc:
            refused.append({"resource": registration["resource"], "code": "REGISTRATION_REFUSED", "why": str(exc)})
            continue
        results.append({"resource": row["resource"], "inventory_name": name, "disposition": disposition,
                        "row_sha256": row.get("row_sha256"), "row_id": row.get("row_id"),
                        "facts_sha256": row.get("facts_sha256"), "content_sha256": row.get("content_sha256"),
                        "catalog_key": row.get("catalog_key"), "revision_index": row.get("revision_index"),
                        "absences": [a["code"] for a in row["facts"]["absences"]],
                        "row": row})

    states = sorted({r["facts"]["governed_delivery"]["state"] for r in registrations})
    report = {"schema": "data_gov.calendar_registration_run.v1", "tool": TOOL,
              "family": args.family,
              "registered_at": registered_at, "actor": args.actor,
              "registry": str(args.registry),
              "inventory_artifact": inventory_ref, "clock_artifact": clock_ref,
              "registered": len([r for r in results if r["disposition"] in ("STORED", "REVISION")]),
              "duplicates": len([r for r in results if r["disposition"] == "DUPLICATE"]),
              "refused": refused,
              "consensus_overlap": overlap,
              "results": results,
              "grants": ("NOTHING. Every resource of this run stays " + ", ".join(states) + ". Registering a "
                         "resource records what is known and missing about it; it authorizes no delivery")}
    if args.evidence_out:
        Path(args.evidence_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.evidence_out).write_text(
            json.dumps(report, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        report_digest = digest(report)
        print(f"wrote {args.evidence_out} (canonical digest {report_digest})")
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2, sort_keys=True))
    for item in results:
        print(f"  {item['disposition']:<9} {item['resource']}  facts={str(item.get('facts_sha256'))[:16]} "
              f"absences={len(item.get('absences') or [])}")
    return 0 if not refused else 1


if __name__ == "__main__":                                       # pragma: no cover - a CLI
    sys.exit(main())
