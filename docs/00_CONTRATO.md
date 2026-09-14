# Contrato: sistema de gobernanza de data lakes

**Fecha:** 2026-09-12  
**Estado:** requisito de Harvey. Sistema de gobernanza de datos (inventario, política, accounting, roles).  
**No es orden de instalar Apache ni de parar I1–I5.**

## 1. Objeto

Esto **no** es un AAA suelto. Es un **sistema de gobernanza de datos**:

- conectar **varios lakes remotos** (on-prem u otros sitios; no nube pública como requisito);
- inventariar recursos (autoinventario por lake);
- aplicar políticas **automáticas** (quién/qué/verbo/rango);
- registrar accounting (hashes, metadata, allow/deny);
- automatizar **todos los roles** de gobernanza y técnicos **excepto** CEO (Harvey) e ingeniero de datos (Musashi), mediante prompts Hermes que actúan **solo cuando hay un evento que los necesita**.

El API de acceso es *una cara* del sistema. Las APIs que cada lake debe exponer para engancharse se definen **después** de este contrato; el núcleo no asume S3 ni GVFS.

Útil primero para Harvey (excepciones, cobertura, qué experimento usó qué). El experimento streamline **no espera** a Musashi ni a Satoshi para un `GET`.

Cada lake es un backend enchufable. Autorización **por lake y por recurso**. Clientes: `predictor`, DOIN, `heuristic-strategy`, otros con clave.

## 2. Fuera de alcance (explícito)

- Nube pública, S3, HDFS, GVFS, credential vending, Iceberg, Spark.
- Gravitino, Unity Catalog, OpenMetadata, DataHub como *el* producto de acceso a datos.
- Tesís doctoral de gobernanza. Esto es infraestructura de evidencia.
- Que el train lea POSIX/`PG*` saltándose el núcleo.
- Autorización manual por agente (ticket a Musashi/Satoshi, “aprueba este CSV”).
- Crons Hermes que despiertan roles “por si acaso” y gastan tokens.
- Políticas que metan un humano en el hot path de un run.

## 3. Dos planos

```text
clientes (predictor, DOIN, heuristic-strategy, …)
        |
        v
núcleo de gobernanza — registro de lakes, inventario, políticas,
              AuthN/AuthZ automático, accounting, eventos a Hermes
        |
        +-- prompts de roles (despiertan por evento; no son porteros)
        |
        +-- adaptador lake_id=financial_files
        +-- adaptador lake_id=olap_public
        +-- adaptador lake_id=…   (futuros)
```

El núcleo **no** almacena los bytes. Cada adaptador sí (archivos, Postgres, lo que venga).

## 4. Lake configurable

Un lake se registra con, como mínimo:

| Campo | Para qué |
|---|---|
| `lake_id` | clave de políticas y de logs |
| `tipo` | `files_inventory` / `sql_olap` / futuro |
| `endpoint` del adaptador | on-prem |
| `capacidades` | `discover`, `read_range`, `import`, `create`, `mutate`, `query` — las que apliquen |
| `inventario` | cómo y cada cuánto se sincroniza |
| `identidad del adaptador` | el núcleo habla con el lake; no es la clave del cliente |

Añadir un lake = configurar adaptador + políticas. No un producto nuevo de gobernanza.

OLAP es un lake más: tablas/vistas/métricas son recursos; RLS/`pgaudit` pueden ser el enforcement *dentro* de Postgres, pero el cliente pasa igual por el núcleo (token + `experiment_key`).

## 5. Autoinventario (obligatorio por adaptador)

El núcleo **no adivina** recursos. Cada adaptador implementa inventario y lo empuja o deja que el núcleo lo tire.

Contrato mínimo de un recurso:

