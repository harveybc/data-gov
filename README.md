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

The process must **stay running**:

```bash
PYTHONPATH=. python3 -m app.main --load_config examples/config/default.json
# same: sh scripts/serve.sh
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

API keys are in the same file under `services.<name>`. Every `read` / `query` needs:

```
Authorization: Bearer <api_key>
X-Experiment-Key: <run id>
```

```python
from app.client import DataGovClient

gov = DataGovClient(
    "http://127.0.0.1:5055",
    api_key="<services.predictor>",
    experiment_key="ann_1575_1d",
)
status, body = gov.read(
    "financial_files",
    "market_data/crypto/funding_rates/btcusdt/funding_rates.parquet",
    start="2020-01-01",
    end="2020-01-31",
)
```

Holdout: timestamps on or after **2025-01-01** are denied and logged.
No experiment header → 403 and a deny row.

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
3. It must implement `discover` (inventory), `describe`, `storage`, and either `read`+`coverage` (files) or `query` (SQL, SELECT only).
4. `discover` is the inventory. Unknown `resource_id` → deny.
5. Register it in `setup.py`, `pip install -e .`, add a `lakes[]` entry and `policies[]` in the JSON.
6. Reuse `files_lake` if it is a directory of csv/parquet; reuse `sql_lake` if it is SQLite. New kinds = new plugin, same group.

Shipped examples:

| `lake_id` | Plugin | What |
|---|---|---|
| `financial_files` | `files_lake` | Sibling `../financial-data`, glob on one BTC funding parquet |
| `olap_lab` | `sql_lake` | `examples/data/olap_lab/olap.sqlite` (not the campaign Postgres) |

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
app/                   CLI, merge, plugin loader, DataGovClient
access_plugins/
accounting_plugins/
lake_plugins/          files_lake, sql_lake
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
var/                   gitignored: credentials.json, accounting.db
```

## What this repo is not

- Not `data-logger` (ESP32 / ThingsBoard telemetry). That may become a lake plugin later.
- Not a human approval queue. Musashi/Satoshi are not on the read hot path.
- Not the populated campaign OLAP. `olap_lab` is a throwaway sqlite.
