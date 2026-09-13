# data-gov

Data **governance** for several lakes (on-prem or remote later). Not a
cloud catalog, not Gravitino, not a thin AAA toy.

It does three jobs:

1. **Kernel** — inventory, automatic policy, accounting (hashes, allow/deny, experiment id).
2. **Lake adapters** — each lake is a plugin. `financial-data` and a lab OLAP are the first two, not the ceiling.
3. **Roles** — Hermes prompts that fire **only on events**. CEO (Harvey) and data engineer (Musashi) stay human. A data-scientist prompt must **not** gate a `GET`.

Experiments do not wait for a ticket. Allow/deny is code + inventory.

| Doc | What |
|---|---|
| [docs/00_CONTRATO.md](docs/00_CONTRATO.md) | Product contract |
| [docs/01_WORKPLAN.md](docs/01_WORKPLAN.md) | Phases G0–G7 |
| [docs/02_DESIGN.md](docs/02_DESIGN.md) | Plugin types and HTTP API |
| [docs/03_LAKE_ADAPTER.md](docs/03_LAKE_ADAPTER.md) | **How to build/connect a lake** |
| [docs/04_FLOW_V2.md](docs/04_FLOW_V2.md) | **Contract**: download with hash, report metrics, lineage |

## Requirements

- Python **3.10+** (exercised on 3.12).
- `pip` packages in `requirements.txt`: Flask, pandas, pyarrow, pytest.
- A checkout of this repo. Optional sibling `../financial-data` for the real file lake.
- Nothing on the GPU. Do not stop Postgres/Metabase/training jobs you did not start.
- Port **5055** free on localhost.

No Docker. No Keycloak in this phase. No S3.

## Install

```bash
git clone https://github.com/harveybc/data-gov.git
cd data-gov
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
python3 scripts/issue_credentials.py   # writes var/credentials.json (gitignored)
python3 scripts/seed_olap.py           # lab sqlite cube
python3 -m pytest tests -q
```

`setup.py` `install_requires` is the runtime set; `requirements.txt` is what we actually install.

If `var/credentials.json` already exists, `issue_credentials.py` will not overwrite it.

## Run

**Three processes**, all left running (lakes first):

```bash
# 1) file lake
cd ../financial-data/lake && pip install -e . && sh scripts/serve.sh
# http://127.0.0.1:5056  inventory of parquet/csv (stat only)

# 2) OLAP cube (read-only, PG*)
cd ../predictor/olap/lake && pip install -e . && sh scripts/serve.sh
# http://127.0.0.1:5057  tables in predictor_olap

# 3) AAA
cd data-gov
PYTHONPATH=. python3 -m app.main --load_config examples/config/default.json
# same: sh scripts/serve.sh  → http://127.0.0.1:5055/login
```

Then open **http://127.0.0.1:5055/login** (not `/var/credentials.json` — that path is a **disk file**, not a web page).

Person login:

```bash
cat var/credentials.json
```

Use username `harvey` (or `musashi`) and the password under `people.<name>`.
CSS is served from `/static/` in this repo (no CDN).

Stop: Ctrl+C.

### Service clients (predictor, DOIN, heuristic-strategy)

API keys are in the same file under `services.<name>`. Every `download` / `read` /
`query` needs:

```
Authorization: Bearer <api_key>
X-Experiment-Key: <experiment or experiment-set key>   # ^[A-Za-z0-9._:-]{1,128}$
```

The governed path is **download a file, use it, report the metrics** (contract:
[docs/04_FLOW_V2.md](docs/04_FLOW_V2.md)). The API key can also come from
`DATA_GOV_API_KEY` or `api_key_file=`; never from git.

```python
from app.client import DataGovClient

gov = DataGovClient(
    "http://127.0.0.1:5055",
    api_key="<services.predictor>",          # or DATA_GOV_API_KEY
    experiment_key="ann_1575_1d",
)

# 1. the dataset as a file: sha256 verified on arrival, cached as <dest>/<sha256>.csv
status, info = gov.download(
    "predictor_examples", "phase_1/normalized_d4.csv", "var/cache/predictor_examples",
)
# info: path, sha256, source_sha256, delivery (AS_IS | CUT), time_column, bytes, cached
# a range cuts on the column's own wall clock; the holdout is never served:
# gov.download(lake, resource, dest, start="2020-01-01", end="2020-01-31")

# 2. train with info["path"] ...

# 3. report: data-gov checks those bytes were served under this key, then writes the
#    report into the named lake (the cube is just another lake)
status, receipt = gov.report_metrics(
    "ann_1575_1d",
    "olap_cube",
    metrics=[{"metric": "MAE", "value": 0.0065, "split": "train", "horizon": 24}],
    datasets=[{"lake": "predictor_examples", "resource": "phase_1/normalized_d4.csv",
               "sha256": info["sha256"], "role": "x_train_file"}],
    config_sha256="…", code_commit="…", project="predictor", phase="phase_1_daily",
)
# 201 stored / 200 already stored; receipt["lineage"] is VERIFIED or UNVERIFIED and
# each dataset carries its reason and the accounting event_id that served it.

gov.usage("ann_1575_1d")            # every accounting row of that key
gov.dataset_usage(info["sha256"])   # every allow download of exactly those bytes
```

