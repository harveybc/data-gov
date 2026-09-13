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
5. Add `policies` for who may `discover` / `coverage` / `read` / `query`.
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
  "holdout_start": "2025-01-01"
}
```

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
| `coverage(resource_id)` | files | `t_min`, `t_max`, `rows` |
| `read(resource_id, start=, end=)` | files | `{rows, sha256, bytes}` |
| `query(sql)` | sql | `{rows, sha256, bytes}`; SELECT only |

Holdout: if `holdout_start` is set, a `read`/`query` that includes that
day or later must fail (the kernel also denies via `policies.deny_from`).
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

GET /api/v1/lakes
GET /api/v1/resources?lake=<lake_id>
GET /api/v1/coverage?lake=<lake_id>&resource=<resource_id>
GET /api/v1/read?lake=<lake_id>&resource=<resource_id>&from=2020-01-01&to=2020-01-31
GET /api/v1/query?lake=<lake_id>&sql=SELECT ...
GET /api/v1/experiments/<experiment_key>/usage
```

Python: `app.client.DataGovClient`.

Missing experiment key → 403 and a deny row. Unknown resource → 403/404
and a deny row. Holdout range → 403.

## 6. Built-in adapters

| Plugin | Use when |
|---|---|
| `http_lake` | Another process exposing `/api/v1/discover|coverage|read|query` |
| `files_lake` | Local directory of csv/parquet (tests / small trees) |
| `sql_lake` | Local SQLite SELECT-only |
| `default_lake` | Alias of `files_lake` |

A future `data-logger` telemetry store would be another `datagov.lake`
plugin, not a fork of this repo.
