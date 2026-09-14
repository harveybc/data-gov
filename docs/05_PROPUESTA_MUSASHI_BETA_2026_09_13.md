# Satoshi → Musashi — data-gov flujo v2: auditoría, plan de trabajo y beta

> **SUPERSEDIDO (2026-09-14, D2-R6).** Esta propuesta v2 es antecedente histórico.
> La especificación vigente es Flow v3: `06_FLOW_V3_FAILSAFE.md` (contrato),
> `07_RESOURCE_CONTRACTS_INSTALLED_2026_09_13.md` (contratos y alcance ejecutable),
> `STORE_KINDS_CHANGE.md` (lake/warehouse) y las órdenes en
> `predictor/docs/handoffs/MUSASHI_TO_GENERAL_SATOSHI_FLOW_V3_ADOPTION_ORDER_2026_09_13.md`
> y `MUSASHI_TO_SATOSHI_FLOW_V3_DEPLOYMENT_ADOPTION_AND_D2_ORDER_2026_09_13.md`.
> Correcciones a lo que sigue: (a) la secuencia de beta es ensayo desechable →
> integración → despliegue acotado, no "reiniciar primero y auditar después";
> (b) el despliegue acotado de :5057/:5056/:5055 lo ejecuta quien la orden N3
> asigne (Musashi en entorno permitido u operador), no el owner "por defecto";
> (c) estados con pruebas reales, no promesas: `IMPLEMENTED` (código + pruebas),
> `PROVEN_DISPOSABLE` (stack desechable), `DEPLOYED` (servicios productivos con el
> código integrado), `PROVEN_PRODUCTION` (micro-run reconciliado en producción).
> Al 2026-09-14: IMPLEMENTED y PROVEN_DISPOSABLE sí; DEPLOYED y PROVEN_PRODUCTION no.
> El relato histórico de abajo se conserva sin editar.

**Fecha:** 2026-09-13
**De:** Satoshi (Claude)
**Para:** Musashi (ChatGPT), ingeniero de datos
**Copia:** Harvey (owner), Retsu (Grok)
**Estado:** propuesta para auditoría. Sustituye la forma del encargo G5 de Retsu (`predictor/docs/RETSU_TO_MUSASHI_DATA_GOV_G5_2026_09_13.md`), no su objetivo.

No es orden de GPU, live, B4, `reset_olap` ni D3. No toca el loader `crispdm-olap-loader`, PostgreSQL ni Metabase.

## 1. Qué es, en cinco pasos

Harvey pidió una cosa simple: que los miles de experimentos que vienen no descarguen archivos a mano ni escriban métricas en CSV sueltos, sino que:

1. El agente (predictor, doin, heuristic-strategy) se identifica: clave de servicio + clave de experimento (o de un *conjunto* de experimentos que comparten datasets).
2. Si autenticación y política lo permiten, **descarga el dataset como archivo** y data-gov registra quién, cuándo, qué recurso y rango, y el **sha256 de los bytes entregados**.
3. El agente usa el archivo como quiera. Nadie espera a un humano.
4. Al terminar, **reporta sus métricas** a data-gov diciendo en qué lago se guardan (el cubo OLAP es un lago más) y con el sha256 de cada dataset que usó. data-gov verifica que esos bytes se sirvieron bajo esa clave, registra el reporte y lo escribe en el lago.
5. Cualquier lago nuevo es un adaptador más un bloque de política. Nada externo: Flask, SQLite, el PostgreSQL que ya existe.

Lo que se gana: trazabilidad (qué bytes exactos usó cada experimento), replicabilidad (si el archivo cambia, el hash cambia y salta un evento) y un cubo que responde "qué experimentos usaron estos bytes" y "qué métricas quedan contaminadas si este archivo estaba mal".

## 2. Qué cambia respecto a G5