`read` (JSON rows, small slices) and `query` (SELECT only) stay as before.

Holdout: timestamps on or after **2025-01-01** are denied and logged; a file that
spans the holdout can only be downloaded with a `from`/`to` range (calendar days).
No experiment header → 403 and a deny row. A lake whose policy has
`require_lineage: true` answers 422 to a report whose datasets were not served under
that key; the lab lake stores it with `lineage=UNVERIFIED` and a warning.

## Use it with an agent

Open **this** repository in Claude, Cursor, Codex, Copilot, Grok, … and paste:

> Read `AGENTS.md` and follow the **Agent quickstart**. Create a venv,
> `pip install -r requirements.txt && pip install -e .`, run
> `python3 scripts/issue_credentials.py` if `var/credentials.json` is
> missing, `python3 scripts/seed_olap.py`, `python3 -m pytest tests -q`.
> Start the UI with `sh scripts/serve.sh` and leave it running. Tell me
> http://127.0.0.1:5055/login , that credentials are the **file**
> `var/credentials.json` (not a URL), and do not print the passwords in
> git. Do not stop GPU/Postgres/Metabase. Do not add S3/Gravitino.

Longer lake work: [docs/03_LAKE_ADAPTER.md](docs/03_LAKE_ADAPTER.md).

## How a data lake must be built to use this AAA

Short version (full text in the adapter doc):

1. Clients never `open()` the lake. They call data-gov.
2. The lake is a **setuptools plugin** in group `datagov.lake` (or, later, the same verbs over HTTP — not shipped yet).
3. It must implement `discover` (inventory), `describe`, `storage`, and either `download`+`coverage` (+ `read` for small JSON slices) for files or `query` (SQL, SELECT only) + `write_metrics` (append-only `gov_*`) for a cube.
4. `discover` is the inventory. Unknown `resource_id` → deny.
5. Register it in `setup.py`, `pip install -e .`, add a `lakes[]` entry and `policies[]` in the JSON.
6. Reuse `files_lake` if it is a directory of csv/parquet; reuse `sql_lake` if it is SQLite. New kinds = new plugin, same group.

Shipped examples:

| `lake_id` | Plugin | What |
|---|---|---|
| `financial_files` | `http_lake` → :5056 | `financial-data/lake` — parquet/csv under data roots + features |
| `olap_cube` | `http_lake` → :5057 | `predictor/olap/lake` — live `predictor_olap`, SELECT-only `query`, append-only `gov_*` via `write_metrics` |
| `predictor_examples` | `files_lake` (in-process) | `../predictor/examples/data_downsampled`, `DATE_TIME` column, holdout 2025-01-01 |

## Plugins (setuptools, same as predictor)

Six types. AuthN+AuthZ are one plugin. Inventory is `lake.discover()`. Roles are one dispatcher.

| Group | Job | Names |
|---|---|---|
| `datagov.pipeline` | Orchestrate | `default_pipeline` |
| `datagov.web` | AdminLTE UI + HTTP API | `default_web` |
| `datagov.access` | People, API keys, policies | `default_access` |
| `datagov.accounting` | Append-only log | `default_accounting` |
| `datagov.lake` | Adapters | `files_lake`, `sql_lake` |
| `datagov.role` | Events (no Hermes until an event exists) | `default_role` |

Config merge (later wins): `plugin_params` → `app/config.py` → `--load_config` JSON → long `--flags` only.

## What you see in the UI

- Lakes the logged-in person is allowed to see
- Host free space per lake, inventoried resources, operation counts
- Last accounting warnings
- Click a lake: description, inventory, usage log, stats by operation and by actor

## Layout

```
app/                   CLI, merge, plugin loader, DataGovClient, report.py (canonical report), httpstream.py
access_plugins/
accounting_plugins/
lake_plugins/          files_lake, sql_lake, http_lake, errors.py
pipeline_plugins/
role_plugins/
web_plugins/           templates + vendored AdminLTE under static/
examples/config/       default.json (hashes only)
examples/data/olap_lab/
docs/
scripts/serve.sh
scripts/issue_credentials.py
scripts/seed_olap.py
tests/                 user / system / integration / unit
var/                   gitignored: credentials.json, accounting.db, spool/ (swept at start),
                       cuts/<source_sha256>/<from>_<to>.<ext> (materialised once), source_sha256.json
```

## What this repo is not

- Not `data-logger` (ESP32 / ThingsBoard telemetry). That may become a lake plugin later.
- Not a human approval queue. Musashi/Satoshi are not on the read hot path.
- Not a general write API to the campaign cube. `query` is SELECT only; the only write is `write_metrics`, append-only on the `gov_*` tables, and nothing here runs `reset_olap`.
