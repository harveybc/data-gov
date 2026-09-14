"""Store classification changes metadata, never policy or adapter capabilities."""
import copy
import json
from pathlib import Path

import pytest

from app.main import assemble, check_startup
from lake_plugins.files_lake import Plugin as Files
from lake_plugins.http_lake import Plugin as Http
from lake_plugins.sql_lake import Plugin as Sql


@pytest.mark.parametrize("cls,legacy,kind,engine", [
    (Files, "files_inventory", "lake", "files_inventory"),
    (Sql, "sql_olap", "warehouse", "sql_olap"),
])
def test_local_metadata_and_legacy_config(cls, legacy, kind, engine):
    plugin = cls()
    for value in (kind, legacy):
        plugin.set_params(kind=value)
        info = plugin.describe()
        assert info["kind"] == kind
        assert info["engine"] == engine
        assert info["transport"] == "local"


@pytest.mark.parametrize("remote_kind,kind", [("files_inventory", "lake"), ("sql_olap", "warehouse"),
                                                ("lake", "lake"), ("warehouse", "warehouse")])
def test_http_uses_remote_kind_not_transport(monkeypatch, remote_kind, kind):
    plugin = Http()
    monkeypatch.setattr(plugin, "_get", lambda *a: {"kind": remote_kind})
    assert plugin.describe()["kind"] == kind
    assert plugin.describe()["transport"] == "http"


def test_configured_http_kind_survives_unavailable_remote(monkeypatch):
    plugin = Http()
    plugin.set_params(kind="warehouse", engine="sql_olap")
    def offline(*args):
        raise RuntimeError("offline")
    monkeypatch.setattr(plugin, "_get", offline)
    assert plugin.describe()["kind"] == "warehouse"
    assert plugin.describe()["engine"] == "sql_olap"


def test_legacy_http_without_type_is_not_called_lake(monkeypatch):
    plugin = Http()
    plugin.set_params(kind="http")
    monkeypatch.setattr(plugin, "_get", lambda *a: {"kind": "http"})
    assert plugin.describe()["kind"] is None


@pytest.mark.parametrize("remote_kind", ["unknown", [], None])
def test_unrecognized_remote_metadata_stays_unclassified(monkeypatch, remote_kind):
    plugin = Http()
    monkeypatch.setattr(plugin, "_get", lambda *a: {"kind": remote_kind})
    assert plugin.describe()["kind"] is None


@pytest.mark.parametrize("cls,kind", [(Files, "warehouse"), (Sql, "lake"), (Http, "lakehouse"),
                                    (Files, False), (Http, "cheap_cloud")])
def test_invalid_classification_refuses_at_configuration(cls, kind):
    with pytest.raises(ValueError, match="kind"):
        cls().set_params(kind=kind)


def test_assembly_does_not_inherit_another_stores_kind(gov_config):
    config = copy.deepcopy(gov_config)
    config.update(kind="warehouse", engine="sql_olap")
    for spec in config["lakes"]:
        spec.pop("kind", None)
    plugins = assemble(config)
    assert plugins["lakes"]["lab_files"].describe()["kind"] == "lake"
    assert plugins["lakes"]["olap_lab"].describe()["kind"] == "warehouse"


def test_same_policies_and_existing_plugin_namespace(gov_config):
    original = copy.deepcopy(gov_config)
    changed = copy.deepcopy(gov_config)
    for spec in changed["lakes"]:
        spec["kind"] = "warehouse" if spec["plugin"] == "sql_lake" else "lake"
    check_startup(changed)
    left, right = assemble(original), assemble(changed)
    principal = {"username": "predictor", "kind": "service"}
    for store in ("lab_files", "olap_lab"):
        for verb in ("discover", "download", "query", "write_metrics"):
            assert left["access"].authorize(principal, store, verb) == right["access"].authorize(principal, store, verb)
    assert changed["policies"] == original["policies"]
    setup = (Path(__file__).resolve().parents[2] / "setup.py").read_text()
    assert '"datagov.lake"' in setup
    assert '"datagov.store"' not in setup


def test_shipped_store_classification():
    config = json.loads((Path(__file__).resolve().parents[2] / "examples/config/default.json").read_text())
    actual = {s["lake_id"]: s["kind"] for s in config["lakes"]}
    assert actual == {"financial_files": "lake", "olap_cube": "warehouse", "predictor_examples": "lake"}
