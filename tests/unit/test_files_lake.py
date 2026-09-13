"""files_lake: stat-only inventory, time-column coverage, AS_IS / CUT downloads (04_FLOW_V2 §2)."""

import hashlib
from pathlib import Path

import pytest

from lake_plugins.errors import UnsupportedError
from lake_plugins.files_lake import Plugin, parse_day
from tests.conftest import (
    BTC_FUNDING,
    FINANCIAL_ROOT,
    LAB_EARLY,
    LAB_HOURLY,
    LAB_STATIC,
    hourly_lines,
    write_lab_files,
)

HOLDOUT = "2025-01-01"
NEEDS_FINANCIAL = pytest.mark.skipif(
    not (FINANCIAL_ROOT / BTC_FUNDING).exists(), reason="financial-data not present"
)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _lake(tmp_path, root, **params):
    settings = dict(
        lake_id="t",
        root_path=str(root),
        include_globs=["**/*.csv", "**/*.parquet"],
        time_column="ts",
        holdout_start=HOLDOUT,
        spool_dir=str(tmp_path / "spool"),
        cuts_dir=str(tmp_path / "cuts"),
    )
    settings.update(params)
    lake = Plugin()
    lake.set_params(**settings)
    return lake


@pytest.fixture
def root(tmp_path):
    return write_lab_files(tmp_path / "root")


def _cut_files(tmp_path):
    return [p for p in (tmp_path / "cuts").rglob("*") if p.is_file()]


def test_discover_is_stat_only(tmp_path, root):
    lake = _lake(tmp_path, root)
    items = lake.discover()
    assert {i["resource_id"] for i in items} == {LAB_EARLY, LAB_HOURLY, LAB_STATIC}
    assert all("sha256_prefix" not in i and i["bytes"] > 0 for i in items)


def test_coverage_reads_time_column(tmp_path, root):
    lake = _lake(tmp_path, root)
    cov = lake.coverage(LAB_HOURLY)
    assert cov["rows"] == 51
    assert cov["time_column"] == "ts"
    assert cov["t_min"] == "2024-12-30 00:00:00"
    assert cov["t_max"] == "2025-01-01 02:00:00"
    static = lake.coverage(LAB_STATIC)
    assert static["rows"] == 2 and static["t_max"] is None


def test_as_is_when_coverage_ends_before_holdout(tmp_path, root):
    lake = _lake(tmp_path, root)
    info = lake.download(LAB_EARLY)
    assert info["delivery"] == "AS_IS"
    assert Path(info["path"]) == root / LAB_EARLY
    assert info["sha256"] == info["source_sha256"] == _sha(root / LAB_EARLY)
    assert info["filename"] == LAB_EARLY
    assert info["time_column"] == "ts"
    assert info["bytes"] == (root / LAB_EARLY).stat().st_size


def test_spans_holdout_is_refused_without_range(tmp_path, root):
    lake = _lake(tmp_path, root)
    with pytest.raises(PermissionError, match="spans holdout"):
        lake.download(LAB_HOURLY)


def test_no_time_column_under_holdout(tmp_path, root):
    lake = _lake(tmp_path, root)
    with pytest.raises(PermissionError, match="no time column under holdout"):
        lake.download(LAB_STATIC)


def test_untimed_resource_is_delivered_as_is(tmp_path, root):
    lake = _lake(tmp_path, root, untimed=[LAB_STATIC])
    info = lake.download(LAB_STATIC)
    assert info["delivery"] == "AS_IS"
    assert info["time_column"] == ""
    assert info["sha256"] == _sha(root / LAB_STATIC)


def test_cut_naive_csv_is_a_byte_subset(tmp_path, root):
    lake = _lake(tmp_path, root)
    info = lake.download(LAB_HOURLY, start="2024-12-30", end="2024-12-31")
    assert info["delivery"] == "CUT"
    assert info["time_column"] == "ts"
    cut = Path(info["path"])
    assert cut == tmp_path / "cuts" / info["source_sha256"] / "2024-12-30_2024-12-31.csv"
    assert info["filename"] == "hourly_2024-12-30_2024-12-31.csv"
    source_lines = (root / LAB_HOURLY).read_bytes().split(b"\n")
    cut_lines = cut.read_bytes().split(b"\n")
    assert cut_lines[0] == b"ts,value"
    assert cut_lines[1:-1] == source_lines[1:49]  # 48 hourly rows before 2025-01-01 00:00
    assert all(line in source_lines for line in cut_lines)
    assert info["sha256"] == _sha(cut)
    assert info["source_sha256"] == _sha(root / LAB_HOURLY)


