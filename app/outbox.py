"""Small durable outbox for terminal reports."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


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
        self.pending.mkdir(parents=True, exist_ok=True)
        self.sent.mkdir(parents=True, exist_ok=True)

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

    def flush(self, sender):
        sent = 0
        for path in sorted(self.pending.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="ascii"))
                response = sender(payload)
                if not isinstance(response, dict) or not response.get("terminal_sha256"):
                    raise RuntimeError("terminal receipt missing")
            except Exception:
                continue
            target = self.sent / path.name
            if target.exists():
                if target.read_bytes() != path.read_bytes():
                    raise RuntimeError("sent outbox identity conflict")
                path.unlink()
            else:
                os.replace(path, target)
            _fsync_dir(self.pending)
            _fsync_dir(self.sent)
            sent += 1
        return {"sent": sent, "pending": len(list(self.pending.glob("*.json")))}
