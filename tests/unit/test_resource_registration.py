"""The registry's key discipline: an absence is registrable, a re-read is a duplicate, a rewrite is refused by name."""

from __future__ import annotations

import json

import pytest

from data_gov import resource_registration as rr

CONTENT = "a" * 64


def _facts(**overrides):
    facts = {
        "what_it_is": "a calendar archive used by an event study",
        "physical_location": {"lake": "financial_files", "resource_path_in_root": "economic_calendar/x.parquet"},
        "coverage": {"status": "MEASURED", "from": "2011-01-01", "to": "2021-04-26"},
        "clock": {"column": "event_date", "timezone_aware_in_the_bytes": False,
                  "evidence": "MEASURED_FROM_DATA_NOT_DOCUMENTED_BY_THE_SOURCE",
                  "documented_by_the_source": False,
                  "eras": [{"from": "2012-05-01", "to": "2018-01-31", "status": "DETERMINED",
                            "utc_offset": "UTC-05:00"},
                           {"from": "2018-02-01", "to": "2018-02-28", "status": "UNDETERMINED",
                            "utc_offset": None}]},
        "publication_instant": {"kind": "ASSUMED_SCHEDULED_PUBLICATION_LOCALIZED", "observed": False,
                                "why": "only a scheduled instant exists"},
        "carries": {"consensus": True, "consensus_non_null_rows": 54291, "actual": True},
        "absences": [{"code": "NO_PUBLICATION_CLOCK", "field": "publication_instant",
                      "why": "no column records when a value became public"}],
        "governed_delivery": {"state": "CLOSED_NOT_IN_ANY_LAKE_ROOT",
                              "why": "outside every configured lake root"},
        "measurement_provenance": {"tool": "test"},
    }
    facts.update(overrides)
    return facts


def _registration(**overrides):
    body = {"lake": "financial_files", "resource": "economic_calendar/x.parquet", "content_sha256": CONTENT,
            "facts": _facts(), "registered_by": "satoshi-iii",
            "registered_at": "2026-09-26T00:00:00+00:00"}
    body.update(overrides)
    return body


# --- the facts a registration must carry -----------------------------------------------------------------------------

def test_a_registration_without_absences_is_refused():
    """A silently empty absence list is how a missing publication clock becomes a footnote."""
    with pytest.raises(rr.RegistrationRefusal, match="NO_ABSENCES_DECLARED"):
        rr.validate_registration(_registration(facts=_facts(absences=[])))


def test_an_unknown_absence_code_is_refused():
    with pytest.raises(rr.RegistrationRefusal, match="UNKNOWN_ABSENCE_CODE"):
        rr.validate_registration(_registration(facts=_facts(
            absences=[{"code": "NO_PUBLICATION_CLOCK_MAYBE", "why": "invented"}])))


def test_an_absence_without_a_reason_is_refused():
    with pytest.raises(rr.RegistrationRefusal, match="ABSENCE_WITHOUT_A_REASON"):
        rr.validate_registration(_registration(facts=_facts(
            absences=[{"code": "NO_PUBLICATION_CLOCK", "why": ""}])))


def test_every_required_fact_is_required():
    for field in rr.REQUIRED_FACTS:
        facts = _facts()
        facts.pop(field)
        with pytest.raises(rr.RegistrationRefusal, match="FACTS_MISSING"):
            rr.validate_registration(_registration(facts=facts))


def test_assumed_and_observed_never_share_a_word():
    """`observed: True` under an assumed kind is the exact confusion the catalog exists to prevent."""
    with pytest.raises(rr.RegistrationRefusal, match="PUBLICATION_OBSERVED_CONTRADICTS_KIND"):
        rr.validate_registration(_registration(facts=_facts(
            publication_instant={"kind": "ASSUMED_SCHEDULED_PUBLICATION", "observed": True, "why": "x"})))
    with pytest.raises(rr.RegistrationRefusal, match="PUBLICATION_OBSERVED_CONTRADICTS_KIND"):
        rr.validate_registration(_registration(facts=_facts(
            publication_instant={"kind": "OBSERVED_ACTUAL_PUBLICATION", "observed": False, "why": "x"})))


