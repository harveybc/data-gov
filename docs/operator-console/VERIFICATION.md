# Verification of documentation and operator consoles

Date: 2026-09-14. Base data-gov revision: `956dec7`. Warehouse source copied
from predictor `182bf89fa0754e3b7b2cea208621531331af931e`, limited to `olap/lake`;
financial adapter update from `f00bc6c15`, limited to the availability code and
its tests. No scientific campaign artifacts are republished as new results.

## Tests and evidence

| Check | Observed result |
|---|---|
| data-gov suite | 166 passed, 1 skipped |
| Warehouse suite, SQLite only | 23 passed, 1 skipped |
| Warehouse suite with disposable PostgreSQL | 24 passed; temporary database removed afterward |
| Financial adapter suite | 60 passed, 1 skipped |
| data-gov -> file lake -> warehouse, disposable processes | Registration, verified delivery, terminal storage and exact reconciliation passed |
| Browser acceptance | 8 pages/viewports, no page errors, local AdminLTE loaded, no horizontal page overflow |
| Browser interactions | Visible SELECT results; validated pending config written; active listener unchanged |
| External-domain reference interface | 14 passed; no domain optimization performed |

The new configuration and schema tests failed on the previous implementation
before the corresponding code was added. The bootstrap test also reproduced
freshly generated credentials with no matching startup config. None of these
tests uses broker accounts, scientific training or production result tables.

Three-service reconciliation returned empty `missing_units`, `accounting_only`
and `lake_only`. Fixture delivery SHA-256:
`5b4cabe43696951ea62d6c67ad4fb0e00f3a3822acc33077eec9fbccd44965d4`.
Delivery availability-contract SHA-256:
`139f3adcd7028fbd2bec90381f9ec34170d32528f5c40a6a63bc41f2a02c90a1`.

## Screenshots

All screenshots show disposable synthetic/demo data, not the populated cube.
Viewports: 1440 x 1000 and 390 x 844. Browser: Chromium 152 via Playwright.

- [Governance dashboard, desktop](images/governance-dashboard-desktop.png)
- [Governance dashboard, mobile](images/governance-dashboard-mobile.png)
- [Governance settings, desktop](images/governance-settings-desktop.png)
- [Governance settings, mobile](images/governance-settings-mobile.png)
- [Warehouse inventory, desktop](images/warehouse-inventory-desktop.png)
- [Warehouse inventory, mobile](images/warehouse-inventory-mobile.png)
- [Warehouse schema, desktop](images/warehouse-schema-desktop.png)
- [Warehouse schema, mobile](images/warehouse-schema-mobile.png)

## Scope and release

The pending configuration UI validates the exposed settings and preserves
non-editable settings server-side; it is not a general remote deployment tool.
Credentials are not included in the form. Third-party backends require their
own contract tests; none is implied by this interface work.

Publication and deployment are tracked separately. A running process does not
reload when a README or default branch changes. The production N3 follow-up
has its own deployment receipt and reconciled micro-run; this test report
alone does not grant scientific acceptance or record a deployment.

Follow-up completed: [the separately authorized N3 deployment and production
micro-run](https://github.com/harveybc/predictor/blob/51ad761/docs/handoffs/MUSASHI_N3_PRODUCTION_ACCEPTANCE_2026_09_14.md)
passed. Three services were updated, six deliveries and 90 metrics were
recorded, reconciliation was exact, and repeated outbox flushes added no rows.
This operational proof remains distinct from the disposable tests above.
