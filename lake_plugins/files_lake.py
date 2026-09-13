"""Directory lake: stat-only inventory, time-column coverage, AS_IS / CUT downloads (04_FLOW_V2 §2)."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import threading
import uuid
from pathlib import Path

from lake_plugins.errors import UnsupportedError

CHUNK = 1024 * 1024
CSV_ROWS_PER_CHUNK = 65536
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_NAMES = {"ts", "time", "date", "datetime", "timestamp"}
# pinned so a cut's bytes never depend on a later pyarrow default
PARQUET_WRITER = {
    "compression": "snappy",
    "version": "2.6",
    "use_dictionary": True,
    "write_statistics": True,
}


class HoldoutError(ValueError):
    pass


def sha256_file(path, chunk=CHUNK) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def parse_day(text):
    """A calendar day (YYYY-MM-DD) as a naive midnight Timestamp, else ValueError('invalid from/to')."""
    import pandas as pd

    if not isinstance(text, str) or not DAY_RE.match(text):
        raise ValueError("invalid from/to")
    try:
        return pd.Timestamp(_dt.date.fromisoformat(text))
    except ValueError as exc:
        raise ValueError("invalid from/to") from exc


class Plugin:
    plugin_params = {
        "lake_id": "financial_files",
        "title": "Financial files",
        "description": "File lake",
        "kind": "files_inventory",
        "root_path": ".",
        "include_globs": ["**/*.csv", "**/*.parquet"],
        "time_column": None,
        "time_columns": {},
        "time_unit": None,
        "untimed": [],
        "holdout_start": None,
        "spool_dir": "./var/spool",
        "cuts_dir": "./var/cuts",
        "max_downloads": 2,
    }

    def __init__(self):
        self.params = dict(self.plugin_params)
        self._memo = None
        self._memo_lock = threading.Lock()

    def set_params(self, **kwargs):
        self.params.update(kwargs)
        self._memo = None

    # paths -------------------------------------------------------------

    def _root(self) -> Path:
        return Path(self.params.get("root_path") or ".").resolve()

    def _cuts_dir(self) -> Path:
        return Path(self.params.get("cuts_dir") or "./var/cuts").resolve()

    def _memo_path(self) -> Path:
        return self._cuts_dir().parent / "source_sha256.json"

    def _path(self, resource_id: str) -> Path:
        path = self._root() / resource_id
        if not path.is_file():
            raise FileNotFoundError(resource_id)
        return path

    # inventory ---------------------------------------------------------

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
                    {"resource_id": rel, "bytes": path.stat().st_size, "kind": "file"}
                )
        return sorted(items, key=lambda row: row["resource_id"])

    def list_resources(self):
        return self.discover()

    # time column -------------------------------------------------------

    def _columns(self, path: Path):
        suffix = path.suffix.lower()
        if suffix == ".parquet":
            import pyarrow.parquet as pq

            return list(pq.read_schema(path).names)
        if suffix == ".csv":
            import pandas as pd

            return [str(c) for c in pd.read_csv(path, nrows=0).columns]
        raise UnsupportedError("unsupported file type")

    def _time_col(self, columns, resource_id):
        named = (self.params.get("time_columns") or {}).get(resource_id)
        if named and named in columns:
            return named
        named = self.params.get("time_column")
        if named and named in columns:
            return named
        for col in columns:
            low = col.lower()
            if low in TIME_NAMES or "time" in low or "date" in low:
                return col
        return None

    def _wall_clock(self, series):
        """Naive timestamps on the column's own wall clock; zones are dropped, not converted."""
        import pandas as pd
        from pandas.api import types as ptypes

        try:
            if ptypes.is_datetime64_any_dtype(series):
                out = series
            elif ptypes.is_numeric_dtype(series) and not ptypes.is_bool_dtype(series):
                unit = self.params.get("time_unit")
                if not unit:
                    raise UnsupportedError("unparseable time column")
                out = pd.to_datetime(series, unit=unit)
            else:
                out = pd.to_datetime(series)
        except (ValueError, TypeError, OverflowError) as exc:
            raise UnsupportedError("unparseable time column") from exc
        if not ptypes.is_datetime64_any_dtype(out):
            raise UnsupportedError("unparseable time column")
        if out.dt.tz is not None:
            out = out.dt.tz_localize(None)
        if out.isna().any():
            raise UnsupportedError("unparseable time column")
        return out

    def _times(self, path: Path, col: str):
        """Yield the wall-clock time column in bounded pieces (row groups / CSV chunks)."""
        if path.suffix.lower() == ".parquet":
            import pyarrow.parquet as pq

            pf = pq.ParquetFile(path)
            for i in range(pf.metadata.num_row_groups):
                column = pf.read_row_group(i, columns=[col]).column(0).to_pandas()
                yield self._wall_clock(column)
            return
        import pandas as pd

        for chunk in pd.read_csv(path, usecols=[col], chunksize=CSV_ROWS_PER_CHUNK):
            yield self._wall_clock(chunk[col])

    def _row_count(self, path: Path, columns) -> int:
        if path.suffix.lower() == ".parquet":
            import pyarrow.parquet as pq

            return int(pq.ParquetFile(path).metadata.num_rows)
        import pandas as pd

        if not columns:
            return 0
        return sum(
            len(chunk)
            for chunk in pd.read_csv(
                path, usecols=[columns[0]], chunksize=CSV_ROWS_PER_CHUNK
            )
        )

    def _holdout(self):
        value = self.params.get("holdout_start")
        if not value:
            return None
        import pandas as pd

        return pd.Timestamp(str(value)[:10])

    def _t_max(self, path: Path, col: str):
        t_max = None
        for times in self._times(path, col):
            if len(times):
                m = times.max()
                t_max = m if t_max is None or m > t_max else t_max
        return t_max

    @staticmethod
    def _assert_holdout(kept_max, holdout):
        if holdout is not None and kept_max is not None and not kept_max < holdout:
            raise PermissionError("holdout")

    def coverage(self, resource_id: str):
        path = self._path(resource_id)
        columns = self._columns(path)
        col = self._time_col(columns, resource_id)
        if col is None:
            return {
                "resource_id": resource_id,
                "rows": self._row_count(path, columns),
                "t_min": None,
                "t_max": None,
                "time_column": None,
            }
        rows = 0
        t_min = t_max = None
        for times in self._times(path, col):
            rows += len(times)
            if not len(times):
                continue
            lo, hi = times.min(), times.max()
            t_min = lo if t_min is None or lo < t_min else t_min
            t_max = hi if t_max is None or hi > t_max else t_max
        return {
            "resource_id": resource_id,
            "rows": int(rows),
            "t_min": None if t_min is None else str(t_min),
            "t_max": None if t_max is None else str(t_max),
            "time_column": col,
        }

    # source hashes -----------------------------------------------------

    def _load_memo(self):
        if self._memo is None:
            try:
                self._memo = json.loads(self._memo_path().read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._memo = {}
            if not isinstance(self._memo, dict):
                self._memo = {}
        return self._memo

    def _save_memo(self, memo):
        path = self._memo_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(memo, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)

    def _file_sha256(self, path: Path) -> str:
        st = path.stat()
        key = str(path)
        with self._memo_lock:
            entry = self._load_memo().get(key)
            if (
                entry
                and entry.get("size") == st.st_size
                and entry.get("mtime_ns") == st.st_mtime_ns
            ):
                return entry["sha256"]
        digest = sha256_file(path)
        with self._memo_lock:
            memo = self._load_memo()
            memo[key] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": digest}
            self._save_memo(memo)
        return digest

    # download ----------------------------------------------------------

    def download(self, resource_id: str, start=None, end=None):
        import pandas as pd

        path = self._path(resource_id)
        holdout = self._holdout()
        untimed = resource_id in (self.params.get("untimed") or [])
        source_sha256 = self._file_sha256(path)
        if start is None and end is None:
            col = None
            if not untimed:
                try:
                    col = self._time_col(self._columns(path), resource_id)
                except UnsupportedError:
                    col = None
                if holdout is not None:
                    if col is None:
                        raise PermissionError("no time column under holdout")
                    t_max = self._t_max(path, col)
                    if t_max is not None and not t_max < holdout:
                        raise PermissionError("spans holdout: request a range")
            return self._result(path, path.name, source_sha256, "AS_IS", col)
        if start is None or end is None:
            raise ValueError("invalid from/to")
        lo = parse_day(start)
        last_day = parse_day(end)
        if last_day < lo:
            raise ValueError("invalid from/to")
        hi = last_day + pd.Timedelta(days=1)
        if holdout is not None and last_day >= holdout:
            raise PermissionError("holdout")
        col = self._time_col(self._columns(path), resource_id)
        if col is None:
            raise UnsupportedError("no time column")
        suffix = path.suffix.lower()
        target = self._cuts_dir() / source_sha256 / f"{start}_{end}{suffix}"
        filename = f"{path.stem}_{start}_{end}{path.suffix}"
        if target.is_file():
            return self._result(target, filename, source_sha256, "CUT", col)
        if suffix == ".parquet":
            written = self._cut_parquet(path, col, lo, hi, target, holdout)
        else:
            written = self._cut_csv(path, col, lo, hi, target, holdout)
        if not written:
            return self._result(path, path.name, source_sha256, "AS_IS", col)
        return self._result(target, filename, source_sha256, "CUT", col)

    def _result(self, path: Path, filename, source_sha256, delivery, col):
        digest = source_sha256 if delivery == "AS_IS" else self._file_sha256(path)
        return {
            "path": str(path),
            "filename": filename,
            "sha256": digest,
            "bytes": path.stat().st_size,
            "source_sha256": source_sha256,
            "delivery": delivery,
            "time_column": col or "",
        }

    @staticmethod
    def _tmp_for(target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        return target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")

    @staticmethod
    def _commit(tmp: Path, target: Path):
        # materialised once: a cut that appeared meanwhile has the same bytes and wins
        if target.exists():
            tmp.unlink(missing_ok=True)
        else:
            os.replace(tmp, target)

    def _cut_csv(self, path, col, lo, hi, target, holdout) -> bool:
        import numpy as np
        import pandas as pd

        masks = []
        kept_max = None
        for chunk in pd.read_csv(path, usecols=[col], chunksize=CSV_ROWS_PER_CHUNK):
            times = self._wall_clock(chunk[col])
            mask = (times >= lo) & (times < hi)
            masks.append(mask.to_numpy())
            if mask.any():
                m = times[mask].max()
                kept_max = m if kept_max is None or m > kept_max else kept_max
        keep = np.concatenate(masks) if masks else np.zeros(0, dtype=bool)
        if keep.all():
            return False
        self._assert_holdout(kept_max, holdout)
        tmp = self._tmp_for(target)
        index = 0
        try:
            with open(path, "rb") as src, open(tmp, "wb") as out:
                out.write(src.readline())
                for raw in src:
                    if not raw.strip():
                        continue
                    if index >= len(keep):
                        index += 1
                        break
                    if keep[index]:
                        out.write(raw)
                    index += 1
            if index != len(keep):
                raise UnsupportedError("unsupported csv")
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        self._commit(tmp, target)
        return True

    def _cut_parquet(self, path, col, lo, hi, target, holdout) -> bool:
        import pyarrow as pa
        import pyarrow.parquet as pq

        pf = pq.ParquetFile(path)
        n_groups = pf.metadata.num_row_groups
        masks = []
        kept_max = None
        removed = False
        for i in range(n_groups):
            times = self._wall_clock(pf.read_row_group(i, columns=[col]).column(0).to_pandas())
            mask = ((times >= lo) & (times < hi)).to_numpy()
            masks.append(mask)
            if not mask.all():
                removed = True
            if mask.any():
                m = times[mask].max()
                kept_max = m if kept_max is None or m > kept_max else kept_max
        if not removed:
            return False
        self._assert_holdout(kept_max, holdout)
        rg_size = max(
            (pf.metadata.row_group(i).num_rows for i in range(n_groups)), default=0
        ) or 1
        tmp = self._tmp_for(target)
        try:
            writer = pq.ParquetWriter(str(tmp), pf.schema_arrow, **PARQUET_WRITER)
            try:
                for i, mask in enumerate(masks):
                    if not mask.any():
                        continue
                    table = pf.read_row_group(i)
                    if not mask.all():
                        table = table.filter(pa.array(mask))
                    writer.write_table(table, row_group_size=rg_size)
            finally:
                writer.close()
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        self._commit(tmp, target)
        return True

    # small JSON slices ---------------------------------------------------

    def _frame(self, resource_id: str):
        path = self._path(resource_id)
        import pandas as pd

        if path.suffix.lower() == ".parquet":
            return pd.read_parquet(path)
        return pd.read_csv(path)

    def read(self, resource_id: str, start=None, end=None):
        import pandas as pd

        frame = self._frame(resource_id)
        col = self._time_col([str(c) for c in frame.columns], resource_id)
        holdout = self._holdout()
        if start or end:
            lo = parse_day(start) if start else None
            last_day = parse_day(end) if end else None
            if lo is not None and last_day is not None and last_day < lo:
                raise ValueError("invalid from/to")
            if holdout is not None and (
                (last_day is not None and last_day >= holdout)
                or (lo is not None and lo >= holdout)
            ):
                raise PermissionError("holdout")
            if col:
                times = self._wall_clock(frame[col])
                mask = pd.Series(True, index=frame.index)
                if lo is not None:
                    mask &= times >= lo
                if last_day is not None:
                    mask &= times < last_day + pd.Timedelta(days=1)
                frame = frame.loc[mask]
        elif holdout is not None and col:
            times = self._wall_clock(frame[col])
            if len(times) and not times.max() < holdout:
                raise PermissionError("spans holdout: request a range")
        payload = frame.to_dict(orient="records")
        canonical = json.dumps(payload, default=str, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        return {
            "resource_id": resource_id,
            "rows": payload,
            "sha256": digest,
            "bytes": len(canonical.encode()),
        }

    # cards -------------------------------------------------------------

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
