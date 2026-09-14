"""Governed downloads must survive the real threaded server.

pandas 3.0.3 with pyarrow 25 segfaults in string_arrow._from_sequence on the
second read_csv issued from a werkzeug worker thread after a streamed delivery
(observed 2026-09-13 on the second `/api/v2/download` of a resource under
holdout). The Flask test client never shows it because it runs requests in the
calling thread, so this test starts the real server in a subprocess and drives
it over HTTP. With pyarrow string storage the process dies on the second
request; with `lake_plugins.files_lake` pinning python storage it answers.
"""

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = {
    "event_time_column": "DATE_TIME",
    "available_time_column": "DATE_TIME",
    "timezone": "NAIVE_WALL_CLOCK",
    "time_unit": None,
    "frequency": "4h",
}


def _port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _request(url, key, headers=None, body=None):
    request_headers = {"Authorization": f"Bearer {key}", **(headers or {})}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=request_headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.status, dict(response.headers), response.read()


def test_second_governed_download_in_a_worker_thread_does_not_kill_the_server(tmp_path):
    root = tmp_path / "root" / "phase"
    root.mkdir(parents=True)
    rows = ["DATE_TIME,typical_price"]
    rows += [f"2013-01-{d:02d} {h:02d}:00:00,{d + h / 100:.4f}" for d in range(1, 29) for h in (0, 4, 8, 12, 16, 20)]
    for name in ("a.csv", "b.csv"):
        (root / name).write_text("\n".join(rows) + "\n", encoding="ascii")
    salt, key = "salt", "threaded-test-key"
    port = _port()
    config = {
        "pipeline_plugin": "default_pipeline", "web_plugin": "default_web",
        "access_plugin": "default_access", "accounting_plugin": "default_accounting",
        "role_plugin": "default_role", "web_host": "127.0.0.1", "web_port": port,
        "accounting_db": str(tmp_path / "accounting.sqlite"),
        "spool_dir": str(tmp_path / "spool"), "cuts_dir": str(tmp_path / "cuts"),
        "save_config": None, "password_salt": salt, "secret_key": "test",
        "principals": {"predictor": {
            "kind": "service", "role": "service",
            "api_key_hash": hashlib.sha256(f"{salt}:{key}".encode()).hexdigest(),
        }},
        "policies": [
            {"principal": "predictor", "lake": "files", "verbs": ["download"], "deny_from": "2025-01-01"},
            {"principal": "predictor", "lake": "sink", "verbs": ["write_terminal"], "deny_from": "2025-01-01"},
        ],
        "lakes": [
            {"plugin": "files_lake", "lake_id": "files", "root_path": str(tmp_path / "root"),
             "include_globs": ["**/*.csv"], "time_column": "DATE_TIME", "holdout_start": "2025-01-01",
             "cuts_dir": str(tmp_path / "lake-cuts"), "spool_dir": str(tmp_path / "lake-spool"),
             "resource_contracts": {"phase/a.csv": CONTRACT, "phase/b.csv": CONTRACT}},
            {"plugin": "http_lake", "lake_id": "sink", "base_url": "http://127.0.0.1:9",
             "holdout_start": "2025-01-01"},
        ],
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    log = open(tmp_path / "server.log", "wb")
    server = subprocess.Popen(
        [sys.executable, "-X", "faulthandler", "app/main.py", "--load_config", str(config_path)],
        cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT), DATA_GOV_LAKE_TOKEN="t"),
        stdout=log, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            assert server.poll() is None, "server exited during start-up"
            try:
                urllib.request.urlopen(base + "/healthz", timeout=1)
                break
            except (OSError, urllib.error.URLError):
                time.sleep(0.1)
        datasets = [
            {"lake": "files", "resource": "phase/a.csv", "role": "x", "from": None, "to": None},
            {"lake": "files", "resource": "phase/a.csv", "role": "y", "from": None, "to": None},
            {"lake": "files", "resource": "phase/b.csv", "role": "z", "from": None, "to": None},
            {"lake": "files", "resource": "phase/a.csv", "role": "cut", "from": "2013-01-10", "to": "2013-01-20"},
        ]
        campaign = {
            "schema": "governed_campaign.v1", "campaign_key": "threaded-001",
            "classification": "GOVERNING", "project": "predictor",
            "code_identity": {"kind": "git_commit", "value": "a" * 40},
            "config_sha256": "b" * 64, "input_mode": "DATASETS",
            "synthetic_spec_sha256": None, "units": ["u"], "datasets": datasets,
            "terminal_lake": "sink",
        }
        status, _, raw = _request(base + "/api/v2/campaigns", key, body=campaign)
        assert status == 201
        campaign_sha = json.loads(raw)["campaign_sha256"]
        headers = {"X-Campaign-SHA256": campaign_sha, "X-Unit-ID": "u"}
        expected = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in ("a.csv", "b.csv")}
        for item in datasets:
            query = f"lake=files&resource={item['resource']}&role={item['role']}"
            if item["from"]:
                query += f"&from={item['from']}&to={item['to']}"
            try:
                status, response_headers, body = _request(base + "/api/v2/download?" + query, key, headers)
            except (OSError, urllib.error.URLError) as exc:
                raise AssertionError(f"server died on {item['role']}: {exc}; exit={server.poll()}")
            assert status == 200, item
            assert hashlib.sha256(body).hexdigest() == response_headers["X-Content-SHA256"]
            if not item["from"]:
                assert response_headers["X-Content-SHA256"] == expected[Path(item["resource"]).name]
            assert server.poll() is None, f"server exited after {item['role']}"
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
        log.close()
    text = (tmp_path / "server.log").read_text(encoding="utf-8", errors="replace")
    assert "Fatal Python error" not in text, text[-2000:]
