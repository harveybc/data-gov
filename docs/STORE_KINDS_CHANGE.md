# Store classification change

Scope: Retsu's 2026-09-13 letter. One governance kernel, two kinds. No policy,
resource ID, receipt, API route or entry-point namespace change.

## Requirements and acceptance designed before implementation

| Requirement | Evidence |
|---|---|
| Files are lake; structured OLAP is warehouse | test_local_metadata_and_legacy_config |
| HTTP is transport, not a kind | test_http_uses_remote_kind_not_transport |
| Offline remote remains correctly labelled when configured | test_configured_http_kind_survives_unavailable_remote |
| Unknown remote does not pretend to be a lake | test_legacy_http_without_type_is_not_called_lake |
| Legacy local configurations still work | test_local_metadata_and_legacy_config |
| No cross-store default contamination | test_assembly_does_not_inherit_another_stores_kind |
| One policy engine and existing plugin namespace | test_same_policies_and_existing_plugin_namespace |
| UI and API agree; links unchanged | test_dashboard_detail_and_api_distinguish_stores |

Implementation boundary: metadata helper, three adapters, per-store assembly,
configuration example, two templates and documentation. Files/SQL know their
own kind; HTTP uses explicit configuration or a known remote declaration.
Unclassified legacy HTTP has null kind, displayed as Unclassified, not a third
kind. No type is guessed from a URL, store ID or allowed verbs.

## Verification

- PRE on `f6dd145`, before implementation: 16 failed, 1 passed. Before that
  measured PRE, two harness mistakes were fixed: importing conftest without
  its package and passing id instead of username to the policy fixture.
- Final focused classification/UI tests: 20 cases, included in the full suite.
- Full suite: **135 passed, 1 skipped in 19.93 s**. The skipped PostgreSQL
  optional test requires its disposable database configuration; no production
  database was used for that test.
- Disposable three-service Flow v3 E2E against reviewed financial and OLAP
  adapters passed: `missing_units=[]`, `accounting_only=[]`, `lake_only=[]`.
- Playwright with Chromium: dashboard, file detail and warehouse detail at
  1440x1000 and 390x844; six screenshots captured, zero document horizontal
  overflow on all six checks. Mobile dashboard and desktop warehouse images
  were inspected visually. UI preview uses only disposable fixtures.
- Existing entry-point names, policy content, resource identifiers, temporal
  rules and terminal schemas are unchanged. The previously expected `http`
  kind assertion now checks transport and unclassified legacy metadata.

The integration branch also merges `master@249df1e`, preserving Satoshi's
new resource contracts. Suite after that merge: 135 passed, 1 skipped in
19.63 s. The shared master worktree has an in-progress edit to `files_lake.py`
and a threaded-download test; those edits were not stashed, staged or
overwritten here. The source change is published on
`musashi/store-kinds-20260913`, not claimed deployed to ports 5055-5057.

State: IMPLEMENTED_AND_TESTED. Next: include this small change in the existing
Flow v3 deployment, not a separate rollout. Shared services were not restarted
by this change. Do not confuse source integration with live adoption.

## Handoff to the ongoing adoption work

This is an additive prerequisite to the next D2 order, not a replacement of
the Flow v3 adoption order Satoshi is already executing. Once the current
threaded-download correction is committed, merge
`origin/musashi/store-kinds-20260913` into the data-gov integration branch,
retaining both that correction and the factual resource contracts. Re-run
the suite and disposable E2E on their combined tip before the planned deploy.
At the already-planned deployment check, verify `/api/v1/lakes` and the UI:
`financial_files=lake`, `olap_cube=warehouse`, `predictor_examples=lake`.
The existing `deny_from`, governed micro-run and reconciliation checks remain
required. Do not rename `datagov.lake`, migrate the cube, split the repo or
introduce additional per-experiment approval steps.

If a custom HTTP store still has only `kind: http`, configure its factual
kind. Until then, an unrecognized remote remains visibly Unclassified without
blocking its otherwise permitted operations. This prevents false labels while
preserving compatibility.
