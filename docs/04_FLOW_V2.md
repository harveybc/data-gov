# 04 — Flow v2: download with hash, report metrics, lineage

**Date:** 2026-09-13
**Status:** contract for the v2 endpoints, revised after three independent reviews (simplicity,
bypass, operations). Supersedes the JSON-rows `read` as the way an experiment obtains a dataset;
`read` and `query` stay for small slices and inspection.

## 0. The flow, in the owner's words

1. An agent (predictor, doin, heuristic-strategy, …) identifies itself: service key +
   an **experiment key** (one experiment, or one *set* of experiments that share datasets).
2. If authentication and the policy allow it, the dataset is **downloaded as a file**, and
   data-gov records who, when, which resource, and the **sha256 of the bytes delivered**.
3. The agent uses the file however it wants. Nothing waits for a human.
4. When the experiment ends, the agent **reports its metrics** to data-gov, naming the lake
   that stores them (the OLAP cube is just another lake) and the sha256 of every dataset it
   used. data-gov checks that those bytes were served under that key, records the report and
   writes it into that lake.
5. Any lake can be added the same way: an adapter plus a policy block. Nothing external.

What this buys: no more "which of the hundred files with odd names did this run use", no
silent data changes behind a metric (the hash changes and an event fires), and a cube that
answers "which experiments used exactly these bytes".

## 1. Keys and identities

- `Authorization: Bearer <service key>` identifies the agent (`actor`). Never taken from a body.
- `X-Experiment-Key: <key>` identifies the experiment **or the experiment set**. Downloads made
  under a set key serve every experiment that names that set (`experiment_set_key`) when it
  reports.
- Keys match `^[A-Za-z0-9._:-]{1,128}$` everywhere (header, path, body); anything else is 400.
  Keys are chosen by the agent; data-gov records them, it does not register them in advance.

## 2. Download

```
GET /api/v1/download?lake=<lake_id>&resource=<resource_id>[&from=YYYY-MM-DD&to=YYYY-MM-DD]
Authorization: Bearer <key>
X-Experiment-Key: <experiment or set>
```

**One rule: what is delivered is a file that exists on the lake's disk, and its sha256 is the
identity.** There is no re-serialisation at delivery time.

- Verb for policies: `download`. The policy's `deny_from` (holdout) applies as below.
- **Without a range** the source file is delivered `AS_IS`. Under a lake with `holdout_start`
  this is allowed only when the resource's time coverage ends before the holdout
  (`t_max < holdout_start`, computed by the lake from the time column alone) or when the lake
  block lists the resource under `untimed` (files without a time axis, declared by a human).
  Otherwise 403 `spans holdout: request a range` (or 403 `no time column under holdout`).
- **With a range** `from`/`to` must each match `^\d{4}-\d{2}-\d{2}$` and be calendar days,
  `from <= to`, else 400 `invalid from/to`. The policy denies a range that reaches the holdout
  (`to >= deny_from`), the lake re-checks it against its own `holdout_start`. The lake then
  serves the **cut** `<var>/cuts/<source_sha256>/<from>_<to>.<ext>`, materialised once and
  never rewritten: the same source bytes and the same range give the same file forever, no
  matter which pandas or pyarrow is installed later. A source change gives a new
  `source_sha256` directory, so old cuts stay valid and addressable.
- **Cut semantics.** The cut keeps rows with `from 00:00:00 <= t < (to + 1 day) 00:00:00` on the
  column's **own wall clock**: tz-aware timestamps are compared after dropping the zone, naive
  ones as-is. The time column is `time_columns[resource_id]` from the lake block, else the lake's
  `time_column`, else the first column whose lowercase name is one of `ts,time,date,datetime,
  timestamp` or contains `time` or `date`; the chosen column is reported in `X-Time-Column`.
  After the cut the lake asserts `max(t) < holdout_start` on that column; if it fails the cut is
  discarded and the answer is 403 `holdout`. A cut that removes no rows is not written: the
  source is delivered `AS_IS` (one identity for the same bytes).
