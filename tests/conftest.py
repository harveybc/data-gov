"""Shared fixtures. Secrets here are test-only."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from app.config import DEFAULT_VALUES
from app.config_merger import merge_config
from app.main import GROUPS, _repo_root, assemble
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
    file_config = {
        "pipeline_plugin": "default_pipeline",
        "web_plugin": "default_web",
        "access_plugin": "default_access",
        "accounting_plugin": "default_accounting",
        "role_plugin": "default_role",
        "secret_key": "test-secret",
        "password_salt": SALT,
        "accounting_db": str(tmp_path / "acct.db"),
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
                "verbs": ["discover", "coverage", "read"],
                "deny_from": HOLD_OUT,
            },
            {
                "principal": "*",
                "lake": "olap_lab",
                "verbs": ["discover", "query"],
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
        ],
    }
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
    plugins = assemble(gov_config)
    return {"config": gov_config, "plugins": plugins}


@pytest.fixture
def client(runtime):
    app = runtime["plugins"]["web"].create_app(runtime)
    app.config["TESTING"] = True
    return app.test_client()
