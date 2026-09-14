"""Prepare operator configuration without mutating a running campaign."""

import copy
import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from app.main import check_startup
from app.store_metadata import store_metadata

STORE_FIELDS = {
    'plugin', 'lake_id', 'title', 'description', 'kind', 'engine', 'base_url',
    'root_path', 'sqlite_path', 'include_globs', 'time_column', 'holdout_start',
    'resource_contracts', 'untimed', 'timeout',
}
TOP_FIELDS = {'web_host', 'web_port', 'max_downloads', 'lakes', 'policies'}
VERBS = {'discover', 'coverage', 'read', 'download', 'query', 'write_metrics', 'write_terminal'}


def editable_config(config):
    stores = []
    for original in config.get('lakes', []):
        store = {key: copy.deepcopy(value) for key, value in original.items() if key in STORE_FIELDS}
        if store.get('kind') == 'files_inventory':
            store['kind'] = 'lake'
        elif store.get('kind') == 'sql_olap':
            store['kind'] = 'warehouse'
        stores.append(store)
    return {
        'web_host': config.get('web_host', '127.0.0.1'),
        'web_port': config.get('web_port', 5055),
        'max_downloads': config.get('max_downloads', 2),
        'lakes': stores,
        'policies': copy.deepcopy(config.get('policies', [])),
    }


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate field: {key}')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f'non-finite JSON value: {value}')


def _day(value, label):
    if value is not None and (not isinstance(value, str) or date.fromisoformat(value).isoformat() != value):
        raise ValueError(f'{label} must be YYYY-MM-DD or null')


def pending_config(config, raw):
    draft = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_invalid_constant)
    if not isinstance(draft, dict) or set(draft) != TOP_FIELDS:
        raise ValueError('expected web_host, web_port, max_downloads, lakes and policies only')
    if not isinstance(draft['web_host'], str) or not draft['web_host'].strip():
        raise ValueError('web_host is required')
    for field, maximum in [('web_port', 65535), ('max_downloads', 64)]:
        if type(draft[field]) is not int or not 1 <= draft[field] <= maximum:
            raise ValueError(f'{field} must be an integer from 1 to {maximum}')
    if not isinstance(draft['lakes'], list) or not isinstance(draft['policies'], list):
        raise ValueError('lakes and policies must be lists')
    ids = set()
    for store in draft['lakes']:
        if not isinstance(store, dict) or set(store) - STORE_FIELDS:
            raise ValueError('unknown store field')
        identifier = store.get('lake_id')
        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', identifier) or identifier in ids:
            raise ValueError('store IDs must be nonempty and unique')
        ids.add(identifier)
        if store.get('kind') not in {'lake', 'warehouse'}:
            raise ValueError('kind must be lake or warehouse')
        plugin = store.get('plugin')
        if plugin not in {'files_lake', 'sql_lake', 'http_lake', 'default_lake'}:
            raise ValueError('unsupported store plugin in operator editor')
        store_metadata(store, adapter_kind=(None if plugin == 'http_lake' else 'warehouse' if plugin == 'sql_lake' else 'lake'))
        if plugin == 'http_lake':
            url = urlsplit(store.get('base_url', ''))
            if url.scheme not in {'http', 'https'} or not url.netloc or url.username or url.password:
                raise ValueError('base_url must be HTTP(S), without embedded credentials')
        elif plugin in {'files_lake', 'default_lake'} and not store.get('root_path'):
            raise ValueError('file store requires root_path')
        elif plugin == 'sql_lake' and not store.get('sqlite_path'):
            raise ValueError('in-process SQL adapter requires sqlite_path')
        _day(store.get('holdout_start'), 'holdout_start')
    for policy in draft['policies']:
        if not isinstance(policy, dict) or set(policy) - {'principal', 'lake', 'verbs', 'require_lineage', 'deny_from'}:
            raise ValueError('unknown policy field')
        if policy.get('lake') not in ids | {'*'}:
            raise ValueError('policy refers to an unknown store')
        if policy.get('principal') not in set(config.get('principals', {})) | {'*'}:
            raise ValueError('policy refers to an unknown principal')
        verbs = policy.get('verbs')
        if not isinstance(verbs, list) or not verbs or any(not isinstance(verb, str) or verb not in VERBS for verb in verbs):
            raise ValueError('invalid policy verbs')
        if 'require_lineage' in policy and type(policy['require_lineage']) is not bool:
            raise ValueError('require_lineage must be boolean')
        _day(policy.get('deny_from'), 'deny_from')
    result = copy.deepcopy(config)
    result.update(draft)
    # Preserve non-editable adapter settings (including credentials) server-side.
    originals = {store['lake_id']: store for store in config.get('lakes', [])}
    for store in result['lakes']:
        original = originals.get(store['lake_id'], {})
        for key, value in original.items():
            if key not in STORE_FIELDS:
                store[key] = copy.deepcopy(value)
    try:
        check_startup(result)
    except SystemExit as exc:
        raise ValueError(str(exc)) from exc
    return result


def destination(config):
    return Path(config.get('operator_config_path') or Path(__file__).resolve().parents[1] / 'var' / 'operator_config.json')


def persist_pending(config, draft):
    path = destination(config)
    content = json.dumps(draft, indent=2, allow_nan=False) + '\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.operator-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path
