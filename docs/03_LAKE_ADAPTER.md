# 03 — How a data lake connects to data-gov

Clients (`predictor`, DOIN, `heuristic-strategy`) **never** open the lake
disk or `PG*` directly. They talk to data-gov (session or API key).
data-gov talks to the lake through a **plugin** in group `datagov.lake`.

A new lake is not a new governance product. It is an adapter + a JSON
block + policies.

Remote HTTP lakes use plugin `http_lake` (`base_url`). Shipped:

- `financial-data/lake` → http://127.0.0.1:5056
- `predictor/olap/lake` → http://127.0.0.1:5057

`files_lake` / `sql_lake` remain for tests and local directories.

## 1. Register it

1. Implement `Plugin` with `plugin_params` and `set_params` (same house
   as predictor).
2. Entry point in `setup.py`:

   ```
   datagov.lake → my_lake=lake_plugins.my_lake:Plugin
   ```

3. `pip install -e .` so the entry point exists.
4. Add a block under `lakes` in the JSON config (see
   `examples/config/default.json`).
5. Add `policies` for who may `discover` / `coverage` / `read` / `download` /
   `query` / `write_metrics`. A policy carries `deny_from` (holdout) and, for a
   metrics lake, `require_lineage` (default `true` when absent). A lake with
   `holdout_start` whose policies lack `deny_from` refuses to start.
6. Resource not returned by `discover` → deny. Fail-closed.

## 2. JSON shape

```json
{
  "plugin": "files_lake",
  "lake_id": "financial_files",
  "title": "Financial files",
  "description": "…",
  "kind": "files_inventory",
  "root_path": "../financial-data",
  "include_globs": ["market_data/crypto/**/*.parquet"],
  "time_column": "fundingTime",
  "time_columns": {"odd/file.csv": "DATE"},
  "untimed": ["static/lookup.csv"],
  "time_unit": null,
  "holdout_start": "2025-01-01",
  "spool_dir": "./var/spool",
  "cuts_dir": "./var/cuts",
  "max_downloads": 2
}
```

- `time_column`: the time axis for every file; `time_columns[resource_id]` overrides it
  per resource; with neither, the first column named `ts,time,date,datetime,timestamp`
  or containing `time`/`date` is used. The chosen column is reported as `X-Time-Column`.
- `untimed`: resources without a time axis, declared by a human. Under a holdout they
  are the only files served whole (`AS_IS`) without a coverage check.
- `time_unit`: needed when the time column holds epoch integers (`s`, `ms`, …); an
  integer column without it, or a column with mixed formats or mixed zone offsets, is
  422 `unparseable time column`.

SQL lab:

```json
{
  "plugin": "sql_lake",
  "lake_id": "olap_lab",
  "kind": "sql_olap",
  "sqlite_path": "examples/data/olap_lab/olap.sqlite",
  "time_column": "ts",
  "holdout_start": "2025-01-01"
}
```

`lake_id` is the name used in policies and in the API (`?lake=`).

## 3. Methods the plugin must implement

| Method | Required | Returns |
|---|---|---|
| `set_params(**config)` | yes | — |
| `describe()` | yes | `lake_id`, `title`, `description`, `kind`, `root_path` |
| `storage()` | yes | `host_total`, `host_used`, `host_free`, `lake_bytes` |
| `discover()` | yes | list of `{resource_id, ...}` — **this is the inventory** |
| `coverage(resource_id)` | files | `t_min`, `t_max`, `rows`, `time_column` — from the time column alone |
| `download(resource_id, start=None, end=None)` | files | `{path, filename, sha256, bytes, source_sha256, delivery, time_column}`; `path` is the source file (`AS_IS`) or the cut materialised once under `cuts_dir/<source_sha256>/<from>_<to>.<ext>` (`CUT`), both persistent |
| `read(resource_id, start=, end=)` | files | `{rows, sha256, bytes}`; small JSON slices, strict `<` upper bound |
| `query(sql)` | sql | `{rows, sha256, bytes}`; SELECT only |
| `write_metrics(report)` | sql | `{stored, already_stored, lineage}`; append-only on `gov_report` / `gov_metric` / `gov_dataset`, recomputes `report_sha256` and refuses a mismatch |

