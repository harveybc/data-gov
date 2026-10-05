# Calendar resources registered in the local data-gov, absences included

**Action of the successor technical lead under the owner's grant of 2026-09-26.**
Signed: **Satoshi III (Mujuro Utsutsu)**, successor technical lead. This is not
written under Musashi's name and carries no review of his.

**Date:** 2026-09-26 · **Repository:** `data-gov` · **Branch:**
`satoshi/calendar-data-gov-20260926`

---

## 1. What was ordered and what was done

Five economic-calendar resources were being read by the causal event-study work
and were in no catalog, so every study reading them carried an ungoverned input.
**All five are now registered in the local data-gov**, each with its absences
declared as part of the registration rather than omitted from it.

The registration surface did not exist, so it was built:

| file | what it is |
|---|---|
| `data_gov/resource_registration.py` | the registry: closed vocabularies, validation, an append-only digest-keyed store with the point-in-time key discipline, `known_at(T)`, `replay()`, and a CLI to read it back |
| `tools/register_calendar_resources.py` | derives the five registrations from two measurement artifacts and appends them |
| `tests/unit/test_resource_registration.py` | 19 tests on the discipline |
| `tests/user/test_register_calendar_resources.py` | 17 tests on the derivation, against synthetic fixtures |
| `docs/08_RESOURCE_REGISTRY.md` | the contract, the vocabularies, and the five registrations |
| `README.md` | a section, a table-of-contents entry and a further-reading link |
| `docs/audits/evidence/CALENDAR_REGISTRATION_2026_09_26/` | the two measurement artifacts (redacted) and the registry's own rows |

Live registry state: **14 rows retained, 5 slots, 5 in force**,
`replay → ALL_ROWS_RE_DERIVED`, 0 quarantined.

## 2. The five registrations (in force at 2026-09-26T23:59:59Z)

| resource | lake | coverage (own clock) | publication instant | carries | absences | `facts_sha256` |
|---|---|---|---|---|---|---|
| `tests/data/economic_calendar_2011_2021.csv` | **NOT_IN_ANY_LAKE_ROOT** | 2011-01-01 → 2021-04-26 (calendar date) | `ASSUMED_SCHEDULED_PUBLICATION_LOCALIZED` | consensus 54,291 rows + actual 114,045 | **12** | `dd90c7a468912ccc…` |
| `economic_calendar/release_actuals/fxmacrodata/announcements.parquet` | `financial_files` | 2024-12-12T08:30Z → 2026-05-01T23:36:47Z | **`OBSERVED_ACTUAL_PUBLICATION`** | actual 17,961; no consensus | 6 | `4cab618ee83fa354…` |
| `economic_calendar/scheduled_events/fxmacrodata/release_calendar.parquet` | `financial_files` | 2026-02-05T12:00Z → 2027-07-14T18:00Z | `ASSUMED_SCHEDULED_PUBLICATION` | neither — a forward schedule, 0 values | 7 | `28bba69e2e11144d…` |
| `economic_calendar/scheduled_events/fred_release_date_proxy/scheduled_events.parquet` | `financial_files` | 1996-01-01 → 2025-12-31 (naive) | `ASSUMED_SCHEDULED_PUBLICATION` (a proxy date) | actual 1,192; consensus column empty | 9 | `8827d8a95d047e3a…` |
| `economic_calendar/release_actuals/cpi_yoy/actuals.parquet` | `financial_files` | 1990-01-01 → 2025-12-01 (naive) | **`NO_PUBLICATION_INSTANT_OF_ANY_KIND`** | actual 431; consensus column empty | 7 | `5478ebe8e3ff9edd…` |

Full digests, bytes digests, row digests and the complete facts are in
`docs/audits/evidence/CALENDAR_REGISTRATION_2026_09_26/registrations.v1.json`
(digest `49a7fae8ca5fa30c…` at the time of writing; regenerated whenever the
registry moves).

**41 absences are now catalog facts**, drawn from 16 codes:

`AVAILABILITY_VOCABULARY_CANNOT_EXPRESS_THIS_CLOCK` ·
`CLOCK_ERA_UNDETERMINED` · `CLOCK_MEASURED_NOT_DOCUMENTED` ·
`CONSENSUS_COLUMN_PRESENT_BUT_EMPTY` · `NOT_IN_ANY_LAKE_ROOT` ·
`NO_AVAILABILITY_CONTRACT` · `NO_CANCELLATION_STATE` · `NO_CONSENSUS_AT_ALL` ·
`NO_CONSENSUS_PUBLICATION_CLOCK` · `NO_PER_ROW_RECEIPT_CLOCK` ·
`NO_PROVENANCE_SIDECAR` · `NO_PUBLICATION_CLOCK` · `NO_REFERENCE_PERIOD` ·
`NO_REVISION_HISTORY` · `NO_TIMEZONE_DECLARED_BY_THE_SOURCE` · `NO_UNIT` ·
`REVISION_HISTORY_UNDECIDABLE`

Each absence carries the field it is about, why it was concluded, its meaning
from the closed vocabulary, and the CAL acceptance cases it blocks.

