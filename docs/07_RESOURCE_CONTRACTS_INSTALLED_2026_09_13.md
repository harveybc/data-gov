# Resource contracts installed for Flow v3 (P0.2, 2026-09-13)

**Status:** factual inventory. Every `resource_contracts` entry below is derived
from physical bytes across the producer chain, never from a column's name.
Resources without an entry stay closed (`resource availability contract
required`); that is a factual deficit, not a request for authorisation.

Order: `predictor/docs/handoffs/MUSASHI_TO_GENERAL_SATOSHI_FLOW_V3_ADOPTION_ORDER_2026_09_13.md`, P0.2.

## 1. Campaigns that consume data now

The only campaign authorised by the order is the governed CPU micro-run of the
predictor toy configuration (`examples/config/phase_1_daily/phase_1_ann_1575_1d_config.json`,
whose six input keys point at three files). D3–D5, selection, models, RL, DOIN
and live remain closed until Musashi's separate decision
(`predictor/docs/integracion_workplan_2026_09_10/06_ESTADO_REAL_PREPROCESAMIENTO_Y_SECUENCIA_2026_09_12.md`, §7),
so no financial-lake resource is consumed by a campaign today.

| lake | resource | role(s) in the campaign | contract |
|---|---|---|---|
| `predictor_examples` | `phase_1/normalized_d4.csv` | `x_train_file`, `y_train_file` | installed |
| `predictor_examples` | `phase_1/normalized_d5.csv` | `x_validation_file`, `y_validation_file` | installed |
| `predictor_examples` | `phase_1/normalized_d6.csv` | `x_test_file`, `y_test_file` | installed |

## 2. Installed entries (`examples/config/default.json`, lake `predictor_examples`)

Identical for the three resources; canonical digest
`5a521473f80a65ef` … (full value in `p02_validate_contracts.n2.out`;
`sha256(json.dumps(contract, sort_keys=True, separators=(",", ":")))`, the value
the lake returns in `X-Availability-Contract-SHA256`). The first installation
(`4d0ead37…`, without the `availability` block) is superseded by GOV-N2.

```json
{"event_time_column": "DATE_TIME", "available_time_column": "DATE_TIME",
 "timezone": "NAIVE_WALL_CLOCK", "time_unit": null, "frequency": "4h",
 "availability": {"label": "WINDOW_END", "completion_lag_max": "1h",
                  "timezone_evidence": "UNKNOWN", "use_class": "OFFLINE_DAY_GRANULAR"}}
```

The `availability` block (GOV-N2) publishes four facts separately, and the lake
executes them rather than noting them:

| fact | value here | executed as |
|---|---|---|
| what the available-time label denotes | `WINDOW_END` — the 4h value at `t` is the mean of hourly rows in `(t−4h, t]` (§3) | header `X-Availability-Label` |
| completion bound | `1h` — the information is complete no later than `t + 1h` (hourly label semantics unproven) | a day cut keeps a row only when `t + 1h < range end`; an AS_IS delivery under holdout needs `max(t) + 1h < holdout`; header `X-Availability-Completion-Lag-Max` |
| time-zone evidence | `UNKNOWN` (no producer statement; `NAIVE_WALL_CLOCK`) | header `X-Timezone-Evidence` |
| use class | `OFFLINE_DAY_GRANULAR` — ranges are calendar days, never intrabar (`from`/`to` with a time part are refused) | header `X-Availability-Use`; consumers record it per input and tag the terminal `availability_use` |

`LIVE_EQUIVALENT` is only accepted with a known label, zero completion lag and
a producer time-zone statement — none of the three resources qualifies. Tests:
`data-gov/tests/unit/test_availability_scope.py` (scope validation, completion
lag excluding a 23:30 row from the cut of its day, interval extremes, incomplete
days, intrabar refusal, train/calibration/confirmation partitions with an altered
future observation leaving every cut byte-identical, holdout with the bound) and
`financial-data/lake/tests/test_availability_scope_v2.py` (same rule and headers
on the financial lake).

