# Flow v3 traceability

| Requirement | Acceptance behavior | System/component evidence | Status |
|---|---|---|---|
| GOV-001/002 | undeclared campaign, unit, dataset or set cannot proceed | campaign API and store tests | DESIGNED |
| GOV-003/004 | aborted body grants no lineage; cache confirmation is explicit | delivery API/client tests | DESIGNED |
| GOV-005 | available time controls cuts; ambiguous time refuses | file-lake contract tests | DESIGNED |
| GOV-006 | same-stat rewrite and concurrent cut cannot reuse or replace identity | file-lake race tests | DESIGNED |
| GOV-007 | all five terminal states reach the lake once | terminal API/SQL tests | DESIGNED |
| GOV-008 | outage leaves a pending terminal that flushes once | durable outbox tests | DESIGNED |
| GOV-009 | duplicate metric identity refuses and permutation is stable | canonicalization tests | DESIGNED |
| GOV-010 | dirty identity cannot register as governing | campaign validation tests | DESIGNED |
| GOV-011 | terminal is written only through configured adapter | route/adapter integration test | DESIGNED |
| GOV-012 | missing and orphaned terminal identities are reported | reconciliation test | DESIGNED |
