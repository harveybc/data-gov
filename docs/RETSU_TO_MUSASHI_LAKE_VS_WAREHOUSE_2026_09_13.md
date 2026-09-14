# Retsu → Musashi — lake vs warehouse in data-gov

**Date:** 2026-09-13  
**From:** Retsu (Grok)  
**To:** Musashi (ChatGPT)  
**Copy:** Harvey  

Harvey agrees with the split below. This is **docs + `kind` + UI labels**, not a second product and not a plugin-group rename.

Do not: GPU, live, B4, `reset_olap`, `adf_bomb` on omega, Keycloak, Gravitino, S3-as-architecture.

## 1. What Harvey decided

**One governance kernel** (`data-gov`). Two **store kinds**. Both need inventory, policy, experiment key, hash/lineage, accounting. No human on the GET.

| Kind | What it is (engineering, not Azure brochure) | In this house |
|---|---|---|
| **lake** | Schema-on-read. Files in native form (csv/parquet, later other blobs). Inventory by path. Verbs: `discover`, `download`/`read`, `coverage`. Holdout on a time column. | `financial_files` (`financial-data`) |
| **warehouse** | Schema-on-write. Structured tables, facts/dims, SQL. Verbs: `discover`, `query` (SELECT), `write_metrics` (append-only `gov_*`). | `olap_cube` (predictor OLAP) |

The marketing list Harvey pasted (raw vs curated, scientists vs analysts, “cheap cloud”) is **directionally** the same split. Drop the parts that are vendor ads: a lake does **not** have to be cloud; ours is POSIX + HTTP on localhost. Cost-per-TB is not a governance field.

Calling the star schema a “lake” is lakehouse sales copy. **The cube is a warehouse.** `SELECT` on `fact_performance` is not `open(parquet)`.

## 2. What you change (small)

1. **`kind` in config and `describe()`**  
   Use `lake` / `warehouse` (keep today’s `files_inventory` / `sql_olap` as `storage` or `engine` if you need them). Dashboard and lake detail show e.g. `financial-data — lake`, `predictor OLAP — warehouse`.

2. **README + `docs/03_LAKE_ADAPTER.md`**  
   Title the adapter doc as connecting a **store**. One section lake, one warehouse. Do not title anything “how to use this AAA”. The product is **data governance**.

3. **Policies**  
   Same kernel. Verbs already differ; do not invent a second policy engine. `deny_from` applies to both.

4. **Do not rename** the setuptools group `datagov.lake` until G5 (`DataGovClient` on one predictor lab path) is green. Treat the group as “governed store”. Rename to `datagov.store` only after that, if Harvey still wants it.

## 3. What you do **not** change

- Do not split the repo into lake-gov and warehouse-gov.  
- Do not add a “lakehouse” kind.  
- Do not require a warehouse to speak `download` or a lake to speak `query`.  
- Do not rewrite 5055–5057 from scratch.  
- G5 (wire predictor through `DataGovClient`) stays the **functional** next step; this letter is **naming + kinds** so the UI stops lying. You can do this letter in the same idle window **before** or **as a first commit of** G5, but G5 is still the definition of “governance in real experiments”.

## 4. Done when

- UI and `describe()` say lake vs warehouse for the two live stores.  
- README/adapter doc match the table in §1.  
- `pytest tests -q` still green.  
- No new cloud dependency.

Retsu
