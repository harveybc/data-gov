"""SQLite lake. `query` is SELECT only with holdout on result timestamps; `write_metrics` is
append-only on the gov_* tables (04_FLOW_V2 §3). The two never share a code path."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from app.report import canonical_body, canonical_json, report_sha256


_SELECT = re.compile(r"^\s*select\b", re.I)
_MULTI = re.compile(r";")

# additive only, safe to run at every start; never inside the report transaction
DDL = (
    """
    CREATE TABLE IF NOT EXISTS gov_report (
        report_sha256 TEXT PRIMARY KEY,
        experiment_key TEXT NOT NULL,
        experiment_set_key TEXT,
        actor TEXT NOT NULL,
        lake_id TEXT NOT NULL,
        received_at TEXT NOT NULL,
        lineage TEXT NOT NULL,
        config_sha256 TEXT,
        code_commit TEXT,
        project TEXT,
        phase TEXT,
        tags_json TEXT,
        n_metrics INTEGER NOT NULL,
        n_datasets INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS gov_metric (
        report_sha256 TEXT NOT NULL,
        experiment_key TEXT NOT NULL,
        metric TEXT NOT NULL,
        value DOUBLE PRECISION,
        split TEXT,
        horizon INTEGER,
        std_dev DOUBLE PRECISION,
        min_value DOUBLE PRECISION,
        max_value DOUBLE PRECISION,
        unit TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS gov_dataset (
        report_sha256 TEXT NOT NULL,
        experiment_key TEXT NOT NULL,
        lake_id TEXT NOT NULL,
        resource_id TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        role TEXT,
        lineage TEXT NOT NULL,
        reason TEXT,
        event_id INTEGER,
        source_sha256 TEXT,
        range_from TEXT,
        range_to TEXT,
        delivery TEXT,
        time_column TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_gov_metric_report ON gov_metric(report_sha256)",
    "CREATE INDEX IF NOT EXISTS ix_gov_dataset_report ON gov_dataset(report_sha256)",
    "CREATE INDEX IF NOT EXISTS ix_gov_dataset_sha256 ON gov_dataset(sha256)",
    "CREATE INDEX IF NOT EXISTS ix_gov_report_experiment ON gov_report(experiment_key, received_at)",
    """
    CREATE VIEW IF NOT EXISTS gov_metric_current AS
    SELECT m.*
    FROM gov_metric m
    JOIN gov_report r ON r.report_sha256 = m.report_sha256
    WHERE r.received_at = (
        SELECT MAX(r2.received_at) FROM gov_report r2
        WHERE r2.experiment_key = r.experiment_key AND r2.lake_id = r.lake_id
    )
    """,
)


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class Plugin:
    plugin_params = {
        "lake_id": "olap_lab",
        "title": "OLAP lab",
        "description": "SQL lake",
        "kind": "sql_olap",
        "sqlite_path": None,
        "time_column": "ts",
        "holdout_start": None,
    }

    def __init__(self):
        self.params = dict(self.plugin_params)

    def set_params(self, **kwargs):
        self.params.update(kwargs)
        if self.params.get("sqlite_path"):
            self._ensure_schema()

    def _sqlite_path(self) -> Path:
        value = self.params.get("sqlite_path")
        if not value:
            raise FileNotFoundError("sqlite_path not set")
        return Path(value)

    def _connect(self, create=False):
        path = self._sqlite_path()
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
        elif not path.exists():
            raise FileNotFoundError(str(path))
        conn = sqlite3.connect(str(path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self):
        conn = self._connect(create=True)
        conn.isolation_level = None
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            for statement in DDL:
                conn.execute(statement)
        finally:
            conn.close()

    def discover(self):
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            return [{"resource_id": row["name"], "kind": "table"} for row in rows]
        finally:
            conn.close()

    def list_resources(self):
        return self.discover()

    def _guard_sql(self, sql: str) -> str:
        text = (sql or "").strip()
        if not _SELECT.match(text):
            raise ValueError("only SELECT is allowed")
        if _MULTI.search(text.rstrip().rstrip(";")):
            raise ValueError("multiple statements are not allowed")
        return text.rstrip().rstrip(";")

    def query(self, sql: str):
        text = self._guard_sql(sql)
        conn = self._connect()
        try:
            cur = conn.execute(text)
            rows = [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()
        holdout = self.params.get("holdout_start")
        time_col = self.params.get("time_column")
        if holdout and time_col:
            limit = date.fromisoformat(str(holdout)[:10])
            for row in rows:
                if time_col in row and row[time_col]:
                    day = date.fromisoformat(str(row[time_col])[:10])
                    if day >= limit:
                        raise PermissionError("holdout")
        canonical = json.dumps(rows, default=str, sort_keys=True, separators=(",", ":"))
        return {
            "rows": rows,
            "sha256": hashlib.sha256(canonical.encode()).hexdigest(),
            "bytes": len(canonical.encode()),
        }

    def write_metrics(self, report: dict):
        digest = report_sha256(report)
        if report.get("report_sha256") != digest:
            raise ValueError("report_sha256 mismatch")
        body = canonical_body(report)
        lineage = report.get("lineage") or "UNVERIFIED"
        received_at = report.get("received_at") or _utc_now()
        conn = self._connect(create=True)
        try:
            with conn:
                cur = conn.execute(
                    """
                    INSERT INTO gov_report (
                        report_sha256, experiment_key, experiment_set_key, actor, lake_id,
                        received_at, lineage, config_sha256, code_commit, project, phase,
                        tags_json, n_metrics, n_datasets
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (report_sha256) DO NOTHING
                    """,
                    (
                        digest,
                        body["experiment_key"],
                        body["experiment_set_key"],
                        body["actor"],
                        body["lake"],
                        received_at,
                        lineage,
                        body["config_sha256"],
                        body["code_commit"],
                        body["project"],
                        body["phase"],
                        canonical_json(body["tags"]),
                        len(body["metrics"]),
                        len(body["datasets"]),
                    ),
                )
                if cur.rowcount == 0:
                    row = conn.execute(
                        "SELECT lineage FROM gov_report WHERE report_sha256 = ?", (digest,)
                    ).fetchone()
                    return {
                        "stored": False,
                        "already_stored": True,
                        "lineage": row["lineage"] if row else lineage,
                    }
                conn.executemany(
                    """
                    INSERT INTO gov_metric (
                        report_sha256, experiment_key, metric, value, split, horizon,
                        std_dev, min_value, max_value, unit
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            digest,
                            body["experiment_key"],
                            m["metric"],
                            m["value"],
                            m["split"],
                            m["horizon"],
                            m["std_dev"],
                            m["min_value"],
                            m["max_value"],
                            m["unit"],
                        )
                        for m in body["metrics"]
                    ],
                )
                conn.executemany(
                    """
                    INSERT INTO gov_dataset (
                        report_sha256, experiment_key, lake_id, resource_id, sha256, role,
                        lineage, reason, event_id, source_sha256, range_from, range_to,
                        delivery, time_column
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            digest,
                            body["experiment_key"],
                            d["lake"],
                            d["resource"],
                            d["sha256"],
                            d.get("role"),
                            d.get("lineage") or "UNVERIFIED",
                            d.get("reason"),
                            d.get("event_id"),
                            d.get("source_sha256"),
                            d.get("range_from"),
                            d.get("range_to"),
                            d.get("delivery"),
                            d.get("time_column"),
                        )
                        for d in report.get("datasets") or []
                    ],
                )
        finally:
            conn.close()
        return {"stored": True, "already_stored": False, "lineage": lineage}

    def storage(self):
        import shutil

        path = Path(self.params.get("sqlite_path") or ".").resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(path.parent)
        size = path.stat().st_size if path.exists() else 0
        return {
            "root": str(path),
            "host_total": usage.total,
            "host_used": usage.used,
            "host_free": usage.free,
            "lake_bytes": size,
        }

    def describe(self):
        return {
            "lake_id": self.params.get("lake_id"),
            "title": self.params.get("title"),
            "description": self.params.get("description"),
            "kind": self.params.get("kind"),
            "root_path": str(Path(self.params.get("sqlite_path") or ".").resolve()),
        }
