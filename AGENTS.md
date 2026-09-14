# AGENTS.md — data-gov

Guidance for AI coding agents. See [agents.md](https://agents.md).

## Project overview

`data-gov` is the **data governance** system for multiple lakes: inventory,
automatic policy, accounting, and event-driven Hermes roles.

CEO (Harvey) and data engineer (Musashi) stay human. Other governance and
technical roles are prompts that fire **only on events**. The data-scientist
role (Satoshi-shaped) must not gate a `GET`.

Plugins resolve through setuptools entry points (`datagov.pipeline`,
`web`, `access`, `accounting`, `lake`, `role`). Config merge:
plugin_params → defaults → JSON file → long-form CLI.

Holdout starts 2025-01-01 (financial-data catalog). Service calls without
`X-Experiment-Key` are 403. Do not put plaintext API keys in git.

`data-logger` is a different product (sensor telemetry). Do not merge them.
A telemetry site may become a **lake adapter** later.

Lake adapters: `docs/03_LAKE_ADAPTER.md`.

Flow v2 (`docs/04_FLOW_V2.md`, the contract): an agent downloads a dataset **as a
file** (`GET /api/v1/download`, `DataGovClient.download`), data-gov records who, when,
which resource and the sha256 of the delivered bytes, and at the end the agent
`report_metrics` naming the target lake and the sha256 of every dataset it used;
data-gov verifies those bytes were served under that experiment (or set) key, writes
the canonical report into the lake's `gov_*` tables and one accounting row. Files
that span the holdout are served only as day-ranged cuts on the column's own wall
clock, materialised once under `var/cuts/`; spool files live under `var/spool/` and
are swept at start; `source_changed` fires inline when a source file's hash differs
from its last download. Do not add cloud, Keycloak, OPA or any external service; a
new lake is an adapter plus a config block.

## Agent quickstart

```bash
# lakes first
cd financial-data/lake && pip install -e . && sh scripts/serve.sh   # :5056
cd predictor/olap/lake && pip install -e . && sh scripts/serve.sh # :5057
cd data-gov
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .
test -f var/credentials.json || python3 scripts/issue_credentials.py
python3 -m pytest tests -q
PYTHONPATH=. python3 -m app.main --load_config examples/config/default.json  # :5055
```

Tell the user:

- UI: http://127.0.0.1:5055/login
- `var/credentials.json` is a **filesystem** path in the checkout, not a
  URL. `cat var/credentials.json`. Username `harvey`.
- Do not print passwords into git or the README.
- `/healthz` must return `ok`.

Force CPU. Do not stop GPU, Postgres, or Metabase. Do not write host names
or credentials into committed files.

## Do not

- Add S3/HDFS/GVFS as the architecture.
- Put Musashi or Satoshi on the read hot path.
- Poll Hermes on a timer with no event.
- Treat the demo SQLite log as production evidence.
- Serve `var/credentials.json` over HTTP.
- Serve `var/credentials.json` over HTTP.
- Run `tests/test_df_lab_evaluation.py` or other heavy predictor labs on this host.