def test_cut_with_offsets_keeps_wall_clock(tmp_path, root):
    (root / "tokyo.csv").write_text(
        "ts,value\n" + "\n".join(hourly_lines(suffix="+09:00")) + "\n"
    )
    lake = _lake(tmp_path, root)
    info = lake.download("tokyo.csv", start="2024-12-31", end="2024-12-31")
    text = Path(info["path"]).read_text()
    assert "2024-12-31 23:00:00+09:00" in text
    assert "2025-01-01 00:00:00+09:00" not in text  # 2024-12-31 15:00 UTC, but 2025 on its clock
    assert "2024-12-30 23:00:00+09:00" not in text
    assert text.count("\n") == 25


def test_parquet_tokyo_daily_excludes_first_2025_bar(tmp_path, root):
    import pandas as pd
    import pyarrow.parquet as pq

    frame = pd.DataFrame(
        {
            "ts": pd.date_range("2024-12-28", periods=5, freq="D", tz="Asia/Tokyo"),
            "close": [1.0, 2.0, 3.0, 4.0, 5.0],
        }
    )
    frame.to_parquet(root / "daily.parquet", index=False)
    lake = _lake(tmp_path, root)
    with pytest.raises(PermissionError, match="spans holdout"):
        lake.download("daily.parquet")
    info = lake.download("daily.parquet", start="2024-12-28", end="2024-12-31")
    assert info["delivery"] == "CUT"
    assert info["path"].endswith(".parquet")
    cut = pq.ParquetFile(info["path"])
    got = cut.read().column("ts").to_pandas().dt.tz_localize(None)
    assert len(got) == 4
    assert got.max() == pd.Timestamp("2024-12-31")
    assert cut.metadata.row_group(0).column(0).compression == "SNAPPY"
    assert cut.schema_arrow.equals(pq.read_schema(root / "daily.parquet"))


def test_multiline_quoted_csv_is_unsupported(tmp_path, root):
    (root / "notes.csv").write_text(
        'ts,note\n2024-06-01 00:00:00,"a\nb"\n2024-06-02 00:00:00,c\n2024-06-03 00:00:00,d\n'
    )
    lake = _lake(tmp_path, root)
    with pytest.raises(UnsupportedError, match="unsupported csv"):
        lake.download("notes.csv", start="2024-06-01", end="2024-06-02")
    assert _cut_files(tmp_path) == []


def test_zero_rows_removed_is_as_is(tmp_path, root):
    lake = _lake(tmp_path, root)
    info = lake.download(LAB_EARLY, start="2024-05-01", end="2024-06-30")
    assert info["delivery"] == "AS_IS"
    assert Path(info["path"]) == root / LAB_EARLY
    assert _cut_files(tmp_path) == []


def test_cut_is_materialised_once(tmp_path, root):
    lake = _lake(tmp_path, root)
    first = lake.download(LAB_HOURLY, start="2024-12-30", end="2024-12-30")
    stat = Path(first["path"]).stat()
    second = lake.download(LAB_HOURLY, start="2024-12-30", end="2024-12-30")
    assert second["path"] == first["path"]
    assert second["sha256"] == first["sha256"]
    assert Path(second["path"]).stat().st_mtime_ns == stat.st_mtime_ns
    assert Path(second["path"]).read_bytes() == Path(first["path"]).read_bytes()
    assert len(_cut_files(tmp_path)) == 1


def test_holdout_assertion(tmp_path, root):
    import pandas as pd

    with pytest.raises(PermissionError, match="holdout"):
        Plugin._assert_holdout(pd.Timestamp("2025-01-01"), pd.Timestamp("2025-01-01"))
    Plugin._assert_holdout(pd.Timestamp("2024-12-31 23:00"), pd.Timestamp("2025-01-01"))
    lake = _lake(tmp_path, root)
    with pytest.raises(PermissionError, match="holdout"):
        lake.download(LAB_HOURLY, start="2024-12-30", end="2025-01-01")
    assert _cut_files(tmp_path) == []