| Campo | Archivos (series) | OLAP |
|---|---|---|
| `lake_id` + `resource_id` | dataset del inventario | `schema.table` / métrica |
| `versión` / hash | sha256 del artefacto o del slice canónico | versión de proyección / corte |
| cobertura | `t_min`, `t_max`, frecuencia, huecos | grano, splits, horizonte si aplica |
| capacidades | las que ese recurso admite | `query` / `insert_fact` / … |
| restricciones | holdout, licencia, `available_at` | no profiler sobre prueba reservada |

Sin fila de inventario **no hay** autorización ni accounting de ese recurso: fail-closed.

Sincronización: al registrar el lake, al cron, y tras create/import. Si el inventario falla, no se inventan IDs.

## 6. API del núcleo (lo que ven los clientes)

Autenticación al núcleo (clave / client credentials). Luego, como mínimo:

- listar lakes y, por lake, recursos inventariados (solo los autorizados)
- cobertura / fechas disponibles de un recurso
- lectura acotada (rango u otra dimensión que el adaptador declare)
- create/import/mutate **solo** si el lake y la política lo permiten
- toda llamada lleva `experiment_key` / `run_id` cuando el verbo toca datos

El adaptador traduce: CSV+inventario, SQL+RLS, etc. El cliente no abre el disco ni `PG*` a pelo.

## 7. Autorización

Políticas sobre el árbol **inventariado**:

`(identidad, lake_id, resource_pattern, verbo, restricciones)`

Ejemplos de restricción: rango temporal, no-holdout, solo lectura, un `experiment_key` prefix.

Una concesión sobre el lake **no** abre un recurso marcado restringido. Recursos no inventariados = denegar.

## 8. Accounting

Append-only, sin credenciales en claro:

auth ok/fail; allow/deny; discover; read (con rango y hash de lo entregado); import/create/mutate; lake_id; resource_id; experiment_key; actor; tiempo.

Un conflicto de hash o un deny se conserva. No sobrescribir.

## 9. Piezas reutilizables (no el producto)

- AuthN: Keycloak o equivalente on-prem cuando haya varios clientes; API key HMAC vale mientras sea un operador.
- AuthZ: motor de políticas (OPA / Keycloak Authorization) **consultado por el núcleo**.
- OLAP: Postgres roles + RLS + `pgaudit` **dentro** de ese lake.
- Núcleo + adaptadores + **prompts de roles**: eso **es** el producto. No hay Apache que sea este sistema.

## 10. Primeros lakes (piloto, no techo)

1. `financial_files` — inventario actual de `financial-data`.
2. Un OLAP throwaway — el cubo como lake.

El diseño debe admitir un tercer lake sin reescribir políticas, accounting ni roles.

## 11. Roles (humanos vs Hermes)

| Rol | Quién | En el hot path de un experimento |
|---|---|---|
| CEO | Harvey, **solo humano** | No. Define qué lakes y clientes existen; ve excepciones |
| Ingeniero de datos | Musashi, **solo humano** | No. Adaptadores, tipos de lake, roturas de contrato |
| Científico de datos | Hermes con prompt Satoshi-shaped | **No autoriza.** Opina en eventos: holdout, métrica, comparabilidad |
| Resto de gobernanza (inventario, políticas, accounting, calidad de ficha) | Hermes, un prompt por rol, funciones exactas | **No.** El motor de políticas decide solo |

Ningún `read`/`import` espera a un agente. Allow/deny es código + inventario. Hermes entra por **evento** (recurso nuevo, hash distinto, deny anómalo, lake nuevo, conflicto de cobertura). Cero evento = cero llamada = cero tokens.

Un rol = un prompt versionado en este repo: disparador, inputs, salida (dictamen / parche de inventario / alerta a Harvey), y qué **no** hace. Nada de “revisa el lake por si acaso”.

## 12. Contrato de APIs de lakes (después)

Cada lake, para engancharse, tendrá que implementar el autoinventario y los verbos de §5–§6. Ese contrato de adaptador se escribe cuando el núcleo de gobernanza y los roles existan; no al revés.
