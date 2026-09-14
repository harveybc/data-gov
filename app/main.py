#!/usr/bin/env python3
"""Entry point: merge config, load plugins, run pipeline."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import os
from app.cli import parse_args
from app.config import DEFAULT_VALUES
from app.config_handler import load_config, save_config
from app.config_merger import merge_config, process_unknown_args
from app.lake_auth import load_token
from app.plugin_loader import get_plugin_params, load_plugin

GROUPS = {
    "pipeline_plugin": "datagov.pipeline",
    "web_plugin": "datagov.web",
    "access_plugin": "datagov.access",
    "accounting_plugin": "datagov.accounting",
    "role_plugin": "datagov.role",
}
PATH_KEYS = ("root_path", "sqlite_path", "spool_dir", "cuts_dir")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _instantiate(group: str, name: str, config: dict[str, Any]):
    cls, _ = load_plugin(group, name)
    plugin = cls()
    plugin.set_params(**config)
    return plugin


def check_startup(config: dict[str, Any]) -> None:
    """A lake with holdout_start is only served through policies that carry deny_from."""
    problems = []
    for lake in config.get("lakes") or []:
        if not lake.get("holdout_start"):
            continue
        lake_id = lake.get("lake_id")
        for policy in config.get("policies") or []:
            if policy.get("lake") not in {lake_id, "*"}:
                continue
            if not policy.get("deny_from"):
                problems.append(
                    f"lake {lake_id!r} has holdout_start {lake['holdout_start']!r} but the "
                    f"policy for principal {policy.get('principal') or '*'!r} has no deny_from"
                )
    if problems:
        raise SystemExit("refusing to start:\n  " + "\n  ".join(problems))


def sweep_spool(spool_dir) -> int:
    """Remove files a crashed process left in the spool; returns how many."""
    spool = Path(spool_dir)
    spool.mkdir(parents=True, exist_ok=True)
    removed = 0
    for path in spool.iterdir():
        if path.is_file():
            path.unlink(missing_ok=True)
            removed += 1
    return removed


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
    config.setdefault("spool_dir", "./var/spool")
    config.setdefault("cuts_dir", "./var/cuts")
    for key in ("spool_dir", "cuts_dir"):
        if not Path(str(config[key])).is_absolute():
            config[key] = str((root / str(config[key])).resolve())
    for lake in config.get("lakes") or []:
        for key in PATH_KEYS:
            rp = lake.get(key)
            if rp and not Path(str(rp)).is_absolute():
                lake[key] = str((root / rp).resolve())
    db = config.get("accounting_db")
    if db and not Path(str(db)).is_absolute():
        config["accounting_db"] = str((root / db).resolve())
    token = load_token()
    if token:
        config["lake_service_token"] = token
        os.environ.setdefault("DATA_GOV_LAKE_TOKEN", token)
    secret_file = root / "var" / "flask_secret"
    if secret_file.is_file():
        config["secret_key"] = secret_file.read_text(encoding="utf-8").strip()

    check_startup(config)
    sweep_spool(config["spool_dir"])
    plugins = assemble(config)

    save_path = config.get("save_config")
    if save_path:
        save_config(config, save_path)

    return plugins["pipeline"].run({"config": config, "plugins": plugins})


def assemble(config: dict[str, Any]) -> dict[str, Any]:
    plugins = {
        "pipeline": _instantiate("datagov.pipeline", config["pipeline_plugin"], config),
        "web": _instantiate("datagov.web", config["web_plugin"], config),
        "access": _instantiate("datagov.access", config["access_plugin"], config),
        "accounting": _instantiate(
            "datagov.accounting", config["accounting_plugin"], config
        ),
        "role": _instantiate("datagov.role", config["role_plugin"], config),
        "lakes": {},
    }
    for spec in config.get("lakes") or []:
        lake_id = spec["lake_id"]
        plugin_name = spec.get("plugin") or "default_lake"
        lake_config = dict(config)
        # Metadata belongs to this store, not the last plugin in the global merge.
        for key in ("kind", "engine"):
            lake_config.pop(key, None)
        lake_config.update(spec)
        plugins["lakes"][lake_id] = _instantiate(
            "datagov.lake", plugin_name, lake_config
        )
    return plugins


if __name__ == "__main__":
    raise SystemExit(main())
