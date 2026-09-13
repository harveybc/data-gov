# data-gov

Sistema de **gobernanza de datos** para varios lakes (remotos u on-prem).
No es un catálogo cloud, no es Gravitino, no es un AAA suelto.

Hace tres cosas a la vez:

1. **Núcleo** — inventario, políticas automáticas, accounting (hashes, allow/deny, experimento).
2. **Adaptadores de lake** — cada lake se enchufa; `financial-data` y un OLAP son los primeros, no el techo.
3. **Roles** — prompts Hermes que despiertan **solo con evento**. CEO (humano) e ingeniero de datos (Musashi) no se automatizan. El científico de datos (Satoshi-shaped) **no autoriza** un `GET`.

El experimento no espera un ticket. Allow/deny es código + inventario.

Contrato: [`docs/00_CONTRATO.md`](docs/00_CONTRATO.md).

## Plugins (setuptools, igual que predictor)

Seis tipos. Autenticación y política son **un** plugin (`access`). El inventario es `lake.discover()`, no un tipo aparte. Los roles Hermes son config/prompts de un solo despachador.

| Grupo | Oficio | Default |
|---|---|---|
| `datagov.pipeline` | Orquesta | `default_pipeline` |
| `datagov.web` | UI AdminLTE + API HTTP | `default_web` |
| `datagov.access` | Personas, API keys, políticas | `default_access` |
| `datagov.accounting` | Bitácora append-only | `default_accounting` |
| `datagov.lake` | Adaptador | `files_lake`, `sql_lake` |
| `datagov.role` | Eventos (sin Hermes hasta que haya evento) | `default_role` |

Merge de config (más tarde gana), misma casa que predictor:

`plugin_params` → `app/config.py` defaults → JSON `--load_config` → flags largos `--…` (los cortos no).

Los lakes se listan en el JSON (`lakes[].plugin`, `lake_id`, `root_path`, …).

## Install

Python 3.10+ (ejercido en 3.12).

```bash
git clone https://github.com/harveybc/data-gov.git
cd data-gov
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

`setup.py` declara un `install_requires` mínimo; usa `requirements.txt` como lista de trabajo.

## Uso básico

Desde el checkout:

```bash
PYTHONPATH=. python3 -m app.main --load_config examples/config/default.json
# o: sh scripts/data-gov.sh
```

Abre **http://127.0.0.1:5055**.

Personas: usuario/clave en `var/credentials.json` (gitignored; se crea con `python3 scripts/issue_credentials.py`).
Servicios (`predictor`, `doin`, `heuristic-strategy`): API key Bearer + header `X-Experiment-Key` en cada `read`/`query`.

```python
from app.client import DataGovClient
gov = DataGovClient("http://127.0.0.1:5055", api_key="…", experiment_key="ann_1575_1d")
gov.read("financial_files", "market_data/crypto/funding_rates/btcusdt/funding_rates.parquet",
         start="2020-01-01", end="2020-01-31")
```

Holdout automático: rangos ≥ `2025-01-01` se deniegan y quedan en accounting.

Lo que ves:

- Dashboard de **lakes que el usuario puede ver**
- Operaciones registradas, recursos inventariados
- **Espacio libre del host** de cada lake
- Últimos **warnings** del accounting
- Click en un lake → descripción, inventario, log de uso, estadísticas por **operación** y por **actor**

Este esqueleto usa dos lakes demo (`financial_files`, `olap_lab`) sobre carpetas locales. Los adaptadores reales (API de `financial-data`, SQL del cubo) vienen después.

Parar: Ctrl+C. No toca GPU, Postgres de campañas, ni Metabase.

## Layout

```
app/                    CLI, defaults, merge, plugin loader
pipeline_plugins/
web_plugins/            AdminLTE (CDN, mismo patrón que doin-node)
authn_plugins/
authz_plugins/
accounting_plugins/
inventory_plugins/
lake_plugins/
role_plugins/
examples/config/
examples/data/
docs/00_CONTRATO.md
```

## Qué no es este repo

- No es `data-logger` (eso es telemetría ESP32 / ThingsBoard).
- No gobierna por “abrir el CSV en el disco”. El cliente, cuando exista el API de lake, pasa por aquí.
- No hay porteros humanos en el hot path. Hermes no se despierta “por si acaso”.
