#!/usr/bin/env python3
"""Create local principals and write plaintext to var/ (gitignored)."""

from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SALT = "datagov-v1"


def digest(secret: str) -> str:
    return hashlib.sha256(f"{SALT}:{secret}".encode()).hexdigest()


def main():
    var = ROOT / "var"
    var.mkdir(parents=True, exist_ok=True)
    secrets_path = var / "credentials.json"
    if secrets_path.exists():
        print(f"already exists: {secrets_path}")
        return 0
    payload = {
        "password_salt": SALT,
        "people": {
            "harvey": secrets.token_urlsafe(12),
            "musashi": secrets.token_urlsafe(12),
        },
        "services": {
            "predictor": secrets.token_urlsafe(24),
            "doin": secrets.token_urlsafe(24),
            "heuristic-strategy": secrets.token_urlsafe(24),
        },
    }
    secrets_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    secrets_path.chmod(0o600)
    print(f"wrote {secrets_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
