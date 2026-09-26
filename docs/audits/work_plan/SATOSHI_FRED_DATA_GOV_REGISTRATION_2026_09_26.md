# Nine sibling FRED resources registered — and the count in the order was eleven

Satoshi, successor technical lead. 2026-09-26. Acting on the owner's grant of 2026-09-26.
Repository `data-gov`, worktree `data-gov-defrep-20260926`, branch `satoshi/defect-repairs-and-fred-20260926`,
from `7eec868` (*Register the five economic-calendar resources, absences included*).
Evidence: [`docs/audits/evidence/FRED_REGISTRATION_2026_09_26/`](../evidence/FRED_REGISTRATION_2026_09_26/).
Items 1 and 2 of the same order are in `predictor`:
`docs/audits/work_plan/SATOSHI_DEFECT_REPAIRS_2026_09_26.md`.

> ## READ THIS HEADER OR READ NOTHING
>
> **Nine of the eleven registered, because there are nine — not because two failed.** The order, and my own
> return of 2026-09-26 that it quotes, say eleven sibling FRED resources are unregistered.
> `economic_calendar/release_actuals/` holds **eleven directories**, of which **ten are FRED** (`fxmacrodata` is
> FXMacroData and was registered separately on 2026-09-26) and one of those ten is `cpi_yoy`, already registered.
> So **nine** were unregistered and **nine are registered now**: 9 `STORED`, 0 refused, 63 absences that are
> catalog facts for the first time. Nothing was stubbed to reach eleven, and no resource was invented to fill
> the count. The earlier return's "twelve directories, the other eleven" was a miscount of the same directory,
> corrected here from `find` and from each `provenance.json`'s own `source` field.
>
> **Registering granted nothing.** All nine are `execution_authorized: false`,
> `governed_delivery: CLOSED_NO_AVAILABILITY_CONTRACT`. No contract was installed, no policy changed, no service
> started, stopped or restarted.
>
> **Nothing of the five calendar slots moved.** The catalog now holds 23 rows over 14 slots,
> `replay → ALL_ROWS_RE_DERIVED`, 0 quarantined. The evidence collector refuses to write if any of the five
> calendar slots' facts digests has changed; it wrote.
>
> **Re-reading is a `DUPLICATE`.** The same nine resources read again 15 seconds later: 9 `DUPLICATE`, 0
> registered, and the row count on disk unchanged. The receipt clock is outside the key.

## 1. What was ordered and what was done

