#!/usr/bin/env python3
"""Generic Flow v3 consumer: run one command on governed inputs and report its terminal.

The predictor consumer (`predictor/tools/governed_run.py`) is the reference. This
tool is its shape for the other repositories of the programme (preprocessor,
feature-eng, feature-extractor, ...): each one builds a `governed_exec_spec.v1`
from its own configuration keys and hands it here, so the governance protocol
lives in one place and the repositories only declare inputs, outputs, command
and metrics.

Protocol (in this order, nothing else touches data-gov):
  1. strict code identity of the consumer checkout (GOVERNING: clean, 40-hex);
  2. execution spec digest fixed before any data is requested;
  3. one campaign with one unit registered; a prior pending terminal refuses;
  4. the unit must have no terminal yet (reconciliation);
  5. the output namespace must be fresh, else REFUSED without downloading;
  6. every dataset downloaded and confirmed through data-gov (content cache);
  7. the governed config is written and the command runs on CPU;
  8. terminal COMPLETED | FAILED | INCONCLUSIVE | REFUSED, bound to deliveries,
     metrics, artifact hashes and cost; written to a durable outbox before any
     send, sent, then reconciled; a pending terminal makes the run non-governing.

usage: governed_exec.py --spec SPEC.json --gov-url URL --api-key-file FILE
                        --out-dir DIR [--cache-dir DIR] [--outbox-dir DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve()
DATA_GOV = HERE.parents[1]
if str(DATA_GOV) not in sys.path:
    sys.path.insert(0, str(DATA_GOV))

from app.client import DataGovClient  # noqa: E402
from app.outbox import TerminalOutbox  # noqa: E402

SPEC_SCHEMA = "governed_exec_spec.v1"
DEFAULT_CACHE = "~/.cache/data-gov"
DEFAULT_OUTBOX = "~/.local/state/data-gov/terminal-outbox"
KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
METRIC_KEY_DISALLOWED = re.compile(r"[^A-Za-z0-9._:-]+")
CHUNK = 1024 * 1024
PLACEHOLDER = re.compile(r"\{(input:[^}]+|out_dir|config|repo_root)\}")


class GovernedExecError(Exception):
    pass


# ----------------------------------------------------------------- helpers
def sha256_file(path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while True:
            block = handle.read(CHUNK)
            if not block:
                break
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def metric_key(label: str) -> str:
    """governed_terminal.v1 metric names are keys over [A-Za-z0-9._:-]."""
    key = METRIC_KEY_DISALLOWED.sub("_", str(label).strip()).strip("_")
    return key or "metric"


def _write_json_atomic(path: Path, value):
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def strict_code_identity(repo_root, classification) -> dict:
    """A governing run needs an exact, clean checkout; a non-governing run records
    the commit and whether the tree was dirty."""
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True,
    )
    if head.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", head.stdout.strip()):
        raise GovernedExecError("code identity requires a git checkout with a 40-hex commit")
    commit = head.stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=repo_root, capture_output=True, text=True, check=True,
    ).stdout.strip()
    if classification == "GOVERNING":
        if dirty:
            raise GovernedExecError("governing run requires a clean checkout")
        return {"kind": "git_commit", "value": commit}
    return {"kind": "git_commit", "value": commit + ("-dirty" if dirty else "")}


# -------------------------------------------------------------------- spec
def load_spec(path) -> dict:
    with open(path, encoding="utf-8") as handle:
        spec = json.load(handle)
    return validate_spec(spec)


def validate_spec(spec) -> dict:
    if not isinstance(spec, dict) or spec.get("schema") != SPEC_SCHEMA:
        raise GovernedExecError(f"spec schema must be {SPEC_SCHEMA}")
    for key in ("project", "campaign_key", "terminal_lake", "repo_root", "command", "config"):
        if key not in spec:
            raise GovernedExecError(f"spec lacks {key}")
    for key in ("project", "campaign_key"):
        if not isinstance(spec[key], str) or not KEY_RE.match(spec[key]):
            raise GovernedExecError(f"invalid spec {key}")
    unit = spec.get("unit_id") or spec["campaign_key"]
    if not isinstance(unit, str) or not KEY_RE.match(unit):
        raise GovernedExecError("invalid spec unit_id")
    classification = spec.get("classification", "GOVERNING")
    if classification not in ("GOVERNING", "NON_GOVERNING"):
        raise GovernedExecError("invalid spec classification")
    datasets = spec.get("datasets") or []
    if not isinstance(datasets, list):
        raise GovernedExecError("invalid spec datasets")
    roles = set()
    for item in datasets:
        for key in ("lake", "resource", "role"):
            if not isinstance(item.get(key), str) or not item[key]:
                raise GovernedExecError(f"dataset lacks {key}")
        if item["role"] in roles:
            raise GovernedExecError(f"duplicate dataset role {item['role']}")
        roles.add(item["role"])
        item.setdefault("from", None)
        item.setdefault("to", None)
    input_keys = spec.get("input_keys") or {}
    if not isinstance(input_keys, dict) or any(role not in roles for role in input_keys.values()):
        raise GovernedExecError("input_keys must map config keys to declared dataset roles")
    if classification == "GOVERNING" and not datasets:
        raise GovernedExecError("a governing run declares at least one dataset")
    if not isinstance(spec["command"], list) or not all(isinstance(x, str) for x in spec["command"]):
        raise GovernedExecError("command must be a list of strings")
    if not isinstance(spec["config"], dict):
        raise GovernedExecError("config must be an object")
    metrics = spec.get("metrics") or {"kind": "none"}
    if metrics.get("kind") not in ("none", "json_numbers", "csv_rows", "row_counts"):
        raise GovernedExecError("metrics.kind must be none|json_numbers|csv_rows|row_counts")
    spec = dict(spec)
    spec["unit_id"] = unit
    spec["classification"] = classification
    spec["datasets"] = datasets
    spec["input_keys"] = input_keys
    spec["output_keys"] = list(spec.get("output_keys") or [])
    spec["extra_args"] = list(spec.get("extra_args") or [])
    spec["artifacts"] = dict(spec.get("artifacts") or {})
    spec["metrics"] = metrics
    spec["env"] = dict(spec.get("env") or {})
    spec["tags"] = {str(k): str(v) for k, v in (spec.get("tags") or {}).items()}
    return spec


def execution_spec(spec) -> str:
    """Canonical pre-execution contract, independent of local paths: config with
    input keys replaced by their roles and output keys by basenames."""
    config = {}
    for key, value in spec["config"].items():
        if key in spec["input_keys"]:
            config[key] = f"role:{spec['input_keys'][key]}"
        elif key in spec["output_keys"]:
            config[key] = Path(str(value)).name if value else value
        else:
            config[key] = value
    body = {
        "schema": "governed_exec_execution_spec.v1",
        "project": spec["project"],
        "command": spec["command"],
        "extra_args": spec["extra_args"],
        "config": config,
        "datasets": sorted(spec["datasets"], key=lambda d: (
            d["lake"], d["resource"], d["role"], d.get("from") or "", d.get("to") or "")),
        "metrics": spec["metrics"],
        "artifacts": sorted(spec["artifacts"]),
    }
    return canonical(body)


# ---------------------------------------------------------------- outputs
def expected_outputs(spec, out_dir: Path) -> dict:
    """Output config keys -> path under out_dir (basename kept, prefixes kept as prefixes)."""
    out = {}
    for key in spec["output_keys"]:
        value = spec["config"].get(key)
        if not value:
            continue
        out[key] = str(out_dir / Path(str(value)).name)
    return out


def refuse_stale_outputs(spec, out_dir: Path, outputs: dict):
    """Any pre-existing scientific output under out_dir refuses the run."""
    stale = []
    for key, path in outputs.items():
        name = Path(path).name
        if key.endswith("_prefix"):
            stale += [str(p) for p in sorted(out_dir.glob(name + "*")) if p.is_file()]
        elif Path(path).exists():
            stale.append(path)
    for name in ("governed_config.json", "run.log"):
        if (out_dir / name).exists():
            stale.append(str(out_dir / name))
    if stale:
        raise GovernedExecError("governing output namespace is not fresh: " + ", ".join(sorted(stale)))


def substitute(text: str, inputs: dict, out_dir: Path, config_path: Path, repo_root: Path) -> str:
    def repl(match):
        token = match.group(1)
        if token.startswith("input:"):
            role = token[6:]
            if role not in inputs:
                raise GovernedExecError(f"command names undeclared input role {role}")
            return inputs[role]
        return {"out_dir": str(out_dir), "config": str(config_path), "repo_root": str(repo_root)}[token]
    return PLACEHOLDER.sub(repl, text)


# ---------------------------------------------------------------- metrics
def _flatten_numbers(value, prefix=""):
    out = []
    if isinstance(value, bool):
        return out
    if isinstance(value, (int, float)):
        if value == value and value not in (float("inf"), float("-inf")):
            out.append((prefix or "value", float(value)))
    elif isinstance(value, dict):
        for key, item in value.items():
            out += _flatten_numbers(item, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            out += _flatten_numbers(item, f"{prefix}.{index}" if prefix else str(index))
    return out


def _metric(metric, value, split=None, horizon=None, unit=None, std_dev=None, lo=None, hi=None):
    return {"metric": metric_key(metric), "split": split, "horizon": horizon, "unit": unit,
            "value": value, "std_dev": std_dev, "min_value": lo, "max_value": hi}


def collect_metrics(spec, out_dir: Path) -> list:
    kind = spec["metrics"]["kind"]
    if kind == "none":
        return []
    if kind == "json_numbers":
        path = out_dir / Path(spec["metrics"]["path"]).name
        if not path.is_file():
            raise GovernedExecError(f"metrics file missing: {path}")
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        keys = spec["metrics"].get("keys")
        rows = []
        seen = set()
        for name, value in _flatten_numbers(data):
            if keys is not None and name not in keys:
                continue
            key = metric_key(name)
            if key in seen:
                continue
            seen.add(key)
            rows.append(_metric(key, value))
        return rows
    if kind == "csv_rows":
        import csv
        path = out_dir / Path(spec["metrics"]["path"]).name
        if not path.is_file():
            raise GovernedExecError(f"metrics file missing: {path}")
        rows = []
        seen = set()
        pattern = re.compile(r"^(Train|Validation|Test)\s+(.+?)(?:\s+H(\d+))?$")
        with open(path, newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                label = (row.get("Metric") or "").strip()
                if not label:
                    continue
                match = pattern.match(label)
                split, name, horizon = (match.group(1).lower(), match.group(2), match.group(3)) if match else (None, label, None)
                horizon = int(horizon) if horizon else None
                key = metric_key(name)
                if (key, split, horizon) in seen:
                    continue
                seen.add((key, split, horizon))

                def num(text):
                    try:
                        value = float(text)
                    except (TypeError, ValueError):
                        return None
                    return value if value == value and abs(value) != float("inf") else None
                rows.append(_metric(key, num(row.get("Average")), split, horizon, None,
                                    num(row.get("Std Dev")), num(row.get("Min")), num(row.get("Max"))))
        return rows
    if kind == "row_counts":
        rows = []
        for item in spec["metrics"]["files"]:
            path = out_dir / Path(item["path"]).name
            if not path.is_file():
                raise GovernedExecError(f"metrics file missing: {path}")
            header = None
            with open(path, "rb") as handle:
                count = 0
                for line in handle:
                    if not line.strip():
                        continue
                    if header is None:
                        header = line
                    count += 1
            count -= 1 if item.get("header", True) else 0
            split = item.get("split") or Path(path).stem
            rows.append(_metric("rows", float(max(count, 0)), split=split, unit="rows"))
            if item.get("header", True) and header is not None:
                rows.append(_metric("columns", float(len(header.decode("utf-8", "replace").rstrip("\r\n").split(","))),
                                    split=split, unit="columns"))
        return rows
    raise GovernedExecError("unknown metrics kind")


def collect_artifacts(spec, out_dir: Path, outputs: dict) -> list:
    artifacts = []
    seen = set()
    for role, target in spec["artifacts"].items():
        if not KEY_RE.match(role):
            raise GovernedExecError(f"invalid artifact role {role}")
        path = Path(outputs[target]) if target in outputs else out_dir / Path(str(target)).name
        if target in outputs and target.endswith("_prefix"):
            candidates = sorted(out_dir.glob(path.name + "*"))
        elif target not in outputs and any(ch in str(target) for ch in "*?["):
            # a glob over the output directory: every file the command produced under that pattern
            candidates = sorted(p for p in out_dir.glob(str(target)) if p.is_file()
                                and p.name not in ("governed_config.json", "GOVERNED_RUN.json", "run.log"))
        else:
            candidates = [path]
        for index, candidate in enumerate(candidates):
            if not candidate.is_file():
                continue
            digest, size = sha256_file(candidate)
            item_role = role if len(candidates) == 1 else f"{role}:{candidate.name}"
            item_role = metric_key(item_role)
            if (item_role, digest) in seen:
                continue
            seen.add((item_role, digest))
            artifacts.append({"role": item_role, "sha256": digest, "bytes": size})
    for name in ("governed_config.json",):
        path = out_dir / name
        if path.is_file():
            digest, size = sha256_file(path)
            artifacts.append({"role": "governed_config", "sha256": digest, "bytes": size})
    return artifacts


# ------------------------------------------------------------------- run
def _reconcile(client, campaign_sha256, unit_id, *, before_run):
    status, body = client.reconcile_campaign(campaign_sha256)
    if status != 200:
        raise GovernedExecError(f"reconciliation failed: http {status} {body.get('error', '')}".strip())
    if body.get("accounting_only") or body.get("lake_only"):
        raise GovernedExecError("terminal accounting and terminal lake diverge")
    missing = body.get("missing_units")
    if not isinstance(missing, list):
        raise GovernedExecError("invalid reconciliation response")
    if before_run and unit_id not in missing:
        raise GovernedExecError("campaign unit already has a terminal")
    if not before_run and unit_id in missing:
        raise GovernedExecError("terminal is missing after accepted report")
    return body


def send_pending(client, outbox) -> dict:
    failures = {}

    def sender(envelope):
        status, receipt = client.report_terminal(
            envelope["campaign_sha256"], envelope["unit_id"], envelope["terminal"]
        )
        if status not in (200, 201):
            raise GovernedExecError(f"terminal refused: http {status} {receipt.get('error', '')}".strip())
        _reconcile(client, envelope["campaign_sha256"], envelope["unit_id"], before_run=False)
        return receipt

    def recording(envelope):
        try:
            return sender(envelope)
        except Exception as exc:
            failures[f"{envelope['campaign_sha256'][:12]}/{envelope['unit_id']}"] = f"{type(exc).__name__}: {exc}"
            raise

    result = outbox.flush(recording)
    result["failures"] = failures
    return result


def run(spec: dict, client: DataGovClient, out_dir, cache_dir, outbox_dir, *, runner=None) -> dict:
    """Execute the protocol; returns the state also written to <out_dir>/GOVERNED_RUN.json.
    `runner(cmd, cwd, env, log_path) -> exit code` is injectable for tests."""
    spec = validate_spec(spec)
    repo_root = Path(spec["repo_root"]).expanduser().resolve()
    out_dir = Path(out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(os.path.expanduser(cache_dir)).resolve()
    outbox = TerminalOutbox(Path(os.path.expanduser(outbox_dir)).resolve())
    code_identity = strict_code_identity(repo_root, spec["classification"])
    exec_spec = execution_spec(spec)
    config_sha256 = sha256_text(exec_spec)
    unit = spec["unit_id"]
    state = {
        "schema": "governed_exec_run.v1", "status": "RUNNING", "project": spec["project"],
        "campaign_key": spec["campaign_key"], "unit_id": unit, "classification": spec["classification"],
        "repo_root": str(repo_root), "out_dir": str(out_dir), "code_identity": code_identity,
        "execution_spec": json.loads(exec_spec), "config_sha256": config_sha256,
    }
    receipt_path = out_dir / "GOVERNED_RUN.json"

    def checkpoint():
        _write_json_atomic(receipt_path, state)

    campaign = {
        "schema": "governed_campaign.v1", "campaign_key": spec["campaign_key"],
        "classification": spec["classification"], "project": spec["project"],
        "code_identity": code_identity, "config_sha256": config_sha256,
        "input_mode": "DATASETS" if spec["datasets"] else "SYNTHETIC",
        "synthetic_spec_sha256": None if spec["datasets"] else config_sha256,
        "units": [unit], "datasets": spec["datasets"], "terminal_lake": spec["terminal_lake"],
    }
    status, campaign_receipt = client.submit_campaign(campaign)
    if status not in (200, 201):
        raise GovernedExecError(f"campaign refused: http {status} {campaign_receipt.get('error', '')}".strip())
    campaign_sha256 = campaign_receipt.get("campaign_sha256")
    if not isinstance(campaign_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", campaign_sha256):
        raise GovernedExecError("campaign receipt has no valid identity")
    state["campaign_sha256"] = campaign_sha256
    checkpoint()

    prior = send_pending(client, outbox)
    state["prior_outbox_flush"] = prior
    if prior["pending"]:
        state["status"], state["reason"] = "REFUSED", "PRIOR_TERMINAL_PENDING"
        checkpoint()
        raise GovernedExecError("a prior terminal remains pending")
    _reconcile(client, campaign_sha256, unit, before_run=True)

    started_at = utc_now()
    wall_start = time.monotonic()
    outputs = expected_outputs(spec, out_dir)
    inputs, delivery_ids, metrics, artifacts = {}, [], [], []
    failure = None
    terminal_status, terminal_reason = "COMPLETED", None
    try:
        refuse_stale_outputs(spec, out_dir, outputs)
        for item in spec["datasets"]:
            status, info = client.governed_download(
                campaign_sha256, unit, item["lake"], item["resource"], item["role"],
                cache_dir / item["lake"], item.get("from"), item.get("to"),
            )
            if status != 200:
                raise GovernedExecError(
                    f"download {item['lake']}/{item['resource']} refused: http {status} {info.get('error', '')}".strip())
            inputs[item["role"]] = info["path"]
            delivery_ids.append(info["delivery_id"])
            state.setdefault("inputs", []).append({"role": item["role"], **{k: info.get(k) for k in (
                "lake", "resource", "path", "sha256", "bytes", "cached", "source_sha256", "delivery",
                "time_column", "availability_contract_sha256", "availability_use", "availability_label",
                "availability_completion_lag_max", "timezone_evidence", "delivery_id", "verification_state",
                "range_from", "range_to")}})
        gcfg = dict(spec["config"])
        for key, role in spec["input_keys"].items():
            gcfg[key] = inputs[role]
        gcfg.update(outputs)
        config_path = out_dir / "governed_config.json"
        _write_json_atomic(config_path, gcfg)
        cmd = [substitute(part, inputs, out_dir, config_path, repo_root) for part in spec["command"] + spec["extra_args"]]
        cwd = substitute(spec.get("cwd") or str(repo_root), inputs, out_dir, config_path, repo_root)
        env = dict(os.environ, CUDA_VISIBLE_DEVICES="", PYTHONPATH=str(repo_root))
        env.update({k: substitute(str(v), inputs, out_dir, config_path, repo_root) for k, v in spec["env"].items()})
        state.update(command=cmd, cwd=cwd, governed_config=str(config_path))
        checkpoint()
        log_path = out_dir / "run.log"
        if runner is None:
            with open(log_path, "ab") as log:
                exit_code = subprocess.run(cmd, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT).returncode
        else:
            exit_code = runner(cmd, cwd, env, log_path)
        state["exit_code"] = exit_code
        if exit_code != 0:
            terminal_status, terminal_reason = "FAILED", f"COMMAND_EXIT_{exit_code}"
            raise GovernedExecError(f"command exited {exit_code}")
        try:
            metrics = collect_metrics(spec, out_dir)
        except GovernedExecError as exc:
            terminal_status, terminal_reason = "INCONCLUSIVE", f"METRICS_UNAVAILABLE:{exc}"
            raise
        artifacts = collect_artifacts(spec, out_dir, outputs)
    except BaseException as exc:
        failure = exc
        if terminal_status == "COMPLETED":
            terminal_status = "REFUSED" if isinstance(exc, GovernedExecError) else "FAILED"
            terminal_reason = (f"GOVERNED_RUN_REFUSED:{exc}" if isinstance(exc, GovernedExecError)
                               else f"UNEXPECTED_{type(exc).__name__.upper()}")
    finished_at = utc_now()
    terminal = {
        "schema": "governed_terminal.v1", "generation": 1, "status": terminal_status,
        "reason": terminal_reason, "started_at": started_at, "finished_at": finished_at,
        "costs": {"wall_seconds": max(0.0, time.monotonic() - wall_start)},
        "deliveries": delivery_ids, "artifacts": artifacts,
        "metrics": metrics if terminal_status == "COMPLETED" else [],
        "tags": {**spec["tags"], "exit_code": str(state.get("exit_code", "")),
                 # the weakest availability scope among the inputs bounds what the result may claim
                 "availability_use": ",".join(sorted({
                     str(item.get("availability_use") or "UNDECLARED") for item in state.get("inputs", [])
                 })) or "NONE"},
    }
    envelope = {"campaign_sha256": campaign_sha256, "unit_id": unit, "terminal": terminal}
    item = outbox.put(envelope)
    state.update(terminal_outbox=str(item.path), terminal=terminal, status=terminal_status)
    if terminal_reason:
        state["reason"] = terminal_reason
    checkpoint()
    flushed = send_pending(client, outbox)
    state["outbox_flush"] = flushed
    if flushed["pending"]:
        state["terminal_pending"] = True
        checkpoint()
        raise GovernedExecError("terminal remains pending; result is not governing")
    state["reconciliation"] = _reconcile(client, campaign_sha256, unit, before_run=False)
    state["terminal_pending"] = False
    checkpoint()
    if failure is not None:
        if isinstance(failure, GovernedExecError):
            raise failure
        raise GovernedExecError(f"{type(failure).__name__}: {failure}") from failure
    return state


def load_api_key(path):
    if path:
        return Path(path).expanduser().read_text(encoding="utf-8").strip()
    key = os.environ.get("DATA_GOV_API_KEY")
    if not key:
        raise GovernedExecError("no API key: pass --api-key-file or set DATA_GOV_API_KEY")
    return key


# ------------------------------------------------------- consumer profiles
def resource_for(path, lake_root: Path) -> str:
    """A config input path -> lake resource id (path relative to the lake root)."""
    target = Path(path).expanduser().resolve()
    root = Path(lake_root).expanduser().resolve()
    if not target.is_relative_to(root):
        raise GovernedExecError(f"input {path} is not under the lake root {lake_root}")
    return target.relative_to(root).as_posix()


def build_spec(profile: dict, config: dict, args, extra: list) -> dict:
    """A consumer profile + its config file -> governed_exec_spec.v1.

    profile keys: project, input_keys (config keys that name input files),
    output_keys (config keys redirected under out_dir), command (argv with
    placeholders), cwd (optional), metrics (callable(config) -> metrics spec or a
    spec), artifacts (role -> output key), tags (optional)."""
    repo_root = Path(args.repo_root).expanduser().resolve()
    lake_root = Path(args.lake_root).expanduser()
    if not lake_root.is_absolute():
        lake_root = repo_root / lake_root
    datasets, input_keys = [], {}
    for key in profile["input_keys"]:
        value = config.get(key)
        if not value or not isinstance(value, str):
            continue
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = repo_root / path
        datasets.append({"lake": args.lake, "resource": resource_for(path, lake_root), "role": key,
                         "from": args.range_from, "to": args.range_to})
        input_keys[key] = key
    if not datasets:
        raise GovernedExecError("the config names none of the profile's input keys")
    metrics = profile["metrics"](config) if callable(profile["metrics"]) else profile["metrics"]
    return validate_spec({
        "schema": SPEC_SCHEMA, "project": profile["project"], "campaign_key": args.experiment_key,
        "unit_id": args.experiment_key, "classification": args.classification,
        "terminal_lake": args.metrics_lake, "repo_root": str(repo_root), "cwd": profile.get("cwd"),
        "datasets": datasets, "input_keys": input_keys, "output_keys": list(profile["output_keys"]),
        "config": config, "command": list(profile["command"]), "extra_args": list(extra),
        "metrics": metrics, "artifacts": dict(profile["artifacts"]), "env": dict(profile.get("env") or {}),
        "tags": {**(profile.get("tags") or {}), "phase": str(args.phase or Path(args.load_config).resolve().parent.name),
                 "experiment_set_key": str(args.experiment_set_key or "")},
    })


def consumer_main(profile: dict, argv=None, *, repo_root) -> int:
    """CLI shared by the repositories' tools/governed_run.py wrappers."""
    argv = list(sys.argv[1:] if argv is None else argv)
    extra = []
    if "--" in argv:
        cut = argv.index("--")
        argv, extra = argv[:cut], argv[cut + 1:]
    parser = argparse.ArgumentParser(
        description=f"Run {profile['project']} on data-gov governed inputs and report its terminal.",
        epilog="Arguments after `--` are passed to the command (long flags only).")
    parser.add_argument("--load_config", required=True, help="the repository's config JSON")
    parser.add_argument("--experiment-key", required=True)
    parser.add_argument("--experiment-set-key")
    parser.add_argument("--gov-url", default="http://127.0.0.1:5055")
    parser.add_argument("--api-key-file")
    parser.add_argument("--lake", required=True, help="data-gov lake of the inputs")
    parser.add_argument("--lake-root", required=True, help="directory the lake serves (inputs map to paths under it)")
    parser.add_argument("--metrics-lake", default="olap_cube")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE)
    parser.add_argument("--outbox-dir", default=DEFAULT_OUTBOX)
    parser.add_argument("--from", dest="range_from", metavar="YYYY-MM-DD")
    parser.add_argument("--to", dest="range_to", metavar="YYYY-MM-DD")
    parser.add_argument("--classification", choices=("GOVERNING", "NON_GOVERNING"), default="GOVERNING")
    parser.add_argument("--phase")
    parser.add_argument("--print-spec", action="store_true", help="print the spec and stop")
    args = parser.parse_args(argv)
    args.repo_root = repo_root
    try:
        for token in extra:
            name = str(token)[2:].split("=", 1)[0] if str(token).startswith("--") else None
            if name and (name in profile["input_keys"] or name in profile["output_keys"] or name == "load_config"):
                raise GovernedExecError(f"--{name} would override a governed input or output; refused")
        with open(Path(args.load_config).expanduser(), encoding="utf-8") as handle:
            config = json.load(handle)
        spec = build_spec(profile, config, args, extra)
        if args.print_spec:
            print(json.dumps(spec, indent=2, sort_keys=True))
            return 0
        client = DataGovClient(args.gov_url, load_api_key(args.api_key_file), args.experiment_key)
        state = run(spec, client, args.out_dir, args.cache_dir, args.outbox_dir)
    except GovernedExecError as exc:
        print(f"governed_run: {exc}", file=sys.stderr)
        return 1
    print(f"governed_run: {state['campaign_key']} status={state['status']} "
          f"campaign={state['campaign_sha256']} receipt={Path(state['out_dir']) / 'GOVERNED_RUN.json'}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--spec")
    parser.add_argument("--gov-url", default="http://127.0.0.1:5055")
    parser.add_argument("--api-key-file")
    parser.add_argument("--out-dir")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE)
    parser.add_argument("--outbox-dir", default=DEFAULT_OUTBOX)
    parser.add_argument("--status", action="store_true", help="outbox health as JSON")
    parser.add_argument("--flush", action="store_true", help="retry every pending envelope once")
    parser.add_argument("--dispose", metavar="FILE", help="close a pending envelope as INVALID_ENVELOPE")
    parser.add_argument("--supersede", metavar="FILE", help="send --terminal as the next generation, then dispose FILE")
    parser.add_argument("--terminal", metavar="T.json")
    parser.add_argument("--reason")
    args = parser.parse_args(argv)
    try:
        outbox = TerminalOutbox(Path(os.path.expanduser(args.outbox_dir)))
        if args.status:
            print(json.dumps(outbox.status(), indent=2, sort_keys=True))
            return 0
        if args.dispose:
            print(json.dumps(outbox.dispose(args.dispose, "INVALID_ENVELOPE", args.reason or ""), sort_keys=True))
            return 0
        if args.flush or args.supersede:
            client = DataGovClient(args.gov_url, load_api_key(args.api_key_file), "terminal-outbox")
            if args.supersede:
                if not args.terminal:
                    raise GovernedExecError("--supersede needs --terminal")
                with open(Path(args.terminal).expanduser(), encoding="utf-8") as handle:
                    corrected = json.load(handle)

                def sender(envelope):
                    status, receipt = client.report_terminal(envelope["campaign_sha256"], envelope["unit_id"], envelope["terminal"])
                    if status not in (200, 201):
                        raise GovernedExecError(f"terminal refused: http {status} {receipt.get('error', '')}".strip())
                    _reconcile(client, envelope["campaign_sha256"], envelope["unit_id"], before_run=False)
                    return receipt
                print(json.dumps(outbox.supersede(args.supersede, corrected, sender, args.reason or ""), sort_keys=True))
                return 0
            result = send_pending(client, outbox)
            print(json.dumps(result, sort_keys=True))
            return 0 if result["pending"] == 0 else 1
        if not args.spec or not args.out_dir:
            raise GovernedExecError("--spec and --out-dir are required to run")
        spec = load_spec(args.spec)
        client = DataGovClient(args.gov_url, load_api_key(args.api_key_file), spec["campaign_key"])
        state = run(spec, client, args.out_dir, args.cache_dir, args.outbox_dir)
    except (GovernedExecError, ValueError, RuntimeError, OSError) as exc:
        print(f"governed_exec: {exc}", file=sys.stderr)
        return 1
    print(f"governed_exec: {state['campaign_key']} status={state['status']} "
          f"campaign={state['campaign_sha256']} receipt={Path(state['out_dir']) / 'GOVERNED_RUN.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
