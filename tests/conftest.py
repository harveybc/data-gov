"""Shared fixtures. Secrets here are test-only."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.config import DEFAULT_VALUES
from app.config_merger import merge_config
from app.main import GROUPS, _repo_root, assemble, check_startup
from app.plugin_loader import get_plugin_params

SALT = "datagov-test-salt"
HOLD_OUT = "2025-01-01"


def digest(secret: str) -> str:
    return hashlib.sha256(f"{SALT}:{secret}".encode()).hexdigest()


PREDICTOR_KEY = "predictor-test-key"
DOIN_KEY = "doin-test-key"
HEURISTIC_KEY = "heuristic-test-key"
HUMAN_PASS = "human-test-pass"

FINANCIAL_ROOT = _repo_root().parent / "financial-data"
BTC_FUNDING = (
    "market_data/crypto/funding_rates/btcusdt/funding_rates.parquet"
)

# third lake of 04_FLOW_V2 §7: the predictor sample data, skipped when the sibling is absent
PREDICTOR_DATA = _repo_root().parent / "predictor" / "examples" / "data_downsampled"
PREDICTOR_RESOURCE = "phase_1/normalized_d4.csv"
HAS_PREDICTOR_DATA = (PREDICTOR_DATA / PREDICTOR_RESOURCE).is_file()

# small in-process lake written per test under tmp_path
LAB_HOURLY = "hourly.csv"  # naive hourly bars 2024-12-30 00:00 .. 2025-01-01 02:00 (spans holdout)
LAB_EARLY = "early.csv"  # daily bars 2024-06-01 .. 2024-06-03 (ends before holdout)
LAB_STATIC = "static.csv"  # no time axis, declared untimed


def hourly_lines(start="2024-12-30 00:00:00", hours=51, suffix=""):
    t0 = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
    return [
        f"{(t0 + timedelta(hours=i)):%Y-%m-%d %H:%M:%S}{suffix},{i}" for i in range(hours)
    ]


def write_lab_files(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / LAB_HOURLY).write_text("ts,value\n" + "\n".join(hourly_lines()) + "\n")
    (root / LAB_EARLY).write_text(
        "ts,value\n2024-06-01 00:00:00,1\n2024-06-02 00:00:00,2\n2024-06-03 00:00:00,3\n"
    )
    (root / LAB_STATIC).write_text("a,b\n1,2\n3,4\n")
    return root


def _write_olap(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE fact_performance (ts TEXT, experiment_key TEXT, metric TEXT, value REAL)"
    )
    conn.executemany(
        "INSERT INTO fact_performance VALUES (?,?,?,?)",
        [
            ("2024-06-01", "ann_1575", "MAE", 0.01),
            ("2025-06-01", "ann_1575", "MAE", 0.99),
        ],
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def gov_config(tmp_path):
    olap = _write_olap(tmp_path / "olap.sqlite")
    lab_root = write_lab_files(tmp_path / "lab_files")
    file_config = {
        "pipeline_plugin": "default_pipeline",
        "web_plugin": "default_web",
        "access_plugin": "default_access",
        "accounting_plugin": "default_accounting",
        "role_plugin": "default_role",
        "secret_key": "test-secret",
        "password_salt": SALT,
        "accounting_db": str(tmp_path / "acct.db"),
        "spool_dir": str(tmp_path / "spool"),
        "cuts_dir": str(tmp_path / "cuts"),
        "max_downloads": 2,
        "seed_demo_logs": False,
        "principals": {
            "harvey": {
                "kind": "person",
                "password_hash": digest(HUMAN_PASS),
                "display_name": "Harvey",
                "role": "ceo",
            },
            "predictor": {
                "kind": "service",
                "api_key_hash": digest(PREDICTOR_KEY),
                "role": "service",
            },
            "doin": {
                "kind": "service",
                "api_key_hash": digest(DOIN_KEY),
                "role": "service",
            },
            "heuristic-strategy": {
                "kind": "service",
                "api_key_hash": digest(HEURISTIC_KEY),
                "role": "service",
            },
        },
        "policies": [
            {
                "principal": "*",
                "lake": "financial_files",
                "verbs": ["discover", "coverage", "read", "download"],
                "deny_from": HOLD_OUT,
            },
            {
                "principal": "*",
                "lake": "olap_lab",
                "verbs": ["discover", "query", "write_metrics"],
                "require_lineage": False,
                "deny_from": HOLD_OUT,
            },
            {
                "principal": "*",
                "lake": "olap_strict",
                "verbs": ["discover", "query", "write_metrics"],
                "require_lineage": True,
            },
            {
                "principal": "*",
                "lake": "lab_files",
                "verbs": ["discover", "coverage", "read", "download"],
                "deny_from": HOLD_OUT,
            },
            {
                "principal": "*",
                "lake": "predictor_examples",
                "verbs": ["discover", "coverage", "read", "download"],
                "deny_from": HOLD_OUT,
            },
        ],
        "lakes": [
            {
                "plugin": "files_lake",
                "lake_id": "financial_files",
                "title": "Financial files",
                "description": "financial-data subset",
                "kind": "files_inventory",
                "root_path": str(FINANCIAL_ROOT),
                "include_globs": [BTC_FUNDING],
                "time_column": "fundingTime",
                "holdout_start": HOLD_OUT,
            },
            {
                "plugin": "sql_lake",
                "lake_id": "olap_lab",
                "title": "OLAP lab",
                "description": "sqlite cube",
                "kind": "sql_olap",
                "sqlite_path": str(olap),
                "time_column": "ts",
                "holdout_start": HOLD_OUT,
            },
            {
                "plugin": "sql_lake",
                "lake_id": "olap_strict",
                "title": "OLAP strict",
                "description": "sqlite cube that requires lineage",
                "kind": "sql_olap",
                "sqlite_path": str(tmp_path / "strict.sqlite"),
                "time_column": "ts",
                "holdout_start": None,
            },
            {
                "plugin": "files_lake",
                "lake_id": "lab_files",
                "title": "Lab files",
                "description": "small csv lake written by the test",
                "kind": "files_inventory",
                "root_path": str(lab_root),
                "include_globs": ["**/*.csv"],
                "time_column": "ts",
                "untimed": [LAB_STATIC],
                "resource_contracts": {
                    LAB_HOURLY: {
                        "event_time_column": "ts",
                        "available_time_column": "ts",
                        "timezone": "NAIVE_WALL_CLOCK",
                        "time_unit": None,
                        "frequency": "1h",
                    },
                    LAB_EARLY: {
                        "event_time_column": "ts",
                        "available_time_column": "ts",
                        "timezone": "NAIVE_WALL_CLOCK",
                        "time_unit": None,
                        "frequency": "1d",
                    },
                },
                "holdout_start": HOLD_OUT,
            },
        ],
    }
    if HAS_PREDICTOR_DATA:
        file_config["lakes"].append(
            {
                "plugin": "files_lake",
                "lake_id": "predictor_examples",
                "title": "predictor examples",
                "description": "predictor sample data",
                "kind": "files_inventory",
                "root_path": str(PREDICTOR_DATA),
                "include_globs": ["**/*.csv"],
                "time_column": "DATE_TIME",
                "holdout_start": HOLD_OUT,
            }
        )
    params = []
    for key, group in GROUPS.items():
        params.append(get_plugin_params(group, file_config[key]))
    for lake in file_config["lakes"]:
        params.append(get_plugin_params("datagov.lake", lake["plugin"]))
    config = merge_config(DEFAULT_VALUES, params, file_config, {}, {})
    config.update(file_config)
    return config


@pytest.fixture
def runtime(gov_config):
    check_startup(gov_config)
    plugins = assemble(gov_config)
    return {"config": gov_config, "plugins": plugins}


@pytest.fixture
def client(runtime):
    app = runtime["plugins"]["web"].create_app(runtime)
    app.config["TESTING"] = True
    return app.test_client()


def lake_spec(runtime, lake_id):
    return next(spec for spec in runtime["config"]["lakes"] if spec["lake_id"] == lake_id)
