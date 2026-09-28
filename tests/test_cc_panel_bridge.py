"""Private one-shot desktop transport: bounded requests and no apply route."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import cc_panel_bridge as bridge


def call(request):
    output = io.BytesIO()
    bridge.serve(io.BytesIO(json.dumps(request).encode()), output)
    return json.loads(output.getvalue())


def message(root, **extra):
    return {'version': 1, 'id': 'test-1', 'operation': 'read', 'project_root': str(root), **extra}


@pytest.mark.parametrize('extra', [dict(operation='apply'), dict(operation='setup'), dict(operation='provider'),
                                  dict(version=True), dict(version=2), dict(id='../CANARY'), dict(unknown='CANARY')])
def test_unsupported_envelope(tmp_path, extra):
    result = call(message(tmp_path, **extra))
    assert result['error']['code'] == 'invalid_message'
    assert 'CANARY' not in json.dumps(result)


@pytest.mark.parametrize('data,code', [(b'{CANARY', 'invalid_message'), (b'\xff', 'invalid_message'),
                                      (b' ' * (bridge.MAX_REQUEST + 1), 'message_limit')],
                         ids=['malformed', 'invalid-utf8', 'oversized'])
def test_bounded_raw_message(data, code):
    output = io.BytesIO()
    bridge.serve(io.BytesIO(data), output)
    assert json.loads(output.getvalue())['error']['code'] == code
    assert b'CANARY' not in output.getvalue()


@pytest.mark.parametrize('operation', ['read', 'preview'])
def test_isolated_real_process_preserves_project(tmp_path, operation):
    marker = tmp_path / 'unrelated'
    marker.write_bytes(b'CANARY')
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    process = subprocess.run([sys.executable, '-I', '-B', str(Path(bridge.__file__).absolute())],
                             input=json.dumps(message(tmp_path, operation=operation)).encode(),
                             capture_output=True, cwd=tmp_path, timeout=20,
                             env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    assert process.returncode == 0 and process.stderr == b''
    result = json.loads(process.stdout)
    assert result['id'] == 'test-1' and result['status'] == 'ok'
    assert result['operation'] == operation and result['observed_at']
    assert b'CANARY' not in process.stdout
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_invalid_project_and_secrets(tmp_path):
    directory = tmp_path / '.controlcoding'
    directory.mkdir()
    (directory / 'cc_config.json').write_bytes(b'{"CANARY": invalid}')
    result = call(message(tmp_path))
    assert result['status'] == 'error'
    assert result['error']['code'] == 'invalid_json'
    assert 'CANARY' not in json.dumps(result)


def test_helper_exception_is_not_exposed(tmp_path, monkeypatch):
    import cc_setup_service
    def fail(*args, **kwargs):
        raise RuntimeError('CANARY-private-exception')
    monkeypatch.setattr(cc_setup_service, 'read_setup_state', fail)
    result = call(message(tmp_path))
    assert result['error']['code'] == 'helper_failure'
    assert 'CANARY' not in json.dumps(result)


def map_message(root, operation='map_preview_v1', **extra):
    return message(root, operation=operation, design_paths=[], observed_at='2026-09-21T12:00:00Z', **extra)


def test_analysis_isolated_transport_has_no_source_bodies_or_runtime_json_fields(tmp_path):
    (tmp_path / 'a.py').write_text('SECRET="CANARY"\ndef run(): return 1\n')
    preview = call(map_message(tmp_path))['result']['scope']['preview_id']
    observed = call(map_message(tmp_path, 'map_read_v1', preview_id=preview))['result']['map']
    options = dict(preview_id=preview, snapshot=observed['review']['source_snapshot'], revision=observed['review']['revision'], controls_scope_id=None)
    scope = call(map_message(tmp_path, 'map_analysis_preview_v1', **options))
    assert scope['status'] == 'ok'
    request = map_message(tmp_path, 'map_analysis_read_v1', **options, scope_id=scope['result']['analysis_scope']['scope_id'])
    process = subprocess.run([sys.executable, '-I', '-B', str(Path(bridge.__file__).absolute())], input=json.dumps(request).encode(), capture_output=True, timeout=20)
    result = json.loads(process.stdout)
    assert process.returncode == 0 and not process.stderr and result['status'] == 'ok'
    assert result['result']['map']['analysis']['files'][0]['state'] == 'parsed'
    assert b'CANARY' not in process.stdout
    for field in ('node', 'worker', 'reports', 'command'):
        assert call({**request, field: 'CANARY'})['status'] == 'error'
    assert list(tmp_path.iterdir()) == [tmp_path / 'a.py']


def test_controls_real_isolated_transport_and_no_write(tmp_path):
    (tmp_path / 'main.py').write_text('SECRET="CANARY"\n')
    preview = call(map_message(tmp_path))['result']['scope']['preview_id']
    observed = call(map_message(tmp_path, 'map_read_v1', preview_id=preview))['result']['map']
    options = dict(preview_id=preview, snapshot=observed['review']['source_snapshot'], revision=observed['review']['revision'])
    scope = call(map_message(tmp_path, 'map_controls_preview_v1', **options))
    assert scope['status'] == 'ok'
    request = map_message(tmp_path, 'map_controls_read_v1', **options, scope_id=scope['result']['controls_scope']['scope_id'])
    process = subprocess.run([sys.executable, '-I', '-B', str(Path(bridge.__file__).absolute())],
                             input=json.dumps(request).encode(), capture_output=True, cwd=tmp_path, timeout=20)
    result = json.loads(process.stdout)
    assert process.returncode == 0 and not process.stderr and result['status'] == 'ok'
    assert result['result']['map']['controls']['host_coverage'] == 'unknown'
    assert b'CANARY' not in process.stdout and list(tmp_path.iterdir()) == [tmp_path / 'main.py']
    for field in ('command', 'token', 'apply', 'change'):
        rejected = call({**request, field: 'CANARY'})
        assert rejected['status'] == 'error' and 'CANARY' not in json.dumps(rejected)


@pytest.mark.parametrize('field', ['snapshot', 'revision', 'scope_id'])
def test_controls_reject_invalid_binding_fields(tmp_path, field):
    request = map_message(tmp_path, 'map_controls_read_v1', preview_id='a'*64,
                          snapshot='snapshot:'+'b'*64, revision='absent', scope_id='c'*64)
    request[field] = True
    assert call(request)['status'] == 'error'


def test_map_preview_and_read_real_transport(tmp_path):
    (tmp_path / 'main.py').write_text('SECRET="CANARY"\ndef run(): return SECRET\n', encoding='utf-8')
    before = (tmp_path / 'main.py').read_bytes()
    preview = call(map_message(tmp_path))
    assert preview['status'] == 'ok'
    request = map_message(tmp_path, 'map_read_v1', preview_id=preview['result']['scope']['preview_id'])
    process = subprocess.run([sys.executable, '-I', '-B', str(Path(bridge.__file__).absolute())],
                             input=json.dumps(request).encode(), capture_output=True, cwd=tmp_path, timeout=20)
    assert process.returncode == 0 and not process.stderr
    result = json.loads(process.stdout)
    assert result['status'] == 'ok' and result['id'] == request['id']
    assert result['result']['map']['projection']['summary']['verified_units'] == 0
    assert result['result']['map']['mode'] == 'observation'
    assert b'CANARY' not in process.stdout and (tmp_path / 'main.py').read_bytes() == before


@pytest.mark.parametrize('change', [dict(preview_id=None),dict(preview_id='a'*64),dict(preview_id=True)])
def test_map_requires_matching_preview(tmp_path, change):
    result = call(map_message(tmp_path, 'map_read_v1', **change))
    assert result['status'] == 'error' and result['error']['code'] == 'preview_mismatch'
    assert result['operation'] == 'map_read_v1'


@pytest.mark.parametrize('change', [dict(command='CANARY'),dict(design_paths='../secret'),dict(observed_at='invalid'),dict(operation='map_read_v2')])
def test_map_invalid_scope_fields(tmp_path, change):
    request = map_message(tmp_path)
    request.update(change)
    result = call(request)
    assert result['status'] == 'error' and 'CANARY' not in json.dumps(result)


def test_map_scope_limit_preserves_correlated_safe_error(tmp_path):
    (tmp_path/'large.py').write_bytes(b'x' * (1024*1024+1))
    preview=call(map_message(tmp_path))
    result=call(map_message(tmp_path,'map_read_v1',preview_id=preview['result']['scope']['preview_id']))
    assert result['id']=='test-1' and result['operation']=='map_read_v1'
    assert result['error']['code']=='scope_limit' and 'result' not in result


def test_map_parser_limit_returns_file_overview_through_isolated_bridge(tmp_path):
    (tmp_path/'large.py').write_text('value="PRIVATE_CANARY"\n' * 6000, encoding='utf-8')
    (tmp_path/'small.py').write_text('def visible(): pass\n', encoding='utf-8')
    preview = call(map_message(tmp_path))
    request = map_message(tmp_path, 'map_read_v1', preview_id=preview['result']['scope']['preview_id'])
    process = subprocess.run([sys.executable, '-I', '-B', str(Path(bridge.__file__).absolute())],
                             input=json.dumps(request).encode(), capture_output=True, cwd=tmp_path, timeout=20)
    assert process.returncode == 0 and not process.stderr
    result = json.loads(process.stdout)
    assert result['status'] == 'ok' and result['id'] == request['id']
    observed = result['result']['map']
    assert observed['projection']['summary']['units'] == 2
    assert observed['projection']['summary']['verified_units'] == 0
    assert {'code':'parse_limit','source':'large.py'} in observed['findings']
    assert b'PRIVATE_CANARY' not in process.stdout and not (tmp_path/'.controlcoding').exists()


def test_review_transport_requires_preview_and_persists_only_definition(tmp_path):
    import os
    if os.name != 'nt':
        pytest.skip('Windows mapping persistence')
    (tmp_path / 'main.py').write_text('def run(): return 1\n')
    scope = call(map_message(tmp_path))['result']['scope']['preview_id']
    observed = call(map_message(tmp_path, 'map_read_v1', preview_id=scope))['result']['map']
    target = next(n['id'] for n in observed['projection']['bundle']['nodes'] if n['kind'] == 'file')
    args = dict(preview_id=scope, snapshot=observed['review']['source_snapshot'], revision='absent', change={'operation': 'rename', 'target': target, 'title': 'API entry'})
    prepared = call(map_message(tmp_path, 'map_review_preview_v1', **args))
    assert prepared['status'] == 'ok' and not (tmp_path / '.controlcoding').exists()
    denied = call(map_message(tmp_path, 'map_review_apply_v1', **args, approval_id='0'*64))
    assert denied['error']['code'] == 'preview_mismatch' and not (tmp_path / '.controlcoding').exists()
    applied = call(map_message(tmp_path, 'map_review_apply_v1', **args, approval_id=prepared['result']['review_preview']['approval_id']))
    assert applied['status'] == 'ok' and applied['result']['review_saved']['saved']
    assert (tmp_path / '.controlcoding/project-map/definition.json').is_file()
    assert (tmp_path / 'main.py').read_text() == 'def run(): return 1\n'


@pytest.mark.parametrize('extra', [{'command': 'CANARY'}, {'approval_id': '0'*64}, {'definition': {}}])
def test_review_rejects_extra_wire_fields(tmp_path, extra):
    request = map_message(tmp_path, 'map_review_preview_v1', preview_id='a'*64, snapshot='snapshot:'+'b'*64,
                          revision='absent', change={'operation': 'accept', 'target': 'node:a'})
    request.update(extra)
    result = call(request)
    assert result['status'] == 'error' and 'CANARY' not in json.dumps(result)
