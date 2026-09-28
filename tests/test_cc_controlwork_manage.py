"""Portable memory writes use disposable external fixture projects."""
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cc_controlwork_manage as manage
from cc_controlwork_observer import observe
from cc_panel_configuration import ConfigurationError
from cc_setup_service import SetupServiceError

ID = 'a' * 32
TIME = '2026-09-22T12:00:00Z'
INIT = {'action': 'init', 'name': 'Example', 'purpose': 'Reviewed project knowledge'}


def plan(root, value=INIT, sources=None, token=ID, time=TIME):
    return manage.prepare(str(root), value, sources or [], token, time)[0]


def apply(root, value=INIT, sources=None, token=ID, time=TIME):
    p = plan(root, value, sources, token, time)
    return manage.save(str(root), value, sources or [], token, time, p['approval_id'])


def inventory(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def read(root):
    scope = observe(str(root), preview=True)['work_scope']
    return observe(str(root), scope_id=scope['scope_id'])['work']


def test_preview_has_no_effects(tmp_path):
    p = plan(tmp_path)
    assert not p['blockers'] and all(f['action'] == 'create' for f in p['files'])
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(os.name != 'nt', reason='Windows writer')
def test_init_repeat_preserves_custom_context_and_records(tmp_path):
    (tmp_path / 'CONTROLWORK.md').write_text('# Custom context\nKeep me.')
    (tmp_path / 'PROJECT.md').write_text('Custom project base')
    result = apply(tmp_path)
    assert result['saved'] and not result['verified']
    assert (tmp_path / 'CONTROLWORK.md').read_text() == '# Custom context\nKeep me.'
    before = inventory(tmp_path)
    assert not apply(tmp_path, time='2026-09-23T12:00:00Z')['files']
    assert before == inventory(tmp_path)
    assert read(tmp_path)['state'] == 'present'
    assert not list(tmp_path.rglob('*.sqlite')) and not list(tmp_path.rglob('*.db'))
    assert not (tmp_path / '.controlwork/ingestion/file-index.json').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows writer')
def test_import_capture_session_are_real_core_records(tmp_path):
    apply(tmp_path)
    source = tmp_path / 'brief.md'
    source.write_text('# Product brief\nVisible source evidence.', encoding='utf-8')
    before = source.read_bytes()
    apply(tmp_path, {'action': 'import', 'area': 'sources'}, ['brief.md'])
    assert source.read_bytes() == before
    assert not apply(tmp_path, {'action': 'import', 'area': 'sources'}, ['brief.md'], time='2026-09-23T12:00:00Z')['files']
    apply(tmp_path, {'action': 'capture', 'area': 'decisions', 'title': 'Architecture', 'body': 'Keep separate responsibilities.'})
    apply(tmp_path, {'action': 'session', 'title': 'Design review', 'body': 'Reviewed architecture.', 'decisions': ['Keep interfaces explicit'], 'followups': ['Test the interface']})
    w = read(tmp_path)
    assert len(w['documents']) == 3 and len(w['sessions']) == 1
    assert any(d['source'] == 'brief.md' and d['lifecycle'] == 'captured' for d in w['documents'])
    assert w['sessions'][0]['status'] == 'completed' and w['sessions'][0]['followups'] == ['Test the interface']
    assert any(n['type'] == 'session' for n in w['graph']['nodes'])


@pytest.mark.skipif(os.name != 'nt', reason='Windows writer')
def test_changed_source_and_record_prevent_write(tmp_path):
    apply(tmp_path)
    source = tmp_path / 'brief.md'; source.write_text('Original')
    value = {'action': 'import', 'area': 'sources'}
    p = plan(tmp_path, value, ['brief.md'])
    source.write_text('Changed')
    before = inventory(tmp_path)
    with pytest.raises(ConfigurationError, match='preview_mismatch'):
        manage.save(str(tmp_path), value, ['brief.md'], ID, TIME, p['approval_id'])
    assert inventory(tmp_path) == before
    result = apply(tmp_path, value, ['brief.md'])
    target = tmp_path / result['files'][0]; target.write_text('Foreign edit')
    with pytest.raises(ConfigurationError, match='record_conflict'):
        plan(tmp_path, value, ['brief.md'])
    assert target.read_text() == 'Foreign edit'


@pytest.mark.parametrize('value', [
    {'action': 'capture', 'area': '../escape', 'title': 'T', 'body': 'B'},
    {'action': 'init', 'name': '', 'purpose': 'P'},
    {'action': 'init', 'name': 'N', 'purpose': 'P', 'path': 'escape'},
    {'action': 'session', 'title': 'T', 'body': 'B', 'decisions': [1], 'followups': []},
    {'action': 'capture', 'area': 'notes', 'title': 'T', 'body': 'x' * 8001},
])
def test_bad_requests_rejected(tmp_path, value):
    with pytest.raises(ConfigurationError):
        plan(tmp_path, value)
    assert not list(tmp_path.iterdir())


def test_capture_requires_explicit_initialization(tmp_path):
    with pytest.raises(ConfigurationError, match='memory_not_initialized'):
        plan(tmp_path, {'action': 'capture', 'area': 'notes', 'title': 'T', 'body': 'B'})
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('name', ['../outside.md', '.env', 'secret.json', 'C:/outside.txt'])
def test_source_scope_is_confined(tmp_path, name):
    with pytest.raises(ConfigurationError):
        plan(tmp_path, {'action': 'import', 'area': 'sources'}, [name])


@pytest.mark.skipif(os.name != 'nt', reason='Windows writer')
def test_oversized_and_hardlinked_sources_rejected(tmp_path):
    apply(tmp_path)
    source = tmp_path / 'brief.md'; source.write_bytes(b'x' * 32769)
    with pytest.raises(ConfigurationError, match='document_limit'):
        plan(tmp_path, {'action': 'import', 'area': 'sources'}, ['brief.md'])
    source.write_text('Private')
    os.link(source, tmp_path / 'link.md')
    with pytest.raises(SetupServiceError, match='unsupported_path'):
        plan(tmp_path, {'action': 'import', 'area': 'sources'}, ['link.md'])


def test_corrupt_config_and_recovery_journal_preserved(tmp_path):
    (tmp_path / '.controlwork').mkdir()
    (tmp_path / '.controlwork/config.json').write_text('{bad')
    before = inventory(tmp_path)
    with pytest.raises(ConfigurationError, match='invalid_json'):
        plan(tmp_path)
    assert inventory(tmp_path) == before
    (tmp_path / '.controlcoding').mkdir()
    (tmp_path / '.controlcoding/panel-setup-transaction.json').write_text('{}')
    with pytest.raises(ConfigurationError, match='recovery_required'):
        plan(tmp_path)


def test_bridge_keeps_correlated_errors(tmp_path):
    from cc_panel_bridge import dispatch
    message = {'version': 1, 'id': 'test', 'operation': 'work_manage_preview_v1', 'project_root': str(tmp_path),
               'value': INIT, 'source_paths': [], 'request_id': ID, 'timestamp': TIME}
    result = dispatch(message)
    assert result['id'] == 'test' and result['status'] == 'ok'
    result = dispatch({**message, 'value': {}})
    assert result['id'] == 'test' and result['error']['code'] == 'invalid_memory_request'


@pytest.mark.skipif(os.name != 'nt', reason='Windows writer')
def test_visible_record_budget_includes_uppercase_and_review_history(tmp_path):
    apply(tmp_path)
    notes = tmp_path / '.controlwork/memory/notes'
    for number in range(94):
        (notes / f'{number}.MD').write_text('# Existing note', encoding='utf-8')
    (tmp_path / '.controlwork/graph-suggestions.json').write_text('{}', encoding='utf-8')
    before = inventory(tmp_path)
    with pytest.raises(ConfigurationError, match='memory_limit'):
        plan(tmp_path, {'action': 'capture', 'area': 'notes', 'title': 'Extra', 'body': 'Content'})
    assert inventory(tmp_path) == before