## 3. The two measured facts, as they survive into the registration

### 3.1 The archive's clock is not UTC — and it is measured, not documented

All eleven eras of `m5phet.calendar_clock.v1` travel into the registration's
`clock.eras`, each with its status, offset, estimate count and confidence:

- **2012-05-01 → 2018-01-31: a fixed `UTC−05:00` all year round** (1,183
  estimates, every month unanimous). The archive did not move with daylight
  saving.
- **From 2018-03: America/New_York local time**, whose alternation between
  `UTC−04:00` and `UTC−05:00` the six later determined eras show.
- **2011-01-01 → 2012-04-30, 2018-02 and 2018-09 are `UNDETERMINED`**
  (`ESTIMATES_DISAGREE`). The registration declares
  `CLOCK_ERA_UNDETERMINED` naming all three with their reasons and estimate
  counts; rows inside them are excluded `CLOCK_PERIOD_UNDETERMINED`, never given
  a neighbouring era's offset.

`clock.evidence` is `MEASURED_FROM_DATA_NOT_DOCUMENTED_BY_THE_SOURCE` and
`clock.documented_by_the_source` is `false`. Because measuring a zone is evidence
and not documentation, the registration declares **both**
`CLOCK_MEASURED_NOT_DOCUMENTED` and `NO_TIMEZONE_DECLARED_BY_THE_SOURCE`: a
consumer matching either code finds it. The `reading` on the clock block states
that reading the column as UTC — which every run before 2026-09-25 did — places
every US release four to five hours before it happened, and that correcting it
removed the only two intervals that excluded zero.

Vocabulary is shared with the studies rather than invented: the publication kinds
are the words `feature_eng_m5phet.events.publication_clock_block` already writes
into every artifact, so a registration and a study cannot disagree about what
"observed" means. `observed: true` is accepted **only** with
`OBSERVED_ACTUAL_PUBLICATION`; any other pairing is refused
`PUBLICATION_OBSERVED_CONTRADICTS_KIND`.

### 3.2 No consensus source overlaps an observed-clock source

This is **measured, not asserted**: the tool compares coverage windows.

```
consensus resources ........ tests/data/economic_calendar_2011_2021.csv
observed-clock resources ... economic_calendar/release_actuals/fxmacrodata/announcements.parquet
consensus window ........... 2011-01-01 .. 2021-04-26
observed window ............ 2024-12-12T08:30:00+00:00 .. 2026-05-01T23:36:47+00:00
overlap .................... NONE          gap_days: 1326
verdict .................... NO_CONSENSUS_SOURCE_OVERLAPS_AN_OBSERVED_CLOCK_SOURCE
```

The verdict is attached to **every** registration, so a study is refused on
whichever resource it opens first. Each row also carries a `study_refusal` code:

| resource | `study_refusal` |
|---|---|
| the 2011–2021 archive | `CONSENSUS_WITHOUT_OBSERVED_PUBLICATION_CLOCK` |
| fxmacrodata announcements | `OBSERVED_PUBLICATION_WITHOUT_CONSENSUS` |
| the other three | `NEITHER_CONSENSUS_NOR_OBSERVED_PUBLICATION` |

That is the `NOT_IDENTIFIED` verdict of the event studies, now a property of the
catalog instead of a finding inside one analysis.

## 4. The `known_at(T)` discipline, exercised on the live registry

- `registered_at` is **outside** the key. A re-run over unchanged bytes and
  unchanged facts returned **5 duplicates, 0 written** — verified on the live
  registry, and again on the last run, where the archive came back `DUPLICATE`
  while the four whose `inventoried` field had been measured came back
  `REVISION`.
- **No row was rewritten.** `write_row` refuses by name; the test forges a key
  with different facts and asserts `ROW_REWRITE_REFUSED` and that the bytes on
  disk are unchanged.
- `known_at("2026-09-25T23:59:59Z")` → **0 rows**;
  `known_at("2026-09-26T00:00:01Z")` → **5 rows** at `revision_index 0`;
  `known_at("2026-09-26T23:59:59Z")` → **5 rows** at the current revisions. The
  earlier rows are absent from the earlier answer and retained on disk.
- This action's own trail is three appends per slot, kept rather than cleaned:
  r0 the first write, r1 adding the governance absence codes
  (`NOT_IN_ANY_LAKE_ROOT` / `NO_AVAILABILITY_CONTRACT`) so a consumer
  enumerating `absences` finds them without knowing to look in
  `governed_delivery`, r2 replacing an assumed `inventoried: true` with one
  measured from the deployed lake's inventory snapshot. Deleting my own fresh
  rows to make the record tidier would have been the first violation of the rule
  this registry exists to enforce, so the supersessions stand and are listed in
  the evidence file.

## 5. What is NOT done, refused, or not measured

- **Registering grants nothing, and nothing was opened.** Four resources stay
  `CLOSED_NO_AVAILABILITY_CONTRACT`; the archive is
  `CLOSED_NOT_IN_ANY_LAKE_ROOT`. `execution_authorized` is `false` in all 14
  rows. No availability contract was installed and no policy was changed.
