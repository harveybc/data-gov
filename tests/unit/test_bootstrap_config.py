import hashlib
import importlib.util
import json
from pathlib import Path


def test_generated_credentials_have_a_matching_startup_config(tmp_path, monkeypatch):
    from app import lake_auth
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location('credential_bootstrap', root / 'scripts/issue_credentials.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / 'examples/config').mkdir(parents=True)
    (tmp_path / 'examples/config/default.json').write_bytes((root / 'examples/config/default.json').read_bytes())
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(lake_auth, 'token_path', lambda: tmp_path / 'var/lake_token')
    monkeypatch.delenv('DATA_GOV_LAKE_TOKEN', raising=False)
    module.main()
    credentials = json.loads((tmp_path / 'var/credentials.json').read_text())
    config_path = tmp_path / 'var/config.json'
    assert config_path.is_file()
    config = json.loads(config_path.read_text())
    for name, password in credentials['people'].items():
        expected = hashlib.sha256(f"{credentials['password_salt']}:{password}".encode()).hexdigest()
        assert config['principals'][name]['password_hash'] == expected
    for name, key in credentials['services'].items():
        expected = hashlib.sha256(f"{credentials['password_salt']}:{key}".encode()).hexdigest()
        assert config['principals'][name]['api_key_hash'] == expected
    before = config_path.read_bytes()
    module.main()
    assert config_path.read_bytes() == before
