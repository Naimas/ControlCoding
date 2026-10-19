"""Real Product CLI and serialized desktop transport; no provider or user project."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import cc_panel_bridge as bridge
import cc_development_lib as core
from test_cc_development_core import act
from test_cc_product_core import accepted

ROOT = Path(__file__).parents[1]


def selection(root):
    return {'kind': 'product_select', 'actor': 'Owner',
            'product_revision': core.read(root)['product']['accepted_revision'],
            'increment_id': 'I1', 'title': 'Accounts', 'features': ['signup'],
            'changes': [{'change_id': 'C1', 'title': 'Accounts', 'owner': 'Owner',
                         'risk': 'routine', 'feature_ids': ['signup']}],
            'criteria': [{'criterion_id': 'signup-success', 'owner_change_id': 'C1',
                          'contributor_change_ids': ['C1'], 'integration_check': None}],
            'dependencies': []}


def call(root, operation, **fields):
    message = {'version': 1, 'id': 'scope-1', 'operation': operation,
               'project_root': str(root), **fields}
    output = io.BytesIO()
    bridge.serve(io.BytesIO(json.dumps(message).encode()), output)
    return json.loads(output.getvalue())


def cli(root, *args):
    return subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/cc.py'),
                           'product', '--project-root', str(root), *args],
                          capture_output=True, text=True, timeout=30,
                          env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})


def test_serialized_preview_commit_read_and_exact_replay(tmp_path):
    accepted(tmp_path)
    act(tmp_path, {'kind': 'activate', 'profile': 'routine'})
    before = core.read(tmp_path)
    action = selection(tmp_path)
    response = call(tmp_path, 'development_preview_v1', action=action,
                    revision=before['revision'])
    assert response['status'] == 'ok'
    preview = response['result']['development_preview']
    assert not preview['blockers']
    assert core.read(tmp_path)['revision'] == before['revision']
    fields = {'action': action, 'revision': before['revision'],
              'approval_id': preview['approval_id']}
    committed = call(tmp_path, 'development_commit_v1', **fields)
    assert committed['status'] == 'ok'
    view = committed['result']['development']
    assert len(view['product']['evolution']['selections']) == 1
    assert view['changes'][0]['decisions'] == []
    replay = call(tmp_path, 'development_commit_v1', **fields)
    assert replay['result']['development_receipt']['idempotent']
    assert replay['result']['development']['revision'] == view['revision']
    read = call(tmp_path, 'development_read_v1')
    assert read['result']['development']['product']['evolution'] == view['product']['evolution']


def test_real_cli_selection_uses_same_review_boundary(tmp_path):
    accepted(tmp_path)
    act(tmp_path, {'kind': 'activate', 'profile': 'routine'})
    revision = core.read(tmp_path)['revision']
    request = tmp_path / 'selection.json'
    request.write_text(json.dumps(selection(tmp_path)), encoding='utf-8')
    result = cli(tmp_path, 'preview', '--action-file', str(request),
                 '--revision', revision, '--json')
    assert result.returncode == 0, result.stdout + result.stderr
    preview = json.loads(result.stdout)
    assert not preview['blockers']
    result = cli(tmp_path, 'apply', '--action-file', str(request),
                 '--revision', revision, '--approval-id', preview['approval_id'], '--json')
    assert result.returncode == 0, result.stdout + result.stderr
    result = cli(tmp_path, 'status', '--json')
    assert result.returncode == 0
    assert len(json.loads(result.stdout)['evolution']['selections']) == 1


@pytest.mark.parametrize('raw', [b'[]', b'null', b'{"kind":"product_select","kind":"product_scope"}',
                                b' ' * (512 * 1024 + 1)], ids=['array', 'null', 'duplicate', 'oversize'])
def test_cli_malformed_evolution_request_has_structured_error_and_no_writes(tmp_path, raw):
    request = tmp_path / 'request.json'
    request.write_bytes(raw)
    result = cli(tmp_path, 'preview', '--action-file', str(request),
                 '--revision', 'absent', '--json')
    assert result.returncode == 1
    assert json.loads(result.stdout)['ok'] is False
    assert not (tmp_path / '.controlcoding').exists()


def test_bridge_rejects_unreviewed_authority_extension(tmp_path):
    response = call(tmp_path, 'development_preview_v1', action={'kind': 'product_scope'},
                    revision='absent', approval=True)
    assert response['error']['code'] == 'invalid_message'
    assert not list(tmp_path.iterdir())
