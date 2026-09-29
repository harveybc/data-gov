"""A discover preflight must not go green against a lake nobody could reach.

The kernel's `/api/v1/resources` route recorded `discover` ALLOW *before* asking the lake, and the
http adapter returned `[]` for a lake it could not reach.  Together those two produced a 200 with
an empty inventory and an ALLOW in the ledger for a store that had been stopped four days earlier
(`public_panels`, 2026-09-27).  A preflight that passes on an outage is worse than no preflight:
it converts the outage into an apparent success, and the failure resurfaces later and somewhere
else as a result that reads like "no resources".

These tests pin the distinction in both places it must appear -- the wire and the ledger -- and
they pin that a lake which is genuinely reachable and genuinely empty still answers 200.
"""

import copy

import pytest

from data_gov.client import DataGovClient
from data_gov.main import assemble, check_startup
from tests.conftest import PREDICTOR_KEY

DEAD = "dead_panels"
EMPTY = "empty_panels"
# Port 1 needs no privilege to connect to and nothing listens on it, so the connection is refused
# immediately.  No service is started, stopped or touched by any of this.
DEAD_URL = "http://127.0.0.1:1"


@pytest.fixture
def runtime_with_lakes(gov_config):
    """The shipped config plus one http lake whose store is not running.  Nothing is deployed."""
    config = copy.deepcopy(gov_config)
    config["lakes"] = list(config["lakes"]) + [
        {
            "plugin": "http_lake",
            "lake_id": DEAD,
            "title": "Dead panels",
            "description": "an http lake whose store is not running",
            "kind": "files_inventory",
            "base_url": DEAD_URL,
            "holdout_start": None,
        },
    ]
    config["policies"] = list(config["policies"]) + [
        {
            "principal": "*",
            "lake": DEAD,
            "verbs": ["discover", "coverage", "read", "download"],
        },
    ]
    check_startup(config)
    return {"config": config, "plugins": assemble(config)}


@pytest.fixture
def client_with_lakes(runtime_with_lakes):
    app = runtime_with_lakes["plugins"]["web"].create_app(runtime_with_lakes)
    app.config["TESTING"] = True
    return app.test_client()


def _api(client):
    return DataGovClient(test_client=client, api_key=PREDICTOR_KEY,
                         experiment_key="unreachable-preflight")


def _discover_rows(runtime, lake_id):
    return [row for row in runtime["plugins"]["accounting"].logs_all()
            if row["verb"] == "discover" and row["lake_id"] == lake_id]


# ------------------------------------------------------------------------------- the wire

def test_discovering_an_unreachable_lake_is_refused_and_names_it(client_with_lakes):
    """The counterexample.  Before the repair this was 200 with `{"resources": []}`."""
    status, body = _api(client_with_lakes).resources(DEAD)
    assert status == 503, f"an unreachable lake answered {status}: {body}"
    assert body.get("reachable") is False
    assert body.get("lake") == DEAD
    message = (body.get("error") or "").lower()
    assert DEAD in message
    assert "unknown, not empty" in message, (
        f"the refusal must say the inventory is unknown rather than empty; got {message!r}"
    )


def test_the_refusal_carries_no_resources_key_that_could_be_read_as_empty(client_with_lakes):
    """A caller that does `body.get("resources", [])` must not be handed a plausible empty list."""
    _, body = _api(client_with_lakes).resources(DEAD)
    assert "resources" not in body


def test_a_reachable_lake_still_answers_200_with_its_inventory(client_with_lakes):
    status, body = _api(client_with_lakes).resources("financial_files")
    assert status == 200
    assert body.get("reachable") is True
    assert isinstance(body.get("resources"), list)