Physical identity of the resources (bytes served `AS_IS`):

| resource | sha256 | bytes | rows | span (wall clock) |
|---|---|---|---|---|
| `phase_1/normalized_d4.csv` | `6412c3cdc42942be2a4de5ba893682a33cfe63613f63ab94571a1ead523c18ee` | 247,741 | 6,298 | 2012-10-16 20:00 → 2017-09-20 00:00 |
| `phase_1/normalized_d5.csv` | `42181b0e995d733642bbcb3ffd30eeae7ef912bea29dee1b3fb8573ef9ab7372` | 62,823 | 1,584 | 2017-09-20 04:00 → 2018-12-14 00:00 |
| `phase_1/normalized_d6.csv` | `439fdd1b2611806983364ef447210109fb055852acc67dd6ea1f2459f7fc25cb` | 69,257 | 1,737 | 2018-12-14 04:00 → 2020-04-29 20:00 |

Columns: `DATE_TIME,typical_price`. Grid: hours {0,4,8,12,16,20}; weekday
session Monday 08:00 → Friday 12:00 (the producer's market-gap margin trims
bars around weekends); no duplicates; monotonic.

## 3. Derivation (reviewable, from bytes)

Tool: `predictor/docs/audits/evidence/repro_runs/flow_v3_tools/p02_contract_derivation.py`
(code sha256 `d3dc273c1e733be915f9f885e4190fe318d58e6f470e9540235bf2bc42173414`),
output `p02_contract_derivation.out`
(`51c69f0ab5ffb655296198c57b44922e402fe1036495407c900f7f2122dc31b3`).

Producer chain established by exact value comparison:

1. `predictor/examples/data_downsampled/phase_1/base_d{4,5,6}.csv` are
   **byte-identical** to `preprocessor/examples/data_downsampled/phase_1b/base_d{4,5,6}.csv`
   (the committed output of `preprocessor` `plugin_default` over
   `examples/data/phase_3b_downsampled.csv`, per `preprocessor/README.md`).
   `normalized_d*.csv` in predictor carry the same `DATE_TIME` values as
   `base_d*` (6,298/6,298 for d4) and were stripped to `DATE_TIME,typical_price`
   in predictor commit `f1bb65d`.
2. `base_d4.typical_price` equals `phase_3b_downsampled.typical_price` at the
   same `DATE_TIME` exactly, 6,298/6,298.
3. `phase_3b_downsampled.csv` (22,521 rows, 4h grid) against
   `phase_3b.csv` (88,084 hourly rows, spacing 3,600 s), same producer
   directory:

   | hypothesis for the value at `DATE_TIME = t` | exact matches |
   |---|---|
   | mean of hourly rows in **(t−4h, t]** (right label) | **21,703 / 22,521**; 21,702 / 21,702 on windows with 4 hourly rows |
   | mean of hourly rows in [t, t+4h) (left label) | 4 |
   | the single hourly row at t (decimation) | 18 |

   The 819 non-matching rows are windows with fewer than four hourly rows
   (gap boundaries), consistent with a partial-window mean.
4. Consequence: the 4h value labelled `t` is built from hourly rows labelled
   `t−3h … t`. Whatever the (unproven) meaning of the hourly label — open or
   close of the hourly bar — the information in the row labelled `t` is
   complete no later than `t + 1h`.
5. The `feature-eng` Dukascopy 4h export (`Gmt time`, hours 1,5,9,…) is **not**
   in this lineage: 0 of 6,298 timestamps coincide. Nothing about GMT is
   therefore inherited by these files; the wall clock of `phase_3b.csv` has no
   producer statement of time zone (session boundaries Friday 20–21h →
   Monday 00h), so `timezone` is declared `NAIVE_WALL_CLOCK`.

## 4. Use class: historical offline, day-granular

