import sqlite3

import pytest

from app.report import report_sha256
from lake_plugins.sql_lake import Plugin


def _report(key="exp-1", metrics=None, datasets=None, received_at="2026-09-13T00:00:00.000000Z"):
    report = {
        "experiment_key": key,
        "experiment_set_key": None,
        "actor": "predictor",
        "lake": "olap_lab",
        "config_sha256": None,
        "code_commit": "abc123",
        "project": "predictor",
        "phase": "phase_1_daily",
        "tags": {"plugin": "ann"},
        "datasets": datasets or [],
        "metrics": metrics
        or [{"metric": "MAE", "value": 0.1, "split": "train", "horizon": 1}],
        "lineage": "VERIFIED" if datasets else "UNVERIFIED",
        "received_at": received_at,
    }
    report["report_sha256"] = report_sha256(report)
    return report


def test_select_only(tmp_path):
    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE fact_performance (ts TEXT, value REAL)")
    conn.execute("INSERT INTO fact_performance VALUES ('2024-01-01', 1)")
    conn.commit()
    conn.close()
    lake = Plugin()
    lake.set_params(lake_id="olap_lab", sqlite_path=str(db), time_column="ts")
    names = {item["resource_id"] for item in lake.discover()}
    assert "fact_performance" in names
    assert {"gov_report", "gov_metric", "gov_dataset"} <= names
    out = lake.query("SELECT ts, value FROM fact_performance")
    assert out["rows"][0]["value"] == 1
    try:
        lake.query("DELETE FROM fact_performance")
        assert False, "expected reject"
    except ValueError:
        pass


def test_ddl_creates_file_in_wal_mode(tmp_path):
    db = tmp_path / "new" / "cube.sqlite"
    lake = Plugin()
    lake.set_params(lake_id="olap_lab", sqlite_path=str(db))
    assert db.is_file()
    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    views = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
    assert "gov_metric_current" in views
    conn.close()
    # a second start is a no-op
    lake.set_params(sqlite_path=str(db))


def test_terminal_dataset_schema_is_upgraded_without_dropping_rows(tmp_path):
    db = tmp_path / "old.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE gov_terminal_dataset (terminal_sha256 TEXT, delivery_id TEXT, "
        "lake_id TEXT, resource_id TEXT, role TEXT, sha256 TEXT, bytes INTEGER, "
        "source_sha256 TEXT, range_from TEXT, range_to TEXT, delivery_kind TEXT, "
        "time_column TEXT, verification_state TEXT)"
    )
    conn.execute(
        "INSERT INTO gov_terminal_dataset (terminal_sha256, sha256) VALUES ('old', 'digest')"
    )
    conn.commit()
    conn.close()
    lake = Plugin()
    lake.set_params(sqlite_path=str(db))
    conn = sqlite3.connect(db)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(gov_terminal_dataset)")}
    assert "availability_contract_sha256" in columns
    assert conn.execute("SELECT terminal_sha256 FROM gov_terminal_dataset").fetchone()[0] == "old"
    conn.close()


def test_write_metrics_is_idempotent_and_view_shows_latest(tmp_path):
    db = tmp_path / "cube.sqlite"
    lake = Plugin()
    lake.set_params(lake_id="olap_lab", sqlite_path=str(db))
    dataset = {
        "lake": "lab_files",
        "resource": "early.csv",
        "sha256": "a" * 64,
        "role": "x_train_file",
        "lineage": "VERIFIED",
        "reason": None,
        "event_id": 7,
        "source_sha256": "b" * 64,
        "range_from": None,
        "range_to": None,
        "delivery": "AS_IS",
        "time_column": "ts",
    }
    first = _report(datasets=[dataset])
    out = lake.write_metrics(first)
    assert out == {"stored": True, "already_stored": False, "lineage": "VERIFIED"}
    again = lake.write_metrics(first)
    assert again == {"stored": False, "already_stored": True, "lineage": "VERIFIED"}

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    assert conn.execute("SELECT COUNT(*) FROM gov_report").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM gov_metric").fetchone()[0] == 1
    row = conn.execute("SELECT * FROM gov_dataset").fetchone()
    assert row["source_sha256"] == "b" * 64 and row["event_id"] == 7 and row["delivery"] == "AS_IS"
    report_row = conn.execute("SELECT * FROM gov_report").fetchone()
    assert report_row["n_metrics"] == 1 and report_row["n_datasets"] == 1
    assert report_row["tags_json"] == '{"plugin":"ann"}'

    second = _report(
        metrics=[{"metric": "MAE", "value": 0.2, "split": "train", "horizon": 1}],
        received_at="2026-09-13T01:00:00.000000Z",
    )
    assert lake.write_metrics(second)["stored"]
    current = conn.execute("SELECT report_sha256, value FROM gov_metric_current").fetchall()
    assert len(current) == 1
    assert current[0]["report_sha256"] == second["report_sha256"]
    assert current[0]["value"] == 0.2
    conn.close()


def test_write_metrics_refuses_hash_mismatch(tmp_path):
    db = tmp_path / "cube.sqlite"
    lake = Plugin()
    lake.set_params(lake_id="olap_lab", sqlite_path=str(db))
    report = _report()
    report["metrics"][0]["value"] = 0.5  # body no longer matches its hash
    with pytest.raises(ValueError, match="report_sha256 mismatch"):
        lake.write_metrics(report)
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM gov_report").fetchone()[0] == 0
    conn.close()
