"""System tests: tools and rules of the governance kernel."""

from tests.conftest import PREDICTOR_KEY


def test_unknown_resource_is_fail_closed(client):
    from app.client import DataGovClient

    gov = DataGovClient(
        test_client=client,
        api_key=PREDICTOR_KEY,
        experiment_key="ghost",
    )
    status, body = gov.read(
        "financial_files",
        "does/not/exist.parquet",
        start="2020-01-01",
        end="2020-01-02",
    )
    assert status in (403, 404)
    assert body.get("error")


def test_sql_injection_is_rejected(client):
    from app.client import DataGovClient

    gov = DataGovClient(
        test_client=client,
        api_key=PREDICTOR_KEY,
        experiment_key="evil",
    )
    status, body = gov.query(
        "olap_lab",
        "SELECT * FROM fact_performance; DROP TABLE fact_performance;",
    )
    assert status == 400


def test_successful_reads_do_not_wake_roles(runtime, client):
    from app.client import DataGovClient

    role = runtime["plugins"]["role"]
    before = len(role.events)
    gov = DataGovClient(
        test_client=client, api_key=PREDICTOR_KEY, experiment_key="quiet"
    )
    gov.lakes()
    assert len(role.events) == before


def test_hash_mismatch_event_is_dispatched(runtime):
    role = runtime["plugins"]["role"]
    runtime["plugins"]["accounting"].record(
        actor="predictor",
        lake_id="financial_files",
        verb="read",
        resource_id="x",
        decision="allow",
        sha256="aaa",
        experiment_key="e1",
    )
    runtime["plugins"]["accounting"].record(
        actor="predictor",
        lake_id="financial_files",
        verb="read",
        resource_id="x",
        decision="allow",
        sha256="bbb",
        experiment_key="e2",
    )
    role.scan_accounting(runtime["plugins"]["accounting"])
    assert any(ev.get("kind") == "hash_mismatch" for ev in role.events)
