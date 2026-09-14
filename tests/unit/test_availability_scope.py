"""N2: the availability scope of a contract is executed by the lake, not only noted.

A contract may declare `availability` = {label, completion_lag_max, timezone_evidence,
use_class}. The lake then keeps a row in a day cut only when
`label + completion_lag_max < range end`, refuses AS_IS under holdout unless
`max(label) + lag < holdout`, keeps ranges at calendar days, and publishes label,
bound, time-zone evidence and use class separately from the contract digest.
"""

import hashlib
import json
from pathlib import Path

import pytest

from lake_plugins.errors import UnsupportedError
from lake_plugins.files_lake import Plugin, availability_scope, scope_of

HOLDOUT = "2025-01-01"
BASE = {"event_time_column": "DATE_TIME", "available_time_column": "DATE_TIME",
        "timezone": "NAIVE_WALL_CLOCK", "time_unit": None, "frequency": "4h"}
OFFLINE = {"label": "WINDOW_END", "completion_lag_max": "1h",
           "timezone_evidence": "UNKNOWN", "use_class": "OFFLINE_DAY_GRANULAR"}
LIVE = {"label": "EVENT_INSTANT", "completion_lag_max": "0s",
        "timezone_evidence": "PRODUCER_STATEMENT", "use_class": "LIVE_EQUIVALENT"}


def _rows(days, hours=(0, 4, 8, 12, 16, 20)):
    out = []
    for day in days:
        for hour in hours:
            out.append(f"2013-01-{day:02d} {hour:02d}:00:00,{day + hour / 100:.2f}")
    return out


def _write(root, name, lines):
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text("DATE_TIME,typical_price\n" + "\n".join(lines) + "\n", encoding="ascii")
    return root / name


def _lake(tmp_path, root, contracts, **params):
    lake = Plugin()
    settings = dict(lake_id="t", root_path=str(root), include_globs=["**/*.csv"], time_column="DATE_TIME",
                    holdout_start=HOLDOUT, spool_dir=str(tmp_path / "spool"), cuts_dir=str(tmp_path / "cuts"),
                    resource_contracts=contracts)
    settings.update(params)
    lake.set_params(**settings)
    return lake


def _read(info):
    try:
        return info["handle"].read()
    finally:
        info["handle"].close()


def _times(data):
    return [line.split(",")[0] for line in data.decode().splitlines()[1:]]


def test_scope_block_is_validated_and_live_equivalent_is_strict():
    assert availability_scope(OFFLINE)["completion_lag"].total_seconds() == 3600
    assert availability_scope(LIVE)["completion_lag"].total_seconds() == 0
    for bad in (
        {**OFFLINE, "label": "CLOSE"}, {**OFFLINE, "use_class": "LIVE"}, {**OFFLINE, "timezone_evidence": "GUESS"},
        {**OFFLINE, "completion_lag_max": None}, {**OFFLINE, "completion_lag_max": "-1h"},
        {**OFFLINE, "completion_lag_max": "soon"}, {**LIVE, "completion_lag_max": "1s"},
        {**LIVE, "timezone_evidence": "UNKNOWN"}, {**LIVE, "label": "UNKNOWN"}, {"label": "UNKNOWN"},
    ):
        with pytest.raises(UnsupportedError):
            availability_scope(bad)
    assert scope_of(BASE) == {"label": "UNKNOWN", "completion_lag_max": None,
                              "timezone_evidence": "UNKNOWN", "use_class": "UNDECLARED"}
    assert scope_of({**BASE, "availability": OFFLINE}) == OFFLINE


