from pathlib import Path

from app.config import DEFAULT_VALUES
from app.config_handler import load_config
from app.config_merger import merge_config
from app.main import GROUPS, _repo_root, assemble
from app.plugin_loader import get_plugin_params, load_plugin


def test_all_default_plugins_register():
    for group, name in [
        ("datagov.pipeline", "default_pipeline"),
        ("datagov.web", "default_web"),
        ("datagov.authn", "default_authn"),
        ("datagov.authz", "default_authz"),
        ("datagov.accounting", "default_accounting"),
        ("datagov.inventory", "default_inventory"),
        ("datagov.lake", "default_lake"),
        ("datagov.role", "default_role"),
    ]:
        cls, _ = load_plugin(group, name)
        assert "plugin_params" in dir(cls)


def test_dashboard_login_and_lake(tmp_path):
    root = _repo_root()
    file_config = load_config(root / "examples" / "config" / "default.json")
    plugin_param_dicts = [
        get_plugin_params(group, file_config.get(key) or DEFAULT_VALUES[key])
        for key, group in GROUPS.items()
    ]
    config = merge_config(DEFAULT_VALUES, plugin_param_dicts, file_config, {}, {})
    config["accounting_db"] = str(tmp_path / "acct.db")
    for lake in config.get("lakes") or []:
        rp = lake.get("root_path")
        if rp and not Path(str(rp)).is_absolute():
            lake["root_path"] = str((root / rp).resolve())
    plugins = assemble(config)
    plugins["accounting"].seed_demo_if_empty(list(plugins["lakes"]))
    for lake in plugins["lakes"].values():
        plugins["inventory"].sync_lake(lake)
    app = plugins["web"].create_app({"config": config, "plugins": plugins})
    client = app.test_client()
    denied = client.get("/", follow_redirects=False)
    assert denied.status_code in (301, 302)
    login = client.post(
        "/login",
        data={"username": "demo", "password": "demo"},
        follow_redirects=True,
    )
    assert login.status_code == 200
    assert b"Financial files" in login.data
    detail = client.get("/lakes/financial_files")
    assert detail.status_code == 200
    assert b"Usage log" in detail.data
    assert b"eth_usdt_1h.csv" in detail.data