- **No running service was started, stopped or restarted.** The kernel at
  `:5055` and the two store hosts continue with the configuration they had. The
  registry is file-backed under the instance's `var/`, so it needed no restart —
  and equally, the running kernel does not serve it.
- **Named gap — the availability vocabulary cannot express a measured clock.**
  `financial_data_store.inventory.TIMEZONE_EVIDENCE` offers only
  `PRODUCER_STATEMENT` and `UNKNOWN`. The archive's clock is neither. No honest
  availability contract can be written for it until that vocabulary carries a
  measured-from-data value; declaring `PRODUCER_STATEMENT` would assert a
  producer statement that does not exist. The registration declares
  `AVAILABILITY_VOCABULARY_CANNOT_EXPRESS_THIS_CLOCK` instead of choosing one.
  Extending the vocabulary is a change to the provider **and** the lake with
  their tests, and would take effect only on an operator-controlled restart, so
  it was not done here.
- **No HTTP or console surface.** The registry is read by its CLI and its Python
  API. `web_plugins/default_web.py` is untouched, so the operator console does
  not show the registrations and no consumer can query them over HTTP.
- **No enforcement.** Nothing in the delivery path consults the registry. The
  closure of the four financial-lake resources is the pre-existing fail-closed
  behaviour of an empty `resource_contracts`, not an effect of registering them.
  A study that ignores the catalog is still able to read the files.
- **Eleven sibling FRED actuals resources are not registered.** `cpi_yoy` was
  registered as representative of the twelve directories under
  `economic_calendar/release_actuals/`; the other eleven have no registration and
  their absences are therefore not catalog facts. Naming them "the same" without
  measuring each one would be the kind of assumption this work exists to remove.
- **No era measurement exists for the four non-archive resources**, and none was
  invented. Two are timezone-aware in the bytes; the two naive FRED columns are a
  reference period and a derived date, with no publication convention to measure
  them against, so their `clock.evidence` is `UNKNOWN` with
  `eras_status: NOT_MEASURED` and the absence
  `NO_TIMEZONE_DECLARED_BY_THE_SOURCE`.
- **Credentials and the live catalog were not read.** Querying the running
  kernel's `/api/v1/resources` needs a principal secret; the environment refused
  the read of `var/credentials.json`, and no attempt was made to work around it.
  `inventoried` was therefore evidenced from the deployed lake's own inventory
  snapshot (5,275 resources, all four paths present) rather than from a live API
  call. Nothing was stubbed to fill the gap.
- **The consensus gap itself is unchanged and unfixable by code.** Trading
  Economics answers HTTP 410 without a subscription and FXStreet requires OAuth
  (`financial-data/economic_calendar/release_surprises/stage13_consensus_gap.md`).
  A consensus feed overlapping the observed window is a purchasing decision.
- **Committed evidence is redacted.** The two measurement artifacts carry
  absolute host paths; the committed copies replace them with `<repos>`,
  `<home>` and `<scratchpad>` and record the original digest, so the committed
  file's own digest differs on purpose. No count, digest, era, role or verdict
  was altered.

## 6. Report

```
CALREG — Register the five economic-calendar resources, absences included
repo/branch/tip: data-gov / satoshi/calendar-data-gov-20260926 / see the commit
files: data_gov/resource_registration.py · tools/register_calendar_resources.py ·
       tests/unit/test_resource_registration.py · tests/user/test_register_calendar_resources.py ·
       docs/08_RESOURCE_REGISTRY.md · README.md ·
       docs/audits/evidence/CALENDAR_REGISTRATION_2026_09_26/{calendar_inventory.v1.redacted.json,
       wp22_measured_calendar_clock.redacted.json, registration_run.v1.redacted.json, registrations.v1.json}
suites (after reinstall): data-gov 209/0 (173 before this work, +36 new: 19 unit + 17 user)
acceptance: registration run {"registered": 5, "duplicates": 0, "refused": []} on the first write;
            {"duplicates": 5, "registered": 0} on an unchanged re-read;
            replay {"verdict": "ALL_ROWS_RE_DERIVED", "rows": 14, "live": 14, "slots": 5, "quarantined": 0};
            known_at 2026-09-25T23:59:59Z → 0 rows · 2026-09-26T00:00:01Z → 5 rows;
            consensus overlap {"verdict": "NO_CONSENSUS_SOURCE_OVERLAPS_AN_OBSERVED_CLOCK_SOURCE", "gap_days": 1326}
what is NOT done / refused / not measured: §5 above — no availability contract installed and nothing opened;
       no HTTP/console surface and no enforcement in the delivery path; 11 sibling FRED resources unregistered;
       no era measurement for the four non-archive clocks and none invented; the timezone-evidence vocabulary
       cannot express a measured clock, declared as an absence rather than mislabelled; live-kernel credentials
       refused by the environment, so `inventoried` was evidenced from the deployed lake's inventory snapshot;
       no service started, stopped or restarted.
```
