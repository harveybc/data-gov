"""Directory lake with glob inventory and time-range reads."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


class HoldoutError(ValueError):
    pass


class Plugin:
    plugin_params = {
        "lake_id": "financial_files",
        "title": "Financial files",
        "description": "File lake",
        "kind": "files_inventory",
        "root_path": ".",
        "include_globs": ["**/*.csv", "**/*.parquet"],
        "time_column": None,
        "holdout_start": None,
    }

    def __init__(self):
        self.params = dict(self.plugin_params)

    def set_params(self, **kwargs):
        self.params.update(kwargs)

    def _root(self) -> Path:
        return Path(self.params.get("root_path") or ".").resolve()

    def discover(self):
        root = self._root()
        items = []
        globs = self.params.get("include_globs") or ["**/*"]
        seen = set()
        for pattern in globs:
            for path in root.glob(pattern):
                if not path.is_file():
                    continue
                rel = path.relative_to(root).as_posix()
                if rel in seen:
                    continue
                seen.add(rel)
                items.append(
                    {
                        "resource_id": rel,
                        "bytes": path.stat().st_size,
                        "sha256_prefix": hashlib.sha256(path.read_bytes()).hexdigest()[:16],
                        "kind": "file",
                    }
                )
        return sorted(items, key=lambda row: row["resource_id"])

    def list_resources(self):
        return self.discover()

    def _frame(self, resource_id: str):
        path = self._root() / resource_id
        if not path.is_file():
            raise FileNotFoundError(resource_id)
        import pandas as pd

        if path.suffix.lower() == ".parquet":
            return pd.read_parquet(path)
        return pd.read_csv(path)

    def _time_col(self, frame):
        named = self.params.get("time_column")
        if named and named in frame.columns:
            return named
        for col in frame.columns:
            if "time" in col.lower() or "date" in col.lower():
                return col
        return None

    def coverage(self, resource_id: str):
        frame = self._frame(resource_id)
        col = self._time_col(frame)
        if col is None:
            return {"resource_id": resource_id, "rows": int(len(frame)), "t_min": None, "t_max": None}
        series = frame[col]
        return {
            "resource_id": resource_id,
            "rows": int(len(frame)),
            "t_min": str(series.min()),
            "t_max": str(series.max()),
            "time_column": col,
        }

    def read(self, resource_id: str, start=None, end=None):
        frame = self._frame(resource_id)
        col = self._time_col(frame)
        if col and (start or end):
            import pandas as pd

            times = pd.to_datetime(frame[col], utc=True)
            mask = pd.Series(True, index=frame.index)
            if start:
                mask &= times >= pd.Timestamp(start, tz="UTC")
            if end:
                mask &= times <= pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
            frame = frame.loc[mask]
        payload = frame.to_dict(orient="records")
        canonical = json.dumps(payload, default=str, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        return {
            "resource_id": resource_id,
            "rows": payload,
            "sha256": digest,
            "bytes": len(canonical.encode()),
        }

    def storage(self):
        import shutil

        root = self._root()
        root.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(root)
        lake_bytes = sum(p.stat().st_size for p in root.glob("*") if p.is_file())
        # do not walk the whole financial-data tree for host card
        return {
            "root": str(root),
            "host_total": usage.total,
            "host_used": usage.used,
            "host_free": usage.free,
            "lake_bytes": lake_bytes,
        }

    def describe(self):
        return {
            "lake_id": self.params.get("lake_id"),
            "title": self.params.get("title"),
            "description": self.params.get("description"),
            "kind": self.params.get("kind"),
            "root_path": str(self._root()),
        }
