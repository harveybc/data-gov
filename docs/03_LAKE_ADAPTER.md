# 03 - Connecting a store to data-gov

One kernel governs lakes and warehouses. Clients use data-gov; adapters
connect it to files or databases. A new store is an adapter, configuration and
policies, not a new governance product.

The filename and `datagov.lake` entry-point group are retained for compatibility.
`lake_id`, `lakes[]` and `?lake=` identify a store of either kind. Do not rename
them as part of the classification change.

## 1. Kind, engine and transport

| Field | Meaning | Values |
|---|---|---|
| `kind` | Store organization | `lake`, `warehouse` |
| `engine` | Implementation | e.g. `files_inventory`, `sql_olap` |
| `transport` in describe | Access mechanism, derived by adapter | `local`, `http` |

A lake uses schema-on-read for native files. A warehouse uses schema-on-write
for structured tables. Neither requires cloud hosting. Both retain the same
inventory, temporal policies, accounting and lineage requirements. Changing
kind changes metadata, not permissions.

### Lake: financial files

```json
{
  "plugin": "http_lake",
  "lake_id": "financial_files",
  "title": "financial-data",
  "kind": "lake",
  "engine": "files_inventory",
  "base_url": "http://127.0.0.1:5056",
  "holdout_start": "2025-01-01"
}
```

For a directory in the same process, use `files_lake`, `root_path` and
`include_globs` instead of `base_url`. Governing downloads need an exact
`resource_contracts` entry for each resource: `event_time_column`,
`available_time_column`, `timezone`, `time_unit`, `frequency`. Derive them
from producer evidence; do not guess availability or copy event time into it
without justification.

Legacy `time_column`, `time_columns` and `untimed` options belong to the
compatibility API in [Flow v2](04_FLOW_V2.md). They do not replace the
governing availability contract in [Flow v3](06_FLOW_V3_FAILSAFE.md).

### Warehouse: OLAP

```json
{
  "plugin": "http_lake",
  "lake_id": "olap_cube",
  "title": "predictor OLAP cube",
  "kind": "warehouse",
  "engine": "sql_olap",
  "base_url": "http://127.0.0.1:5057",
  "holdout_start": "2025-01-01"
}
```

The remote adapter owns its database connection configuration. `query` is
SELECT-only; metrics and complete terminals append to `gov_*` through reporting
methods. No generic mutation of experiment tables is added by declaring a
warehouse. Built-in `sql_lake` is SQLite for local tests and also has kind
warehouse: location does not determine the classification.

## 2. Register and describe a store

1. Implement `Plugin`, `plugin_params` and `set_params(**config)`.
2. Register `my_store=lake_plugins.my_store:Plugin` in `datagov.lake`, then
   reinstall with `pip install -e .`.
3. Add one `lakes[]` entry with a stable `lake_id` and an explicit kind.
4. Configure verbs in the existing `policies` list. Preserve temporal
   restrictions and lineage requirements; there is no second policy engine.
5. Test in a disposable environment before deployment.

Built-in `describe()` returns `lake_id`, `title`, `description`, `kind`,
`engine`, `transport` and `root_path`. The UI and existing `GET /api/v1/lakes`
consume that same description.

Compatibility rules:

- Local legacy `files_inventory` maps to lake; `sql_olap` maps to warehouse.
  Contradictory local types are configuration errors.
- HTTP uses configured kind first, then a known remote declaration. Explicit
  configuration is the local catalog declaration and should be checked when
  connecting a store; it is not inferred from a URL or permitted verbs.
- Legacy `http` alone cannot determine the data model. Without a recognized
  remote declaration, kind is null and the UI displays Unclassified.
- Unknown configured kinds are errors. There is no `lakehouse` kind.
- Resource-level `kind: file` and `kind: table` remain unchanged; they describe
  inventory items rather than stores.

## 3. Interfaces

| Method | Support | Result |
|---|---|---|
| `describe()` | Both | Store metadata |
| `storage()` | Both | Capacity/usage; existing `lake_bytes` field retained |
| `discover()` | Both | Resource inventory, not content identity |
| `coverage(resource_id)` | File lake | Temporal extent, rows and time column |
| `download(resource_id, ...)` | File lake | Bytes, hashes and temporal evidence |
| `read(resource_id, ...)` | File lake | Small legacy JSON slices |
| `query(sql)` | Warehouse | SELECT results |
| `write_metrics(report)` | Warehouse | Legacy append-only metric report |
| `write_terminal(terminal)` | Warehouse | Complete, idempotent Flow v3 outcome |
| `terminal_digests(campaign_sha256)` | Warehouse | Reconciliation identities |

An adapter need not implement the other kind's operations. HTTP proxies the
operations its remote implements; it does not grant all operations.

## 4. Consumption and reporting

The client registers a campaign, confirms deliveries, runs locally, persists
its terminal and reports through data-gov. The kernel attaches complete
delivery evidence before the warehouse stores the terminal. Retries use the
durable outbox and reconciliation.

[Flow v3](06_FLOW_V3_FAILSAFE.md) defines exact schemas and routes for
campaigns, downloads, confirmations, terminals and reconciliation. Use its
shared client; do not add HTTP calls inside `fit`, `transform`, `step` or a
training batch. Preserve bounded file cuts and their producer identities.
Reproduce memory problems with small fixtures, not large live files.

## 5. Adoption check

Check both types through one kernel: permitted/denied verbs, temporal
restrictions, receipts, result writes and idempotent replay. Then verify UI
labels and navigation. Classification is not proof that G5/Flow v3 governs
a real experiment: keep its separate end-to-end acceptance and controlled
deployment procedure.