def test_an_unknown_publication_kind_is_refused():
    with pytest.raises(rr.RegistrationRefusal, match="UNKNOWN_PUBLICATION_KIND"):
        rr.validate_registration(_registration(facts=_facts(
            publication_instant={"kind": "PROBABLY_OBSERVED", "observed": True, "why": "x"})))


def test_the_facts_digest_is_computed_here_not_accepted():
    """A caller cannot publish a digest of something other than what it registered."""
    normalised = rr.validate_registration(_registration())
    assert normalised["facts_sha256"] == rr.digest({"schema": rr.SCHEMA, "facts": normalised["facts"]})
    forged = rr.validate_registration(_registration(facts_sha256="0" * 64))
    assert forged["facts_sha256"] != "0" * 64


def test_a_naive_receipt_clock_is_refused_never_localized():
    with pytest.raises(rr.RegistrationRefusal, match="AMBIGUOUS_LOCAL_TIME"):
        rr.validate_registration(_registration(registered_at="2026-09-26T00:00:00"))


# --- the key discipline ----------------------------------------------------------------------------------------------

def test_the_receipt_clock_is_outside_the_key_so_a_re_read_is_a_duplicate(tmp_path):
    registry = rr.ResourceRegistry(tmp_path / "registry")
    first, disposition = registry.append(_registration())
    assert disposition == "STORED"
    again, disposition = registry.append(_registration(registered_at="2026-09-27T12:00:00+00:00"))
    assert disposition == "DUPLICATE"
    assert again["row_sha256"] == first["row_sha256"]
    assert again["registered_at"] == first["registered_at"]      # the first reading's clock is the one retained
    assert len(registry.refresh().rows()) == 1


def test_changed_facts_are_a_new_row_with_a_higher_revision_and_the_old_row_survives(tmp_path):
    registry = rr.ResourceRegistry(tmp_path / "registry")
    first, _ = registry.append(_registration())
    revised_facts = _facts()
    revised_facts["absences"] = revised_facts["absences"] + [
        {"code": "NO_CONSENSUS_PUBLICATION_CLOCK", "field": "consensus", "why": "a consensus with no instant"}]
    second, disposition = registry.append(_registration(facts=revised_facts,
                                                        registered_at="2026-09-27T00:00:00+00:00"))
    assert disposition == "REVISION"
    assert second["revision_index"] == 1
    assert first["row_sha256"] in second["supersedes"]
    rows = registry.refresh().rows()
    assert len(rows) == 2
    assert all(row["integrity"] == "OK" for row in rows)


def test_new_bytes_under_the_same_path_are_a_revision_of_the_same_slot(tmp_path):
    registry = rr.ResourceRegistry(tmp_path / "registry")
    first, _ = registry.append(_registration())
    second, disposition = registry.append(_registration(content_sha256="b" * 64,
                                                        registered_at="2026-09-28T00:00:00+00:00"))
    assert disposition == "REVISION"
    assert second["catalog_key"] == first["catalog_key"]         # a consumer asks the catalog about a path
    assert second["registration_sha256"] != first["registration_sha256"]


def test_row_rewrite_is_refused_by_name(tmp_path):
    registry = rr.ResourceRegistry(tmp_path / "registry")
    stored, _ = registry.append(_registration())
    forged = dict(stored)
    forged["registration_sha256"] = rr.digest({"forged": True})
    forged["facts"] = _facts(what_it_is="something else entirely")
    with pytest.raises(rr.RegistrationRefusal, match="ROW_REWRITE_REFUSED"):
        registry.write_row(forged)
    on_disk = registry.refresh().find_row(stored["row_id"])
    assert on_disk["facts"]["what_it_is"] == stored["facts"]["what_it_is"]


