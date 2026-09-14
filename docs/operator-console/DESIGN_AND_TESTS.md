# Operator documentation and store console

## Scope and use cases

An evaluator must find runnable lake and warehouse implementations from the
README. An operator must inspect the effective governance configuration,
prepare a changed configuration, and distinguish it from the running one.
An analyst must inspect warehouse tables, views and column metadata without
scanning every result table. Existing delivery and terminal protocols do not
change. Production services and data are outside this change's test scope.

## Acceptance and system tests (top down)

| Requirement | Alpha acceptance | Component and unit evidence |
|---|---|---|
| DOC-1 | README links resolve on default branches; examples name real options | Link and configuration checks; CLI help |
| GOV-UI-1 | Signed-in operator sees stores, policies, service settings and principal roles, not secrets | Configuration projection omits credentials; GET has no writes |
| GOV-UI-2 | Operator validates and saves a pending configuration; active context stays unchanged | Strict form parsing, store IDs/kinds, policy references, temporal startup rule, atomic persistence, no partial writes |
| WH-1 | Warehouse inventory lists tables and views with inspectable schema | SQLAlchemy reflection; columns, nullability, keys and relationships; missing resource errors |
| WH-2 | Warehouse configuration persists and is explicitly activated on restart | JSON roundtrip, invalid dates rejected before mutation, no database URL secrets in HTML |
| WH-3 | SELECT result is visible, not merely a flash with a row count | Escaped result table; bounded existing query path; API behavior preserved |
| SYS-1 | Existing experiment flow still works | Existing suites and isolated three-service end-to-end test |
| UI-1 | Desktop and mobile remain usable | Playwright screenshots, local assets loaded, no horizontal page overflow |

## Architecture and boundaries

Keep one data-gov kernel. A small operator-config module projects editable
settings and writes a pending JSON file; it never starts plugins or changes
the active context. Existing access roles identify human operators. Store
credentials remain in environment/files. Main config loading remains the
activation mechanism. A pending file is not an authorization or a deployment.

The warehouse stays in predictor/olap/lake, with its historical package name.
Its SQL query plugin owns reflection; its web plugin owns inventory, schema,
query result and settings presentation. PostgreSQL is the deployed database;
SQLite supplies disposable tests. Metadata discovery must not count every row
in every table. No destructive SQL or runtime process management is added.

## Implementation order (bottom up)

1. Tests for config projection/validation/persistence and SQL schema reflection.
2. Config and metadata helpers, followed by route integration.
3. AdminLTE templates, behavior tests and browser acceptance.
4. README, integration guide, evidence and default-branch publication.

Beta with external users is optional and not claimed. New UI code on GitHub
does not imply an already-running service has reloaded it.
