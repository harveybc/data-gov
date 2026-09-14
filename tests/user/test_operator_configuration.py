import copy
import json

import pytest

from tests.conftest import HUMAN_PASS


def login(client):
    client.post('/login', data={'username': 'harvey', 'password': HUMAN_PASS})


def payload(runtime):
    from app.operator_config import editable_config
    return editable_config(runtime['config'])


def test_configuration_requires_login(client):
    assert client.get('/settings').status_code == 302


def test_settings_are_read_only_and_redacted(client, runtime, tmp_path):
    dest = tmp_path / 'pending.json'
    runtime['config']['operator_config_path'] = str(dest)
    runtime['config']['lake_service_token'] = 'not-for-the-browser'
    login(client)
    page = client.get('/settings')
    assert page.status_code == 200
    assert b'financial_files' in page.data
    assert b'not-for-the-browser' not in page.data
    assert b'password_hash' not in page.data
    assert not dest.exists()


def test_save_is_pending_not_active(client, runtime, tmp_path):
    dest = tmp_path / 'pending.json'
    runtime['config']['operator_config_path'] = str(dest)
    before = copy.deepcopy(runtime['config'])
    login(client)
    client.get('/settings')
    with client.session_transaction() as state:
        csrf = state['settings_csrf']
    proposed = payload(runtime)
    proposed['web_port'] = 15055
    response = client.post('/settings', data={'configuration': json.dumps(proposed), 'csrf': csrf})
    assert response.status_code == 200
    saved = json.loads(dest.read_text())
    assert saved['web_port'] == 15055
    assert saved['principals'] == before['principals']
    assert runtime['config'] == before
    assert b'Pending restart' in response.data


@pytest.mark.parametrize('case', ['duplicate_store', 'unknown_store', 'port_bool', 'no_deny', 'extra', 'invalid_kind'])
def test_invalid_drafts_do_not_write(client, runtime, tmp_path, case):
    dest = tmp_path / 'pending.json'
    runtime['config']['operator_config_path'] = str(dest)
    login(client)
    client.get('/settings')
    with client.session_transaction() as state:
        csrf = state['settings_csrf']
    proposed = payload(runtime)
    if case == 'duplicate_store':
        proposed['lakes'].append(proposed['lakes'][0])
    elif case == 'unknown_store':
        proposed['policies'][0]['lake'] = 'does-not-exist'
    elif case == 'port_bool':
        proposed['web_port'] = True
    elif case == 'no_deny':
        proposed['policies'][0].pop('deny_from')
    elif case == 'extra':
        proposed['principals'] = {}
    else:
        proposed['lakes'][0]['kind'] = 'magic'
    result = client.post('/settings', data={'configuration': json.dumps(proposed), 'csrf': csrf})
    assert result.status_code == 400
    assert not dest.exists()


def test_duplicate_json_fields_and_stale_form_refuse(client, runtime, tmp_path):
    runtime['config']['operator_config_path'] = str(tmp_path / 'pending.json')
    login(client)
    assert client.post('/settings', data={'configuration': '{}'}).status_code == 400
    client.get('/settings')
    with client.session_transaction() as state:
        csrf = state['settings_csrf']
    result = client.post('/settings', data={'configuration': '{"web_port": 5, "web_port": 6}', 'csrf': csrf})
    assert result.status_code == 400

