"""Generic Flow v3 consumer (tools/governed_exec.py) against the in-process service."""

import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from app.client import DataGovClient
from tests.conftest import LAB_EARLY, PREDICTOR_KEY

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("governed_exec_subject", ROOT / "tools" / "governed_exec.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["governed_exec_subject"] = module
    spec.loader.exec_module(module)
    return module


GE = _load()

COMMAND = (
    "import json, shutil, sys\n"
    "cfg = json.load(open(sys.argv[1]))\n"
    "shutil.copy(cfg['input_file'], cfg['output_file'])\n"
    "json.dump({'mae': 0.5, 'rows': 3, 'nested': {'r2 score': 0.1, 'name': 'x'}}, open(cfg['save_log'], 'w'))\n"
    "sys.exit(int(cfg.get('exit_code', 0)))\n"
)


def _repo(tmp_path, dirty=False):
    repo = tmp_path / "consumer"
    repo.mkdir()
    (repo / "tool.py").write_text(COMMAND, encoding="utf-8")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init"], cwd=repo, check=True, env={**env, "PATH": "/usr/bin:/bin"})
    if dirty:
        (repo / "scratch.txt").write_text("x", encoding="utf-8")
    return repo


def _spec(repo, key="exec-001", exit_code=0, **changes):
    spec = {
        "schema": "governed_exec_spec.v1",
        "project": "preprocessor",
        "campaign_key": key,
        "classification": "GOVERNING",
        "terminal_lake": "olap_strict",
        "repo_root": str(repo),
        "datasets": [{"lake": "lab_files", "resource": LAB_EARLY, "role": "x", "from": None, "to": None}],
        "input_keys": {"input_file": "x"},
        "output_keys": ["output_file", "save_log"],
        "config": {"input_file": "examples/original.csv", "output_file": "examples/out/result.csv",
                   "save_log": "examples/out/debug.json", "exit_code": exit_code},
        "command": [sys.executable, "tool.py", "{config}"],
        "metrics": {"kind": "json_numbers", "path": "debug.json"},
        "artifacts": {"result": "output_file"},
        "tags": {"phase": "test"},
    }
    spec.update(changes)
    return spec


def _client(client, key="exec-001"):
    return DataGovClient(api_key=PREDICTOR_KEY, experiment_key=key, test_client=client)


def _terminals(runtime):
    path = next(l for l in runtime["config"]["lakes"] if l["lake_id"] == "olap_strict")["sqlite_path"]
    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT campaign_key, status, reason FROM gov_terminal ORDER BY received_at").fetchall()
        metrics = conn.execute("SELECT metric, value FROM gov_terminal_metric ORDER BY metric").fetchall()
        datasets = conn.execute("SELECT role, verification_state, availability_contract_sha256 FROM gov_terminal_dataset").fetchall()
    return rows, metrics, datasets


def test_completed_run_registers_delivery_metrics_and_reconciles(client, runtime, tmp_path):
    repo = _repo(tmp_path)
    out = tmp_path / "out"
    state = GE.run(_spec(repo), _client(client), out, tmp_path / "cache", tmp_path / "outbox")
    assert state["status"] == "COMPLETED" and state["terminal_pending"] is False
    assert state["reconciliation"] == {"campaign_sha256": state["campaign_sha256"], "missing_units": [],
                                       "accounting_only": [], "lake_only": []}
    assert state["inputs"][0]["verification_state"] == "VERIFIED_TRANSFER"
    assert state["inputs"][0]["availability_contract_sha256"]
    assert state["inputs"][0]["path"].endswith(f"{state['inputs'][0]['sha256']}.csv")
    assert (out / "result.csv").read_bytes() == Path(state["inputs"][0]["path"]).read_bytes()
    gcfg = json.loads((out / "governed_config.json").read_text())
    assert gcfg["input_file"] == state["inputs"][0]["path"] and gcfg["output_file"] == str(out / "result.csv")
    rows, metrics, datasets = _terminals(runtime)
    assert rows == [("exec-001", "COMPLETED", None)]
    assert metrics == [("mae", 0.5), ("nested.r2_score", 0.1), ("rows", 3.0)]
    assert datasets == [("x", "VERIFIED_TRANSFER", state["inputs"][0]["availability_contract_sha256"])]
    assert {a["role"] for a in state["terminal"]["artifacts"]} == {"result", "governed_config"}
    assert len(list((tmp_path / "outbox" / "sent").glob("*.json"))) == 1
    assert not list((tmp_path / "outbox" / "pending").glob("*.json"))
    receipt = json.loads((out / "GOVERNED_RUN.json").read_text())
    assert receipt["status"] == "COMPLETED" and receipt["outbox_flush"] == {"sent": 1, "pending": 0, "failures": {}}


def test_stale_outputs_are_refused_without_downloading(client, runtime, tmp_path):
    repo = _repo(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "result.csv").write_text("old", encoding="utf-8")
    with pytest.raises(GE.GovernedExecError, match="not fresh"):
        GE.run(_spec(repo), _client(client), out, tmp_path / "cache", tmp_path / "outbox")
    receipt = json.loads((out / "GOVERNED_RUN.json").read_text())
    assert receipt["status"] == "REFUSED" and "inputs" not in receipt
    assert (out / "result.csv").read_text() == "old"
    rows, metrics, datasets = _terminals(runtime)
    assert [r[1] for r in rows] == ["REFUSED"] and metrics == [] and datasets == []


def test_command_failure_is_a_failed_terminal_with_deliveries(client, runtime, tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(GE.GovernedExecError, match="exited 2"):
        GE.run(_spec(repo, exit_code=2), _client(client), tmp_path / "out", tmp_path / "cache", tmp_path / "outbox")
    rows, metrics, datasets = _terminals(runtime)
    assert rows == [("exec-001", "FAILED", "COMMAND_EXIT_2")] and metrics == []
    assert [d[0] for d in datasets] == ["x"]


def test_missing_metrics_file_is_inconclusive(client, runtime, tmp_path):
    repo = _repo(tmp_path)
    spec = _spec(repo, metrics={"kind": "json_numbers", "path": "absent.json"})
    with pytest.raises(GE.GovernedExecError, match="metrics file missing"):
        GE.run(spec, _client(client), tmp_path / "out", tmp_path / "cache", tmp_path / "outbox")
    rows, _, _ = _terminals(runtime)
    assert rows[0][1] == "INCONCLUSIVE" and rows[0][2].startswith("METRICS_UNAVAILABLE")


def test_governing_run_refuses_a_dirty_checkout_before_any_campaign(client, tmp_path):
    repo = _repo(tmp_path, dirty=True)
    with pytest.raises(GE.GovernedExecError, match="clean checkout"):
        GE.run(_spec(repo), _client(client), tmp_path / "out", tmp_path / "cache", tmp_path / "outbox")
    assert not (tmp_path / "out" / "GOVERNED_RUN.json").exists()
    status, body = _client(client).reconcile_campaign("0" * 64)
    assert status == 404


def test_terminal_outage_leaves_a_pending_result_that_flushes_exactly_once(client, runtime, tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    gov = _client(client)
    real = gov.report_terminal
    monkeypatch.setattr(gov, "report_terminal", lambda *a, **k: (503, {"error": "terminal lake unreachable"}))
    with pytest.raises(GE.GovernedExecError, match="remains pending"):
        GE.run(_spec(repo), gov, tmp_path / "out", tmp_path / "cache", tmp_path / "outbox")
    receipt = json.loads((tmp_path / "out" / "GOVERNED_RUN.json").read_text())
    assert receipt["status"] == "COMPLETED" and receipt["terminal_pending"] is True
    assert list(receipt["outbox_flush"]["failures"].values()) == ["GovernedExecError: terminal refused: http 503 terminal lake unreachable"]
    assert len(list((tmp_path / "outbox" / "pending").glob("*.json"))) == 1
    assert _terminals(runtime)[0] == []
    monkeypatch.setattr(gov, "report_terminal", real)
    assert GE.send_pending(gov, GE.TerminalOutbox(tmp_path / "outbox")) == {"sent": 1, "pending": 0, "failures": {}}
    assert GE.send_pending(gov, GE.TerminalOutbox(tmp_path / "outbox")) == {"sent": 0, "pending": 0, "failures": {}}
    assert [r[1] for r in _terminals(runtime)[0]] == ["COMPLETED"]
    # while a terminal stays pending, a new governing run is refused before it opens data
    monkeypatch.setattr(gov, "report_terminal", lambda *a, **k: (503, {"error": "down"}))
    with pytest.raises(GE.GovernedExecError, match="remains pending"):
        GE.run(_spec(repo, key="exec-002"), gov, tmp_path / "out2", tmp_path / "cache", tmp_path / "outbox")
    with pytest.raises(GE.GovernedExecError, match="prior terminal remains pending"):
        GE.run(_spec(repo, key="exec-003"), gov, tmp_path / "out3", tmp_path / "cache", tmp_path / "outbox")
    receipt3 = json.loads((tmp_path / "out3" / "GOVERNED_RUN.json").read_text())
    assert receipt3["reason"] == "PRIOR_TERMINAL_PENDING" and "inputs" not in receipt3
    monkeypatch.setattr(gov, "report_terminal", real)
    assert GE.send_pending(gov, GE.TerminalOutbox(tmp_path / "outbox"))["sent"] == 1
    assert [r[1] for r in _terminals(runtime)[0]] == ["COMPLETED", "COMPLETED"]


def test_profile_builds_spec_from_a_config_and_refuses_inputs_outside_the_lake(tmp_path, capsys):
    repo = _repo(tmp_path)
    lake = tmp_path / "lake"
    (lake / "phase").mkdir(parents=True)
    (lake / "phase" / "a.csv").write_text("ts,v\n")
    config = tmp_path / "cfg" / "c.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"input_file": str(lake / "phase" / "a.csv"), "output_file": "o/out.csv", "k": 1}))
    profile = {"project": "preprocessor", "input_keys": ["input_file"], "output_keys": ["output_file"],
               "command": [sys.executable, "tool.py", "{config}"], "metrics": {"kind": "none"}, "artifacts": {}}
    assert GE.consumer_main(profile, [
        "--load_config", str(config), "--experiment-key", "k-1", "--lake", "files", "--lake-root", str(lake),
        "--out-dir", str(tmp_path / "out"), "--print-spec", "--", "--epochs", "2",
    ], repo_root=repo) == 0
    spec = json.loads(capsys.readouterr().out)
    assert spec["datasets"] == [{"lake": "files", "resource": "phase/a.csv", "role": "input_file", "from": None, "to": None}]
    assert spec["extra_args"] == ["--epochs", "2"] and spec["tags"]["phase"] == "cfg"
    assert GE.execution_spec(spec) == GE.execution_spec({**spec, "repo_root": "/elsewhere"})
    assert "role:input_file" in GE.execution_spec(spec) and str(lake) not in GE.execution_spec(spec)
    assert GE.consumer_main(profile, [
        "--load_config", str(config), "--experiment-key", "k-1", "--lake", "files", "--lake-root", str(tmp_path / "other"),
        "--out-dir", str(tmp_path / "out"), "--print-spec",
    ], repo_root=repo) == 1
    assert "not under the lake root" in capsys.readouterr().err


def test_metric_keys_csv_rows_and_row_counts(tmp_path):
    assert GE.metric_key("Naive MAE") == "Naive_MAE" and GE.metric_key("r2 (score)") == "r2_score"
    (tmp_path / "results.csv").write_text(
        "Metric,Average,Std Dev,Min,Max\nTrain Naive MAE H9,1,0,1,1\nTest MAE H9,nan,0,2,2\nSharpe,3,,,\n")
    rows = GE.collect_metrics({"metrics": {"kind": "csv_rows", "path": "results.csv"}}, tmp_path)
    assert [(r["metric"], r["split"], r["horizon"], r["value"]) for r in rows] == [
        ("Naive_MAE", "train", 9, 1.0), ("MAE", "test", 9, None), ("Sharpe", None, None, 3.0)]
    (tmp_path / "base_d1.csv").write_text("a,b\n1,2\n3,4\n\n")
    counts = GE.collect_metrics({"metrics": {"kind": "row_counts", "files": [{"path": "base_d1.csv", "split": "d1"}]}}, tmp_path)
    assert counts == [{"metric": "rows", "split": "d1", "horizon": None, "unit": "rows", "value": 2.0,
                       "std_dev": None, "min_value": None, "max_value": None},
                      {"metric": "columns", "split": "d1", "horizon": None, "unit": "columns", "value": 2.0,
                       "std_dev": None, "min_value": None, "max_value": None}]


def test_outbox_failure_classes_status_dispose_and_supersede(tmp_path):
    """N4 for the generic consumer: same rules as the predictor outbox."""
    from app.outbox import TerminalOutbox, classify_failure

    assert classify_failure("terminal refused: http 503 down") == "TRANSIENT"
    assert classify_failure("terminal refused: http 401 x") == "CONFIGURATION"
    assert classify_failure("terminal refused: http 400 invalid metric") == "REFUSED_BY_SERVER"
    assert classify_failure("terminal accounting and terminal lake diverge") == "UNRESOLVED"
    outbox = TerminalOutbox(tmp_path / "outbox")

    def terminal(status="COMPLETED", metric="MAE (x)"):
        return {"schema": "governed_terminal.v1", "generation": 1, "status": status,
                "reason": None if status == "COMPLETED" else "X", "started_at": "2026-09-14T00:00:00Z",
                "finished_at": "2026-09-14T00:00:01Z", "costs": {"wall_seconds": 1.0}, "deliveries": ["a" * 32],
                "artifacts": [], "metrics": [{"metric": metric, "split": None, "horizon": None, "unit": None,
                                              "value": 1.0, "std_dev": None, "min_value": None, "max_value": None}],
                "tags": {}}

    bad = outbox.put({"campaign_sha256": "c" * 64, "unit_id": "u1", "terminal": terminal(status="FAILED")})
    raw = bad.path.read_bytes()

    def refuse(payload):
        raise RuntimeError("terminal refused: http 400 invalid metric")
    outbox.flush(refuse)
    health = outbox.status()
    assert health["pending"][0]["class"] == "REFUSED_BY_SERVER" and health["awaiting_adjudication"] == 1
    with pytest.raises(ValueError, match="keeps the original outcome"):
        outbox.supersede(bad.path.name, terminal(status="COMPLETED", metric="MAE_x"), lambda p: {"terminal_sha256": "9" * 64}, "r")
    sent = []
    record = outbox.supersede(bad.path.name, terminal(status="FAILED", metric="MAE_x"),
                              lambda p: sent.append(p) or {"terminal_sha256": "9" * 64}, "key canonicalised")
    assert record["decision"] == "SUPERSEDED" and record["successor_generation"] == 2 and sent[0]["terminal"]["generation"] == 2
    assert (tmp_path / "outbox" / "adjudicated" / bad.path.name).read_bytes() == raw and not bad.path.exists()
    other = outbox.put({"campaign_sha256": "c" * 64, "unit_id": "u2", "terminal": terminal()})
    outbox.flush(refuse)
    with pytest.raises(ValueError, match="states its reason"):
        outbox.dispose(other.path.name, "INVALID_ENVELOPE", "")
    outbox.dispose(other.path.name, "INVALID_ENVELOPE", "manufactured probe")
    health = outbox.status()
    assert health["pending"] == [] and sorted(a["decision"] for a in health["adjudicated"]) == ["INVALID_ENVELOPE", "SUPERSEDED"]
    assert health["sent"] == 1


def test_row_counts_report_columns_too(tmp_path):
    (tmp_path / "out.csv").write_text("DATE_TIME,a,b\n1,2,3\n4,5,6\n")
    rows = GE.collect_metrics({"metrics": {"kind": "row_counts", "files": [{"path": "out.csv", "split": "output"}]}}, tmp_path)
    assert [(r["metric"], r["value"], r["unit"]) for r in rows] == [("rows", 2.0, "rows"), ("columns", 3.0, "columns")]
