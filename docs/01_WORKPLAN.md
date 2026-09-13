# 01 — Work plan data-gov

**Fecha:** 2026-09-12  
**Estado:** propuesta de Retsu para discutir con Harvey. No es orden de campaña ni de GPU.

El esqueleto (plugins + AdminLTE) **ya está**. Esto es cómo llenarlo sin volver a Gravitino, sin porteros humanos y sin gastar Hermes en vacío.

## 0. Principios

1. Gobernanza = inventario + política automática + accounting + roles por **evento**. El API de acceso es una cara, no el producto.
2. Fail-closed: recurso no inventariado = deny. Deny se conserva.
3. Hot path de un experimento **no espera** a Musashi ni a Satoshi.
4. CEO (Harvey) e ingeniero de datos (Musashi) no se automatizan.
5. Científico de datos (prompt Satoshi-shaped) **opina**, no autoriza.
6. Un rol Hermes = un prompt versionado: disparador, inputs, salida, qué no hace. Cero evento = cero llamada.
7. Varios lakes, on-prem o remotos. Nube pública no es requisito ni arquitectura.
8. No pisa I1–I5 / T2 / E0 de Musashi. Calendario: este repo en huecos CPU; no B4, no GPU.

## 1. Qué ya existe (G0)

- Grupos `datagov.*` y un `default_*` por grupo.
- Merge predictor-style.
- UI: login, dashboard de lakes, disco del host, warnings, detalle + log + stats.
- Accounting SQLite de demo.
- `default_lake` = directorio local.

Eso **no** es gobernanza todavía: no hay API de lake, no hay `experiment_key` obligatorio, Hermes no existe, las políticas son “logueado ⇒ ve todo”.

## 2. Fases

```text
G0 esqueleto          HECHO
G1 tablero honesto    ← siguiente
G2 accounting de verdad
G3 contrato de adaptador + 2 lakes
G4 políticas por recurso (sigue automático)
G5 clientes (predictor / DOIN / heuristic)
G6 roles Hermes por evento
G7 lakes remotos (mismo contrato HTTP)
```

G5 no empieza sin G3. G6 no empieza sin G2 (tiene que haber eventos reales). G7 no inventa S3.

### G1 — Tablero para Harvey (esta semana de calendario, CPU)

**Para qué:** que tú veas *tus* lakes, espacio, avisos y “quién tocó qué”, sin teatro.

- Login de personas (sesión) distinto de claves de **servicio** (predictor/DOIN). El `demo/demo` no sobrevive G1.
- Cada lake del JSON: inventario real del root, `disk_usage` del host de **ese** root.
- Warnings = filas de accounting con `warning`, no un recuadro inventado.
- Nada de API de descarga todavía. Nada de Hermes.

**Done:** abres la UI, ves dos roots configurados, click enseña archivos de verdad y un log que coincide con la SQLite.

### G2 — Accounting como evidencia

**Para qué:** responder “este experimento usó estos bytes”.

- Toda operación programática lleva `experiment_key` o `run_id`. Sin eso, 403 (solo la UI humana puede listar sin experimento).
- Registro: actor, lake, resource, verbo, decisión, rango si aplica, bytes, sha256 de lo entregado, ts.
- Consultas: por experimento, por lake, por actor, por verbo.
- Seed demo se apaga en cuanto haya un lake real.

**Done:** una query reproduce un run sintético: mismas filas, mismo hash.

### G3 — Contrato de adaptador (el API que cada lake debe hablar)

No es “Gravity”. Es **nuestro** contrato, implementado dos veces:

| Verbo | files (`financial_files`) | OLAP (`sql_olap`) |
|---|---|---|
| `discover` | lista + cobertura temporal si el inventario la tiene | tablas/vistas visibles |
| `coverage` | `t_min`/`t_max`/huecos | grano / splits |
| `read` / `read_range` | slice o archivo, con hash | query acotada, con hash de resultado |
| `import`/`create` | solo si la política lo dice | insert no en G3 |

Autoinventario: el núcleo tira `discover` al registrar el lake, en cron corto, y tras import.

Piloto: (1) carpeta al estilo `financial-data` **local**; (2) Postgres **throwaway**, no el cubo poblado.

**Done:** un cliente de prueba pide un rango, recibe bytes + hash, la UI muestra esa fila. Predictor **aún no** está cableado.

### G4 — Autorización automática por recurso

Políticas en config/código: `(identidad, lake_id, resource_pattern, verbo, restricciones)`.

- Recurso no inventariado = deny.
- Concesión de lake no abre un holdout marcado.
- Sigue sin humano en el path.

**Done:** un usuario ve lake A y no el recurso restringido de A; el deny queda en accounting.

### G5 — Clientes

Sustituir `open(csv)` / `PG*` a pelo en **un** camino de `predictor` (config de laboratorio, no campañas). DOIN y heuristic después, mismo cliente.

Header: token de servicio + `experiment_key`.

**Done:** un train CPU chico deja en data-gov el hash del dataset que usó. Si el archivo en disco cambia y el hash no, G2 lo delata.

### G6 — Roles Hermes (eventos, no cron)

Disparadores **únicos**:

- recurso nuevo en inventario
- hash distinto al último `read` del mismo resource+versión
- ráfaga de deny
- lake nuevo registrado

Prompts en `prompts/` de este repo, uno por rol, I/O JSON. El default_role de G0 se vuelve despachador.

El científico de datos **no** devuelve allow/deny. Devuelve dictamen (holdout, comparabilidad) o silencio.

**Done:** un hash mismatch genera **una** llamada Hermes y un aviso en el tablero. Un millón de `read` ok = cero tokens.

### G7 — Lakes remotos

El mismo contrato G3 sobre HTTP (el lake es otro proceso/host). Identidad de adaptador ≠ identidad del cliente. Sigue sin S3 como modelo.

`data-logger` (telemetría) puede ser un lake **aquí**, no al revés.

## 3. Encaje con Musashi / ciencia

| Frente | data-gov |
|---|---|
| I1–I5, T2, E0 | **primero** en CPU científico. data-gov no los bloquea |
| Cubo OLAP real | no es el piloto G3 |
| B4 / live | no |
| P-MOD PDF | no cita data-gov como hipótesis |

Cuando G5 exista, I-INFO puede **escribir** `D.hash` también aquí. No al revés: no retrases el banco de ruido a que data-gov esté “completo”.

## 4. Fuera (explícito)

Gravitino, Unity, OpenMetadata como producto de acceso. Tickets de aprobación. Cron de roles. Porteros Satoshi/Musashi. Nube como requisito. Fusionar con `data-logger`.

## 5. Orden de discusión (Harvey)

Si esto se acepta, el siguiente *código* es **G1** (tablero honesto + claves de servicio vs personas). G3 se diseña en papel en paralelo (contrato de 1 página: verbos + campos de inventario). Hermes no se toca hasta G6.