`discover` is stat-only: no content hash. `download` without a range under a holdout
is allowed only when `t_max < holdout_start` or the resource is `untimed`; a range
(`YYYY-MM-DD`, calendar days, `from <= to`) must end before the holdout and the cut keeps
`from 00:00 <= t < to + 1 day` on the column's own wall clock (zones dropped, not
converted); after the cut the lake asserts `max(t) < holdout_start`. A cut that removes
no rows is delivered `AS_IS`. Errors: `PermissionError` (403: `holdout`, `spans holdout:
request a range`, `no time column under holdout`), `ValueError` (400 `invalid
from/to`), `FileNotFoundError` (404), `lake_plugins.errors.UnsupportedError` (422:
`unsupported csv` for multi-line quoted records, `unparseable time column`, `no time
column`), `RuntimeError` (503: unreachable, `lake hash mismatch`).

Operational rules, same for every lake and for data-gov:

- Intermediate files live under `spool_dir` (default `var/spool/`), cuts under
  `cuts_dir` (default `var/cuts/`), never under `tempfile` defaults. Spool files are
  unlinked right after opening; the spool is swept at process start.
- Bytes stream in 1 MiB chunks with the digest updated as they pass; nothing reads a
  whole body.
- Download handlers hold a `BoundedSemaphore(max_downloads)` (default 2); no free slot
  → 503 with `Retry-After: 30`.
- Download connections use a connect timeout only (30 s), never a read timeout.
- `source_sha256` is memoised per `(path, size, mtime_ns)` in `<var>/source_sha256.json`.
- An HTTP lake exposes `GET /api/v1/download?resource=&from=&to=` and
  `POST /api/v1/metrics` with the lake token, same headers, same errors; `http_lake`
  streams the body into the spool, recomputes the sha256 and rejects a mismatch.

Do not invent resource ids that are not on disk / in the catalog.

## 4. What the lake does **not** do

- Authentication (that is `datagov.access`).
- Accounting (that is `datagov.accounting`).
- Hermes roles.
- Serving AdminLTE.

The lake only answers: what exists, what range, what bytes, what hash.

## 5. How a client calls it (after the lake is registered)

```
Authorization: Bearer <api_key>
X-Experiment-Key: <run id>          # required on read and query

GET  /api/v1/lakes
GET  /api/v1/resources?lake=<lake_id>
GET  /api/v1/coverage?lake=<lake_id>&resource=<resource_id>
GET  /api/v1/download?lake=<lake_id>&resource=<resource_id>[&from=2020-01-01&to=2020-01-31]
GET  /api/v1/read?lake=<lake_id>&resource=<resource_id>&from=2020-01-01&to=2020-01-31
GET  /api/v1/query?lake=<lake_id>&sql=SELECT ...
POST /api/v1/experiments/<experiment_key>/metrics          {lake, metrics, datasets, ...}
GET  /api/v1/experiments/<experiment_key>/usage?limit=&before_id=
GET  /api/v1/datasets/<sha256>/usage?limit=&before_id=
```

Python: `app.client.DataGovClient` (`download`, `report_metrics`, `usage`,
`dataset_usage`, plus `read` / `query`). Body shapes and the lineage rules are in
`04_FLOW_V2.md`.

Missing experiment key → 403 and a deny row. Unknown resource → 404 and a
deny row. Holdout range → 403. Unverified lineage under `require_lineage` → 422.

## 6. Built-in adapters

| Plugin | Use when |
|---|---|
| `http_lake` | Another process exposing `/api/v1/discover|coverage|read|download|query|metrics` |
| `files_lake` | Local directory of csv/parquet (`predictor_examples`, tests, small trees) |
| `sql_lake` | Local SQLite: SELECT-only `query`, append-only `gov_*` via `write_metrics` (DDL once at start, WAL, `timeout=30`, file created if missing) |
| `default_lake` | Alias of `files_lake` |

A future `data-logger` telemetry store would be another `datagov.lake`
plugin, not a fork of this repo.
