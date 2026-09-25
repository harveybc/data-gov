"""Shared lake-service token. Lakes refuse /api/v1 without it; data-gov sends it."""

from __future__ import annotations

import hmac
import os
from pathlib import Path


def token_path() -> Path:
    env = os.getenv("DATA_GOV_LAKE_TOKEN_FILE")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[1] / "var" / "lake_token"


def load_token() -> str | None:
    env = os.getenv("DATA_GOV_LAKE_TOKEN")
    if env:
        return env.strip()
    path = token_path()
    if path.is_file():
        return path.read_text(encoding="utf-8").strip() or None
    return None


def write_token(value: str) -> Path:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def check_bearer(header: str | None, expected: str | None) -> bool:
    if not expected:
        return False
    given = (header or "").strip()
    if not given.lower().startswith("bearer "):
        return False
    got = given[7:].strip()
    return hmac.compare_digest(got, expected)
