# The resource registry: registering what a resource is, and what it is missing

**Status:** implemented and populated locally on 2026-09-26 with five economic-calendar
resources. Module `data_gov/resource_registration.py`, tool
`tools/register_calendar_resources.py`, tests
`tests/unit/test_resource_registration.py` and
`tests/user/test_register_calendar_resources.py`.

## Why it exists

`docs/00_CONTRATO.md` §5 is fail-closed: *sin fila de inventario no hay
autorización ni accounting de ese recurso*, and *el núcleo no adivina recursos*.
A file that a study reads while the catalog knows nothing about it is an
ungoverned input, whatever the study's own document says about it.

Registering only what a resource **has** is not enough. An economic-calendar
resource answers "what was the surprise in this release, and when did it become
public". One that carries a consensus but no publication instant answers the
first half; one that carries an observed publication instant but no consensus
answers the second. A catalog that lists only their columns makes them look like
halves of one usable dataset, and a study joining them finds out in an analysis
instead of being refused by the catalog. So a registration carries a **named
`absences` list**, and a consumer matches on the codes.

A resource contract (`resource_contracts` in a provider's settings) and a
registration are different objects. A contract is executed by the lake on a
delivery. A registration is a statement of knowledge, including the knowledge
that no contract can honestly be written yet. **Registering grants nothing:**
`execution_authorized` is `False` in every row, and a resource with no
availability contract stays closed.

## The key discipline

Restated from `financial-data/_scripts/lib/point_in_time_store.py`, because a
cross-repository import is not available:

| Rule | Consequence |
|---|---|
| A key is a digest this module computes from what the row is | A supplied name is never a key (`INVALID_ROW_KEY`) |
| `registered_at` is **outside** the key | Re-reading a resource that has not moved is a `DUPLICATE`, not a revision; a catalog sweep manufactures nothing |
| A row is never rewritten | Changed facts are a NEW row with a higher `revision_index`; `write_row` refuses by name, `ROW_REWRITE_REFUSED` |
| `known_at(T)` | Per slot, the last valid row registered no later than T. A later row is **absent**, not merely ranked lower |
| A row is checked against the key it was found under | A whole, self-consistent registration of another resource fails `MISPLACED_ROW` |
| A failed row is quarantined by its own bytes | Never deleted and never re-signed |
| Writes are exclusive creates (`os.link`) | A second writer reads the first one's row back |

Two keys, not one. `catalog_key` says **which** resource (lake + path): the
catalog slot a consumer asks about. `registration_identity` says **what was
registered** about it (`content_sha256`, `facts_sha256`). New bytes under the
same path are a revision of that slot, not a new slot.

## What a registration must carry

Every field in `REQUIRED_FACTS` is required; an omitted field reads as "nothing
to say", and the absences are exactly what must be said.

`what_it_is`, `physical_location`, `coverage`, `clock`, `publication_instant`,
`carries`, `absences`, `governed_delivery`, `measurement_provenance`.

Three vocabularies are closed, and a value outside them is refused rather than
accepted as free text:

- `PUBLICATION_KINDS` — the same words
  `feature_eng_m5phet.events.publication_clock_block` writes into every study
  artifact, so a registration and a study cannot disagree about what "observed"
  means. `observed: true` is accepted only with `OBSERVED_ACTUAL_PUBLICATION`.
- `ABSENCE_CODES` — every absence the catalog can refuse on. A code is added
  here with its meaning before it can be declared.
- `DELIVERY_STATES` — whether data-gov can serve the resource today.

`facts_sha256` is computed from the facts by the registry, never accepted from
the caller, so a row cannot publish a digest of something other than what it
registered. A row whose stored facts are edited on disk fails
`FACTS_DIGEST_MISMATCH` on the next read.

## Reading the registry back

```bash
python -m data_gov.resource_registration --registry var/registry
python -m data_gov.resource_registration --registry var/registry --known-at 2026-09-26T12:00:00Z
python -m data_gov.resource_registration --registry var/registry --replay
```

`var/` is gitignored: the registry is the local instance's own state. The rows
registered on 2026-09-26 are committed for review under
`docs/audits/evidence/CALENDAR_REGISTRATION_2026_09_26/`.

## The five economic-calendar registrations (2026-09-26)

Derived, not declared: every count, role, unit and vintage verdict comes from
`m5phet.calendar_inventory.v1`; the era offsets come from
`m5phet.calendar_clock.v1`; the coverage window and the bytes digest are
re-measured by the tool on the file itself, and a resource whose bytes moved
since the inventory is refused `BYTES_MOVED_SINCE_THE_INVENTORY` rather than
registered from stale facts.

| resource | lake | coverage | publication instant | carries | absences |
|---|---|---|---|---|---|
| `tests/data/economic_calendar_2011_2021.csv` | **none** | 2011-01-01 → 2021-04-26 | `ASSUMED_SCHEDULED_PUBLICATION_LOCALIZED` | consensus (54,291 rows) + actuals | 12 |
| `economic_calendar/release_actuals/fxmacrodata/announcements.parquet` | `financial_files` | 2024-12-12 → 2026-05-01 (UTC) | `OBSERVED_ACTUAL_PUBLICATION` | actuals only (17,961) | 6 |
| `economic_calendar/scheduled_events/fxmacrodata/release_calendar.parquet` | `financial_files` | 2026-02-05 → 2027-07-14 (UTC) | `ASSUMED_SCHEDULED_PUBLICATION` | neither: a schedule | 7 |
| `economic_calendar/scheduled_events/fred_release_date_proxy/scheduled_events.parquet` | `financial_files` | 1996-01-01 → 2025-12-31 | `ASSUMED_SCHEDULED_PUBLICATION` | actuals only (1,192) | 9 |
| `economic_calendar/release_actuals/cpi_yoy/actuals.parquet` | `financial_files` | 1990-01-01 → 2025-12-01 | `NO_PUBLICATION_INSTANT_OF_ANY_KIND` | actuals only (431) | 7 |

Whether the deployed lake actually walks a path is **measured, not inferred from
the include globs**: pass `--lake-inventory <snapshot>` and `inventoried` is
determined from that snapshot, whose digest the row records. Without it the field
is registered `UNKNOWN` with the reason, because a glob says what *would* be
walked. The four `financial_files` paths were confirmed against the deployed
lake's own inventory snapshot (5,275 resources).

Two facts the registrations make catalog facts rather than footnotes in one
study:

1. **The 2011–2021 archive's clock is not UTC.** Its eleven measured eras travel
   into the registration: a fixed `UTC−05:00` all year round to 2018-01 (the
   archive did not move with daylight saving), then America/New_York local time
   from 2018-03, whose alternation between `UTC−04:00` and `UTC−05:00` the eras
   show; three eras are `UNDETERMINED` and rows in them are excluded
   `CLOCK_PERIOD_UNDETERMINED` rather than given a neighbour's offset. The
   registration marks this `MEASURED_FROM_DATA_NOT_DOCUMENTED_BY_THE_SOURCE` and
   declares both `CLOCK_MEASURED_NOT_DOCUMENTED` and
   `NO_TIMEZONE_DECLARED_BY_THE_SOURCE`: measuring a zone is evidence, not
   documentation.
2. **No consensus source overlaps an observed-clock source.** The tool *measures*
   this rather than asserting it, by comparing coverage windows, and attaches the
   verdict to every row so a study is refused on whichever resource it opens
   first. The only consensus is in 2011-01→2021-04 and the only observed
   publication instants are in 2024-12→2026-05: a 1,326-day gap, so no row
   anywhere can carry both. That is why the event studies are `NOT_IDENTIFIED`.

## Named gap: the availability vocabulary cannot express a measured clock

`financial_data_store.inventory.TIMEZONE_EVIDENCE` offers `PRODUCER_STATEMENT`
and `UNKNOWN`. The archive's clock is neither: it is measured from data. No
honest availability contract can be written for it until that vocabulary carries
a measured-from-data value, and declaring `PRODUCER_STATEMENT` would assert a
producer statement that does not exist. The registration declares
`AVAILABILITY_VOCABULARY_CANNOT_EXPRESS_THIS_CLOCK` instead of picking one, and
extending the vocabulary is a change to the provider and the lake, with their
tests — not something a registration may do on its own.

## What is not implemented

- **No web or HTTP surface.** The registry is read by its CLI and its Python
  API. It is not exposed through `web_plugins/default_web.py`, so the operator
  console does not show it and no consumer can query it over HTTP yet.
- **No enforcement.** Nothing in the delivery path consults the registry: a
  registration records that a resource is closed, it does not close it. The
  closure of the four financial-lake resources is the pre-existing fail-closed
  behaviour of the empty `resource_contracts`, not an effect of registering them.
- ~~**One FRED actuals resource is registered, not twelve.**~~ Closed on
  2026-09-26 by the nine sibling registrations below. The count in this bullet was
  wrong: there are **ten** FRED directories under
  `economic_calendar/release_actuals/`, not twelve, and `cpi_yoy` is one of them,
  so **nine** siblings were unregistered rather than eleven. See *The nine sibling
  FRED actuals registrations* below.

## The nine sibling FRED actuals registrations (2026-09-26)

`cpi_yoy` was registered *"as representative of the twelve sibling FRED actual
resources"*. Representative is not measured, and the count was wrong: the
directory holds **eleven** entries, ten of them FRED (`fxmacrodata` is
FXMacroData and was registered separately) and one of those ten is `cpi_yoy`
itself. So **nine** siblings were unregistered, and each one is now registered
from its own bytes rather than from its resemblance to `cpi_yoy`.

| resource (`economic_calendar/release_actuals/<slug>/actuals.parquet`) | FRED series | rows | `date` window | absences | `facts_sha256` |
|---|---|---|---|---|---|
| `core_cpi_yoy` | `CPILFESL` | 432 | 1990-01-01 → 2025-12-01 | 7 | `fee3cc9b815a67be` |
| `core_pce_yoy` | `PCEPILFE` | 432 | 1990-01-01 → 2025-12-01 | 7 | `d94a6e41cefa7008` |
| `fed_funds` | `FEDFUNDS` | 432 | 1990-01-01 → 2025-12-01 | 7 | `8a0296972a16b4ac` |
| `gdp_qoq_annualized` | `A191RL1Q225SBEA` | 144 | 1990-01-01 → 2025-10-01 | 7 | `14721c01cd710c71` |
| `initial_claims` | `ICSA` | 1,878 | 1990-01-06 → 2025-12-27 | 7 | `669a62facdc24d23` |
| `nonfarm_payrolls_mom` | `PAYEMS` | 432 | 1990-01-01 → 2025-12-01 | 7 | `b936c0285a328e61` |
| `retail_sales_mom` | `RSAFS` | 408 | 1992-01-01 → 2025-12-01 | 7 | `ad8d0ebd1f9341a7` |
| `treasury_10y` | `DGS10` | 9,393 | 1990-01-01 → 2025-12-31 | 7 | `762315a06c9e018b` |
| `unemployment_rate` | `UNRATE` | 432 | 1990-01-01 → 2025-12-01 | 7 | `90af8a8b5f10f126` |

All nine are lake `financial_files`, `NO_PUBLICATION_INSTANT_OF_ANY_KIND`,
`observed: false`, `study_refusal: NEITHER_CONSENSUS_NOR_OBSERVED_PUBLICATION`,
`governed_delivery: CLOSED_NO_AVAILABILITY_CONTRACT` and
`execution_authorized: false`. The 63 absences are seven per resource — the same
seven `cpi_yoy` carries — and each one names what was looked for.

**Derived, not asserted.** `tools/inventory_fred_release_actuals.py` measures each
file (digest, rows, every column's non-null count and value types, dtypes, the
`date` span and whether its values are timezone-aware, the unit column, the
vintage key ladder, the series and event the rows carry, and whether the
`provenance.json` digest is these bytes) into
`data_gov.fred_release_actuals_inventory.v1`, which
`tools/register_calendar_resources.py --family fred_release_actuals` then
consumes — the same registrar, not a second one. Only two things per resource are
typed in, and both are checked rather than trusted: the sentence attributed to the
README must appear in that file (`QUOTE_NOT_FOUND_IN_THE_DOCUMENTATION`) and the
declared FRED series must be the series the rows carry
(`DECLARED_SERIES_DOES_NOT_MATCH_THE_BYTES`). Bytes that moved since the
inventory are still refused `BYTES_MOVED_SINCE_THE_INVENTORY`.

**The role assignment is carried over under a measurement, never a resemblance.**
Which column plays which catalog role is declared once in the producer; the
*blocked-case list* of each role is the upstream `m5phet.calendar_inventory.v1`
record of `cpi_yoy`, carried over only to files whose column set is measured
identical to that sibling's. A file whose columns differ is recorded
`REFUSED_COLUMN_SET_DIFFERS_FROM_THE_MEASURED_SIBLING` and the registrar refuses
it `INVENTORY_STATUS_...` rather than registering guessed roles. Run on the real
`cpi_yoy` bytes the producer reproduces the upstream record exactly — digest,
bytes, rows, every column's counts and types, dtypes, units, the vintage key
ladder and verdict, and all thirteen roles with their case lists.

**What stays unknown, with the reason.** `clock.evidence` is `UNKNOWN` and
`eras_status` is `NOT_MEASURED` for all nine: `date` is the reference period of
the value, naive in the bytes, with no producer statement of zone and no release
wall clock to measure it against, so the catalog declares no zone rather than
reading it as UTC, and `NO_TIMEZONE_DECLARED_BY_THE_SOURCE` is declared.
`inventoried` is `UNKNOWN` with `NO_LAKE_INVENTORY_SNAPSHOT_GIVEN`: the snapshot
the calendar run used is not retained anywhere, and a fresh filesystem walk by
this tool would show that a path exists, not that the deployed lake walked it.
`consensus_overlap` is `NO_PAIR_TO_COMPARE`, and its reading says that no window
comparison was made instead of repeating the calendar rows' finding.

**What registering them changed about access: nothing.** The catalog holds 23
rows over 14 slots, `replay → ALL_ROWS_RE_DERIVED`, 0 quarantined. Re-reading the
same nine resources is nine `DUPLICATE`s and writes no row, because the receipt
clock is outside the key. No row of the five calendar slots was rewritten or
revised: the evidence collector refuses to write if any of their facts digests
moved.

**Still not registered.** Everything outside
`economic_calendar/release_actuals/`: the ~150 FRED series under
`macro_economic/fred/` are a different family and no measurement of them exists
here.