def test_contract_digest_binds_the_scope_and_a_bad_scope_closes_the_resource(tmp_path):
    root = tmp_path / "root"
    _write(root, "a.csv", _rows([2, 3]))
    plain = _lake(tmp_path, root, {"a.csv": BASE})
    scoped = _lake(tmp_path, root, {"a.csv": {**BASE, "availability": OFFLINE}})
    a = plain.governed_download("a.csv")
    b = scoped.governed_download("a.csv")
    _read(a), _read(b)
    assert a["availability_contract_sha256"] != b["availability_contract_sha256"]
    assert b["availability_contract_sha256"] == hashlib.sha256(json.dumps(
        {**BASE, "availability": OFFLINE}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert a["availability"]["use_class"] == "UNDECLARED" and b["availability"]["use_class"] == "OFFLINE_DAY_GRANULAR"
    broken = _lake(tmp_path, root, {"a.csv": {**BASE, "availability": {**OFFLINE, "label": "CLOSE"}}})
    with pytest.raises(UnsupportedError, match="label"):
        broken.governed_download("a.csv")


def test_completion_lag_excludes_rows_not_complete_before_the_range_end(tmp_path):
    root = tmp_path / "root"
    # day 3 has a bar labelled 23:30 that completes at 00:30 of day 4 under a 1h bound
    _write(root, "a.csv", _rows([2, 3]) + ["2013-01-03 23:30:00,9.99"] + _rows([4]))
    lag = _lake(tmp_path, root, {"a.csv": {**BASE, "availability": OFFLINE}})
    zero = _lake(tmp_path, root, {"a.csv": {**BASE, "availability": {**OFFLINE, "completion_lag_max": "0s"}}})
    with_lag = _times(_read(lag.governed_download("a.csv", "2013-01-02", "2013-01-03")))
    without = _times(_read(zero.governed_download("a.csv", "2013-01-02", "2013-01-03")))
    assert "2013-01-03 23:30:00" not in with_lag and "2013-01-03 20:00:00" in with_lag
    assert "2013-01-03 23:30:00" in without
    assert len(with_lag) == 12 and len(without) == 13


def test_ranges_are_calendar_days_never_intrabar(tmp_path):
    root = tmp_path / "root"
    _write(root, "a.csv", _rows([2, 3]))
    lake = _lake(tmp_path, root, {"a.csv": {**BASE, "availability": OFFLINE}})
    for start, end in (("2013-01-02T04:00", "2013-01-02T12:00"), ("2013-01-02 04:00:00", "2013-01-02"),
                       ("2013-01-02", None), (None, "2013-01-02")):
        with pytest.raises(ValueError, match="invalid from/to"):
            lake.governed_download("a.csv", start, end)


def test_interval_extremes_and_incomplete_days(tmp_path):
    root = tmp_path / "root"
    # day 4 is incomplete: bars only until 12:00 (a Friday-like session end)
    _write(root, "a.csv", _rows([2, 3]) + _rows([4], hours=(0, 4, 8, 12)))
    lake = _lake(tmp_path, root, {"a.csv": {**BASE, "availability": OFFLINE}})
    first = _times(_read(lake.governed_download("a.csv", "2013-01-02", "2013-01-02")))
    assert first == [f"2013-01-02 {h:02d}:00:00" for h in (0, 4, 8, 12, 16, 20)]
    last = _times(_read(lake.governed_download("a.csv", "2013-01-04", "2013-01-04")))
    assert last == [f"2013-01-04 {h:02d}:00:00" for h in (0, 4, 8, 12)]
    beyond = lake.governed_download("a.csv", "2013-02-01", "2013-02-02")
    assert _times(_read(beyond)) == [] and beyond["delivery"] == "CUT"
    whole = lake.governed_download("a.csv", "2013-01-01", "2013-01-31")
    assert whole["delivery"] == "AS_IS" and len(_times(_read(whole))) == 16


def test_partition_boundaries_and_an_altered_future_observation(tmp_path):
    root = tmp_path / "root"
    lines = _rows(range(2, 12))
    source = _write(root, "a.csv", lines)
    lake = _lake(tmp_path, root, {"a.csv": {**BASE, "availability": OFFLINE}})
    ranges = {"train": ("2013-01-02", "2013-01-05"), "calibration": ("2013-01-06", "2013-01-08"),
              "confirmation": ("2013-01-09", "2013-01-10")}
    cuts = {name: lake.governed_download("a.csv", *r) for name, r in ranges.items()}
    data = {name: _read(info) for name, info in cuts.items()}
    times = {name: _times(d) for name, d in data.items()}
    assert not (set(times["train"]) & set(times["calibration"])) and not (set(times["calibration"]) & set(times["confirmation"]))
    assert times["train"][-1] == "2013-01-05 20:00:00" and times["calibration"][0] == "2013-01-06 00:00:00"
    assert sum(len(t) for t in times.values()) == 9 * 6
    # alter one observation after the confirmation range: day 11 bar changes value
    altered = [line if not line.startswith("2013-01-11 08") else "2013-01-11 08:00:00,99.99" for line in lines]
    _write(root, "b.csv", altered)
    lake2 = _lake(tmp_path / "second", root, {"b.csv": {**BASE, "availability": OFFLINE}})
    again = {name: lake2.governed_download("b.csv", *r) for name, r in ranges.items()}
    for name in ranges:
        assert again[name]["source_sha256"] != cuts[name]["source_sha256"]
        assert _read(again[name]) == data[name] and again[name]["sha256"] == cuts[name]["sha256"]


def test_as_is_under_holdout_applies_the_completion_bound(tmp_path):
    root = tmp_path / "root"
    _write(root, "a.csv", ["2024-12-31 20:00:00,1", "2024-12-31 23:30:00,2"])
    lag = _lake(tmp_path, root, {"a.csv": {**BASE, "availability": OFFLINE}})
    with pytest.raises(PermissionError, match="spans holdout"):
        lag.governed_download("a.csv")
    zero = _lake(tmp_path, root, {"a.csv": {**BASE, "availability": {**OFFLINE, "completion_lag_max": "0s"}}})
    assert _read(zero.governed_download("a.csv")) and zero.governed_download("a.csv")["delivery"] == "AS_IS"
    # a day cut ending at the holdout delivers only the row complete before it
    cut = lag.governed_download("a.csv", "2024-12-31", "2024-12-31")
    assert cut["delivery"] == "CUT" and _times(_read(cut)) == ["2024-12-31 20:00:00"]
