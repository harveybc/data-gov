#!/usr/bin/env python3
"""Run Flow v3 across disposable financial, governance and OLAP services."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def _port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _request(url, *, token, body=None, headers=None):
    request_headers = {"Authorization": f"Bearer {token}", **(headers or {})}
    data = None
    if body is not None:
        data = json.dumps(body, allow_nan=False).encode("ascii")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def _json(url, *, token, body=None, headers=None):
    status, response_headers, raw = _request(
        url, token=token, body=body, headers=headers
    )
    return status, response_headers, json.loads(raw.decode() or "{}")


def _wait(url, processes, deadline=30):
    until = time.monotonic() + deadline
    while time.monotonic() < until:
        for process in processes:
            if process.poll() is not None:
                raise RuntimeError(f"service exited early with {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.1)
    raise RuntimeError(f"service did not become ready: {url}")


def _start(checkout, config, env, log, argv=None, python=None):
    """Start a service. `argv`/`python` let a reusable host stand in for a legacy checkout."""
    handle = open(log, "wb")
    process_env = dict(os.environ, **env)
    process_env["PYTHONPATH"] = str(checkout)
    if argv is None:
        argv = [python or sys.executable, "app/main.py", "--load_config", str(config)]
        cwd = checkout
    else:
        argv = [*argv, "--load_config", str(config)]
        cwd = Path(config).parent
        process_env.pop("PYTHONPATH", None)
    process = subprocess.Popen(
        argv, cwd=cwd, env=process_env, stdout=handle, stderr=subprocess.STDOUT,
    )
    process._flow_v3_log_handle = handle
    return process


def _stop(processes):
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()
    for process in reversed(processes):
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process._flow_v3_log_handle.close()


def verify(data_gov, financial_lake, olap_lake, new_lake_python=None, new_warehouse_python=None):
    actor_key = "disposable-actor-key"
    lake_token = "disposable-lake-token"
    password_salt = "disposable-salt"
    ports = [_port(), _port(), _port()]
    financial_port, olap_port, governance_port = ports
    with tempfile.TemporaryDirectory(prefix="flow-v3-e2e-") as tmp:
        tmp = Path(tmp)
        source = tmp / "source"
        source.mkdir()
        resource = source / "panel.csv"
        resource.write_text(
            "event_time,available_time,value\n"
            "2024-01-01T00:00:00Z,2024-01-01T00:01:00Z,1\n"
            "2024-01-01T01:00:00Z,2024-01-01T01:01:00Z,2\n",
            encoding="ascii",
        )
        contract = {
            "event_time_column": "event_time",
            "available_time_column": "available_time",
            "timezone": "UTC",
            "time_unit": None,
            "frequency": "1h",
        }
        contract_sha = hashlib.sha256(json.dumps(
            contract, sort_keys=True, separators=(",", ":")
        ).encode("ascii")).hexdigest()
        cube = tmp / "cube.sqlite"
        fin_cfg = tmp / "financial.json"
        olap_cfg = tmp / "olap.json"
        gov_cfg = tmp / "governance.json"
        _write(fin_cfg, {
            "pipeline_plugin": "default_pipeline", "web_plugin": "default_web",
            "inventory_plugin": "fs_inventory", "web_host": "127.0.0.1",
            "web_port": financial_port, "root_path": str(source),
            "include_globs": ["**/*.csv"], "resource_contracts": {"panel.csv": contract},
            "holdout_start": None, "spool_dir": str(tmp / "fin-spool"),
            "cuts_dir": str(tmp / "fin-cuts"),
        })
        _write(olap_cfg, {
            "pipeline_plugin": "default_pipeline", "web_plugin": "default_web",
            "query_plugin": "sql_query", "web_host": "127.0.0.1",
            "web_port": olap_port, "sqlite_path": str(cube), "holdout_start": None,
        })
        _write(gov_cfg, {
            "pipeline_plugin": "default_pipeline", "web_plugin": "default_web",
            "access_plugin": "default_access", "accounting_plugin": "default_accounting",
            "role_plugin": "default_role", "web_host": "127.0.0.1",
            "web_port": governance_port, "accounting_db": str(tmp / "accounting.sqlite"),
            "spool_dir": str(tmp / "gov-spool"), "cuts_dir": str(tmp / "gov-cuts"),
            "save_config": None, "password_salt": password_salt,
            "principals": {"predictor": {
                "kind": "service", "role": "service",
                "api_key_hash": hashlib.sha256(
                    f"{password_salt}:{actor_key}".encode()
                ).hexdigest(),
            }},
            "policies": [
                {"principal": "predictor", "lake": "financial_files",
                 "verbs": ["download"]},
                {"principal": "predictor", "lake": "olap_cube",
                 "verbs": ["write_terminal"]},
            ],
            "lakes": [
                {"plugin": "http_lake", "lake_id": "financial_files",
                 "base_url": f"http://127.0.0.1:{financial_port}", "holdout_start": None},
                {"plugin": "http_lake", "lake_id": "olap_cube",
                 "base_url": f"http://127.0.0.1:{olap_port}", "holdout_start": None},
            ],
        })
        env = {"DATA_GOV_LAKE_TOKEN": lake_token}
        processes = []
        try:
            if new_lake_python:
                # The reusable host with the provider installed as a separate distribution.
                # The governance configuration below is untouched: the kernel must not be
                # able to tell which host answers.
                _write(tmp / "financial.host.json", {
                    "store_id": "financial_files", "web_port": financial_port,
                    "backend": {"entry_point": "financial_files",
                                "distribution": "financial-data-store",
                                "settings": {"root_path": str(source), "include_globs": ["**/*.csv"],
                                             "resource_contracts": {"panel.csv": contract},
                                             "holdout_start": None,
                                             "spool_dir": str(tmp / "fin-spool"),
                                             "cuts_dir": str(tmp / "fin-cuts")}}})
                processes.append(_start(financial_lake, tmp / "financial.host.json", env,
                                        tmp / "financial.log",
                                        argv=[new_lake_python, "-m", "data_lake_service.main"]))
            else:
                processes.append(_start(financial_lake, fin_cfg, env, tmp / "financial.log"))
            if new_warehouse_python:
                _write(tmp / "olap.host.json", {
                    "store_id": "olap_cube", "web_port": olap_port,
                    "backend": {"entry_point": "predictor_olap",
                                "distribution": "predictor-olap-store",
                                "settings": {"sqlite_path": str(cube), "holdout_start": None,
                                             "lake_id": "olap_cube"}}})
                processes.append(_start(olap_lake, tmp / "olap.host.json", env, tmp / "olap.log",
                                        argv=[new_warehouse_python, "-m",
                                              "data_warehouse_service.main"]))
            else:
                processes.append(_start(olap_lake, olap_cfg, env, tmp / "olap.log"))
            _wait(f"http://127.0.0.1:{financial_port}/healthz", processes)
            _wait(f"http://127.0.0.1:{olap_port}/healthz", processes)
            processes.append(_start(data_gov, gov_cfg, env, tmp / "governance.log"))
            _wait(f"http://127.0.0.1:{governance_port}/healthz", processes)

            base = f"http://127.0.0.1:{governance_port}"
            campaign = {
                "schema": "governed_campaign.v1", "campaign_key": "e2e-001",
                "classification": "GOVERNING", "project": "predictor",
                "code_identity": {"kind": "git_commit", "value": "a" * 40},
                "config_sha256": "b" * 64, "input_mode": "DATASETS",
                "synthetic_spec_sha256": None, "units": ["unit-001"],
                "datasets": [{"lake": "financial_files", "resource": "panel.csv",
                              "role": "x", "from": None, "to": None}],
                "terminal_lake": "olap_cube",
            }
            status, _, receipt = _json(
                base + "/api/v2/campaigns", token=actor_key, body=campaign
            )
            assert status == 201, receipt
            campaign_sha = receipt["campaign_sha256"]
            headers = {
                "X-Campaign-SHA256": campaign_sha,
                "X-Unit-ID": "unit-001",
            }
            query = urllib.parse.urlencode({
                "lake": "financial_files", "resource": "panel.csv", "role": "x"
            })
            status, download_headers, raw = _request(
                base + "/api/v2/download?" + query, token=actor_key, headers=headers
            )
            assert status == 200
            digest = hashlib.sha256(raw).hexdigest()
            assert digest == download_headers["X-Content-SHA256"]
            assert contract_sha == download_headers["X-Availability-Contract-SHA256"]
            delivery_id = download_headers["X-Delivery-ID"]
            status, _, confirmation = _json(
                base + f"/api/v2/deliveries/{delivery_id}/confirm", token=actor_key,
                headers={"X-Campaign-SHA256": campaign_sha},
                body={"schema": "delivery_confirmation.v1", "sha256": digest,
                      "bytes": len(raw), "cached": False},
            )
            assert status == 200 and confirmation["state"] == "VERIFIED_TRANSFER"
            terminal = {
                "schema": "governed_terminal.v1", "generation": 1,
                "status": "COMPLETED", "reason": None,
                "started_at": "2026-09-13T20:00:00Z",
                "finished_at": "2026-09-13T20:00:01Z",
                "costs": {"wall_seconds": 1.0}, "deliveries": [delivery_id],
                "artifacts": [], "metrics": [{
                    "metric": "MAE", "split": "test", "horizon": 1,
                    "unit": None, "value": 0.1, "std_dev": None,
                    "min_value": None, "max_value": None,
                }], "tags": {"purpose": "disposable-e2e"},
            }
            status, _, terminal_receipt = _json(
                base + f"/api/v2/campaigns/{campaign_sha}/units/unit-001/terminal",
                token=actor_key, headers=headers, body=terminal,
            )
            assert status == 201, terminal_receipt
            status, _, reconciliation = _json(
                base + f"/api/v2/campaigns/{campaign_sha}/reconcile",
                token=actor_key, headers={"X-Campaign-SHA256": campaign_sha},
            )
            assert status == 200
            assert reconciliation["missing_units"] == []
            assert reconciliation["accounting_only"] == []
            assert reconciliation["lake_only"] == []
            with sqlite3.connect(cube) as conn:
                row = conn.execute(
                    "SELECT sha256, availability_contract_sha256, verification_state "
                    "FROM gov_terminal_dataset"
                ).fetchone()
                assert row == (digest, contract_sha, "VERIFIED_TRANSFER")
                assert conn.execute("SELECT status FROM gov_terminal").fetchone()[0] == "COMPLETED"
            return {
                "hosts": {"financial": "data_lake_service" if new_lake_python else "legacy",
                          "olap": "data_warehouse_service" if new_warehouse_python else "legacy"},
                "campaign_sha256": campaign_sha,
                "terminal_sha256": terminal_receipt["terminal_sha256"],
                "dataset_sha256": digest,
                "availability_contract_sha256": contract_sha,
                "reconciliation": reconciliation,
            }
        except Exception as exc:
            logs = {}
            for name in ("financial", "olap", "governance"):
                path = tmp / f"{name}.log"
                if path.exists():
                    logs[name] = path.read_text(encoding="utf-8", errors="replace")[-4000:]
            raise RuntimeError(f"Flow v3 E2E failed: {exc}; logs={logs}") from exc
        finally:
            _stop(processes)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--financial-lake-checkout", required=True, type=Path)
    parser.add_argument("--olap-lake-checkout", required=True, type=Path)
    parser.add_argument("--new-lake-python",
                        help="interpreter with data-lake-service and the file provider installed; "
                             "the reusable host then serves the financial store")
    parser.add_argument("--new-warehouse-python",
                        help="interpreter with data-warehouse-service and the OLAP provider installed")
    args = parser.parse_args(argv)
    result = verify(
        Path(__file__).resolve().parents[1],
        args.financial_lake_checkout.resolve(),
        args.olap_lake_checkout.resolve(),
        new_lake_python=args.new_lake_python,
        new_warehouse_python=args.new_warehouse_python,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