| G5 de Retsu | Esta propuesta | Por qué |
|---|---|---|
| Sustituir `open(csv)` por `DataGovClient.read(...)` (filas JSON) | `GET /api/v1/download`: el archivo tal cual, con `X-Content-SHA256` | `read` convierte el dataset a JSON en memoria: revienta con archivos grandes y el hash depende de la serialización, no de los datos |
| El cubo (5057) es solo SELECT | SELECT para `query`; **append-only** en tablas nuevas `gov_*` a través de `write_metrics` | Harvey quiere las métricas en el cubo **a través** de la gobernanza; el ETL manual de predictor no es el camino |
| Sin reporte de métricas | `POST /api/v1/experiments/<key>/metrics` con linaje verificado | Es la mitad del flujo que faltaba |
| Dos lagos | Tres: se añade `predictor_examples` (lago de archivos en proceso sobre `predictor/examples/data_downsampled`) | Demuestra que añadir un lago es un bloque de config, y alimenta el camino de laboratorio de predictor |
| El camino predictor lo hace Musashi | `predictor/tools/governed_run.py` ya lo hace (descarga, corre, reporta) | Queda para Musashi lo mismo con DOIN y heuristic-strategy, con el mismo cliente |

## 3. El contrato

Texto completo: `docs/04_FLOW_V2.md`. Reglas que conviene tener en la cabeza al auditar:

- **Identidad = archivo en el disco del lago.** Sin rango se entrega el archivo fuente `AS_IS`, solo si su cobertura termina antes del holdout o el recurso está declarado `untimed`. Con rango (`from`/`to`, días `YYYY-MM-DD`), el lago materializa **una vez** el corte en `var/cuts/<sha256 fuente>/<from>_<to>.<ext>` y lo sirve tal cual para siempre: mismo fuente + mismo rango = mismos bytes aunque cambie pandas o pyarrow. Si el fuente cambia, cambia el directorio y salta `source_changed`.
- **Holdout en el reloj de pared de la columna** (no en UTC) y aserción post-corte `max(t) < holdout`. Un `to` con hora se rechaza (400). Un archivo sin columna de tiempo bajo un lago con holdout se niega salvo declaración explícita.
- **CSV se corta línea a línea** (subconjunto de bytes del fuente, sin pandas en la copia); parquet por row groups con opciones de escritura fijas. Memoria acotada; intermedios bajo `var/` (aquí `/tmp` es tmpfs).
- **data-gov no confía en el hash del lago:** rehashea lo que entrega y registra ese valor. El cliente verifica `X-Content-SHA256` antes de dar por bueno el archivo.
- **Linaje de cinco campos:** lago, recurso, sha256, actor y clave (experimento o conjunto). Un reporte sin datasets o con alguno no verificado es `UNVERIFIED`; con `require_lineage: true` (el cubo) se rechaza con 422 y fila de deny; el lago de laboratorio lo guarda marcado.
- **`report_sha256` canónico** (listas ordenadas, números normalizados, sin NaN) y recalculado por el lago: re-enviar es idempotente (`already_stored`). Primero escribe el lago, luego la fila de accounting.
- **Tablas `gov_report`, `gov_metric`, `gov_dataset` + vista `gov_metric_current`,** creadas una vez al arrancar, aditivas, sin claves compuestas con NULL, `ON CONFLICT DO NOTHING`. Para experimentos gobernados `gov_metric` es la verdad; el ETL viejo (`fact_performance`) no se corre para esas claves.
- **Semáforo de descargas** (2 por proceso, 503 + `Retry-After`), solo timeout de conexión, índices en accounting, WAL en SQLite.

## 4. Revisión previa a implementar

Tres revisores independientes (simplicidad, evasión, operación) criticaron el primer contrato contra el código y los datos reales. Lo que cambió por ellos:

| Hallazgo (verificado) | Decisión |
|---|---|
| El corte re-serializado hacía que el hash dependiera de la versión de pandas/pyarrow | Cortes materializados una vez en disco; identidad = archivo |
| `datasets: []` guardaba métricas como si estuvieran gobernadas | `lineage` en `gov_report`; `require_lineage` en el cubo |
| `to=2024-12-31 20:00` pasaba la política de días y empujaba el corte a 2025-01-01; el `read` viejo entregaba la fila de medianoche | Rango solo en días, cota superior estricta `<`, también en `read` |
| Corte en UTC entregaba el primer día de holdout de archivos en Asia/Sídney | Reloj de pared + aserción post-corte |
| 46+ CSV sin columna de tiempo se entregaban enteros bajo holdout | Fail-closed salvo `untimed` declarado |
| El linaje ignoraba lago, recurso y actor | Cinco campos + `event_id` |
| `/tmp` es tmpfs: un corte de 3.3 GB iba a RAM | Todo intermedio bajo `var/`, unlink-after-open |
| Claves compuestas con NULL: Postgres las rechaza, SQLite no deduplica | Sin claves compuestas; índices |
| DDL dentro de la transacción con el rol de solo lectura | DDL al arrancar en autocommit; rol de escritura separado opcional |
| `config_sha256` antes de las banderas CLI y `code_commit` con árbol sucio | Hash de la config efectiva que escribe predictor, canónica; `-dirty` |

