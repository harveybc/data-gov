"""Integration: access + lake + accounting without the HTTP layer."""

from tests.conftest import PREDICTOR_KEY, digest


def test_access_accepts_service_key_and_rejects_unknown(runtime):
    access = runtime["plugins"]["access"]
    principal = access.authenticate_api_key(PREDICTOR_KEY)
    assert principal["username"] == "predictor"
    assert access.authenticate_api_key("nope") is None


def test_files_lake_discover_and_coverage(runtime):
    lake = runtime["plugins"]["lakes"]["financial_files"]
    found = {item["resource_id"] for item in lake.discover()}
    assert any("funding_rates.parquet" in name for name in found)
    coverage = lake.coverage(
        "market_data/crypto/funding_rates/btcusdt/funding_rates.parquet"
    )
    assert coverage["t_min"] < coverage["t_max"]


def test_accounting_roundtrip_experiment(runtime):
    acc = runtime["plugins"]["accounting"]
    acc.record(
        actor="predictor",
        lake_id="financial_files",
        verb="read",
        resource_id="r",
        decision="allow",
        sha256="abc",
        bytes=10,
        experiment_key="exp-int",
    )
    rows = acc.usage("exp-int")
    assert rows[0]["sha256"] == "abc"
