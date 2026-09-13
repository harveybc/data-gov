from pathlib import Path

from lake_plugins.files_lake import Plugin
from tests.conftest import BTC_FUNDING, FINANCIAL_ROOT

import pytest


pytestmark = pytest.mark.skipif(
    not (FINANCIAL_ROOT / BTC_FUNDING).exists(),
    reason="financial-data not present",
)


def test_slice_hash_is_stable():
    lake = Plugin()
    lake.set_params(
        lake_id="financial_files",
        root_path=str(FINANCIAL_ROOT),
        include_globs=[BTC_FUNDING],
        time_column="fundingTime",
        holdout_start="2025-01-01",
    )
    first = lake.read(BTC_FUNDING, start="2020-01-01", end="2020-01-07")
    second = lake.read(BTC_FUNDING, start="2020-01-01", end="2020-01-07")
    assert first["sha256"] == second["sha256"]
    assert first["rows"]
    assert Path(BTC_FUNDING).as_posix() in [
        item["resource_id"] for item in lake.discover()
    ]
