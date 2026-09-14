"""Append-only SQLite accounting for auth and lake operations."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

COLUMNS = (
    "id, ts, actor, lake_id, verb, resource_id, decision, bytes, sha256, "
    "experiment_key, warning, detail"
)


def _utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Plugin:
    plugin_params = {
        "accounting_db": "./data_gov_accounting.db",
    }

    def __init__(self):
        self.params = dict(self.plugin_params)
        self._conn = None
        # one shared connection, one lock: sqlite3 connections are not thread-safe
        self._lock = threading.Lock()

    def set_params(self, **kwargs):
        self.params.update(kwargs)
        self._conn = None

    def _db(self):
        if self._conn is None:
            path = Path(self.params.get("accounting_db") or "./data_gov_accounting.db")
            path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
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
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_events_sha256 ON events(sha256)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_events_experiment ON events(experiment_key)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_events_lake_resource ON events(lake_id, resource_id)"
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS governed_campaigns (
                    campaign_sha256 TEXT PRIMARY KEY,
                    campaign_key TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    classification TEXT NOT NULL,
                    terminal_lake TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(actor, campaign_key)
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS governed_deliveries (
                    delivery_id TEXT PRIMARY KEY,
                    campaign_sha256 TEXT NOT NULL,
                    unit_id TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    lake_id TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    range_from TEXT,
                    range_to TEXT,
                    sha256 TEXT NOT NULL,
                    bytes INTEGER NOT NULL,
                    source_sha256 TEXT,
                    delivery_kind TEXT,
                    time_column TEXT,
                    availability_contract_sha256 TEXT,
                    state TEXT NOT NULL,
                    cached INTEGER,
                    created_at TEXT NOT NULL,
                    verified_at TEXT
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_governed_delivery_campaign "
                "ON governed_deliveries(campaign_sha256, unit_id)"
            )
            delivery_columns = {
                row[1] for row in self._conn.execute("PRAGMA table_info(governed_deliveries)")
            }
            if "availability_contract_sha256" not in delivery_columns:
                self._conn.execute(
                    "ALTER TABLE governed_deliveries "
                    "ADD COLUMN availability_contract_sha256 TEXT"
                )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS governed_terminals (
                    terminal_sha256 TEXT PRIMARY KEY,
                    campaign_sha256 TEXT NOT NULL,
                    unit_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    terminal_lake TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(campaign_sha256, unit_id, generation)
                )
                """
            )
            self._conn.commit()
        return self._conn

    def _rows(self, sql, params=()):
        with self._lock:
            rows = self._db().execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def _one(self, sql, params=()):
        with self._lock:
            row = self._db().execute(sql, params).fetchone()
        return dict(row) if row else None

    def record(self, **fields):
        with self._lock:
            conn = self._db()
            cur = conn.execute(
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
            return cur.lastrowid

    def seed_demo_if_empty(self, lake_ids):
        n = self._one("SELECT COUNT(*) AS n FROM events")["n"]
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
        return self._rows(
            """
            SELECT ts, actor, lake_id, verb, warning
            FROM events
            WHERE warning IS NOT NULL AND warning != ''
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )

    def logs(self, lake_id, limit=200):
        return self._rows(
            f"""
            SELECT {COLUMNS}
            FROM events
            WHERE lake_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (lake_id, limit),
        )

    def stats_by_verb(self, lake_id):
        return self._rows(
            """
            SELECT verb, COUNT(*) AS n, COALESCE(SUM(bytes), 0) AS bytes
            FROM events WHERE lake_id = ?
            GROUP BY verb ORDER BY n DESC
            """,
            (lake_id,),
        )

    def stats_by_actor(self, lake_id):
        return self._rows(
            """
            SELECT COALESCE(actor, 'unknown') AS actor, COUNT(*) AS n
            FROM events WHERE lake_id = ?
            GROUP BY actor ORDER BY n DESC
            """,
            (lake_id,),
        )

    def request_count(self, lake_id):
        row = self._one(
            "SELECT COUNT(*) AS n FROM events WHERE lake_id = ?",
            (lake_id,),
        )
        return int(row["n"] if row else 0)

    def logs_all(self, limit=500):
        return self._rows(
            f"""
            SELECT {COLUMNS}
            FROM events
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )

    def usage(self, experiment_key, limit=1000, before_id=None):
        """Exactly WHERE experiment_key = ?; newest first, paged with before_id."""
        sql = f"SELECT {COLUMNS} FROM events WHERE experiment_key = ?"
        params = [experiment_key]
        if before_id is not None:
            sql += " AND id < ?"
            params.append(int(before_id))
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        return self._rows(sql, tuple(params))

    def usage_by_sha256(self, sha256, limit=1000, before_id=None):
        """Allow downloads of exactly those bytes, newest first."""
        sql = (
            f"SELECT {COLUMNS} FROM events "
            "WHERE sha256 = ? AND verb = 'download' AND decision = 'allow'"
        )
        params = [sha256]
        if before_id is not None:
            sql += " AND id < ?"
            params.append(int(before_id))
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        return self._rows(sql, tuple(params))

    def last_download(self, lake_id, resource_id):
        """The most recent allow download of (lake, resource), or None."""
        return self._one(
            f"""
            SELECT {COLUMNS} FROM events
            WHERE lake_id = ? AND resource_id = ? AND verb = 'download' AND decision = 'allow'
            ORDER BY id DESC LIMIT 1
            """,
            (lake_id, resource_id),
        )

    def find_download(self, lake_id, resource_id, sha256, actor, keys):
        """The allow download row (with its id) that served these bytes to this actor under one
        of `keys`, or None."""
        keys = [k for k in (keys or []) if k]
        if not keys:
            return None
        marks = ",".join("?" for _ in keys)
        return self._one(
            f"""
            SELECT {COLUMNS} FROM events
            WHERE sha256 = ? AND verb = 'download' AND decision = 'allow'
              AND lake_id = ? AND resource_id = ? AND actor = ?
              AND experiment_key IN ({marks})
            ORDER BY id DESC LIMIT 1
            """,
            (sha256, lake_id, resource_id, actor, *keys),
        )

    # Flow v3 ---------------------------------------------------------------

    def register_campaign(self, campaign, canonical_json):
        with self._lock:
            conn = self._db()
            existing = conn.execute(
                "SELECT campaign_sha256, body_json FROM governed_campaigns "
                "WHERE actor = ? AND campaign_key = ?",
                (campaign["actor"], campaign["campaign_key"]),
            ).fetchone()
            if existing:
                if existing["campaign_sha256"] != campaign["campaign_sha256"]:
                    raise ValueError("campaign_key already registered with different content")
                return False
            conn.execute(
                """
                INSERT INTO governed_campaigns (
                    campaign_sha256, campaign_key, actor, classification,
                    terminal_lake, body_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    campaign["campaign_sha256"], campaign["campaign_key"], campaign["actor"],
                    campaign["classification"], campaign["terminal_lake"], canonical_json,
                    _utc(),
                ),
            )
            conn.commit()
            return True

    def campaign(self, campaign_sha256):
        row = self._one(
            "SELECT body_json FROM governed_campaigns WHERE campaign_sha256 = ?",
            (campaign_sha256,),
        )
        if not row:
            return None
        return json.loads(row["body_json"])

    def create_delivery(self, *, campaign_sha256, unit_id, actor, lake_id, resource_id,
                        role, range_from, range_to, sha256, bytes_count,
                        source_sha256, delivery_kind, time_column,
                        availability_contract_sha256):
        if not isinstance(unit_id, str) or not unit_id:
            raise ValueError("governed delivery requires unit_id")
        delivery_id = uuid.uuid4().hex
        with self._lock:
            conn = self._db()
            conn.execute(
                """
                INSERT INTO governed_deliveries (
                    delivery_id, campaign_sha256, unit_id, actor, lake_id, resource_id,
                    role, range_from, range_to, sha256, bytes, source_sha256,
                    delivery_kind, time_column, availability_contract_sha256,
                    state, cached, created_at, verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'AUTHORIZED', NULL, ?, NULL)
                """,
                (
                    delivery_id, campaign_sha256, unit_id, actor, lake_id, resource_id,
                    role, range_from, range_to, sha256, int(bytes_count), source_sha256,
                    delivery_kind, time_column, availability_contract_sha256, _utc(),
                ),
            )
            conn.commit()
        return delivery_id

    def delivery(self, delivery_id):
        return self._one(
            "SELECT * FROM governed_deliveries WHERE delivery_id = ?", (delivery_id,)
        )

    def confirm_delivery(self, delivery_id, *, campaign_sha256, actor, sha256,
                         bytes_count, cached):
        with self._lock:
            conn = self._db()
            row = conn.execute(
                "SELECT * FROM governed_deliveries WHERE delivery_id = ?", (delivery_id,)
            ).fetchone()
            if not row:
                raise LookupError("unknown delivery")
            if row["campaign_sha256"] != campaign_sha256 or row["actor"] != actor:
                raise PermissionError("delivery does not belong to campaign")
            if row["sha256"] != sha256 or int(row["bytes"]) != int(bytes_count):
                raise RuntimeError("delivery confirmation mismatch")
            state = "VERIFIED_CACHE" if cached else "VERIFIED_TRANSFER"
            if row["state"] in ("VERIFIED_CACHE", "VERIFIED_TRANSFER"):
                if row["state"] != state:
                    raise RuntimeError("delivery confirmation conflict")
                return state, False
            conn.execute(
                "UPDATE governed_deliveries SET state = ?, cached = ?, verified_at = ? "
                "WHERE delivery_id = ? AND state = 'AUTHORIZED'",
                (state, int(cached), _utc(), delivery_id),
            )
            conn.commit()
            return state, True

    def verified_deliveries(self, delivery_ids, *, campaign_sha256, actor, unit_id):
        if not delivery_ids:
            return []
        marks = ",".join("?" for _ in delivery_ids)
        rows = self._rows(
            f"SELECT * FROM governed_deliveries WHERE delivery_id IN ({marks})",
            tuple(delivery_ids),
        )
        if len(rows) != len(delivery_ids):
            raise ValueError("unknown delivery")
        by_id = {row["delivery_id"]: row for row in rows}
        ordered = []
        for delivery_id in delivery_ids:
            row = by_id[delivery_id]
            if row["campaign_sha256"] != campaign_sha256 or row["actor"] != actor:
                raise PermissionError("delivery does not belong to campaign")
            if row["unit_id"] != unit_id:
                raise PermissionError("delivery does not belong to unit")
            if row["state"] not in ("VERIFIED_CACHE", "VERIFIED_TRANSFER"):
                raise ValueError("delivery is not verified")
            ordered.append(row)
        return ordered

    def terminal_slot(self, campaign_sha256, unit_id, generation):
        return self._one(
            "SELECT * FROM governed_terminals WHERE campaign_sha256 = ? "
            "AND unit_id = ? AND generation = ?",
            (campaign_sha256, unit_id, int(generation)),
        )

    def record_terminal(self, terminal, canonical_json):
        existing = self.terminal_slot(
            terminal["campaign_sha256"], terminal["unit_id"], terminal["generation"]
        )
        if existing:
            if existing["terminal_sha256"] != terminal["terminal_sha256"]:
                raise RuntimeError("terminal generation conflict")
            return False
        with self._lock:
            conn = self._db()
            try:
                conn.execute(
                    """
                    INSERT INTO governed_terminals (
                        terminal_sha256, campaign_sha256, unit_id, generation,
                        status, terminal_lake, body_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        terminal["terminal_sha256"], terminal["campaign_sha256"],
                        terminal["unit_id"], terminal["generation"], terminal["status"],
                        terminal["terminal_lake"], canonical_json, _utc(),
                    ),
                )
            except sqlite3.IntegrityError:
                conn.rollback()
                existing = conn.execute(
                    "SELECT terminal_sha256 FROM governed_terminals WHERE campaign_sha256 = ? "
                    "AND unit_id = ? AND generation = ?",
                    (terminal["campaign_sha256"], terminal["unit_id"], terminal["generation"]),
                ).fetchone()
                if existing and existing["terminal_sha256"] == terminal["terminal_sha256"]:
                    return False
                raise RuntimeError("terminal generation conflict") from None
            conn.commit()
            return True

    def terminal_digests(self, campaign_sha256):
        rows = self._rows(
            "SELECT terminal_sha256, unit_id, generation FROM governed_terminals "
            "WHERE campaign_sha256 = ? ORDER BY unit_id, generation",
            (campaign_sha256,),
        )
        return rows
