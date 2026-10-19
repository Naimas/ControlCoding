"""Actual bounded CLI and private bridge planning transport; no provider route."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import cc_panel_bridge as bridge
import cc_planning

ROOT = Path(__file__).parents[1]


def call(root, payload, **extra):
    message = {'version': 1, 'id': 'planning-1', 'operation': 'planning_v1',
               'project_root': str(root), 'payload': payload, **extra}
    output = io.BytesIO()
    bridge.serve(io.BytesIO(json.dumps(message).encode()), output)
    return json.loads(output.getvalue())


def create_payload():
    return {'operation': 'planning_create', 'session_id': 'S1', 'operation_id': 'a' * 64,
            'actor': 'Author', 'task_id': 'T1'}


def cli(root, *args):
    return subprocess.run([sys.executable, '-B', str(ROOT / 'scripts' / 'cc.py'), 'planning',
                           '--project-root', str(root), *args], capture_output=True, text=True,
                          env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}, timeout=30)


def test_bridge_create_read_and_replay_preserve_disabled_control(tmp_path):
    from cc_development_lib import read
    saved = call(tmp_path, create_payload())
    assert saved['status'] == 'ok'
    view = saved['result']['planning']
    assert view['session']['id'] == 'S1'
    first = read(tmp_path)
    replay = call(tmp_path, create_payload())
    assert replay['result']['planning']['revision'] == view['revision']
    assert read(tmp_path)['revision'] == first['revision']
    assert not first['enabled'] and not first['changes']
    response = call(tmp_path, {'operation': 'planning_read', 'session_id': 'S1'})
    assert response['result']['planning']['revision'] == view['revision']


@pytest.mark.parametrize('payload', [None, [], {}, {'operation': 'provider'},
    {'operation': 'planning_read', 'session_id': 'S1', 'approval': True},
    {'operation': 'planning_read', 'session_id': '../CANARY'}])
def test_bridge_strict_inner_protocol_never_exposes_request_material(tmp_path, payload):
    result = call(tmp_path, payload)
    assert result['status'] == 'error'
    assert 'CANARY' not in json.dumps(result)
    assert not list(tmp_path.iterdir())


def test_bridge_rejects_outer_extra_authority_and_bounded_wire(tmp_path):
    assert call(tmp_path, create_payload(), approval_id='a' * 64)['error']['code'] == 'invalid_message'
    output = io.BytesIO()
    bridge.serve(io.BytesIO(b' ' * (bridge.MAX_PRODUCT_REQUEST + 1)), output)
    assert json.loads(output.getvalue())['error']['code'] == 'message_limit'
    assert not list(tmp_path.iterdir())


def test_public_planning_command_routes_and_required_gate_cover_the_actual_adapters():
    import cc
    for name in ('read', 'create', 'append', 'preview', 'prepare', 'reserve', 'finish', 'review'):
        assert cc._truth_command_route_exists('planning ' + name)
        assert cc._truth_extract_route_from_tokens(['python', 'scripts/cc.py', 'planning', name, '--json']) == ('planning', name)
    contract = json.loads((ROOT / 'controlcoding.verification.json').read_text())
    gate = next(s for s in contract['suites'] if s['id'] == 'planning-discussion-regression')
    assert gate['required']
    assert all('tests/' + name in gate['command'] for name in ('test_cc_planning.py',
        'test_cc_planning_upgrade.py', 'test_cc_planning_transport.py', 'test_cc_planning_quality.py'))


def test_real_cli_create_read_list_and_no_provider(tmp_path):
    request = tmp_path / 'request.json'
    payload = create_payload()
    del payload['operation']
    request.write_text(json.dumps(payload), encoding='utf-8')
    result = cli(tmp_path, 'create', '--request-file', str(request), '--json')
    assert result.returncode == 0, result.stdout + result.stderr
    session = json.loads(result.stdout)
    assert session['session']['id'] == 'S1'
    resumed = cli(tmp_path, 'read', '--session-id', 'S1', '--json')
    assert resumed.returncode == 0
    assert json.loads(resumed.stdout)['revision'] == session['revision']
    listed = cli(tmp_path, 'read', '--json')
    assert listed.returncode == 0 and json.loads(listed.stdout)['sessions'][0]['id'] == 'S1'


@pytest.mark.parametrize('raw', [b'{"actor":"a","actor":"b"}', b'[]',
                               b'{"operation":"planning_finish"}', b' ' * (1024 * 1024 + 1)],
                         ids=['duplicate', 'array', 'operation-mismatch', 'oversize'])
def test_cli_rejects_duplicate_mismatch_and_oversized_requests(tmp_path, raw):
    request = tmp_path / 'request.json'
    request.write_bytes(raw)
    result = cli(tmp_path, 'create', '--request-file', str(request), '--json')
    assert result.returncode == 1
    assert json.loads(result.stdout)['ok'] is False
    assert not (tmp_path / '.controlcoding').exists()