- **Cut construction, bounded memory, library independent where possible.**
  CSV: pass one reads only the time column in chunks (pandas, `usecols`) to build a keep-mask;
  pass two streams the raw lines and copies the header plus the kept lines byte for byte, so the
  cut is a byte subset of the source. If the physical line count does not match the parsed row
  count (multi-line quoted records) the answer is 422 `unsupported csv`. Parquet: pyarrow
  filters by row group with pinned writer options (`compression=snappy`, `version=2.6`,
  `use_dictionary=True`, `write_statistics=True`, row group size of the source) and, because the
  file is materialised once, its bytes never depend on a later upgrade. A time column that does
  not parse (mixed formats, epoch integers without a declared `time_unit`) is 422 `unparseable
  time column`.
- **Headers on 200:** `Content-Disposition: attachment; filename="<basename>"`,
  `Content-Length`, `X-Content-SHA256` (sha256 of the body), `X-Source-SHA256` (sha256 of the
  source file on the lake's disk), `X-Delivery` (`AS_IS` | `CUT`), `X-Time-Column` (may be
  empty).
- **Accounting row** (`verb=download`, `decision=allow`): `actor`, `lake_id`, `resource_id`,
  `experiment_key`, `bytes`, `sha256` = the digest **data-gov computed itself** over the bytes it
  delivered, `detail` = JSON `{"from","to","source_sha256","delivery","time_column"}`.
  Denials write a deny row with the reason. Before inserting an allow row data-gov compares
  `source_sha256` with the last allow download of the same `(lake, resource)` (indexed query,
  not a window); a difference raises the `source_changed` event and writes a `verb=event`
  accounting row, so a silently changed file is caught on the next download, thousands of runs
  later or not.
- **Errors:** 400 invalid range / key, 401 bad key, 403 (no experiment key, policy, holdout, spans
  holdout, no time column under holdout), 404 unknown lake or resource, 422 unsupported or
  unparseable file, 503 lake unreachable, lake hash mismatch, or `Retry-After: 30` when the
  download slots are busy.

### Lake side

HTTP lakes (`financial-data/lake` at :5056) expose
`GET /api/v1/download?resource=&from=&to=` with the lake token, same body and headers, same
errors. In-process `files_lake` implements
`download(resource_id, start=None, end=None) -> {"path", "filename", "sha256", "bytes",
"source_sha256", "delivery", "time_column"}` where `path` is the source file or the materialised
cut (both persistent, nothing for the caller to delete). `coverage` reads the time column only
(parquet: that column; CSV: `usecols` in chunks), never the whole frame. `discover` is
stat-only (no content hash).

Operational rules, same on every lake and on data-gov:
- Every intermediate file lives under a configured on-disk directory (`spool_dir`, default
  `<repo>/var/spool/`; cuts under `<repo>/var/cuts/`), never under `tempfile` defaults (`/tmp` is
  tmpfs here). Spool files are unlinked right after opening, so a crash leaves nothing behind;
  the spool directory is swept at process start.
- Bytes are streamed in 1 MiB chunks with the digest updated as they pass; nothing calls
  `.read()` on a whole body.
- Download handlers hold a `BoundedSemaphore(max_downloads)` (default 2); when no slot is free
  they answer 503 with `Retry-After: 30` and the client sleeps and retries.
- Download connections use a connect timeout only (30 s), never a read timeout.
- `source_sha256` is memoised per `(path, size, mtime_ns)` under `<var>/source_sha256.json`.

data-gov never trusts the lake's hash: `http_lake.download` streams the lake's body into a spool
file computing sha256, compares it with the lake's `X-Content-SHA256`, and on mismatch raises
`lake hash mismatch` (deny row, 503).

## 3. Report metrics