## 5. Estado de implementación

| Repositorio | Commits | Pruebas (tip) |
|---|---|---|
| data-gov `master` | `5183009` código, `e0676b5` pruebas, `f6b671a` documentación, `70d2dae` bloqueantes | 96 passed |
| financial-data `lake/` (`satoshi/c122-c145-20260912`) | `285943ab9` verbo download, `1867d45d0` bloqueantes | 50 passed |
| predictor `olap/lake` + `tools/governed_run.py` (`satoshi/c166-c184-20260913`) | `a6eeeb8` write_metrics, `fe1151b` governed_run, `6c5e82f` documentación, `f922865` bloqueantes | lago 14 passed, 1 skipped (PostgreSQL sin URL de prueba); governed_run 17 passed |

**Revisión adversarial.** Tres revisores por repositorio (evasión, corrección, operación) y verificación por votos. Los subagentes se quedaron sin créditos de uso a mitad de la verificación, así que ni la etapa de corrección ni la prueba de extremo a extremo se ejecutaron. Corregí yo los bloqueantes, cada uno con prueba de regresión:

1. Un `resource_id` podía salir del lago (`../`, ruta absoluta, enlace simbólico) en `download`, `coverage` y `read`, en data-gov y en el lago financiero.
2. `read` entregaba un archivo sin columna de tiempo completo, holdout incluido, donde `download` lo negaba.
3. data-gov y el lago del cubo calculaban `report_sha256` distinto: `role` se hasheaba en las métricas de un solo lado, así que todo reporte al cubo se habría rechazado. `role` queda solo en los datasets, y una prueba cruzada entre repositorios fija el mismo hash.
4. En `governed_run`, una bandera extra (`--x_train_file`, `--results_file`, `--load_config` ...) podía sustituir los datos gobernados mientras el reporte citaba sus hashes. Ahora se rechaza, y tras la corrida se exige que la config efectiva use los archivos descargados.

También corregí dos hallazgos mayores baratos: archivos `.part` únicos por escritor (cliente y `governed_run`) y `config_sha256` sin `load_config`.

**Hallazgos mayores abiertos** (confirmados o sin verificar por falta de créditos; los dejo para tu auditoría):
- La fila `allow` de descarga se escribe antes de enviar el cuerpo. Un cliente que corta a mitad deja linaje de bytes que no recibió.
- Un corte materializado no se re-verifica tras escribirse. Si cambia la columna de tiempo configurada, se re-sirve un corte hecho con la columna anterior.
- El corte de parquet carga el row group entero con todas las columnas, y `read` del lago financiero carga el archivo completo antes de aplicar el límite de filas. Estos son los que, al reproducirse contra archivos reales grandes con tope de 2 GiB, produjeron dos muertes OOM contenidas en omega a las 17:17 y 17:22.
- Un único lock serializa todas las descargas mientras se materializa un corte, y la columna de tiempo se re-parsea en cada descarga.
- `POST /config` del GUI del lago financiero no pide autenticación y puede vaciar `holdout_start`.
- Archivos corruptos o vacíos responden 400 `invalid from/to` o 500 en lugar de 422.
- En el lago del cubo, `received_at` se acepta del cuerpo sin validar y ordena `gov_metric_current`. Un fallo transitorio de DDL deja `write_metrics` en 503 hasta reiniciar.
- El proxy de `write_metrics` en data-gov no tiene timeout de lectura.

**Prueba de extremo a extremo:** no ejecutada. El script `scripts/e2e_local.py` no existe todavía.

**Servicios vivos:** 5055–5057 siguen con el código anterior. No deben reiniciarse con el código nuevo hasta que la prueba de extremo a extremo pase; esa es la fase 0 del plan de beta.

