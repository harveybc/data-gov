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
    else:
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
    from app.lake_auth import load_token, write_token

    if not load_token():
        token = secrets.token_urlsafe(32)
        write_token(token)
        print("wrote var/lake_token")
    secret = var / "flask_secret"
    if not secret.exists():
        secret.write_text(secrets.token_urlsafe(32) + "\n", encoding="utf-8")
        secret.chmod(0o600)
        print("wrote var/flask_secret")
    config_path = var / "config.json"
    if not config_path.exists():
        credentials = json.loads(secrets_path.read_text(encoding="utf-8"))
        config = json.loads((ROOT / "examples/config/default.json").read_text(encoding="utf-8"))
        salt = credentials["password_salt"]
        config["password_salt"] = salt
        for category, field in (("people", "password_hash"), ("services", "api_key_hash")):
            for name, value in credentials[category].items():
                record = config["principals"].setdefault(name, {
                    "kind": "person" if category == "people" else "service", "role": "operator",
                })
                record[field] = hashlib.sha256(f"{salt}:{value}".encode()).hexdigest()
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        config_path.chmod(0o600)
        print("wrote var/config.json with matching principal hashes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
