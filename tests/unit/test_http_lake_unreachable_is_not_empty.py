"""An unreachable lake must refuse by name, never report an empty inventory.

`http_lake.discover()` used to be `except RuntimeError: return []`.  That is a governance false
green, and it is worse than having no preflight at all: an empty list is a POSITIVE claim -- *I
reached this lake and it holds nothing* -- so returning it for a lake nobody reached converts an
outage into an apparent success.  The caller's preflight goes green, the ledger records `discover`
ALLOW, and the failure resurfaces later and somewhere else as a result that reads like "no
resources".

On 2026-09-27 that is exactly what the live kernel recorded: a `discover` on `public_panels`,
decision ALLOW, four days after that lake's service had been stopped.

`reachable and empty` and `unreachable` are different facts.  These tests pin the difference in
BOTH places it has to appear: the adapter's return value, and the recorded accounting event.
"""

import pytest

from lake_plugins.errors import LakeUnreachable
from lake_plugins.http_lake import Plugin


def _lake(**params):
    lake = Plugin()
    lake.set_params(lake_id="dead", base_url="http://127.0.0.1:1", **params)
    return lake


# ----------------------------------------------------------------- the adapter's return value

def test_an_unreachable_lake_refuses_discovery_by_name_instead_of_returning_empty():
    """The counterexample.  Before the repair this returned [] and the caller could not tell."""
    lake = _lake()
    with pytest.raises(LakeUnreachable) as caught:
        lake.discover()
    assert "unreachable" in str(caught.value).lower()


def test_list_resources_refuses_too_because_it_is_the_same_question():
    lake = _lake()
    with pytest.raises(LakeUnreachable):
        lake.list_resources()


def test_the_refusal_is_a_named_type_and_not_a_bare_runtime_error():
    """`LakeUnreachable` is what the web layer maps to 503; a bare RuntimeError is a 500.

    It subclasses RuntimeError so callers that only know that family still catch it, but the
    specific type is what lets a caller distinguish transport from everything else.
    """
    lake = _lake()
    with pytest.raises(LakeUnreachable):
        lake.discover()
    assert issubclass(LakeUnreachable, RuntimeError)


def test_a_reachable_lake_that_holds_nothing_still_returns_an_empty_list(monkeypatch):
    """The other half of the distinction: empty is a legitimate answer when someone answered."""
    lake = _lake()
    monkeypatch.setattr(Plugin, "_get", lambda self, path, params=None: {"resources": []})
    assert lake.discover() == []


def test_a_reachable_lake_with_resources_is_unchanged(monkeypatch):
    lake = _lake()
    monkeypatch.setattr(
        Plugin, "_get",
        lambda self, path, params=None: {"resources": [{"resource_id": "a.parquet"}]},
    )
    assert [r["resource_id"] for r in lake.discover()] == ["a.parquet"]


def test_a_store_that_answers_401_is_not_an_empty_inventory_either(monkeypatch):
    """Nothing else is swallowed into emptiness either: a rejected token is not "no resources"."""
    lake = _lake()

    def _boom(self, path, params=None):
        raise RuntimeError("unauthenticated")

    monkeypatch.setattr(Plugin, "_get", _boom)
    with pytest.raises(RuntimeError):
        lake.discover()


# ------------------------------------------------------------- describe/storage say so as well

def test_describe_of_an_unreachable_lake_reports_itself_unreachable():
    """It still answers, because it feeds a page -- but it no longer looks healthy."""
    meta = _lake().describe()
    assert meta["reachable"] is False
    assert "unreachable" in (meta["unreachable_reason"] or "").lower()


def test_storage_of_an_unreachable_lake_labels_its_zeroes_as_an_outage():
    """Zero bytes free on a dead lake is not a storage fact; it is the absence of one."""
    storage = _lake().storage()
    assert storage["reachable"] is False
    assert storage["lake_bytes"] == 0
    assert "unreachable" in (storage["unreachable_reason"] or "").lower()


def test_storage_of_a_reachable_lake_is_marked_reachable(monkeypatch):
    lake = _lake()
    monkeypatch.setattr(
        Plugin, "_get",
        lambda self, path, params=None: {"host_total": 10, "host_used": 1, "host_free": 9,
                                         "lake_bytes": 1},
    )
    storage = lake.storage()
    assert storage["reachable"] is True and storage["unreachable_reason"] is None
