"""An operator issues one consumer's API key with data-gov's own code.

No hand-made token: the key is `secrets.token_urlsafe`, hashed by the access
plugin that will later verify it, and the script refuses unless the key it just
issued authenticates against the configuration it wrote.
"""

import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "issue_service_key", ROOT / "scripts" / "issue_service_key.py"
)
issue_service_key = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(issue_service_key)

from access_plugins.default_access import Plugin as AccessPlugin  # noqa: E402


def _config(tmp_path, **extra):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"password_salt": "salt-for-the-test", "principals": {}, **extra}),
        encoding="utf-8",
    )
    return path


def test_issued_key_authenticates_and_is_only_stored_as_a_digest(tmp_path):
    config_path = _config(tmp_path)
    key_file = tmp_path / "secrets" / "consumer.key"

    assert issue_service_key.main(
        ["--config", str(config_path), "--principal", "consumer", "--key-file", str(key_file)]
    ) == 0

    key = key_file.read_text(encoding="utf-8").strip()
    assert len(key) >= 32
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600

    config = json.loads(config_path.read_text(encoding="utf-8"))
    record = config["principals"]["consumer"]
    assert record["kind"] == "service" and record["role"] == "service"
    assert key not in config_path.read_text(encoding="utf-8")

    access = AccessPlugin()
    access.set_params(password_salt=config["password_salt"], principals=config["principals"], policies=[])
    assert access.authenticate_api_key(key)["username"] == "consumer"
    assert access.authenticate_api_key(key + "x") is None


def test_an_existing_principal_is_not_overwritten_without_rotate(tmp_path):
    config_path = _config(tmp_path)
    key_file = tmp_path / "consumer.key"
    assert issue_service_key.main(
        ["--config", str(config_path), "--principal", "consumer", "--key-file", str(key_file)]
    ) == 0
    first = key_file.read_text(encoding="utf-8")

    assert issue_service_key.main(
        ["--config", str(config_path), "--principal", "consumer", "--key-file", str(key_file)]
    ) == 2
    assert key_file.read_text(encoding="utf-8") == first

    assert issue_service_key.main(
        ["--config", str(config_path), "--principal", "consumer",
         "--key-file", str(key_file), "--rotate"]
    ) == 0
    rotated = key_file.read_text(encoding="utf-8")
    assert rotated != first

    config = json.loads(config_path.read_text(encoding="utf-8"))
    access = AccessPlugin()
    access.set_params(password_salt=config["password_salt"], principals=config["principals"], policies=[])
    assert access.authenticate_api_key(rotated.strip())["username"] == "consumer"
    assert access.authenticate_api_key(first.strip()) is None


def test_a_person_principal_is_refused(tmp_path):
    config_path = _config(
        tmp_path, principals={"harvey": {"kind": "person", "role": "ceo"}}
    )
    os.chmod(config_path, 0o600)
    assert issue_service_key.main(
        ["--config", str(config_path), "--principal", "harvey",
         "--key-file", str(tmp_path / "k"), "--rotate"]
    ) == 2
    assert not (tmp_path / "k").exists()


def test_a_configuration_without_a_salt_is_refused(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"principals": {}}), encoding="utf-8")
    assert issue_service_key.main(
        ["--config", str(path), "--principal", "consumer", "--key-file", str(tmp_path / "k")]
    ) == 2
    assert not (tmp_path / "k").exists()
