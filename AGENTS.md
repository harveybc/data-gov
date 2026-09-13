# AGENTS.md — data-gov

Guidance for AI coding agents. See [agents.md](https://agents.md).

## Project overview

`data-gov` is the **data governance** system for multiple lakes: inventory,
automatic policy, accounting, and event-driven Hermes roles. It is **not**
a cloud catalog, not Gravitino, and not a standalone AAA API.

CEO (Harvey) and data engineer (Musashi) stay human. Other governance and
technical roles are prompts that fire **only on events**. The data-scientist
role (Satoshi-shaped) must not gate a `GET`.

Plugins resolve through setuptools entry points in `setup.py` (`datagov.*`
groups). Config merge: plugin_params → defaults → JSON file → long-form CLI.

`data-logger` is a different product (sensor telemetry). Do not merge them.
A telemetry site may become a **lake adapter** later.

## Agent quickstart

```bash
cd data-gov
pip install -r requirements.txt
pip install -e .
PYTHONPATH=. python3 -m app.main --load_config examples/config/default.json
```

Open http://127.0.0.1:5055 — `demo` / `demo`. Force CPU. Do not stop GPU,
Postgres, or Metabase processes you did not start. Do not write host names
or credentials into committed files.

## Do not

- Add S3/HDFS/GVFS as the architecture.
- Put Musashi or Satoshi on the read hot path.
- Poll Hermes on a timer with no event.
- Treat the demo SQLite log as production evidence.
