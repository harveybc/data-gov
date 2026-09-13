#!/usr/bin/env python3
"""Entry point: merge config, load plugins, run pipeline."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from app.cli import parse_args
from app.config import DEFAULT_VALUES
from app.config_handler import load_config, save_config
from app.config_merger import merge_config, process_unknown_args
from app.plugin_loader import get_plugin_params, load_plugin

GROUPS = {
    "pipeline_plugin": "datagov.pipeline",
    "web_plugin": "datagov.web",
    "authn_plugin": "datagov.authn",
    "authz_plugin": "datagov.authz",
    "accounting_plugin": "datagov.accounting",
    "inventory_plugin": "datagov.inventory",
    "role_plugin": "datagov.role",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _instantiate(group: str, name: str, config: dict[str, Any]):
    cls, _ = load_plugin(group, name)
    plugin = cls()
    plugin.set_params(**config)
    return plugin


def main(argv=None) -> int:
    if argv is not None:
        sys.argv = [sys.argv[0], *argv]
    args, unknown = parse_args()
    cli_args = {k: v for k, v in vars(args).items() if v is not None}
    unknown_args = process_unknown_args(unknown)

    file_config: dict[str, Any] = {}
    load_path = cli_args.get("load_config") or DEFAULT_VALUES.get("load_config")
    if load_path:
        path = Path(str(load_path))
        if not path.is_absolute():
            path = _repo_root() / path
        file_config = load_config(path)

    names = dict(DEFAULT_VALUES)
    names.update(file_config)
    names.update(cli_args)

    plugin_param_dicts = []
    for key, group in GROUPS.items():
        plugin_param_dicts.append(get_plugin_params(group, names[key]))
    for lake in file_config.get("lakes") or []:
        plugin_name = lake.get("plugin") or "default_lake"
        plugin_param_dicts.append(get_plugin_params("datagov.lake", plugin_name))

    config = merge_config(
        DEFAULT_VALUES, plugin_param_dicts, file_config, cli_args, unknown_args
    )
    root = _repo_root()
    for lake in config.get("lakes") or []:
        rp = lake.get("root_path")
        if rp and not Path(str(rp)).is_absolute():
            lake["root_path"] = str((root / rp).resolve())
    db = config.get("accounting_db")
    if db and not Path(str(db)).is_absolute():
        config["accounting_db"] = str((root / db).resolve())

    plugins = assemble(config)

    save_path = config.get("save_config")
    if save_path:
        save_config(config, save_path)

    return plugins["pipeline"].run({"config": config, "plugins": plugins})


def assemble(config: dict[str, Any]) -> dict[str, Any]:
    plugins = {
        "pipeline": _instantiate("datagov.pipeline", config["pipeline_plugin"], config),
        "web": _instantiate("datagov.web", config["web_plugin"], config),
        "authn": _instantiate("datagov.authn", config["authn_plugin"], config),
        "authz": _instantiate("datagov.authz", config["authz_plugin"], config),
        "accounting": _instantiate(
            "datagov.accounting", config["accounting_plugin"], config
        ),
        "inventory": _instantiate(
            "datagov.inventory", config["inventory_plugin"], config
        ),
        "role": _instantiate("datagov.role", config["role_plugin"], config),
        "lakes": {},
    }
    for spec in config.get("lakes") or []:
        lake_id = spec["lake_id"]
        plugin_name = spec.get("plugin") or "default_lake"
        lake_config = dict(config)
        lake_config.update(spec)
        plugins["lakes"][lake_id] = _instantiate(
            "datagov.lake", plugin_name, lake_config
        )
    return plugins


if __name__ == "__main__":
    raise SystemExit(main())
