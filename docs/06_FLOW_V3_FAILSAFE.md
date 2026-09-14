# Flow v3: reproducible campaigns, verified deliveries and durable terminals

**Status:** implementation contract. It supersedes Flow v2 for evidence that
can change a scientific or operational decision. Flow v2 remains a legacy,
non-governing compatibility API.

## Requirements

| ID | Requirement |
|---|---|
| GOV-001 | A governing run belongs to a content-addressed campaign registered before data delivery. |
| GOV-002 | Campaign membership, data requests and result destination are fixed by the campaign manifest. |
| GOV-003 | A server-side authorization is not evidence of receipt; the client must confirm the bytes it verified. |
| GOV-004 | A cache reuse is rehashed and recorded separately from a transfer. |
| GOV-005 | Governing temporal cuts use a declared `available_time`, never a guessed column. |
| GOV-006 | Source identity and cuts are stable under path replacement, same-size rewrites and concurrent writers. |
| GOV-007 | Every unit emits exactly one terminal per generation, including failures and inconclusive outcomes. |
| GOV-008 | A terminal is persisted locally before network reporting and survives service interruption. |
| GOV-009 | Metric identity is complete, unique and invariant to input ordering. |
| GOV-010 | Governing code has an exact clean identity; unidentified code is non-governing. |
| GOV-011 | The cube is written only by a configured lake adapter and contains all terminal states. |
| GOV-012 | Reconciliation detects missing cube/accounting records without deleting or inventing evidence. |
| GOV-013 | A governing run uses a fresh output namespace and never overwrites prior scientific artifacts. |

## Use cases

1. One campaign declares one hundred units and three shared datasets. Data is
   transferred once per host and cache reuse is confirmed by digest.
2. A unit outside the manifest, an undeclared resource or an invented set key
   is refused before data is opened.
3. Completed, failed, inconclusive, refused and quarantined units are visible
   in the cube with the same lineage contract.
4. If data-gov or the cube is unavailable after computation, the local terminal
   remains pending and is sent exactly once after recovery.
5. Synthetic work is governed by the generator specification and code digest;
   mechanics-only probes are explicitly non-governing.

## API

### Register campaign

`POST /api/v2/campaigns` with an exact `governed_campaign.v1` body. The actor
comes from the service credential. The response returns `campaign_sha256`.
Registration is automatic when existing policy permits every declared data
request and terminal destination; no person approves individual runs.

### Deliver data

`GET /api/v2/download` requires `X-Campaign-SHA256`, `X-Unit-ID`, and
an exact declared role/resource/range. The response contains `X-Delivery-ID`.
It also carries the delivered/source hashes, available-time column and the
digest of the availability contract used by the lake.
The initial state is `AUTHORIZED`, which grants no lineage.

`POST /api/v2/deliveries/<delivery_id>/confirm` follows client-side hashing and
records `VERIFIED_TRANSFER` or `VERIFIED_CACHE`. Only this state can be cited by
a terminal.

### Report terminal

`POST /api/v2/campaigns/<campaign_sha256>/units/<unit_id>/terminal` accepts an
exact `governed_terminal.v1` body. Metrics may be empty. Dataset lineage is a
list of verified delivery IDs. Data-gov resolves those IDs to their complete
verified delivery evidence and includes that evidence in `terminal_sha256`.
The terminal lake stores the full record transactionally; the accounting
database records the same digest after the lake response.

### Reconcile

`GET /api/v2/campaigns/<campaign_sha256>/reconcile` compares declared units,
accounting terminals and terminal-lake rows. It reports missing and orphaned
identities and never repairs them silently.

## Performance contract

There are no remote calls inside `fit`, `transform`, `step`, `learn`, a batch
or a gradient update. Control operations scale with unique datasets and run
units, not observations. Terminal clients use a durable local outbox and batch
retry; a temporary reporting outage does not change scientific computation.
CSV and Parquet temporal cuts are processed in bounded batches. The data
inventory and holdout configuration are immutable while a lake service is
running, and the cut-materializer identity is part of the cut path.

## Role of repositories

- `financial-data` owns source bytes, temporal contracts, licenses and cuts.
- `preprocessor` and `feature-eng` publish derived artifacts with parent and
  operator identities.
- `predictor`, `agent-multi` and strategy runners consume receipts and produce
  terminals. Their governing runners use fresh output directories; a mechanics
  run outside this path cannot promote a scientific result.
- DOIN converts ledger events into the same terminal schema.
- data-gov authorizes and records; its configured adapter writes the local or
  remote OLAP. Experiments never receive database write credentials.