```
POST /api/v1/experiments/<experiment_key>/metrics
Authorization: Bearer <key>
Content-Type: application/json           (body at most 16 MiB, else 413)
{
  "lake": "olap_cube",
  "experiment_set_key": "phase1-daily-2026-09",            # optional
  "config_sha256": "…", "code_commit": "…",                 # optional in general, required on the lab path
  "project": "predictor", "phase": "phase_1_daily",         # optional
  "tags": {"plugin": "ann"},                                 # optional map of strings
  "datasets": [ {"lake": "predictor_examples", "resource": "phase_1/normalized_d4.csv",
                 "sha256": "<X-Content-SHA256 of the download>", "role": "x_train_file"} ],
  "metrics":  [ {"metric": "MAE", "value": 0.0065,
                 "split": "train", "horizon": 24, "std_dev": 0.0007, "min_value": 0.0055,
                 "max_value": 0.0071, "unit": null} ]
}
```

- Verb for policies: `write_metrics` on the target lake. The key is in the path; `actor` comes
  from the bearer key.
- **Validation (400):** `lake` known; `metrics` non-empty; each metric has `metric` (string) and
  `value` (number or null); `split`, `horizon`, `std_dev`, `min_value`, `max_value`, `unit`,
  `role` are optional and passed through; `datasets` entries have `lake`, `resource`, a 64-hex
  `sha256` and optional `role`; duplicates by `(lake, resource, sha256, role)` are collapsed, not
  an error. Numbers are normalised before anything else: `horizon` → int, the value fields →
  float, and a non-finite value (`NaN`, `Infinity`) is 400 `non-finite value`.
- **Lineage check.** For each dataset data-gov looks for an `allow` row in its own accounting
  with `verb=download`, the same `lake_id`, `resource_id` and `sha256`, the same `actor`, and
  `experiment_key` equal to the report's key or its `experiment_set_key`. Found → `VERIFIED`
  with the matched `event_id`; else `UNVERIFIED` with a reason (`not served under this key or
  set`, `different actor`, `hash seen for another resource`, `never served`). The report's
  `lineage` is `VERIFIED` only when it has at least one dataset and every dataset is VERIFIED.
- **Policy attribute `require_lineage`** (default `true` for `olap_cube`, `false` for the lab
  lake): when true, an UNVERIFIED report is answered 422 `unverified lineage` with a deny row and
  nothing is stored. When false the report is stored with `lineage=UNVERIFIED` and the
  accounting row carries the warning `unverified dataset lineage`.
- **Report identity.** `report_sha256` = sha256 of `json.dumps(body, sort_keys=True,
  separators=(",", ":"), ensure_ascii=True, allow_nan=False)` where `body` =
  `{experiment_key, experiment_set_key (or null), actor, lake, config_sha256 (or null),
  code_commit (or null), project (or null), phase (or null), tags (or {}),
  datasets sorted by (lake, resource, sha256, role) without lineage fields,
  metrics sorted by (metric, split or "", horizon or -1)}`. The receipt time and the lineage are
  stored but not hashed, so re-posting the same report is idempotent. The lake recomputes
  `report_sha256` from the body it stores and rejects a mismatch with 400.
- **Order and failure.** Lake write first, then one accounting row (`verb=write_metrics`,
  `resource_id=experiment/<key>`, `sha256=report_sha256`, `bytes` = canonical size, `detail` =
  the canonical report JSON with lineage and `stored`/`already_stored`): `allow` when the lake
  answers stored or already stored; on lake failure a deny row with warning `lake unreachable`
  and 503 (the retry is idempotent).
- **Response** `201` (stored) or `200` (already stored, returning the lineage stored the first
  time): `{"report_sha256", "stored", "already_stored", "lineage", "datasets": [{lake, resource,
  sha256, role, lineage, reason, event_id}]}`.

### Lake side

Adapters implement `write_metrics(report) -> {"stored", "already_stored", "lineage"}`. HTTP lakes
expose `POST /api/v1/metrics` (lake token) with the report as body and the same answer;
`http_lake.write_metrics` forwards it. The OLAP lake reads the lake token only from
`DATA_GOV_LAKE_TOKEN` or `DATA_GOV_LAKE_TOKEN_FILE`, never by a relative path into another
checkout.

