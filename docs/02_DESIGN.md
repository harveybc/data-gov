# 02 — Diseño (TDD)

Decisiones de sistema. Sin nube. Sin catálogo Apache.

## Cómo habla el usuario

| Actor | Canal |
|---|---|
| Humano (Harvey, Musashi) | UI AdminLTE, sesión usuario/clave |
| `predictor`, `doin`, `heuristic-strategy` | HTTP API + `Authorization: Bearer` + `X-Experiment-Key` |

API (programático):

```
GET  /api/v1/lakes
GET  /api/v1/resources?lake=
GET  /api/v1/coverage?lake=&resource=
GET  /api/v1/read?lake=&resource=&from=&to=
GET  /api/v1/query?lake=&sql=
GET  /api/v1/experiments/<experiment_key>/usage
```

`read`/`query` sin `X-Experiment-Key` → 403 y deny en accounting.

## Herramientas

- Flask (UI + API, un proceso)
- SQLite accounting (append-only)
- pandas/pyarrow para parquet/csv
- sqlite3 para OLAP de laboratorio
- hashlib para secretos (no Keycloak en esta fase)
- setuptools entry points

## Componentes (6 tipos de plugin, no 8)

| Grupo | Por qué existe | Default |
|---|---|---|
| `datagov.pipeline` | Orquesta | `default_pipeline` |
| `datagov.web` | UI + API (una boca) | `default_web` |
| `datagov.access` | Identidad **y** política (mismo config) | `default_access` |
| `datagov.accounting` | Bitácora | `default_accounting` |
| `datagov.lake` | Adaptadores; `discover` **es** el inventario | `files_lake`, `sql_lake` |
| `datagov.role` | Un despachador; los roles son prompts/config | `default_role` |

Se eliminan `authn`/`authz`/`inventory` como grupos: no merecían tipos distintos (solo config).

Lakes piloto: `financial_files` (glob sobre `financial-data`) y `olap_lab` (sqlite). Holdout: `2025-01-01` (catálogo financiero).

## Relación

```
cliente → web → access.allow? → lake.verb → accounting.record
                              ↘ deny → accounting.record
eventos (hash mismatch, deny spike, recurso nuevo) → role.dispatch
```
