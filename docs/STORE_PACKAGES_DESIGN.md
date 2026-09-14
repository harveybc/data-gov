# Reusable lake and warehouse hosts

Status: architecture proposed with the owner's direction on 2026-09-14.
This is not a claim that either new repository exists or that migration has
already happened. Current services remain usable during implementation.

## Decision

Separate reusable service hosts from data-specific providers:

| Repository | Python namespace | Responsibility |
|---|---|---|
| `data-gov` | Current package, retained during migration | Governance, policy, accounting and campaign protocol |
| Proposed `data-lake` | `data_lake_service` | HTTP contract, AdminLTE console, configuration and file-store backend interface |
| Proposed `data-warehouse` | `data_warehouse_service` | HTTP contract, AdminLTE console, configuration and structured-store backend interface |
| `financial-data` | Proposed `financial_data_store` provider package | Financial inventory, producer contracts, source locations and domain-specific behavior |
| `predictor` | Proposed `predictor_olap_store` provider package | Existing OLAP schema, reporting tables and domain-specific ETL |

No new package uses top-level `app`, `web_plugins` or `query_plugins`.
Configuration differences use the same provider with different settings;
create another provider only for genuinely different storage/behavior.

## Plugin discovery

Proposed service-host entry-point groups are `datalake.backends` and
`datawarehouse.backends`. The existing governance group `datagov.lake` and
the HTTP route/receipt contracts remain unchanged.

An external financial distribution would register, for example:

```python
setup(
    name="financial-data-store",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    entry_points={
        "datalake.backends": [
            "financial_files=financial_data_store.provider:FinancialStore"
        ]
    },
)
```

The **proposed**, not current, host configuration selects the installed plugin:

```json
{
  "backend": {
    "entry_point": "financial_files",
    "distribution": "financial-data-store",
    "settings": {"root_path": "/data/financial"}
  }
}
```

A repo name is a provenance/install reference, not a Python import path. The
deployment installs a versioned wheel or a pinned VCS revision beforehand.
The host verifies the selected entry point belongs to the named installed
distribution, rejects duplicate names, and records distribution version,
source commit/build identity and config hash. It does not auto-install code
from JSON or fall back to arbitrary imports.

## What stays shared

- Stable store/resource IDs and existing policy behavior.
- Campaign, delivered bytes, temporal availability, receipts and outcomes.
- One governance kernel; neither host copies campaign authorization logic.
- Common behavior where applicable: describe, inventory, metadata and status.
- Explicit capabilities: file delivery for lakes, query/reporting for warehouses.
- JSON configuration plus AdminLTE settings/inventory views.
- Local synthetic mechanics still produce identifiable artifacts and outcomes.

Not every backend must support every operation. Do not implement a fake
download endpoint for a warehouse or arbitrary SQL writes for a file lake.
The interface reports unsupported operations clearly.

## Test-led migration sequence

1. Freeze requirements and acceptance tests from the existing HTTP examples.
2. Specify backend protocols and capability schemas, without moving data.
3. Test plugin discovery in installed wheels: unique package namespaces,
   missing provider, wrong distribution, duplicate entry point and version
   compatibility. Include a small independently packaged test provider.
4. Build each service host against a disposable provider and database.
5. Package the current financial and OLAP providers externally; do not create
   symlinks or `sys.path` workarounds to make tests pass.
6. Re-run the same receipt/cut/terminal fixtures against old and new hosts.
   Compare observable results, error behavior, bounded memory and causality.
7. Verify desktop/mobile settings and resource metadata in both hosts.
8. Shadow only on disposable data, then choose a deployment window. Preserve
   existing logs, IDs, pending outboxes and source data. Publish rollback steps.
9. Retire old launch paths only after a reconciled production micro-run.

The first deliverable is an installed external provider passing the unchanged
governance contract, not merely two new repositories with similar code.

## Scope exclusions

This separation must not block the current D2 re-adjudication, alter its
scientific design, move datasets by hand, recreate the cube, or restart other
services. A new generic host is not a reason to postpone experiments already
supported by the current tested integration.