def test_known_at_excludes_a_registration_made_after_t(tmp_path):
    registry = rr.ResourceRegistry(tmp_path / "registry")
    registry.append(_registration(resource="economic_calendar/a.parquet",
                                  registered_at="2026-09-26T00:00:00+00:00"))
    registry.append(_registration(resource="economic_calendar/b.parquet",
                                  registered_at="2026-09-28T00:00:00+00:00"))
    registry.refresh()
    before = registry.known_at("2026-09-27T00:00:00+00:00")
    assert [r["resource"] for r in before] == ["economic_calendar/a.parquet"]
    after = registry.known_at("2026-09-29T00:00:00+00:00")
    assert sorted(r["resource"] for r in after) == ["economic_calendar/a.parquet", "economic_calendar/b.parquet"]


def test_known_at_returns_the_registration_in_force_not_the_newest(tmp_path):
    registry = rr.ResourceRegistry(tmp_path / "registry")
    first, _ = registry.append(_registration(registered_at="2026-09-26T00:00:00+00:00"))
    revised = _facts(what_it_is="the same archive, described again")
    registry.append(_registration(facts=revised, registered_at="2026-10-01T00:00:00+00:00"))
    in_force = registry.refresh().known_at("2026-09-27T00:00:00+00:00")
    assert len(in_force) == 1
    assert in_force[0]["row_sha256"] == first["row_sha256"]


def test_a_row_moved_under_another_key_fails_integrity_and_is_not_read_as_that_resource(tmp_path):
    """A whole, self-consistent registration of another resource answers the self-hash question perfectly."""
    registry = rr.ResourceRegistry(tmp_path / "registry")
    a, _ = registry.append(_registration(resource="economic_calendar/a.parquet"))
    b, _ = registry.append(_registration(resource="economic_calendar/b.parquet"))
    path_a = tmp_path / "registry" / a["row_id"][:2] / f"{a['row_id']}.json"
    path_a.write_text(json.dumps(b), encoding="utf-8")
    row = registry.refresh()._read(path_a)
    assert row["integrity"] == "FAILED"
    assert any("MISPLACED_ROW" in problem for problem in row["integrity_problems"])
    assert registry.known_at("2026-10-01T00:00:00+00:00") and all(
        r["integrity"] == "OK" for r in registry.known_at("2026-10-01T00:00:00+00:00"))


def test_a_tampered_facts_digest_fails_integrity(tmp_path):
    registry = rr.ResourceRegistry(tmp_path / "registry")
    stored, _ = registry.append(_registration())
    path = tmp_path / "registry" / stored["row_id"][:2] / f"{stored['row_id']}.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["facts"]["absences"] = []                              # the absence quietly dropped on disk
    path.write_text(json.dumps(body), encoding="utf-8")
    row = registry.refresh()._read(path)
    assert row["integrity"] == "FAILED"
    assert any("FACTS_DIGEST_MISMATCH" in p for p in row["integrity_problems"])
    assert registry.replay()["verdict"] == "ROWS_FAILED_RE_DERIVATION"


def test_a_supplied_name_is_never_a_registry_key(tmp_path):
    registry = rr.ResourceRegistry(tmp_path / "registry")
    for forged in ("../escape", "not-a-digest", "A" * 64):
        with pytest.raises(rr.RegistrationRefusal, match="INVALID_ROW_KEY"):
            registry._path(forged)


def test_registering_grants_nothing(tmp_path):
    registry = rr.ResourceRegistry(tmp_path / "registry")
    row, _ = registry.append(_registration())
    assert row["execution_authorized"] is False
    assert row["facts"]["governed_delivery"]["state"].startswith("CLOSED")
    assert "NOTHING" in row["registration_grants"]


def test_replay_re_derives_every_row(tmp_path):
    registry = rr.ResourceRegistry(tmp_path / "registry")
    registry.append(_registration(resource="economic_calendar/a.parquet"))
    registry.append(_registration(resource="economic_calendar/b.parquet"))
    report = registry.refresh().replay()
    assert report["verdict"] == "ALL_ROWS_RE_DERIVED"
    assert report["live"] == 2 and report["slots"] == 2 and report["failed"] == []
