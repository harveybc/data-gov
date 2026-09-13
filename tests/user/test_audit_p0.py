"""User-level: the audit holes must stay closed."""

from tests.conftest import PREDICTOR_KEY, FINANCIAL_ROOT

import pytest
from app.client import DataGovClient


RESOURCE = "market_data/crypto/funding_rates/btcusdt/funding_rates.parquet"


def test_login_page_does_not_leak_home_path(client):
    page = client.get("/login")
    assert page.status_code == 200
    assert b"/home/" not in page.data
    assert b"var/credentials.json" in page.data


def test_read_without_range_is_rejected(client):
    gov = DataGovClient(
        test_client=client, api_key=PREDICTOR_KEY, experiment_key="no-range"
    )
    status, body = gov.read("financial_files", RESOURCE)
    assert status == 400
    assert "from" in (body.get("error") or "").lower()


@pytest.mark.skipif(not (FINANCIAL_ROOT / RESOURCE).exists(), reason="no parquet")
def test_holdout_still_denied(client):
    gov = DataGovClient(
        test_client=client, api_key=PREDICTOR_KEY, experiment_key="holdout"
    )
    status, body = gov.read(
        "financial_files", RESOURCE, start="2025-06-01", end="2025-06-30"
    )
    assert status == 403