## 6. Qué te pido auditar

1. **Evasión del holdout** por descarga: rangos, columnas con zona horaria, archivos sin tiempo, `AS_IS` de recursos que cruzan 2025, `read` vs `download`.
2. **Linaje:** que ningún reporte llegue al cubo citando bytes que no se sirvieron bajo esa clave y ese actor; que `experiment_set_key` no abra un agujero.
3. **Idempotencia y canonicalización** del reporte en ambos motores (SQLite y PostgreSQL) y que el lago recalcule `report_sha256`.
4. **Aditividad en el cubo:** solo `CREATE ... IF NOT EXISTS` y `INSERT`; ninguna tabla existente tocada; el loader `crispdm-olap-loader` sin reinicio.
5. **Memoria y disco:** nada bajo `/tmp`, nada de cargar archivos enteros, semáforo, spool limpio tras un crash.
6. **Secretos:** ningún token ni clave en git; el token del lago solo por variable de entorno o archivo explícito.
7. **Lo que declaro como límite** (sección 8), por si lo consideras bloqueante para la beta.

## 7. Plan de beta

| Fase | Qué | Quién | Listo cuando |
|---|---|---|---|
| 0 | Reiniciar 5055, 5056 y 5057 con el código nuevo (mismos `serve.sh`); opcional: rol PostgreSQL con `INSERT` solo en `gov_*` | Harvey | `/healthz` en los tres y `gov_*` creadas en `predictor_olap` |
| 1 | Corrida de juguete de predictor por `tools/governed_run.py` (config `phase_1_daily/phase_1_ann_1575_1d`, 2 épocas) contra los servicios vivos | Satoshi | `usage` muestra 3 descargas con sha256 y un `write_metrics`; `gov_report.lineage = VERIFIED` en el cubo |
| 2 | Auditoría de Musashi sobre este documento, el contrato y el e2e | Musashi | Veredicto |
| 3 | Un experimento real de predictor (un config de campaña, CPU) por el mismo camino; comparar sus métricas con las del ETL viejo del mismo run | Satoshi | Igualdad de métricas; ninguna fila fuera de `gov_*` |
| 4 | Mismo cliente en DOIN y luego en heuristic-strategy (G5 tal como lo pidió Retsu, ya con `download` + `report_metrics`) | Musashi | Cada uno deja su `write_metrics` con linaje VERIFIED |
| 5 | Incorporar al work plan: toda campaña nueva declara `experiment_set_key`, descarga por data-gov y reporta al cubo; el ETL manual queda solo para históricos | Harvey / Musashi | Regla escrita en el work plan |

Criterio de éxito de la beta: durante dos semanas, cero experimentos nuevos con métricas en el cubo sin fila `gov_dataset` verificada, y cero descargas fuera de data-gov en los tres agentes.

## 8. Límites que dejo declarados y lo que mejoraría después

1. **El token del lago lo puede leer el mismo usuario que corre los agentes.** Un agente podría hablarle al lago directo. El lago recalcula `report_sha256`, pero nada concilia todavía el cubo con accounting. Siguiente evento: `orphan_report` (un SELECT por el verbo `query`, sin servicios nuevos).
2. **Cualquier clave de servicio consulta el uso de cualquier experimento.** Hoy los agentes son un equipo; si entra un tercero, política `usage` por actor.
3. **La caché del cliente no tiene tope.** Limpieza manual o `--cache-max-bytes`.
4. **Un tablero en Metabase sobre `gov_*`** (experimentos por dataset, datasets por experimento, reportes UNVERIFIED) haría visible la gobernanza sin más código.
5. **Rol PostgreSQL de escritura acotado** (`INSERT` en `gov_*`), un `GRANT`.

## 9. Mi opinión, que Harvey pidió

Sí me gusta. La forma es la correcta: AAA con el hash de lo entregado como identidad, el cubo como un lago más, lagos por configuración, nada externo. Lo que no me gustaba (hash dependiente de librerías, métricas sin datos gobernados, holdout evadible) quedó cerrado en el contrato antes de escribir código, y el resto está en la sección 8 como trabajo conocido, no escondido.

Satoshi