`available_time_column = DATE_TIME` is exact for the delivery mechanics of this
lake: governed cuts are day ranges `[from 00:00, to + 1 day 00:00)` on the
available-time column, the last bar of any day is labelled 20:00, and its
information is complete by 21:00 < 24:00. A delivered cut therefore never
contains information that completes after the range end. This was verified on
the bytes (§5, `complete_before_range_end_under_1h_bound = true`).

It is **not** a live-equivalent contract: at bar level the label may precede
completion by up to one hour (the hourly label semantics are unproven). Per
the order, the resources are declared for historical offline use only. Any
live or bar-level use needs a producer statement of the hourly label and time
zone; none exists in `preprocessor`, `feature-eng` or `predictor`.

## 5. Validation against physical bytes before deployment

Tool: `predictor/docs/audits/evidence/repro_runs/flow_v3_tools/p02_validate_contracts.py`
(`867da4a34d40e13a00f5a0d9b9ddd71012cc46c11663e9960aae57e826f97867`), output
`p02_validate_contracts.out` (`78e0b0c5b3d6a93e815215e57733b67a1e4da65f8c04705b95f740c90cd8233c`).
It instantiates `lake_plugins/files_lake.py` with the exact lake entry of the
deployed config (throwaway cuts directory) and checks, per resource:

- `AS_IS` governed download: delivered sha256 = source sha256 = sha256 of the
  file on disk; bytes = file size; contract digest = `4d0ead37…`.
- One-year governed cut: rows equal an independent pandas count, every kept
  row inside `[from, to+1d)`, byte subset of the source, delivered digest
  equals the digest of the streamed bytes, completion bound holds.

| resource | cut | rows | max available time in cut |
|---|---|---|---|
| `normalized_d4.csv` | 2013-01-01..2013-12-31, `7a8eb4b46332bd42…`, 49,751 B | 1,284 = 1,284 | 2013-12-31 08:00 |
| `normalized_d5.csv` | 2018-01-01..2018-12-31, `22cd8c949aaadde1…`, 48,542 B | 1,226 = 1,226 | 2018-12-14 00:00 |
| `normalized_d6.csv` | 2019-01-01..2019-12-31, `f15e488f8a2a2039…`, 50,837 B | 1,274 = 1,274 | 2019-12-31 12:00 |

Result `ok = true`. The other 105 resources of the lake are refused with
`resource availability contract required` (all 105 checked).

## 6. Closed resources (factual deficits, no contract invented)

| lake | resources | deficit |
|---|---|---|
| `predictor_examples` | `phase_1_b/*` (68 predictor configs), `phase_1_c/*`, `phase_2_*`, `phase_3/*`, `phase_1/base_d*` — 105 files | `phase_1_b/c` derive from the Dukascopy 4h export (`feature-eng/generate_phase1{b,c}_labels.py`, column `Gmt time`, bars at 01,05,09,…h): the export's label convention (bar open vs close) and the exact GMT/UTC relation are not stated by a producer document in any checkout. Other phases: lineage not traced. |
| `financial_files` | 5,275 files served; 198 first-batch files (`financial-data/features/census/FINANCIAL_FIRST_BATCH_CONTRACTS.v1.json`, all present in the live inventory); 38 panels (`predictor/docs/audits/evidence/PANEL_INVENTORY.v1.json`) | Every first-batch contract declares `time.timestamp_meaning`, `time.timezone`, `time.availability_rule`, `time.availability_delay_seconds` and each variable's `available_time_rule` as `UNKNOWN`. `resource_contracts` of the financial lake stays `{}` (fail-closed). What is needed per resource: the provider's documented timestamp semantics (e.g. bar open/close, publication lag for macro series) tied to the file's columns, then validation against bytes as in §5. |

No `financial_files` resource is needed until D3 opens; when it does, contracts
are produced resource by resource from provider evidence, not from this table.
