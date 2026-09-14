# data-gov

**Data governance for lakes and warehouses.** One kernel provides inventory,
automatic policy, data lineage, usage accounting and result reconciliation.
Experiments work locally after receiving their data; no person approves each
download, and governance calls do not run inside the learning loop.

## Lakes and warehouses

| Store kind | Data model | Example | Operations |
|---|---|---|---|
| `lake` | Schema-on-read: files in native form, interpreted by consumers | financial-data, CSV/Parquet | Inventory, coverage, download, read |
| `warehouse` | Schema-on-write: structured tables with defined schemas, facts and dimensions | PostgreSQL OLAP cube | Inventory, SELECT queries, append-only reporting |

A lake can be local; cloud hosting is not a requirement. A Parquet file may
have a schema without being a warehouse. The distinction describes how a
store is organized and consumed, not its price, quality or physical location.
The OLAP cube is a **warehouse**, not another lake or a new lakehouse product.

Both kinds use the same policy and accounting engine. `kind` does not grant
operations: policies still decide them. `deny_from` remains applicable to both
through their existing temporal rules. A warehouse need not implement file
downloads; a lake need not implement SQL queries.

Three metadata fields distinguish the concepts:

- `kind`: `lake` or `warehouse`.
- `engine`: implementation, such as `files_inventory` or `sql_olap`.
- `transport`: `http` or `local` for built-in adapters.

The existing `datagov.lake` plugin group, `lakes[]` configuration, `lake_id`
identifiers and `/api/v1/lakes` route remain unchanged for compatibility.
They collectively refer to governed **stores**. No repository split or
plugin-group migration is required.

## Experiment flow

[Flow v3](docs/06_FLOW_V3_FAILSAFE.md) is the implemented contract for new
governing experiments:

1. Register a campaign with units, effective configuration, code identity,
   requested inputs and result destination.
2. Download each declared input, verify its hash and confirm receipt. Reused
   cache entries are rehashed and recorded as cache uses.
3. Run locally. Source hash, delivered hash, role, range and temporal contract
   stay attached to the experiment.
4. Persist a terminal for every outcome, including failure, refusal and
   inconclusive results. A durable outbox retains reports during an outage.
5. Report through data-gov to the configured warehouse and reconcile the
   campaign, accounting log and stored terminals.

A matching hash identifies bytes; it does not prove causal validity or
scientific usefulness. Keep source artifacts, generator specifications,
partitions and software identities for reproduction. Temporal resources need
explicit availability contracts. Small mechanics-only tests may be marked
`NON_GOVERNING`; they cannot promote a scientific result.

The client is `app.client.DataGovClient`. Predictor's integration is documented
in [GOVERNED_RUN.md](https://github.com/harveybc/predictor/blob/musashi/data-gov-consumer-v3-20260913/docs/GOVERNED_RUN.md).
The older [Flow v2 API](docs/04_FLOW_V2.md) remains documented for compatibility;
its experiment-key-only reporting is not the new governing path.

## Install and test

Python 3.10+ (exercised on 3.12). Dependencies are in `requirements.txt`.

```bash
git clone https://github.com/harveybc/data-gov.git
cd data-gov
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
python3 scripts/issue_credentials.py
python3 -m pytest tests -q
```

`issue_credentials.py` does not overwrite an existing credentials file.
Credentials belong in local configuration, never in Git. The optional
`scripts/seed_olap.py` creates a SQLite demonstration cube, not production
experiment evidence.

## Run

Start configured stores before the governance kernel, each in its own shell
and environment. If the services already run, use the reviewed deployment
procedure instead of starting duplicates or restarting experiments.

| Component | Command from the indicated repository directory | Default address |
|---|---|---|
| Financial lake | In `financial-data/lake`: `sh scripts/serve.sh` | `http://127.0.0.1:5056` |
| OLAP warehouse adapter | In `predictor/olap/lake`: `sh scripts/serve.sh` | `http://127.0.0.1:5057` |
| Governance kernel | In `data-gov`: `sh scripts/serve.sh` | `http://127.0.0.1:5055/login` |

The warehouse uses configured PostgreSQL access. `query` is SELECT-only;
result writes use the reporting adapter and additive `gov_*` tables.
Experiments do not receive database write credentials. Do not run
`reset_olap` against the populated cube.

Login credentials are in the local disk file `var/credentials.json`, not a web
URL. Service clients use their configured API key. `/healthz` checks service
health; it does **not** certify end-to-end experiment adoption.

Before claiming deployment, pass the disposable three-service check
`tools/verify_flow_v3_e2e.py`, then verify a governed micro-run through the
actual deployed services. Implementation, deployment and experiment adoption
are separate statuses; store labels alone do not complete that work.

## Configured stores

| `lake_id` (stable identifier) | `kind` | `engine` | Adapter |
|---|---|---|---|
| `financial_files` | `lake` | `files_inventory` | `http_lake`, financial-data |
| `olap_cube` | `warehouse` | `sql_olap` | `http_lake`, predictor OLAP |
| `predictor_examples` | `lake` | `files_inventory` | Local `files_lake` |

Set kind explicitly for HTTP stores so labels remain available when a remote
service is offline. Legacy `files_inventory` and `sql_olap` kinds normalize on
input. Legacy `http` alone is only a transport: a known remote description
supplies the kind; otherwise the UI says **Unclassified** and `describe()`
returns `kind: null`. That is missing metadata, not a third kind.

The dashboard lists stores, kinds, engines, resources and accounting activity.
Store details retain inventory and usage links for both kinds.

## Plugins and documents

| Plugin group | Responsibility |
|---|---|
| `datagov.pipeline` | Application orchestration |
| `datagov.web` | UI and HTTP API |
| `datagov.access` | Principals and automatic policies |
| `datagov.accounting` | Usage and campaign records |
| `datagov.lake` | Store adapters: `files_lake`, `sql_lake`, `http_lake` |
| `datagov.role` | Event-driven roles, outside the read hot path |

Configuration precedence: plugin defaults, application defaults, JSON config,
then long-form CLI flags. Store kind and engine belong to each `lakes[]` entry,
not another adapter's global defaults.

- [Product contract](docs/00_CONTRATO.md)
- [Work plan](docs/01_WORKPLAN.md)
- [Connecting a store](docs/03_LAKE_ADAPTER.md)
- [Flow v3 and reproducibility](docs/06_FLOW_V3_FAILSAFE.md)
- [Classification change and test evidence](docs/STORE_KINDS_CHANGE.md)

`data-logger` remains a different product. A future telemetry store can use an
adapter here; it does not require another governance kernel.