Storage in a SQL lake (SQLite for the lab, PostgreSQL for the cube). The DDL runs **once at
plugin start, in autocommit**, additive (`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT
EXISTS`, `CREATE OR REPLACE VIEW` / `CREATE VIEW IF NOT EXISTS`), never inside the report
transaction and never touching existing tables:

```
gov_report  (report_sha256 TEXT PRIMARY KEY, experiment_key TEXT NOT NULL,
             experiment_set_key TEXT, actor TEXT NOT NULL, lake_id TEXT NOT NULL,
             received_at TEXT NOT NULL, lineage TEXT NOT NULL,
             config_sha256 TEXT, code_commit TEXT, project TEXT, phase TEXT, tags_json TEXT,
             n_metrics INTEGER NOT NULL, n_datasets INTEGER NOT NULL)
gov_metric  (report_sha256 TEXT NOT NULL, experiment_key TEXT NOT NULL, metric TEXT NOT NULL,
             value DOUBLE PRECISION, split TEXT, horizon INTEGER, std_dev DOUBLE PRECISION,
             min_value DOUBLE PRECISION, max_value DOUBLE PRECISION, unit TEXT)
gov_dataset (report_sha256 TEXT NOT NULL, experiment_key TEXT NOT NULL, lake_id TEXT NOT NULL,
             resource_id TEXT NOT NULL, sha256 TEXT NOT NULL, role TEXT, lineage TEXT NOT NULL,
             reason TEXT, event_id INTEGER, source_sha256 TEXT, range_from TEXT, range_to TEXT,
             delivery TEXT, time_column TEXT)
indexes: gov_metric(report_sha256), gov_dataset(report_sha256), gov_dataset(sha256),
         gov_report(experiment_key, received_at)
view gov_metric_current: the gov_metric rows of the latest report (max received_at) per
         (experiment_key, lake_id)
```
No composite primary keys with nullable columns (PostgreSQL forbids NULL in a key; SQLite then
fails to deduplicate). One transaction per report: `INSERT INTO gov_report … ON CONFLICT
(report_sha256) DO NOTHING`; rowcount 0 → `already_stored` (return the stored lineage) and nothing
else is written; else insert `gov_metric` and `gov_dataset` in the same transaction. The
`source_sha256`, range, delivery and time column of a VERIFIED dataset are copied from the
matched accounting row, so the cube alone answers "which metrics are tainted if this source file
was wrong". SQLite lakes open with `timeout=30` and `PRAGMA journal_mode=WAL`, and create the
file if missing.

**One truth.** For a governed experiment `gov_metric` (and `gov_metric_current`) is the record;
the legacy predictor ETL (`fact_performance`) is not run for governed experiment keys. The OLAP
lake service is SELECT-only for `query` and append-only on `gov_*` through `write_metrics`; those
are separate methods, the SQL guard is untouched. The connection for writes may use a dedicated
role (`PGUSER_WRITE` / `PGPASSWORD_WRITE`, defaulting to `PGUSER` / `PGPASSWORD`); a role with
`INSERT` on `gov_*` only is the recommended hardening and needs one `GRANT`.

## 4. Lineage queries

```
GET /api/v1/experiments/<key>/usage?limit=1000&before_id=   accounting rows of that key
GET /api/v1/datasets/<sha256>/usage?limit=1000&before_id=   allow downloads of those exact bytes
```
Both need a service key. `usage(K)` is exactly `WHERE experiment_key = K`; downloads made under a
set appear under `usage(S)`. The `write_metrics` row's `detail` holds the canonical report, so
"what did this experiment report" is answered by data-gov alone. In the cube, `gov_dataset` joins
metrics to dataset and source hashes: "which experiments trained on these bytes" and "which
metrics are tainted if this source was wrong" are one SQL each.

## 5. Events

`source_changed` fires inline at download time (see §2). The existing windowed
`scan_accounting` in the role dispatcher is replaced by that indexed comparison. Zero events when
nothing changes, zero Hermes calls.

## 6. Client

`app.client.DataGovClient` gains:

