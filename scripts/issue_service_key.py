#!/usr/bin/env python3
"""Issue (or rotate) one service principal's API key in a data-gov configuration.

`issue_credentials.py` bootstraps the whole demo checkout. This issues a single
service principal into an arbitrary configuration file, so an operator can give
one consumer its own identity without touching the others' secrets.

The key is produced by `secrets.token_urlsafe` and stored only as the digest the
access plugin itself computes: there is exactly one hashing implementation,
`access_plugins.default_access.Plugin._digest`, and this script calls it. The
plaintext is written to `--key-file` (mode 600) and is never printed, never
returned and never placed inside the checkout.

    python -m scripts.issue_service_key --config <config.json> \
        --principal m5phet --role service --key-file ~/.config/<app>/data-gov.key

Exit 0 on success; the last line names the principal and the key file.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path

if __package__ in (None, ""):  # running the file directly
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from access_plugins.default_access import Plugin as AccessPlugin  # noqa: E402

KEY_BYTES = 32


def _write_private(path: Path, text: str) -> None:
    """Create/replace `path` with mode 600, never leaving a readable window."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.chmod(path, 0o600)


def issue(config_path: Path, principal: str, role: str, key_file: Path, rotate: bool) -> int:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    principals = config.setdefault("principals", {})
    record = principals.get(principal)
    if record is not None and not rotate:
        print(f"refusing: principal {principal!r} already exists (use --rotate)", file=sys.stderr)
        return 2
    if record is not None and record.get("kind") != "service":
        print(f"refusing: principal {principal!r} is not a service principal", file=sys.stderr)
        return 2

    salt = config.get("password_salt")
    if not salt:
        print("refusing: the configuration has no password_salt", file=sys.stderr)
        return 2

    key = secrets.token_urlsafe(KEY_BYTES)
    access = AccessPlugin()
    access.set_params(password_salt=salt, principals={}, policies=[])
    digest = access._digest(key)

    principals[principal] = dict(
        record or {}, kind="service", role=role, api_key_hash=digest
    )

    # the issued key must authenticate against the configuration as written
    check = AccessPlugin()
    check.set_params(password_salt=salt, principals=principals, policies=[])
    identified = check.authenticate_api_key(key)
    if not identified or identified["username"] != principal:
        print("refusing: the issued key does not authenticate; nothing written", file=sys.stderr)
        return 3

    _write_private(key_file, key + "\n")
    _write_private(config_path, json.dumps(config, indent=2) + "\n")
    print(f"issued service principal {principal!r} (role {role!r}); key in {key_file}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True, help="data-gov configuration JSON to update")
    parser.add_argument("--principal", required=True, help="service principal name")
    parser.add_argument("--role", default="service", help="role for the principal (default: service)")
    parser.add_argument("--key-file", required=True, help="where the plaintext key is written (mode 600)")
    parser.add_argument("--rotate", action="store_true", help="replace an existing principal's key")
    args = parser.parse_args(argv)
    return issue(
        Path(args.config).expanduser(),
        args.principal,
        args.role,
        Path(args.key_file).expanduser(),
        args.rotate,
    )


if __name__ == "__main__":
    raise SystemExit(main())
