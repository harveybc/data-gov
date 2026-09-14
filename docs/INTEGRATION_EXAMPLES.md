# Integration examples

These examples describe the current HTTP services, not the proposed package
extraction. Each service has its own environment. Use separate configuration
and output locations for development and production.

## Temporal resource contract

For a fixture whose producer explicitly writes these two timestamps:

```csv
event_time,available_time,value
2024-01-01T00:00:00Z,2024-01-01T00:01:00Z,1
2024-01-01T01:00:00Z,2024-01-01T01:01:00Z,2
```

the file-lake configuration can include:

```json
{
  "root_path": "/absolute/path/to/fixtures",
  "include_globs": ["panel.csv"],
  "resource_contracts": {
    "panel.csv": {
      "event_time_column": "event_time",
      "available_time_column": "available_time",
      "timezone": "UTC",
      "time_unit": null,
      "frequency": "1h"
    }
  }
}
```

The two columns have different meanings. Do not reuse this contract for a
financial file without evidence of its producer and publication timing.
Additional `availability` metadata describes completion lag and permitted use
where the current adapter supports it. An offline day-granular contract does
not imply intrabar or live equivalence. Test future perturbation and partition
boundaries before allowing a transform into a scientific comparison.

## Inspect stores from a service client

The generated service API key can be supplied through `DATA_GOV_API_KEY`, or
through a local file containing only that key. It is different from the
store-to-governance token.

```python
from app.client import DataGovClient

client = DataGovClient(
    base_url="http://127.0.0.1:5055",
    experiment_key="inventory-check",
)
status, catalog = client.lakes()
if status != 200:
    raise RuntimeError(catalog)
print(catalog)

status, inventory = client.resources("olap_cube")
if status != 200:
    raise RuntimeError(inventory)
print(inventory)

status, result = client.query(
    "olap_cube", "SELECT unit_id, status FROM gov_terminal LIMIT 20"
)
if status != 200:
    raise RuntimeError(result)
print(result)
```

This is inspection, not campaign registration or training. Run it in the
data-gov environment: the current `app` name is not safe to co-install with
the other repositories. Use the generic executable below from other packages.

## Command-based consumer

Create a spec using your consumer's **absolute** clean checkout path and a
unique campaign key. This illustrative command copies one small delivered
CSV as its output; row counts are mechanics metrics, not model performance.

```json
{
  "schema": "governed_exec_spec.v1",
  "project": "copy-demo",
  "campaign_key": "copy-demo-001",
  "unit_id": "copy-001",
  "classification": "NON_GOVERNING",
  "terminal_lake": "olap_cube",
  "repo_root": "/absolute/path/to/consumer-checkout",
  "config": {},
  "datasets": [
    {"lake": "fixture_files", "resource": "panel.csv", "role": "source"}
  ],
  "command": [
    "python", "-c",
    "import shutil,sys; shutil.copyfile(sys.argv[1],sys.argv[2])",
    "{input:source}", "{out_dir}/copy.csv"
  ],
  "metrics": {"kind": "row_counts", "files": [{"path": "copy.csv", "header": true}]},
  "artifacts": {"copied_data": "copy.csv"}
}
```

Register `fixture_files` and its policies first using the file-lake example.
Keep the spec outside the consumer checkout so that writing the spec does not
change the code identity. Then, from data-gov:

```bash
python tools/governed_exec.py \
  --spec /absolute/path/to/copy-spec.json \
  --gov-url http://127.0.0.1:5055 \
  --api-key-file /absolute/path/to/service-key \
  --out-dir /absolute/path/to/fresh-output \
  --cache-dir /absolute/path/to/cache \
  --outbox-dir /absolute/path/to/outbox
```

The helper registers the campaign, verifies deliveries, writes the effective
config, runs the command, hashes outputs, persists the outcome and reconciles.
For real scientific work, supply the actual training/evaluation command,
metrics, artifact list and `GOVERNING` classification; a clean code identity
and eligible declared inputs are then required. Do not relabel this copy demo
as a scientific experiment.

Inspect output state and pending reports:

```bash
python tools/governed_exec.py --status --outbox-dir /absolute/path/to/outbox
python tools/governed_exec.py --flush \
  --gov-url http://127.0.0.1:5055 \
  --api-key-file /absolute/path/to/service-key \
  --outbox-dir /absolute/path/to/outbox
```

The CLI's status/disposition commands concern that local outbox, not all
outboxes on all machines. Neither a successful command nor an empty outbox
alone substitutes for checking the campaign reconciliation.

## Warehouse inventory and table metadata

The [warehouse service](https://github.com/harveybc/predictor/tree/master/olap/lake)
lists tables **and views** in its configured schema. Its local operator UI
links each resource to a schema page. An adapter-level integration test can
call `GET /api/v1/schema?resource=gov_terminal` with the lake service token;
ordinary experiment clients should use the governance-facing API.

Reflection reports SQL types, nullable columns, primary/foreign keys and
indexes. It cannot infer business meaning, units, measurement quality,
dataset license or the grain of an arbitrary user's table. The warehouse
README documents the known `gov_*` grains; external tables need their own
data dictionary.

## Remote warehouse and DOIN

Deploy the warehouse adapter next to its database or configure its `PGHOST`
to reach that database. Register the adapter URL in data-gov. Changing the
warehouse host does not require changes to experiment model code.

A DOIN ledger-to-OLAP ETL should map ledger outcomes to the same experiment
outcome contract, retain source event identity and send through data-gov.
This paragraph describes the required integration, not a claim that the
current DOIN ETL has already been ported. See the consumer's own reviewed
implementation and tests before treating that path as operational.
