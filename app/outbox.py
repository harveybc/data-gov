"""Small durable outbox for terminal reports."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

FAILURE_CLASSES = ("TRANSIENT", "CONFIGURATION", "REFUSED_BY_SERVER", "UNRESOLVED")
DISPOSITIONS = ("INVALID_ENVELOPE", "SUPERSEDED")
_HTTP_IN_ERROR = re.compile(r"\bhttp (\d{3})\b")


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def classify_failure(error: str) -> str:
    """What a send failure means for the pending envelope. A 4xx alone never decides
    that the envelope is invalid: it awaits an explicit disposition."""
    match = _HTTP_IN_ERROR.search(error or "")
    if match is None:
        text = (error or "").lower()
        if "diverge" in text or "reconciliation" in text or "missing after accepted" in text:
            return "UNRESOLVED"
        return "TRANSIENT"
    status = int(match.group(1))
    if status >= 500 or status == 429:
        return "TRANSIENT"
    if status in (401, 403, 404):
        return "CONFIGURATION"
    return "REFUSED_BY_SERVER"


def _fsync_dir(path: Path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@dataclass(frozen=True)
class OutboxItem:
    path: Path
    state: str
    payload: dict


class TerminalOutbox:
    """Write-once pending files. Successful sends are atomically moved to sent/."""

    def __init__(self, root):
        self.root = Path(root)
        self.pending = self.root / "pending"
        self.sent = self.root / "sent"
        self.adjudicated = self.root / "adjudicated"
        self.pending.mkdir(parents=True, exist_ok=True)
        self.sent.mkdir(parents=True, exist_ok=True)
        self.adjudicated.mkdir(parents=True, exist_ok=True)

    def put(self, payload):
        raw = (_canonical(payload) + "\n").encode("ascii")
        digest = hashlib.sha256(raw).hexdigest()
        path = self.pending / f"{digest}.json"
        if path.exists():
            if path.read_bytes() != raw:
                raise RuntimeError("outbox identity conflict")
            return OutboxItem(path, "PENDING", payload)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb", closefd=True) as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        _fsync_dir(self.pending)
        return OutboxItem(path, "PENDING", payload)

    # failure sidecars (N4): a refused send never deletes evidence ----------
    def _failure_path(self, path: Path) -> Path:
        return path.with_name(path.name[:-5] + ".failure")

    def _record_failure(self, path: Path, error: str) -> dict:
        sidecar = self._failure_path(path)
        record = {"attempts": 0, "first_seen": _utc_now()}
        if sidecar.is_file():
            record = json.loads(sidecar.read_text(encoding="utf-8"))
        record.update(attempts=int(record.get("attempts", 0)) + 1, last_seen=_utc_now(),
                      last_error=error, **{"class": classify_failure(error)})
        tmp = sidecar.with_name(sidecar.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, sidecar)
        return record

    def _failure(self, path: Path):
        sidecar = self._failure_path(path)
        return json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.is_file() else None

    def status(self) -> dict:
        """Recoverable pendings, envelopes awaiting adjudication, unresolved failures
        and adjudicated cases are told apart; nothing is hidden."""
        pending = []
        for path in sorted(self.pending.glob("*.json")):
            envelope = json.loads(path.read_text(encoding="ascii"))
            failure = self._failure(path) or {}
            pending.append({
                "file": path.name, "campaign_sha256": envelope["campaign_sha256"],
                "unit_id": envelope["unit_id"], "generation": envelope["terminal"].get("generation"),
                "status": envelope["terminal"].get("status"),
                "class": failure.get("class", "NOT_YET_SENT"), "attempts": failure.get("attempts", 0),
                "last_error": failure.get("last_error"),
            })
        adjudicated = [json.loads(p.read_text(encoding="utf-8"))
                       for p in sorted(self.adjudicated.glob("*.disposition.json"))]
        return {"schema": "terminal_outbox_status.v1", "sent": len(list(self.sent.glob("*.json"))),
                "pending": pending, "adjudicated": adjudicated,
                "recoverable": sum(p["class"] in ("NOT_YET_SENT", "TRANSIENT", "CONFIGURATION") for p in pending),
                "awaiting_adjudication": sum(p["class"] == "REFUSED_BY_SERVER" for p in pending),
                "unresolved": sum(p["class"] == "UNRESOLVED" for p in pending)}

    def dispose(self, name: str, decision: str, reason: str, *, successor_terminal_sha256=None,
                successor_generation=None) -> dict:
        """Move a pending envelope, unchanged, to adjudicated/ with a write-once disposition.
        INVALID_ENVELOPE closes it; SUPERSEDED links the accepted successor terminal."""
        if decision not in DISPOSITIONS:
            raise ValueError(f"unknown disposition {decision!r}")
        if not reason or not str(reason).strip():
            raise ValueError("a disposition states its reason")
        path = self.pending / name
        if not path.is_file() or not name.endswith(".json"):
            raise ValueError(f"{name} is not a pending envelope")
        if decision == "SUPERSEDED" and not (isinstance(successor_terminal_sha256, str)
                                             and re.fullmatch(r"[0-9a-f]{64}", successor_terminal_sha256)):
            raise ValueError("SUPERSEDED needs the accepted successor terminal_sha256")
        raw = path.read_bytes()
        envelope = json.loads(raw)
        record = {
            "schema": "terminal_outbox_disposition.v1", "file": name, "decision": decision,
            "reason": str(reason), "envelope_sha256": hashlib.sha256(raw).hexdigest(),
            "campaign_sha256": envelope["campaign_sha256"], "unit_id": envelope["unit_id"],
            "generation": envelope["terminal"].get("generation"), "status": envelope["terminal"].get("status"),
            "failure": self._failure(path), "successor_terminal_sha256": successor_terminal_sha256,
            "successor_generation": successor_generation, "disposed_at": _utc_now(),
        }
        target = self.adjudicated / name
        if target.exists():
            raise RuntimeError("adjudicated outbox identity conflict")
        disposition = self.adjudicated / (name[:-5] + ".disposition.json")
        fd = os.open(disposition, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(path, target)
        sidecar = self._failure_path(path)
        if sidecar.is_file():
            os.replace(sidecar, self.adjudicated / sidecar.name)
        _fsync_dir(self.pending)
        _fsync_dir(self.adjudicated)
        return record

    def supersede(self, name: str, corrected_terminal: dict, sender, reason: str) -> dict:
        """Send a corrected terminal as the next generation of the same campaign and unit
        (same outcome, same deliveries), then dispose the original as SUPERSEDED."""
        path = self.pending / name
        if not path.is_file() or not name.endswith(".json"):
            raise ValueError(f"{name} is not a pending envelope")
        original = json.loads(path.read_text(encoding="ascii"))
        base = original["terminal"]
        if not isinstance(corrected_terminal, dict) or corrected_terminal.get("schema") != "governed_terminal.v1":
            raise ValueError("the successor must be a governed_terminal.v1")
        if corrected_terminal.get("status") != base.get("status"):
            raise ValueError("a successor keeps the original outcome")
        if sorted(corrected_terminal.get("deliveries") or []) != sorted(base.get("deliveries") or []):
            raise ValueError("a successor keeps the original deliveries")
        generation = int(base.get("generation", 1)) + 1
        successor = dict(corrected_terminal, generation=generation)
        successor["tags"] = {**(successor.get("tags") or {}),
                             "supersedes_envelope_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                             "supersedes_generation": str(base.get("generation", 1))}
        envelope = {"campaign_sha256": original["campaign_sha256"], "unit_id": original["unit_id"],
                    "terminal": successor}
        item = self.put(envelope)
        receipt = sender(envelope)
        if not isinstance(receipt, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("terminal_sha256"))):
            self._record_failure(item.path, "terminal receipt missing")
            raise RuntimeError("successor terminal was not accepted")
        os.replace(item.path, self.sent / item.path.name)
        _fsync_dir(self.pending)
        _fsync_dir(self.sent)
        return self.dispose(name, "SUPERSEDED", reason, successor_terminal_sha256=receipt["terminal_sha256"],
                            successor_generation=generation)

    def flush(self, sender):
        sent = 0
        for path in sorted(self.pending.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="ascii"))
                response = sender(payload)
                if not isinstance(response, dict) or not response.get("terminal_sha256"):
                    raise RuntimeError("terminal receipt missing")
            except Exception as exc:
                self._record_failure(path, f"{type(exc).__name__}: {exc}")
                continue
            target = self.sent / path.name
            if target.exists():
                if target.read_bytes() != path.read_bytes():
                    raise RuntimeError("sent outbox identity conflict")
                path.unlink()
            else:
                os.replace(path, target)
            self._failure_path(path).unlink(missing_ok=True)
            _fsync_dir(self.pending)
            _fsync_dir(self.sent)
            sent += 1
        return {"sent": sent, "pending": len(list(self.pending.glob("*.json")))}
