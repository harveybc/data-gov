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
GET  /api/v1/download?lake=&resource=[&from=YYYY-MM-DD&to=YYYY-MM-DD]   bytes de un archivo del lake; X-Content-SHA256, X-Source-SHA256, X-Delivery, X-Time-Column
GET  /api/v1/read?lake=&resource=&from=&to=                               filas JSON (cortes pequeños); cota superior estricta <
GET  /api/v1/query?lake=&sql=                                             SELECT solamente
POST /api/v1/experiments/<experiment_key>/metrics                         reporte de métricas → lineage + escritura en el lake destino (gov_*)
GET  /api/v1/experiments/<experiment_key>/usage?limit=&before_id=         filas de accounting de esa clave
GET  /api/v1/datasets/<sha256>/usage?limit=&before_id=                    descargas allow de exactamente esos bytes
```

`download`/`read`/`query` sin `X-Experiment-Key` → 403 y deny en accounting.
Claves (`X-Experiment-Key`, ruta, `experiment_set_key`): `^[A-Za-z0-9._:-]{1,128}$`, si no 400.
Contrato completo de `download` / `metrics` / linaje: [04_FLOW_V2.md](04_FLOW_V2.md).
Reglas operativas: semáforo `max_downloads` (503 + `Retry-After: 30`), spool bajo `var/spool`
barrido al arrancar, cortes materializados una sola vez bajo `var/cuts`, `source_changed`
comparado en línea contra la última descarga de `(lake, resource)`; un lake con
`holdout_start` cuyas policies no tengan `deny_from` impide el arranque.

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

Lakes piloto: `financial_files` (glob sobre `financial-data`), `olap_lab` (sqlite) y
`predictor_examples` (`files_lake` sobre `../predictor/examples/data_downsampled`). Holdout: `2025-01-01`.

## Relación

```
cliente → web → access.allow? → lake.verb → accounting.record
                              ↘ deny → accounting.record
eventos (hash mismatch, deny spike, recurso nuevo) → role.dispatch
```