def test_a_reachable_lake_that_holds_nothing_is_200_and_empty_not_503(
    gov_config, monkeypatch
):
    """`reachable and empty` must stay a success, or the repair would have broken the other half."""
    from lake_plugins.http_lake import Plugin

    config = copy.deepcopy(gov_config)
    config["lakes"] = list(config["lakes"]) + [
        {"plugin": "http_lake", "lake_id": EMPTY, "title": "Empty panels",
         "description": "reachable, holds nothing", "kind": "files_inventory",
         "base_url": DEAD_URL, "holdout_start": None},
    ]
    config["policies"] = list(config["policies"]) + [
        {"principal": "*", "lake": EMPTY, "verbs": ["discover"]},
    ]
    monkeypatch.setattr(Plugin, "_get", lambda self, path, params=None: {"resources": []})
    check_startup(config)
    runtime = {"config": config, "plugins": assemble(config)}
    app = runtime["plugins"]["web"].create_app(runtime)
    app.config["TESTING"] = True
    client = app.test_client()

    status, body = _api(client).resources(EMPTY)
    assert status == 200 and body["resources"] == [] and body["reachable"] is True
    rows = _discover_rows(runtime, EMPTY)
    assert rows and rows[0]["decision"] == "allow", (
        "a lake that was reached and holds nothing is an ALLOW; only an outage is not"
    )


# ------------------------------------------------------------------------------ the ledger

def test_the_recorded_event_says_unreachable_and_not_allow(
    client_with_lakes, runtime_with_lakes
):
    """The half of the false green that survived in the record even after the wire was fixed."""
    _api(client_with_lakes).resources(DEAD)
    rows = _discover_rows(runtime_with_lakes, DEAD)
    assert rows, "the attempt was not recorded at all"
    assert rows[0]["decision"] == "unreachable", (
        f"an outage was recorded as {rows[0]['decision']!r}; a discover that reached nothing must "
        "never be indistinguishable from one that succeeded"
    )
    assert "unreachable" in (rows[0].get("warning") or "").lower()


def test_an_outage_is_distinguishable_from_a_refusal_and_from_a_success(
    client_with_lakes, runtime_with_lakes
):
    """Three outcomes, three decisions: allow, deny, unreachable.

    The refusal leg uses a lake with no policy.  Every lake the shipped test fixture declares has
    one, so `no_such_lake` is the honest way to get an authorization deny here -- `authorize`
    returns `no policy` for it, which is a deny and not a missing-lake 404.
    """
    api = _api(client_with_lakes)
    api.resources(DEAD)                     # outage
    api.resources("financial_files")        # success
    api.resources("no_such_lake")           # no policy -> refusal
    decisions = {
        row["lake_id"]: row["decision"]
        for row in runtime_with_lakes["plugins"]["accounting"].logs_all()
        if row["verb"] == "discover"
    }
    assert decisions.get(DEAD) == "unreachable"
    assert decisions.get("financial_files") == "allow"
    assert decisions.get("no_such_lake") == "deny"
    assert len(set(decisions.values())) == 3, (
        f"the three outcomes collapsed into {sorted(set(decisions.values()))}"
    )


def test_an_unknown_lake_is_still_a_deny_and_not_an_outage(
    client_with_lakes, runtime_with_lakes
):
    """"I have never heard of this lake" is not "I could not reach it"."""
    status, _ = _api(client_with_lakes).resources("no_such_lake")
    assert status in (403, 404)
    rows = _discover_rows(runtime_with_lakes, "no_such_lake")
    assert rows and rows[0]["decision"] == "deny"


# -------------------------------------------------------------------------- the operator page

def test_the_dashboard_survives_a_dead_lake_and_does_not_show_it_as_zero(
    client_with_lakes
):
    """The page must not 500 now that discover refuses, and must not print "0 resources"."""
    client_with_lakes.post("/login", data={"username": "harvey", "password": "human-test-pass"})
    page = client_with_lakes.get("/")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    if DEAD in body:
        assert "unreachable" in body.lower(), (
            "the dashboard rendered an unreachable lake without saying so"
        )


def test_the_lake_page_of_a_dead_lake_opens_and_says_the_inventory_is_unknown(
    client_with_lakes
):
    """Its logs and statistics are local and stay useful during an outage, so the page still opens."""
    client_with_lakes.post("/login", data={"username": "harvey", "password": "human-test-pass"})
    page = client_with_lakes.get(f"/lakes/{DEAD}", follow_redirects=True)
    assert page.status_code == 200
    body = page.get_data(as_text=True).lower()
    assert "unreachable" in body or "not available" in body
