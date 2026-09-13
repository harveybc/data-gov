"""Regression tests for the review blockers of flow v2: ids that escape the lake, and read() under holdout."""

import os

import pytest

from lake_plugins.files_lake import Plugin


def _lake(tmp_path, **params):
    root = tmp_path / "lake"
    (root / "market").mkdir(parents=True)
    (root / "market" / "hourly.csv").write_text("ts,v\n2024-12-30 00:00:00,1\n2024-12-31 00:00:00,2\n")
    (root / "market" / "untimed.csv").write_text("name,value\na,1\nb,2\n")
    (tmp_path / "secret.csv").write_text("ts,v\n2025-06-01 00:00:00,9\n")
    os.symlink(tmp_path / "secret.csv", root / "market" / "link.csv")
    lake = Plugin()
    lake.set_params(lake_id="lab", root_path=str(root), include_globs=["**/*.csv"], holdout_start="2025-01-01",
                    spool_dir=str(tmp_path / "spool"), cuts_dir=str(tmp_path / "cuts"), **params)
    return lake


@pytest.mark.parametrize("rid", ["../secret.csv", "market/../../secret.csv", "/etc/hostname", "", ".",
                                 "market//hourly.csv", "market\\hourly.csv", "market/link.csv"])
def test_an_id_outside_the_lake_is_unknown_on_every_verb(tmp_path, rid):
    lake = _lake(tmp_path)
    with pytest.raises(FileNotFoundError):
        lake.download(rid)
    with pytest.raises(FileNotFoundError):
        lake.coverage(rid)
    with pytest.raises(FileNotFoundError):
        lake.read(rid, start="2024-12-30", end="2024-12-31")


def test_read_without_a_time_column_under_holdout_is_refused(tmp_path):
    lake = _lake(tmp_path)
    with pytest.raises(PermissionError, match="no time column"):
        lake.read("market/untimed.csv", start="2020-01-01", end="2020-01-02")
    with pytest.raises(PermissionError, match="no time column"):
        lake.read("market/untimed.csv")


def test_read_of_a_declared_untimed_resource_is_allowed(tmp_path):
    lake = _lake(tmp_path, untimed=["market/untimed.csv"])
    assert len(lake.read("market/untimed.csv")["rows"]) == 2


def test_a_normal_id_still_reads(tmp_path):
    lake = _lake(tmp_path)
    assert len(lake.read("market/hourly.csv", start="2024-12-30", end="2024-12-31")["rows"]) == 2
