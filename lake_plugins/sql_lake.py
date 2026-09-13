"""SQLite OLAP lake. SELECT only. Holdout enforced on result timestamps."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import date
from pathlib import Path


_SELECT = re.compile(r"^\s*select\b", re.I)
_MULTI = re.compile(r";")


class Plugin:
    plugin_params = {
        "lake_id": "olap_lab",
        "title": "OLAP lab",
        "description": "SQL lake",
        "kind": "sql_olap",
        "sqlite_path": "./olap.sqlite",
        "time_column": "ts",
        "holdout_start": None,
    }

    def __init__(self):
        self.params = dict(self.plugin_params)

    def set_params(self, **kwargs):
        self.params.update(kwargs)

    def _connect(self):
        path = Path(self.params.get("sqlite_path") or "./olap.sqlite")
        if not path.exists():
            raise FileNotFoundError(str(path))
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        return conn

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
