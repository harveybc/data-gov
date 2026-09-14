"""Storage, temporal and outbox safeguards for Flow v3."""

import hashlib
import os
from pathlib import Path

import pytest

from app.client import DataGovClient
from app.httpstream import BufferedResponse
from app.outbox import TerminalOutbox
from lake_plugins.files_lake import Plugin as FilesLake
from tests.conftest import LAB_EARLY, write_lab_files


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_same_size_same_mtime_rewrite_is_rehashed(tmp_path):
    root = write_lab_files(tmp_path / "source")
    lake = FilesLake()
    lake.set_params(
        root_path=str(root), include_globs=["**/*.csv"],
        source_hash_cache=str(tmp_path / "source_sha256.json"),
        cuts_dir=str(tmp_path / "cuts"),
    )
    path = root / LAB_EARLY
    first = lake._file_sha256(path)
    before = path.stat()
    original = path.read_bytes()
    changed = original.replace(b",1\n", b",9\n", 1)
    assert len(changed) == len(original)
    path.write_bytes(changed)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    second = lake._file_sha256(path)
    assert second == _sha(path) and second != first


def test_cut_publication_never_overwrites_different_bytes(tmp_path):
    target = tmp_path / "cut.csv"
    first = tmp_path / "first.tmp"
    second = tmp_path / "second.tmp"
    first.write_bytes(b"first")
    second.write_bytes(b"other")
    FilesLake._commit(first, target)
    with pytest.raises(RuntimeError, match="cut identity conflict"):
        FilesLake._commit(second, target)
    assert target.read_bytes() == b"first"


def test_outbox_survives_failure_and_flushes_once(tmp_path):
    outbox = TerminalOutbox(tmp_path / "outbox")
    terminal = {
        "campaign_sha256": "a" * 64,
        "unit_id": "u001",
        "body": {"schema": "governed_terminal.v1", "generation": 1},
    }
    item = outbox.put(terminal)
    assert item.state == "PENDING"

    calls = []

    def unavailable(_payload):
        calls.append("down")
        raise OSError("service unavailable")

    assert outbox.flush(unavailable) == {"sent": 0, "pending": 1}
    reopened = TerminalOutbox(tmp_path / "outbox")

    def accepted(payload):
        calls.append(payload["unit_id"])
        return {"terminal_sha256": "b" * 64, "already_stored": False}

    assert reopened.flush(accepted) == {"sent": 1, "pending": 0}
    assert reopened.flush(accepted) == {"sent": 0, "pending": 0}
    assert calls == ["down", "u001"]


def _delivery_response(body, **headers):
    digest = hashlib.sha256(body).hexdigest()
    values = {
        "Content-Disposition": 'attachment; filename="data.csv"',
        "X-Content-SHA256": digest,
        "X-Source-SHA256": "a" * 64,
        "X-Availability-Contract-SHA256": "b" * 64,
        **headers,
    }
    return BufferedResponse(200, values, body), digest


def test_official_client_refuses_missing_governing_contract_digest(tmp_path):
    response, _ = _delivery_response(
        b"x\n", **{"X-Availability-Contract-SHA256": ""}
    )
    status, body = DataGovClient()._save(
        response, tmp_path / "cache", governing=True
    )
    assert status == 502
    assert body == {"error": "missing availability contract digest"}


def test_official_client_never_replaces_conflicting_cached_bytes(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    response, digest = _delivery_response(b"governing bytes\n")
    target = cache / f"{digest}.csv"
    target.write_bytes(b"different bytes\n")

    status, body = DataGovClient()._save(response, cache, governing=True)
    assert status == 409
    assert body["error"] == "cache identity conflict"
    assert target.read_bytes() == b"different bytes\n"
