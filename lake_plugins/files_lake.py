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

from app.store_metadata import store_metadata
from lake_plugins.errors import UnsupportedError

import pandas as _pd

# pandas 3.0.3 + pyarrow 25 on this stack: the default pyarrow-backed string
# storage segfaults inside pandas' string_arrow._from_sequence on the second
# read_csv issued from a werkzeug worker thread (governed download after a
# streamed delivery; predictor evidence flow_v3_tools/p03_*). The same reads in
# plain threads or in the main thread never fault. Python storage is exact for
# the only string data these paths build (column labels, ISO timestamps) and
# removes the fault; it is set once at import, before any frame is built.
_pd.set_option("mode.string_storage", "python")

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
CUT_MATERIALIZER = "data-gov-causal-cut.v3"


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
        "kind": "lake",
        "root_path": ".",
        "include_globs": ["**/*.csv", "**/*.parquet"],
        "time_column": None,
        "time_columns": {},
        "time_unit": None,
        "resource_contracts": {},
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
        store_metadata(dict(self.params, **kwargs), adapter_kind="lake", adapter_engine="files_inventory")
        self.params.update(kwargs)
        self._memo = None

    # paths -------------------------------------------------------------

    def _root(self) -> Path:
        return Path(self.params.get("root_path") or ".").resolve()

    def _cuts_dir(self) -> Path:
        return Path(self.params.get("cuts_dir") or "./var/cuts").resolve()

    def _memo_path(self) -> Path:
        configured = self.params.get("source_hash_cache")
        return (Path(configured).resolve() if configured
                else self._cuts_dir().parent / "source_sha256.json")

    def _path(self, resource_id: str) -> Path:
        # A resource id is a relative path inside the lake: no absolute ids, no '..', no escape by symlink.
        rid = str(resource_id or "")
        if not rid or rid.startswith(("/", "\\")) or "\\" in rid or any(p in ("", ".", "..") for p in rid.split("/")):
            raise FileNotFoundError(rid)
        root = self._root().resolve()
        path = (root / rid).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise FileNotFoundError(rid)
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

    def _wall_clock(self, series, *, time_unit=None, timezone_mode=None):
        """Naive timestamps on the column's own wall clock; zones are dropped, not converted."""
        import pandas as pd
        from pandas.api import types as ptypes

        try:
            if ptypes.is_datetime64_any_dtype(series):
                out = series
            elif ptypes.is_numeric_dtype(series) and not ptypes.is_bool_dtype(series):
                unit = time_unit if time_unit is not None else self.params.get("time_unit")
                if not unit:
                    raise UnsupportedError("unparseable time column")
                out = pd.to_datetime(series, unit=unit)
            else:
                out = pd.to_datetime(series, utc=(timezone_mode == "UTC"))
        except (ValueError, TypeError, OverflowError) as exc:
            raise UnsupportedError("unparseable time column") from exc
        if not ptypes.is_datetime64_any_dtype(out):
            raise UnsupportedError("unparseable time column")
        if out.dt.tz is not None:
            out = (out.dt.tz_convert("UTC").dt.tz_localize(None)
                   if timezone_mode == "UTC" else out.dt.tz_localize(None))
        if out.isna().any():
            raise UnsupportedError("unparseable time column")
        return out

    def _times(self, path: Path, col: str):
        """Yield the wall-clock time column in bounded record batches / CSV chunks."""
        if path.suffix.lower() == ".parquet":
            import pyarrow.parquet as pq

            pf = pq.ParquetFile(path)
            for batch in pf.iter_batches(batch_size=CSV_ROWS_PER_CHUNK, columns=[col]):
                column = batch.column(0).to_pandas()
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
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        try:
            st = os.fstat(fd)
            path_st = path.stat(follow_symlinks=False)
            if (st.st_dev, st.st_ino) != (path_st.st_dev, path_st.st_ino):
                raise RuntimeError("source identity changed")
        except BaseException:
            os.close(fd)
            raise
        key = str(path)
        with self._memo_lock:
            entry = self._load_memo().get(key)
            if (
                entry
                and entry.get("dev") == st.st_dev
                and entry.get("ino") == st.st_ino
                and entry.get("size") == st.st_size
                and entry.get("mtime_ns") == st.st_mtime_ns
                and entry.get("ctime_ns") == st.st_ctime_ns
            ):
                os.close(fd)
                return entry["sha256"]
        digest_obj = hashlib.sha256()
        try:
            while True:
                block = os.read(fd, CHUNK)
                if not block:
                    break
                digest_obj.update(block)
            after = os.fstat(fd)
            path_after = path.stat(follow_symlinks=False)
        finally:
            os.close(fd)
        facts = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        after_facts = (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
        )
        if facts != after_facts or (after.st_dev, after.st_ino) != (
            path_after.st_dev, path_after.st_ino
        ):
            raise RuntimeError("source identity changed")
        digest = digest_obj.hexdigest()
        with self._memo_lock:
            memo = self._load_memo()
            memo[key] = {
                "dev": st.st_dev,
                "ino": st.st_ino,
                "size": st.st_size,
                "mtime_ns": st.st_mtime_ns,
                "ctime_ns": st.st_ctime_ns,
                "sha256": digest,
            }
            self._save_memo(memo)
        return digest

    def _resource_contract(self, resource_id):
        contracts = self.params.get("resource_contracts") or {}
        contract = contracts.get(resource_id)
        required = {
            "event_time_column", "available_time_column", "timezone", "time_unit", "frequency"
        }
        if not isinstance(contract, dict) or set(contract) != required:
            raise UnsupportedError("resource availability contract required")
        for key in ("event_time_column", "available_time_column", "timezone", "frequency"):
            if not isinstance(contract[key], str) or not contract[key]:
                raise UnsupportedError(f"invalid resource contract {key}")
        if contract["timezone"] not in {"UTC", "NAIVE_WALL_CLOCK"}:
            raise UnsupportedError("invalid resource contract timezone")
        if contract["time_unit"] is not None and contract["time_unit"] not in {
            "s", "ms", "us", "ns"
        }:
            raise UnsupportedError("invalid resource contract time_unit")
        return contract

    # download ----------------------------------------------------------

    def download(self, resource_id: str, start=None, end=None):
        return self._download(resource_id, start, end)

    def _download(self, resource_id: str, start=None, end=None, *, explicit_col=None,
                  time_unit=None, timezone_mode=None, contract_digest=None):
        import pandas as pd

        path = self._path(resource_id)
        holdout = self._holdout()
        untimed = resource_id in (self.params.get("untimed") or [])
        source_sha256 = self._file_sha256(path)
        if start is None and end is None:
            col = None
            if not untimed:
                try:
                    col = explicit_col or self._time_col(self._columns(path), resource_id)
                except UnsupportedError:
                    col = None
                if holdout is not None:
                    if col is None:
                        raise PermissionError("no time column under holdout")
                    t_max = self._t_max_contract(path, col, time_unit, timezone_mode)
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
        columns = self._columns(path)
        col = explicit_col or self._time_col(columns, resource_id)
        if explicit_col and explicit_col not in columns:
            raise UnsupportedError("available_time column missing")
        if col is None:
            raise UnsupportedError("no time column")
        suffix = path.suffix.lower()
        target_dir = self._cuts_dir() / source_sha256
        if contract_digest:
            target_dir = target_dir / contract_digest
        materializer_sha = hashlib.sha256(json.dumps({
            "schema": CUT_MATERIALIZER,
            "writer": PARQUET_WRITER if suffix == ".parquet" else "byte-line-subset.v1",
        }, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()
        target_dir = target_dir / materializer_sha
        target = target_dir / f"{start}_{end}{suffix}"
        filename = f"{path.stem}_{start}_{end}{path.suffix}"
        if target.is_file():
            return self._result(target, filename, source_sha256, "CUT", col)
        target_existed = target.exists()
        if suffix == ".parquet":
            written = self._cut_parquet(
                path, col, lo, hi, target, holdout, time_unit, timezone_mode
            )
        else:
            written = self._cut_csv(
                path, col, lo, hi, target, holdout, time_unit, timezone_mode
            )
        if self._file_sha256(path) != source_sha256:
            if not target_existed:
                target.unlink(missing_ok=True)
            raise RuntimeError("source changed while materialising cut")
        if not written:
            return self._result(path, path.name, source_sha256, "AS_IS", col)
        return self._result(target, filename, source_sha256, "CUT", col)

    def _t_max_contract(self, path, col, time_unit, timezone_mode):
        if time_unit is None and timezone_mode is None:
            return self._t_max(path, col)
        t_max = None
        import pandas as pd
        if path.suffix.lower() == ".parquet":
            import pyarrow.parquet as pq
            pf = pq.ParquetFile(path)
            chunks = (
                batch.column(0).to_pandas()
                for batch in pf.iter_batches(
                    batch_size=CSV_ROWS_PER_CHUNK, columns=[col]
                )
            )
        else:
            chunks = (chunk[col] for chunk in pd.read_csv(
                path, usecols=[col], chunksize=CSV_ROWS_PER_CHUNK
            ))
        for chunk in chunks:
            times = self._wall_clock(
                chunk, time_unit=time_unit, timezone_mode=timezone_mode
            )
            if len(times):
                value = times.max()
                t_max = value if t_max is None or value > t_max else t_max
        return t_max

    def governed_download(self, resource_id: str, start=None, end=None):
        """Return a retained descriptor for bytes validated by an availability contract."""
        contract = self._resource_contract(resource_id)
        contract_sha256 = hashlib.sha256(
            json.dumps(contract, sort_keys=True, separators=(",", ":")).encode("ascii")
        ).hexdigest()
        info = self._download(
            resource_id, start, end,
            explicit_col=contract["available_time_column"],
            time_unit=contract["time_unit"], timezone_mode=contract["timezone"],
            contract_digest=contract_sha256,
        )
        path = Path(info["path"])
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            before = os.fstat(fd)
            named = path.stat(follow_symlinks=False)
            if (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino):
                raise RuntimeError("delivery identity changed")
            digest = hashlib.sha256()
            size = 0
            while True:
                block = os.read(fd, CHUNK)
                if not block:
                    break
                digest.update(block)
                size += len(block)
            after = os.fstat(fd)
            named_after = path.stat(follow_symlinks=False)
            if (
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or (after.st_dev, after.st_ino) != (named_after.st_dev, named_after.st_ino)
            ):
                raise RuntimeError("delivery identity changed")
            actual = digest.hexdigest()
            if actual != info["sha256"] or size != info["bytes"]:
                raise RuntimeError("delivery digest changed")
            os.lseek(fd, 0, os.SEEK_SET)
            info = dict(
                info, sha256=actual, bytes=size, handle=os.fdopen(fd, "rb"),
                availability_contract_sha256=contract_sha256,
            )
            fd = None
            return info
        finally:
            if fd is not None:
                os.close(fd)

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
        """Publish without replacement; an existing target must have identical bytes."""
        try:
            os.link(tmp, target)
        except FileExistsError:
            if sha256_file(tmp) != sha256_file(target):
                raise RuntimeError("cut identity conflict")
        finally:
            tmp.unlink(missing_ok=True)

    def _cut_csv(self, path, col, lo, hi, target, holdout,
                 time_unit=None, timezone_mode=None) -> bool:
        import numpy as np
        import pandas as pd

        masks = []
        kept_max = None
        for chunk in pd.read_csv(path, usecols=[col], chunksize=CSV_ROWS_PER_CHUNK):
            times = self._wall_clock(
                chunk[col], time_unit=time_unit, timezone_mode=timezone_mode
            )
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

    def _cut_parquet(self, path, col, lo, hi, target, holdout,
                     time_unit=None, timezone_mode=None) -> bool:
        import pyarrow as pa
        import pyarrow.parquet as pq

        pf = pq.ParquetFile(path)
        kept_max = None
        removed = False
        tmp = self._tmp_for(target)
        try:
            writer = pq.ParquetWriter(str(tmp), pf.schema_arrow, **PARQUET_WRITER)
            try:
                column_index = pf.schema_arrow.get_field_index(col)
                for batch in pf.iter_batches(batch_size=CSV_ROWS_PER_CHUNK):
                    times = self._wall_clock(
                        batch.column(column_index).to_pandas(),
                        time_unit=time_unit, timezone_mode=timezone_mode,
                    )
                    mask = ((times >= lo) & (times < hi)).to_numpy()
                    if not mask.all():
                        removed = True
                    if not mask.any():
                        continue
                    m = times[mask].max()
                    kept_max = m if kept_max is None or m > kept_max else kept_max
                    table = pa.Table.from_batches([batch])
                    if not mask.all():
                        table = table.filter(pa.array(mask))
                    writer.write_table(table, row_group_size=CSV_ROWS_PER_CHUNK)
            finally:
                writer.close()
            if not removed:
                tmp.unlink(missing_ok=True)
                return False
            self._assert_holdout(kept_max, holdout)
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
        if col is None and holdout is not None and resource_id not in (self.params.get("untimed") or []):
            # same rule as download: without a time column nothing proves the rows end before the holdout
            raise PermissionError("no time column under holdout")
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
            **store_metadata(self.params, adapter_kind="lake", adapter_engine="files_inventory"),
            "root_path": str(self._root()),
        }