| file | what it does |
|---|---|
| `tools/inventory_fred_release_actuals.py` | **new.** Measures each FRED release-actuals resource into `data_gov.fred_release_actuals_inventory.v1`, in the record shape the existing registrar already consumes |
| `tools/register_calendar_resources.py` | the same registrar, extended: nine declarations, a `--family` selector, two checks on what is typed in, and a `NO_PAIR_TO_COMPARE` reading that does not borrow the calendar rows' finding |
| `docs/08_RESOURCE_REGISTRY.md` | the nine-row table, how the facts were derived, what stays unknown and why; the stale "one, not twelve" bullet struck through and corrected |
| `tests/user/test_register_fred_release_actuals.py` | **new, 18 tests**: the registration, every refusal, and what the live run produced |
| `docs/audits/evidence/FRED_REGISTRATION_2026_09_26/` | the inventory artifact, the two run reports (redacted, originals' digests recorded), the nine rows in force, and `collect_evidence.py` that produces them |

**No second registrar was written.** `tools/register_calendar_resources.py` is the only thing that appends a
registration; the new tool only *measures*.

## 2. The nine registrations (in force at 2026-09-26T23:59:59Z)

| resource `economic_calendar/release_actuals/<slug>/actuals.parquet` | FRED series | rows | `date` window | absences | `facts_sha256` |
|---|---|---|---|---|---|
| `core_cpi_yoy` | `CPILFESL` | 432 | 1990-01-01 → 2025-12-01 | 7 | `fee3cc9b815a67be` |
| `core_pce_yoy` | `PCEPILFE` | 432 | 1990-01-01 → 2025-12-01 | 7 | `d94a6e41cefa7008` |
| `fed_funds` | `FEDFUNDS` | 432 | 1990-01-01 → 2025-12-01 | 7 | `8a0296972a16b4ac` |
| `gdp_qoq_annualized` | `A191RL1Q225SBEA` | 144 | 1990-01-01 → 2025-10-01 | 7 | `14721c01cd710c71` |
| `initial_claims` | `ICSA` | 1 878 | 1990-01-06 → 2025-12-27 | 7 | `669a62facdc24d23` |
| `nonfarm_payrolls_mom` | `PAYEMS` | 432 | 1990-01-01 → 2025-12-01 | 7 | `b936c0285a328e61` |
| `retail_sales_mom` | `RSAFS` | 408 | 1992-01-01 → 2025-12-01 | 7 | `ad8d0ebd1f9341a7` |
| `treasury_10y` | `DGS10` | 9 393 | 1990-01-01 → 2025-12-31 | 7 | `762315a06c9e018b` |
| `unemployment_rate` | `UNRATE` | 432 | 1990-01-01 → 2025-12-01 | 7 | `90af8a8b5f10f126` |

All nine: lake `financial_files`, `publication_instant.kind = NO_PUBLICATION_INSTANT_OF_ANY_KIND`,
`observed: false`, `study_refusal = NEITHER_CONSENSUS_NOR_OBSERVED_PUBLICATION`,
`governed_delivery = CLOSED_NO_AVAILABILITY_CONTRACT`, `execution_authorized: false`.

**63 absences, seven per resource, from seven codes** — the same seven the registered `cpi_yoy` row carries:
`NO_PUBLICATION_CLOCK`, `CONSENSUS_COLUMN_PRESENT_BUT_EMPTY`, `NO_REVISION_HISTORY`,
`NO_PER_ROW_RECEIPT_CLOCK`, `NO_CANCELLATION_STATE`, `NO_TIMEZONE_DECLARED_BY_THE_SOURCE`,
`NO_AVAILABILITY_CONTRACT`. Every one carries a `why` naming what was looked for; the registry refuses an
absence without one, and refuses a registration whose absence list is empty
(`NO_ABSENCES_DECLARED`) — that is why seven is a measured number and not a formality.

## 3. Derived, not asserted — and the two things that are typed in are checked

`tools/inventory_fred_release_actuals.py` measures, per file: the bytes and their digest, the row count, every
column's non-null and null counts with its value types, the pandas dtypes, the `date` span and whether its values
are timezone-aware, the unit column's distinct values, the vintage key ladder and its verdict, the series and
event identity the rows carry, and whether the `provenance.json` declared digest is these bytes.

Exactly **two** things per resource are typed in, and neither is trusted:

| typed in | checked by | refusal if it is wrong |
|---|---|---|
| the sentence attributed to the resource's `README.md` | the registrar reads that file and requires the sentence verbatim | `QUOTE_NOT_FOUND_IN_THE_DOCUMENTATION` |
| the FRED series id | the registrar compares it with the series the rows carry | `DECLARED_SERIES_DOES_NOT_MATCH_THE_BYTES` |

The role *assignment* (which column plays which catalog role) is declared once in the producer. The
*blocked-case list* of each role is the upstream `m5phet.calendar_inventory.v1` record of `cpi_yoy`, and it is
carried over **only** to a file whose column set is measured identical to that sibling's. A file whose columns
differ is recorded `REFUSED_COLUMN_SET_DIFFERS_FROM_THE_MEASURED_SIBLING` and the registrar then refuses it
`INVENTORY_STATUS_REFUSED_COLUMN_SET_DIFFERS_FROM_THE_MEASURED_SIBLING` rather than registering guessed roles.
A `provenance.json` that declares other bytes is `REFUSED_PROVENANCE_DIGEST_MISMATCH` and never contributes an
acquisition clock. Both refusals are exercised by tests.

**Parity with the upstream producer, on the real bytes.** Run on `cpi_yoy` with `--include-reference`, the new
tool reproduces the retained `m5phet.calendar_inventory.v1` record **exactly** — digest, bytes, rows, every
column's counts and types, dtypes, units, the vintage key ladder and verdict, and all thirteen roles with their
case lists: **zero differences** on every field the registrar consumes. That is the licence for carrying the
case lists over, and it is a test (`…reproduces_the_upstream_record_of_the_sibling_it_carries_roles_from`),
skipped where the financial-data root is absent.

All the non-negotiable properties of the 2026-09-26 registration are the same code and were exercised again:
bytes that moved since the inventory are refused `BYTES_MOVED_SINCE_THE_INVENTORY` rather than registered from
stale facts; `observed: true` is accepted only with `OBSERVED_ACTUAL_PUBLICATION`
(`PUBLICATION_OBSERVED_CONTRADICTS_KIND` otherwise); the receipt clock stays outside the key so a re-read is a
`DUPLICATE`; `ROW_REWRITE_REFUSED` stands; `execution_authorized` is forced `false` by the registry, not by the
caller.

## 4. What is registered as UNKNOWN, with the reason

| field | value | the reason, registered with it |
|---|---|---|
| `clock.evidence` | `UNKNOWN`, `eras_status: NOT_MEASURED` | `date` is the **reference period** of the value, naive in the bytes, with no producer statement of zone. It is not a release wall clock, so there is no publication convention to measure it against and no era measurement exists or could be made. The catalog declares no zone rather than reading it as UTC, and declares `NO_TIMEZONE_DECLARED_BY_THE_SOURCE` |
| `governed_delivery.inventoried` | `UNKNOWN`, `NO_LAKE_INVENTORY_SNAPSHOT_GIVEN` | the snapshot the calendar run measured `inventoried` from is not retained anywhere in this repository, and a fresh filesystem walk by this tool would show that a path exists — not that the deployed lake walked it. Pass `--lake-inventory <snapshot>` and the field becomes a measurement whose digest the row records |
| `carries.consensus_overlap_…` | `NO_PAIR_TO_COMPARE` | these nine carry no consensus and no observed publication instant, so no window comparison was made. Its reading says exactly that, and explicitly does **not** repeat the calendar rows' "the windows do not touch": asserting a comparison that was not made is how a finding gets laundered into rows that did not earn it |

Consensus status itself is **not** unknown: it is an absence with a measurement behind it. The
`consensus_estimate` column exists in all nine and is null in every row (0 of 432, 0 of 9 393, …), which is
`CONSENSUS_COLUMN_PRESENT_BUT_EMPTY` — a different fact from a missing column, and the reason each row also
refuses the event study by name.

## 5. What is NOT done, refused, or not measured

- **`inventoried` is UNKNOWN for all nine**, per §4. Closing it needs a snapshot from the deployed lake's own
  inventory; I did not produce one, because a walk I perform is not evidence of what the lake walked.
- **`measurement_provenance.reading` still says "the era offsets come from the clock artifact"** on rows whose
  `clock_artifact` is `null`. It is the existing tool's shared sentence. Rewording it would change the facts
  digest of four calendar rows whose facts did not change, i.e. manufacture revisions — the exact thing the
  receipt-clock discipline exists to prevent. Left as it is, on purpose, and named here instead.
- **`--clock` is still required** although these nine have no measured clock. The file passed is the redacted
  clock artifact committed in this repository; its digest is recorded in the run report and it contributed
  nothing to any fact (`clock_artifact: null` in all nine rows).
- **The ~150 FRED series under `macro_economic/fred/` are not registered.** They are a different family, no
  measurement of them exists here, and the order's "siblings" are the siblings of the registered `cpi_yoy`.
- **Nothing is enforced by this.** No delivery path consults the registry. Registering a resource records what
  is known and missing about it; the closure of these nine is the pre-existing fail-closed behaviour of the empty
  `resource_contracts`, not an effect of registering them.
- **No row was rewritten, and none of my own rows was deleted** to make the record tidier.
- **No service was started, stopped or restarted**, and no host name, address, token or account identifier was
  written into this repository: the committed evidence is redacted to `<repos>`/`<home>` with each original's
  digest recorded, and a grep for the home-directory prefix over it returns nothing.

## 6. Report

```
FREDREG — item 3 of the 2026-09-26 defect order
repo/branch: data-gov / satoshi/defect-repairs-and-fred-20260926 (from 7eec868)
files: tools/inventory_fred_release_actuals.py (new)
       tools/register_calendar_resources.py (nine declarations, --family, quote + series checks,
                                             NO_PAIR_TO_COMPARE reading)
       tests/user/test_register_fred_release_actuals.py (18 new)
       docs/08_RESOURCE_REGISTRY.md (the nine-row section; the stale "one, not twelve" bullet corrected)
       docs/audits/evidence/FRED_REGISTRATION_2026_09_26/{collect_evidence.py,
                                             fred_release_actuals_inventory.v1.json (4617b6fec0e6751f),
                                             registration_run.v1.redacted.json (original f8359600769c9ac9),
                                             reread_is_a_duplicate.v1.redacted.json,
                                             registrations.v1.json (7924425be3fb51c3)}
       docs/audits/work_plan/SATOSHI_FRED_DATA_GOV_REGISTRATION_2026_09_26.md
suites: data-gov full `python3 -m pytest tests -q` 226 passed / 1 skipped / 0 failed in 559 s
        (209/0 before this work; +18 new, and the skip is not mine to claim either way -- it was observed, not
        investigated) · test_register_fred_release_actuals 18/0 · test_resource_registration +
        test_register_calendar_resources 36/0, unchanged by the extension: the default --family still registers
        exactly the five
acceptance: 9 of 9 registered, 0 refused — NINE is the number of unregistered FRED siblings that exist
        (eleven directories under release_actuals, ten of them FRED, cpi_yoy among those ten already
        registered); 63 absences from 7 codes, each with a reason; catalog 23 rows / 14 slots /
        replay ALL_ROWS_RE_DERIVED / 0 quarantined; re-read 15 s later = 9 DUPLICATE, 0 registered, row count
        unchanged; the five calendar slots' facts digests unchanged (the collector refuses to write otherwise);
        every row execution_authorized false; producer/upstream parity on cpi_yoy = 0 differences
what is NOT done / refused / not measured: inventoried UNKNOWN for all nine (no retained lake snapshot; my own
        walk is not the lake's) · clock UNKNOWN with the reason, never read as UTC · the shared
        measurement_provenance sentence left inaccurate rather than revise four untouched calendar rows ·
        macro_economic/fred (~150 series) not registered, different family, unmeasured · no enforcement, no
        contract installed, no row rewritten, no service touched
```
