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

### Offline study admission

`data_gov.calendar_study_admission.admit_study(snapshot, as_of, requirements)`
evaluates a **full-row**, all-revisions registry snapshot without opening the
registry directory or any source file. The CLI accepts the same JSON and exits
`0` on `ADMITTED_OFFLINE`, `2` on a named refusal:

```bash
python -m data_gov.resource_registration --registry var/registry --json > /tmp/calendar-registry-snapshot.json
python -m data_gov.calendar_study_admission \
  --snapshot /tmp/calendar-registry-snapshot.json \
  --as-of 2026-09-26T12:00:00Z \
  --requirements /tmp/calendar-study-requirements.json
```

The requirements file is structured JSON, for example:

```json
{
  "resources": [
    {"lake": "financial_files", "resource": "economic_calendar/release_actuals/fxmacrodata/announcements.parquet", "row_sha256": "<64-character registry row digest>", "content_sha256": "<64-character measured bytes digest>"}
  ],
  "require_governed_delivery": true,
  "require_consensus_observed_overlap": true
}
```

Supply all study inputs, including a consensus source, in `resources`. Each pin
must match the revision known at the `--as-of` instant. The result includes the
selected revision, receipt time, bytes digest, delivery state, coverage and
each consensus/observed coverage comparison. Named refusals include
`RESOURCE_NOT_KNOWN_AT_T`, `RESOURCE_REVISION_MISMATCH`,
`GOVERNED_DELIVERY_UNAVAILABLE`, `AVAILABILITY_UNKNOWN`,
`CONSENSUS_SOURCE_MISSING`, `OBSERVED_PUBLICATION_SOURCE_MISSING`,
`COVERAGE_UNKNOWN`, and `NO_CONSENSUS_OBSERVED_OVERLAP`.

This is catalog-only admission. A snapshot must be captured from the trusted
registry and retained with the study; a partial or independently edited snapshot
cannot establish catalog completeness. The row digests detect accidental edits,
not forgery by someone able to replace both rows and pins. `ADMITTED_OFFLINE`
does not verify current source bytes, issue a governed delivery, or identify a
causal effect. Set `require_governed_delivery` or
`require_consensus_observed_overlap` to `false` only when the study explicitly
does not require that property; the decision records the flags supplied.

Traceability for this independent addition: `CAL-ADM-01` (as-of revision and
future exclusion) is tested by `test_future_row_cannot_cure_a_refusal_and_future_only_row_is_absent`;
`CAL-ADM-02` (availability fail-closed) by `test_unknown_availability_and_nonoverlap_are_named`
and `test_unknown_delivery_state_is_not_treated_as_open`; `CAL-ADM-03`
(measured overlap and absent roles) by `test_unknown_coverage_and_missing_role_fail_closed`;
`CAL-ADM-04` (snapshot integrity and read-only CLI) by
`test_tampered_snapshot_cannot_admit` and `test_cli_reads_snapshot_without_writing_it`.

- **No web or HTTP surface.** The registry is read by its CLI and its Python
  API. It is not exposed through `web_plugins/default_web.py`, so the operator
  console does not show it and no consumer can query it over HTTP yet.
- **No enforcement.** Nothing in the delivery path consults the registry: a
  registration records that a resource is closed, it does not close it. The
  closure of the four financial-lake resources is the pre-existing fail-closed
  behaviour of the empty `resource_contracts`, not an effect of registering them.
- **One FRED actuals resource is registered, not twelve.** `cpi_yoy` is
  registered as representative; the eleven sibling resources under
  `economic_calendar/release_actuals/` are not registered, and their absences are
  therefore not catalog facts yet.