def test_invalid_ranges(tmp_path, root):
    lake = _lake(tmp_path, root)
    for start, end in (
        ("2024-1-1", "2024-01-02"),
        ("2024-01-02", "2024-01-01"),
        ("2024-02-30", "2024-03-01"),
        ("2024-01-01T00:00:00", "2024-01-02"),
        ("2024-01-01", None),
    ):
        with pytest.raises(ValueError, match="invalid from/to"):
            lake.download(LAB_EARLY, start=start, end=end)
    with pytest.raises(ValueError):
        parse_day("20240101")


def test_ranged_download_of_a_file_without_time_column(tmp_path, root):
    lake = _lake(tmp_path, root, untimed=[LAB_STATIC])
    with pytest.raises(UnsupportedError, match="no time column"):
        lake.download(LAB_STATIC, start="2024-01-01", end="2024-01-02")


def test_unparseable_time_columns(tmp_path, root):
    (root / "mixed.csv").write_text("ts,v\n2024-06-01 00:00:00,1\n01/06/2024,2\n")
    (root / "epoch.csv").write_text("ts,v\n1717200000,1\n1717286400,2\n")
    lake = _lake(tmp_path, root)
    with pytest.raises(UnsupportedError, match="unparseable time column"):
        lake.download("mixed.csv", start="2024-06-01", end="2024-06-01")
    with pytest.raises(UnsupportedError, match="unparseable time column"):
        lake.download("epoch.csv", start="2024-06-01", end="2024-06-01")
    lake.set_params(time_unit="s")
    info = lake.download("epoch.csv", start="2024-06-01", end="2024-06-01")
    assert info["delivery"] == "CUT"
    assert Path(info["path"]).read_text() == "ts,v\n1717200000,1\n"


def test_read_uses_strict_upper_bound_and_holdout(tmp_path, root):
    lake = _lake(tmp_path, root)
    out = lake.read(LAB_HOURLY, start="2024-12-30", end="2024-12-30")
    assert len(out["rows"]) == 24
    assert all(str(r["ts"]) < "2024-12-31" for r in out["rows"])
    with pytest.raises(PermissionError, match="holdout"):
        lake.read(LAB_HOURLY, start="2024-12-31", end="2025-01-01")
    with pytest.raises(PermissionError, match="spans holdout"):
        lake.read(LAB_HOURLY)
    assert len(lake.read(LAB_EARLY)["rows"]) == 3


def test_source_sha256_is_memoised_per_stat(tmp_path, root):
    lake = _lake(tmp_path, root)
    first = lake.download(LAB_EARLY)["source_sha256"]
    memo = tmp_path / "source_sha256.json"
    assert memo.is_file() and first in memo.read_text()
    (root / LAB_EARLY).write_text("ts,value\n2024-06-01 00:00:00,9\n")
    second = lake.download(LAB_EARLY)["source_sha256"]
    assert second != first and second == _sha(root / LAB_EARLY)


@NEEDS_FINANCIAL
def test_slice_hash_is_stable():
    lake = Plugin()
    lake.set_params(
        lake_id="financial_files",
        root_path=str(FINANCIAL_ROOT),
        include_globs=[BTC_FUNDING],
        time_column="fundingTime",
        holdout_start="2025-01-01",
    )
    first = lake.read(BTC_FUNDING, start="2020-01-01", end="2020-01-07")
    second = lake.read(BTC_FUNDING, start="2020-01-01", end="2020-01-07")
    assert first["sha256"] == second["sha256"]
    assert first["rows"]
    assert Path(BTC_FUNDING).as_posix() in [
        item["resource_id"] for item in lake.discover()
    ]


@NEEDS_FINANCIAL
def test_real_parquet_row_group_cut(tmp_path):
    import pandas as pd
    import pyarrow.parquet as pq

    lake = _lake(tmp_path, FINANCIAL_ROOT, time_column="fundingTime", include_globs=[BTC_FUNDING])
    with pytest.raises(PermissionError, match="spans holdout"):
        lake.download(BTC_FUNDING)
    info = lake.download(BTC_FUNDING, start="2020-01-01", end="2020-01-07")
    assert info["delivery"] == "CUT"
    got = pq.read_table(info["path"]).column("fundingTime").to_pandas().dt.tz_localize(None)
    assert len(got) == 21  # three funding bars a day, seven days
    assert got.min() >= pd.Timestamp("2020-01-01") and got.max() < pd.Timestamp("2020-01-08")
