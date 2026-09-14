# Flow v3 traceability

| Requirement | Acceptance behavior | System/component evidence | Status |
|---|---|---|---|
| GOV-001/002 | undeclared campaign, unit, dataset or set cannot proceed | campaign API and store tests | VERIFIED |
| GOV-003/004 | aborted body grants no lineage; cache confirmation is explicit | delivery API/client tests | VERIFIED |
| GOV-005 | available time controls cuts; ambiguous time refuses | file-lake contract tests | VERIFIED |
| GOV-006 | same-stat rewrite and concurrent cut cannot reuse or replace identity | file-lake race tests | VERIFIED |
| GOV-007 | all five terminal states reach the lake once | terminal API/SQL tests | VERIFIED |
| GOV-008 | outage leaves a pending terminal that flushes once | durable outbox tests | VERIFIED |
| GOV-009 | duplicate metric identity refuses and permutation is stable | canonicalization tests | VERIFIED |
| GOV-010 | dirty identity cannot register as governing | campaign validation tests | VERIFIED |
| GOV-011 | terminal is written only through configured adapter | disposable three-service E2E | VERIFIED |
| GOV-012 | missing and orphaned terminal identities are reported | reconciliation and disposable E2E | VERIFIED |
| GOV-013 | stale output bytes cannot be overwritten by a governing unit | predictor Flow-v3 runner tests | VERIFIED |
