"""Append-only SQLite accounting for auth and lake operations."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Plugin:
    plugin_params = {
        "accounting_db": "./data_gov_accounting.db",
    }

    def __init__(self):
        self.params = dict(self.plugin_params)
        self._conn = None

    def set_params(self, **kwargs):
        self.params.update(kwargs)
        self._conn = None

    def _db(self):
        if self._conn is None:
            path = Path(self.params.get("accounting_db") or "./data_gov_accounting.db")
            path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    actor TEXT,
                    lake_id TEXT,
                    verb TEXT NOT NULL,
                    resource_id TEXT,
                    decision TEXT,
                    bytes INTEGER,
                    sha256 TEXT,
                    experiment_key TEXT,
                    warning TEXT,
                    detail TEXT
                )
                """
            )
            self._conn.commit()
        return self._conn

    def record(self, **fields):
        conn = self._db()
        conn.execute(
            """
            INSERT INTO events (
                ts, actor, lake_id, verb, resource_id, decision,
                bytes, sha256, experiment_key, warning, detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fields.get("ts") or _utc(),
                fields.get("actor"),
                fields.get("lake_id"),
                fields.get("verb") or "unknown",
                fields.get("resource_id"),
                fields.get("decision"),
                fields.get("bytes"),
                fields.get("sha256"),
                fields.get("experiment_key"),
                fields.get("warning"),
                fields.get("detail"),
            ),
        )
        conn.commit()

    def seed_demo_if_empty(self, lake_ids):
        conn = self._db()
        n = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        if n:
            return
        samples = [
            ("demo", "financial_files", "read_range", "eth_usdt_1h", "allow", 1_048_576, None),
            ("demo", "financial_files", "discover", None, "allow", 0, None),
            ("predictor", "financial_files", "read_range", "eth_usdt_1h", "allow", 2_097_152, None),
            ("demo", "olap_lab", "query", "fact_performance", "allow", 8192, None),
            ("unknown", "financial_files", "read_range", "holdout_d6", "deny", 0, "resource not in inventory"),
            ("demo", "olap_lab", "query", "fact_performance", "allow", 4096, "slow query"),
        ]
        if lake_ids:
            samples = [row for row in samples if row[1] in lake_ids] or samples
        for actor, lake, verb, resource, decision, nbytes, warning in samples:
            self.record(
                actor=actor,
                lake_id=lake,
                verb=verb,
                resource_id=resource,
                decision=decision,
                bytes=nbytes,
                warning=warning,
                experiment_key="skeleton-demo",
            )

    def warnings(self, limit=8):
        rows = self._db().execute(
            """
            SELECT ts, actor, lake_id, verb, warning
            FROM events
            WHERE warning IS NOT NULL AND warning != ''
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def logs(self, lake_id, limit=200):
        rows = self._db().execute(
            """
            SELECT ts, actor, verb, resource_id, decision, bytes, sha256,
                   experiment_key, warning, detail
            FROM events
            WHERE lake_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (lake_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def stats_by_verb(self, lake_id):
        rows = self._db().execute(
            """
            SELECT verb, COUNT(*) AS n, COALESCE(SUM(bytes), 0) AS bytes
            FROM events WHERE lake_id = ?
            GROUP BY verb ORDER BY n DESC
            """,
            (lake_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def stats_by_actor(self, lake_id):
        rows = self._db().execute(
            """
            SELECT COALESCE(actor, 'unknown') AS actor, COUNT(*) AS n
            FROM events WHERE lake_id = ?
            GROUP BY actor ORDER BY n DESC
            """,
            (lake_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def request_count(self, lake_id):
        row = self._db().execute(
            "SELECT COUNT(*) AS n FROM events WHERE lake_id = ?",
            (lake_id,),
        ).fetchone()
        return int(row["n"] if row else 0)
