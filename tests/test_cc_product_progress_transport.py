"""Real typed CLI/private transport parity and strict progress wire boundaries."""
import copy
import argparse
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import cc_panel_bridge as bridge
import cc_product_progress as progress
from test_cc_product_core import saved
from cc_product_progress_contract import decode_cursor, encode_cursor

ROOT = Path(__file__).parents[1]


def query(**fields):
    return {'view': 'overview', 'entity_id': None, 'collection': None,
            'cursor': None, 'observation_id': None, **fields}


def message(root, value=None):
    return {'version': 1, 'id': 'progress-1', 'operation': 'product_progress_v1',
            'project_root': str(root), 'query': query() if value is None else value}


def call(root, value=None, raw=None):
    output = io.BytesIO()
    bridge.serve(io.BytesIO(raw if raw is not None else json.dumps(message(root, value)).encode()), output)
    return json.loads(output.getvalue()), output.getvalue()


def cli(root, *args):
    return subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/cc.py'), 'product', 'progress',
                           '--project-root', str(root), '--json', *args], capture_output=True,
                          text=True, encoding='utf-8', timeout=60,
                          env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1'})


def facts(response):
    return {key: value for key, value in response.items() if key not in ('observation', 'pagination')}


def test_a10_core_cli_and_actual_isolated_private_transport_share_facts_without_memory(tmp_path):
    saved(tmp_path)
    expected = progress.read(tmp_path)
    result = cli(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    public = json.loads(result.stdout)
    assert facts(public) == facts(expected)
    assert public['observation']['observation_id'] == expected['observation']['observation_id']
    assert len(result.stdout.rstrip('\r\n').encode('utf-8')) <= progress.MAX_RESPONSE_BYTES
    assert result.stdout.rstrip('\r\n') == json.dumps(public, ensure_ascii=False, separators=(',', ':'))
    native = subprocess.run([sys.executable, '-I', '-B', str(ROOT / 'scripts/cc_panel_bridge.py')],
        input=json.dumps(message(tmp_path)), capture_output=True, text=True, encoding='utf-8', timeout=60,
        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1'})
    assert native.returncode == 0, native.stderr
    response = json.loads(native.stdout)
    assert response['status'] == 'ok', response
    assert set(response['result']) == {'project_root', 'progress'}
    assert facts(response['result']['progress']) == facts(expected)
    assert not (tmp_path / '.controlwork').exists()
    assert not (tmp_path / '.controlcoding/memory.db').exists()


@pytest.mark.parametrize('value', [
    query(extra=True), query(view='unknown'), query(view=True), query(entity_id='signup'),
    query(view='feature'), query(view='feature', entity_id='../bad'), query(collection='tasks'),
    query(cursor='e30'), query(observation_id='A' * 64), query(cursor=True, observation_id='a' * 64),
    query(view='change', entity_id='C1', collection='features'),
    query(view='history', entity_id=1), query(collection=False), query(observation_id=False),
])
def test_a10_invalid_queries_fail_before_any_projection_owner_read(tmp_path, monkeypatch, value):
    monkeypatch.setattr(progress, 'read', lambda *a, **kw: pytest.fail('Invalid selector reached owner read'))
    response, _ = call(tmp_path, value)
    assert response['status'] == 'error'
    assert response['error']['code'] in ('progress_invalid_selector', 'progress_invalid_cursor')
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('fault', ['query_duplicate', 'outer_duplicate', 'extra_field', 'missing_query'])
def test_a17_duplicate_and_extended_messages_never_reach_owner(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(progress, 'read', lambda *a, **kw: pytest.fail('Malformed message reached owner'))
    request = message(tmp_path)
    if fault == 'extra_field': request['approval_id'] = 'a' * 64
    if fault == 'missing_query': del request['query']
    raw = json.dumps(request).encode()
    if fault == 'query_duplicate': raw = raw.replace(b'"view": "overview"', b'"view":"overview","view":"feature"')
    if fault == 'outer_duplicate': raw = raw.replace(b'"operation": "product_progress_v1"', b'"operation":"product_progress_v1","operation":"product_progress_v1"')
    response, _ = call(tmp_path, raw=raw)
    assert response['status'] == 'error' and response['error']['code'] == 'invalid_message'
    assert list(tmp_path.iterdir()) == []


def test_a12_progress_keeps_64k_request_and_global_response_ceiling(tmp_path, monkeypatch):
    assert bridge.MAX_REQUEST == 65536 and bridge.MAX_RESPONSE == 1048576
    assert 'product_progress_v1' not in bridge.PRODUCT_FIELDS
    monkeypatch.setattr(progress, 'read', lambda *a, **kw: pytest.fail('Oversize request reached owner'))
    raw = json.dumps(message(tmp_path)).encode()
    response, _ = call(tmp_path, raw=raw + b' ' * (bridge.MAX_REQUEST + 1 - len(raw)))
    assert response['error']['code'] == 'message_limit'
    assert list(tmp_path.iterdir()) == []


def test_a12_utf8_is_not_ascii_expanded_and_response_validator_is_applied(tmp_path, monkeypatch):
    saved(tmp_path, __import__('test_cc_product_core').document() | {'title': 'Progetto città 日本語'})
    expected = progress.read(tmp_path)
    response, raw = call(tmp_path)
    assert response['status'] == 'ok', response
    assert 'città 日本語'.encode() in raw
    assert len(json.dumps(response['result']['progress'], ensure_ascii=False, separators=(',', ':')).encode()) <= progress.MAX_RESPONSE_BYTES
    malformed = copy.deepcopy(expected)
    malformed['unexpected_authority'] = True
    monkeypatch.setattr(progress, 'read', lambda *a, **kw: malformed)
    rejected, _ = call(tmp_path)
    assert rejected['status'] == 'error'


def test_a12_exact_512k_unicode_projection_remains_bounded_in_cli_and_bridge(tmp_path, monkeypatch, capsys):
    from cc_product_cli import dispatch_product_command
    saved(tmp_path)
    value = progress.read(tmp_path)
    row = value['rows'][0]
    row['data']['description'] = '日本語é'
    size = lambda: len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    row['data']['description'] += 'x' * (progress.MAX_RESPONSE_BYTES - size())
    assert size() == progress.MAX_RESPONSE_BYTES
    progress.validate_response(value)
    monkeypatch.setattr(progress, 'read', lambda *a, **kw: value)
    args = argparse.Namespace(product_command='progress', json_output=True, **query())
    assert dispatch_product_command(args, tmp_path, None) == 0
    output = capsys.readouterr().out
    assert len(output.rstrip('\r\n').encode('utf-8')) == progress.MAX_RESPONSE_BYTES
    response, raw = call(tmp_path)
    assert response['status'] == 'ok' and len(raw) <= bridge.MAX_RESPONSE
    assert len(json.dumps(response['result']['progress'], ensure_ascii=False, separators=(',', ':')).encode('utf-8')) == progress.MAX_RESPONSE_BYTES
    row['data']['description'] += 'x'
    assert dispatch_product_command(args, tmp_path, None) == 1
    assert json.loads(capsys.readouterr().out)['ok'] is False
    response, _ = call(tmp_path)
    assert response['status'] == 'error'


def cursor_value(**fields):
    return {'schema_version': 1, 'project_identity': 'bounded-project', 'method_digest': 'a' * 64,
            'control_revision': None, 'observation_id': 'b' * 64, 'input_digest': 'c' * 64,
            'view': 'overview', 'entity_id': None, 'collection': 'features',
            'order': 'declaration_then_id_v1', 'offset': 32, 'expires_at': 2000000000, **fields}


@pytest.mark.parametrize('fields', [{'schema_version': True}, {'offset': True}, {'offset': -1},
                                    {'offset': 0.5}, {'expires_at': False}, {'expires_at': -1},
                                    {'extra': 'authority'}, {'method_digest': 'A' * 64}])
def test_a10_cursor_rejects_invalid_numeric_and_extra_fields(fields):
    raw = json.dumps(cursor_value(**fields), separators=(',', ':')).encode()
    token = base64.urlsafe_b64encode(raw).decode().rstrip('=')
    with pytest.raises(ValueError) as failure: decode_cursor(token)
    assert failure.value.code == 'progress_invalid_cursor'


def test_a10_cursor_requires_canonical_encoding_and_unique_keys():
    canonical = encode_cursor(cursor_value())
    assert decode_cursor(canonical) == cursor_value()
    duplicate = json.dumps(cursor_value(), separators=(',', ':')).replace('"offset":32', '"offset":32,"offset":0')
    for token in (canonical + '=', 'é', 'x' * 2049,
                  base64.urlsafe_b64encode(duplicate.encode()).decode().rstrip('=')):
        with pytest.raises(ValueError) as failure: decode_cursor(token)
        assert failure.value.code == 'progress_invalid_cursor'


def test_a10_cross_view_starts_new_observation_and_same_view_token_requires_current_inputs(tmp_path):
    saved(tmp_path)
    first = progress.read(tmp_path)
    detail = progress.read(tmp_path, view='feature', entity_id='signup')
    assert detail['observation']['observation_id'] != first['observation']['observation_id']
    with pytest.raises(ValueError) as error:
        progress.read(tmp_path, view='feature', entity_id='signup',
                      observation_id=first['observation']['observation_id'])
    assert error.value.code == 'progress_observation_changed'
    saved(tmp_path, __import__('test_cc_product_core').document() | {'title': 'Changed scope title'})
    with pytest.raises(ValueError) as error:
        progress.read(tmp_path, observation_id=first['observation']['observation_id'])
    assert error.value.code == 'progress_observation_changed'


@pytest.mark.parametrize('args', [('--view', 'feature'), ('--view', 'overview', '--entity-id', 'signup'),
                                  ('--collection', 'events'), ('--cursor', 'bad')])
def test_public_cli_invalid_selector_is_structured_and_read_only(tmp_path, args):
    result = cli(tmp_path, *args)
    assert result.returncode == 1
    assert json.loads(result.stdout)['error']['code'] in ('progress_invalid_selector', 'progress_invalid_cursor')
    assert list(tmp_path.iterdir()) == []


def test_a10_actual_dependency_leaf_crosses_public_cli_and_isolated_private_transport(tmp_path):
    from cc_product_progress_fixture import fixture_seed, assert_transport_rows
    fixture_seed(tmp_path)
    rows = assert_transport_rows(tmp_path, [('increment', 'I2', 'dependencies', 'dependency')])
    assert rows[0]['rows'] == 1
    edge = progress.read(tmp_path, view='increment', entity_id='I2', collection='dependencies')['rows'][0]
    assert edge['summary'] is None and edge['data']['required_result'] == 'released'
    assert edge['data']['state'] == 'blocked' and edge['data']['precision'] == 'edge'
