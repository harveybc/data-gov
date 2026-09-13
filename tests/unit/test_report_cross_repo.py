"""data-gov and the predictor OLAP lake must compute the same report_sha256, or every forwarded report is refused."""

import importlib.util
import sys
from pathlib import Path

import pytest

from app.report import report_sha256

LAKE_QUERY = Path(__file__).resolve().parents[3] / "predictor" / "olap" / "lake" / "query_plugins" / "sql_query.py"


def _lake_module():
    spec = importlib.util.spec_from_file_location("olap_lake_sql_query_for_test", LAKE_QUERY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skipif(not LAKE_QUERY.is_file(), reason="predictor checkout not beside data-gov")
def test_both_sides_hash_a_report_identically():
    report = {
        "experiment_key": "exp-1", "experiment_set_key": "set-1", "actor": "predictor", "lake": "olap_cube",
        "config_sha256": "c" * 64, "code_commit": "abc123", "project": "predictor", "phase": "phase_1_daily",
        "tags": {"plugin": "ann"},
        "datasets": [{"lake": "predictor_examples", "resource": "phase_1/normalized_d5.csv", "sha256": "b" * 64,
                      "role": "x_validation_file"},
                     {"lake": "predictor_examples", "resource": "phase_1/normalized_d4.csv", "sha256": "a" * 64,
                      "role": "x_train_file"}],
        "metrics": [{"metric": "MAE", "value": 0.25, "split": "validation", "horizon": 24, "std_dev": 0.5,
                     "min_value": 0.125, "max_value": 0.75, "unit": None},
                    {"metric": "MAE", "value": 0.5, "split": "train", "horizon": 24, "std_dev": None,
                     "min_value": None, "max_value": None, "unit": None}],
    }
    lake = _lake_module()
    assert report_sha256(report) == lake.report_sha256(report)