```python
gov.download(lake, resource, dest_dir, start=None, end=None)
    # -> (status, info); streams to dest_dir/<sha256><ext>.part, verifies X-Content-SHA256,
    #    renames on success, deletes on mismatch (never returns a path it did not verify);
    #    a cache hit is re-hashed before use; retries on 503 Retry-After
gov.report_metrics(experiment_key, lake, metrics, datasets, experiment_set_key=None,
                   config_sha256=None, code_commit=None, project=None, phase=None, tags=None)
gov.dataset_usage(sha256, limit=1000)
```
The API key comes from `DATA_GOV_API_KEY` or a key file the caller names; never from git.

## 7. Policies and lakes in `examples/config/default.json`

- `financial_files`: verbs `discover, coverage, read, download`, `deny_from 2025-01-01`.
- `olap_cube`: verbs `discover, query, write_metrics`, `require_lineage: true`.
- `predictor_examples` (new, in-process `files_lake` over `../predictor/examples/data_downsampled`,
  `time_column: DATE_TIME`, `holdout_start 2025-01-01`): verbs `discover, coverage, read,
  download`, `deny_from 2025-01-01`. This is the third lake: adding one is a config block.
- Startup check: a lake with `holdout_start` whose policies lack `deny_from` refuses to start.

`read` keeps its JSON shape but adopts the same day-only range validation and the strict `<`
upper bound (the old `<= to + 1 day` delivered the first holdout midnight row).

## 8. Predictor lab path (replaces the JSON `read` in G5)

`predictor/tools/governed_run.py --load_config <cfg> --experiment-key K [--experiment-set-key S]
--gov-url http://127.0.0.1:5055 --api-key-file <file> --lake predictor_examples
--lake-root examples/data_downsampled --metrics-lake olap_cube --out-dir <dir>
[--cache-dir <dir>] [--from YYYY-MM-DD --to YYYY-MM-DD] -- <extra --flags>`

1. For every **distinct** path among the six input keys (`x_train_file, y_train_file,
   x_validation_file, y_validation_file, x_test_file, y_test_file`) map it to a lake resource
   (relative to `--lake-root`), download it (no range unless `--from/--to` are given), verify
   the hash, cache as `<cache>/<lake>/<sha256><ext>`.
2. Write `<out-dir>/governed_config.json`: inputs point at the cached files; `results_file`,
   `output_file`, `uncertainties_file`, every `*_plot_file`, `save_model`, `save_config` and
   `save_log` point under `<out-dir>`, so committed samples are never overwritten.
3. Run `app/main.py --load_config <out-dir>/governed_config.json <extra>` with
   `CUDA_VISIBLE_DEVICES=""`.
4. `config_sha256` is computed **after the run** from the effective config predictor wrote to
   `save_config`, canonicalised: the six input keys replaced by `gov:<lake>/<resource>@<sha256>`,
   output paths by their basenames, `save_config`/`save_log` dropped, then compact sorted JSON
   (the canonical text is kept in `GOVERNED_RUN.json`). `code_commit` is `git rev-parse HEAD`,
   suffixed `-dirty` when `git status --porcelain` is non-empty.
5. Parse the results CSV (`Train MAE H24` → metric `MAE`, split `train`, horizon 24, value =
   Average, std/min/max) and report with the datasets (each with `role` = its config key),
   `config_sha256` and `code_commit`.
6. Write `<out-dir>/GOVERNED_RUN.json` with everything above and the report receipt.

Definition of done: a toy CPU run leaves `download` rows with sha256 under its key, a
`write_metrics` allow row whose detail is the report, and in the cube a `gov_report` row with
`lineage = VERIFIED` whose `gov_dataset` rows cite those sha256 with their `event_id`.

## 9. Known limits, on purpose

- The lake token is readable by the user that runs the agents on this machine, so an agent
  could post to a lake service directly. The lake recomputes `report_sha256`, so a forged body
  must be self-consistent, but nothing yet reconciles cube reports against accounting; that is
  the next event to add (`orphan_report`).
- Any service key may query any experiment's usage. The agents are one trusted team today.
- The client cache is unbounded; clean it by hand or with `--cache-max-bytes` later.
