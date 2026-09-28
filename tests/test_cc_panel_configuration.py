"""Reviewed setup writes are exercised only against external pytest fixtures."""
import copy
import json
import os
from pathlib import Path
import sys
import subprocess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cc_panel_configuration as config
import cc_panel_transaction as transaction
from cc_setup_service import SetupServiceError


def preview(root, draft=None, operation='save'):
    current = config.read_draft(str(root))
    draft = draft or current['draft']
    result, _ = config.prepare(str(root), draft, current['revision'], operation)
    return result


def apply(root, plan):
    return config.save_or_apply(str(root), plan['draft'], plan['revision'], plan['operation'], plan['approval_id'])


def files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_read_and_preview_are_pure(tmp_path):
    initial = config.read_draft(str(tmp_path))
    assert initial['revision'] == 'absent' and not initial['saved']
    p = preview(tmp_path, operation='apply')
    assert not p['blockers'] and p['configured_not_verified']
    assert any(f['path'] == 'CONTROLCODING.md' for f in p['files'])
    assert not files(tmp_path)


@pytest.mark.skipif(os.name != 'nt', reason='Windows writer')
def test_save_reopen_stale_and_noop(tmp_path):
    d = config.default_draft(tmp_path)
    d['name'] = 'Résumé project'
    plan = preview(tmp_path, d)
    result = apply(tmp_path, plan)
    assert result['saved'] and result['files'] == [config.DRAFT]
    assert config.read_draft(str(tmp_path))['draft'] == d
    assert set(files(tmp_path)) == {config.DRAFT}
    with pytest.raises(config.ConfigurationError, match='draft_conflict'):
        apply(tmp_path, plan)
    assert apply(tmp_path, preview(tmp_path))['files'] == []


@pytest.mark.skipif(os.name != 'nt', reason='Windows writer')
@pytest.mark.parametrize('host', config.HOSTS)
def test_fresh_core_install_and_repeat(tmp_path, host):
    (tmp_path / '.git/hooks').mkdir(parents=True)
    d = config.default_draft(tmp_path)
    d['user_host'] = host
    d['decisions']['boundaries'].update(mode='manual', status='accepted', value='src/core | stable | deny\nsrc/ui | features | none')
    p = preview(tmp_path, d, 'apply')
    assert not p['blockers']
    result = apply(tmp_path, p)
    assert result['saved'] and not (tmp_path / transaction.JOURNAL).exists()
    assert (tmp_path / 'devlog').is_dir()
    assert (tmp_path / '.git/hooks/post-commit').is_file()
    settings = json.loads((tmp_path / '.controlcoding/cc_config.json').read_text())
    assert settings['memory_default_policy'] == 'deferred'
    assert settings['protected_zones'][0]['path'] == 'src/core/'
    assert not (tmp_path / '.controlwork').exists()
    again = preview(tmp_path, operation='apply')
    assert not again['blockers']
    assert not apply(tmp_path, again)['files']


def test_foreign_context_and_unknown_config_preserved(tmp_path):
    (tmp_path / 'CONTROLCODING.md').write_text('FOREIGN CONTEXT', encoding='utf-8')
    (tmp_path / '.controlcoding').mkdir()
    (tmp_path / '.controlcoding/cc_config.json').write_text(json.dumps({'custom': {'stay': True}, 'hooks_location': 'local'}))
    before = files(tmp_path)
    p = preview(tmp_path, operation='apply')
    assert p['blockers'] and any(f['path'] == 'CONTROLCODING.md' and f['action'] == 'conflict' for f in p['files'])
    with pytest.raises(config.ConfigurationError, match='plan_conflict'):
        apply(tmp_path, p)
    assert files(tmp_path) == before


@pytest.mark.skipif(os.name != 'nt', reason='Windows writer')
def test_preview_binding_detects_new_file(tmp_path):
    p = preview(tmp_path, operation='apply')
    (tmp_path / 'STATUS.md').write_text('external change')
    with pytest.raises(config.ConfigurationError, match='preview_mismatch'):
        apply(tmp_path, p)
    assert files(tmp_path) == {'STATUS.md': b'external change'}


@pytest.mark.skipif(os.name != 'nt', reason='Windows writer')
def test_failure_rolls_back_owned_writes(tmp_path, monkeypatch):
    apply(tmp_path, preview(tmp_path))
    before = files(tmp_path)
    d = config.read_draft(str(tmp_path))['draft']
    d['goal'] = 'new goal'
    p = preview(tmp_path, d, 'apply')
    original = transaction._write
    calls = 0
    def fail_once(fd, data):
        nonlocal calls
        calls += 1
        if calls == 5:
            os.write(fd, b'partial')
            raise OSError('simulated disk failure')
        return original(fd, data)
    monkeypatch.setattr(transaction, '_write', fail_once)
    with pytest.raises(transaction.PanelWriteError, match='write_conflict'):
        apply(tmp_path, p)
    assert files(tmp_path) == before


def test_journal_blocks_subsequent_operations(tmp_path):
    (tmp_path / '.controlcoding').mkdir()
    (tmp_path / transaction.JOURNAL).write_text('{"state":"prepared"}')
    with pytest.raises(config.ConfigurationError, match='recovery_required'):
        config.read_draft(str(tmp_path))


def test_hardlink_draft_refused(tmp_path):
    source = tmp_path / 'source.json'
    source.write_bytes(config.encoded(config.default_draft(tmp_path)))
    (tmp_path / '.controlcoding').mkdir()
    os.link(source, tmp_path / config.DRAFT)
    with pytest.raises(SetupServiceError, match='unsupported_path'):
        config.read_draft(str(tmp_path))


@pytest.mark.parametrize('path', ['../outside', 'A:/other', 'src\\file', '.env', 'CON', 'src/../file', 'src:alt'])
def test_unsafe_design_paths_rejected(tmp_path, path):
    d = config.default_draft(tmp_path)
    d['design_paths'] = [path]
    with pytest.raises(config.ConfigurationError):
        preview(tmp_path, d)


def test_ai_proposals_bound_to_sources_and_require_review(tmp_path):
    (tmp_path / 'README.md').write_text('# Example\nA Python tool.\n')
    d = config.default_draft(tmp_path)
    d['decisions']['stack']['mode'] = 'ai'
    d['design_paths'] = ['README.md']
    packet = config.analysis_packet(str(tmp_path), d)
    proposal = {'schema_version': 1, 'request_id': packet['request_id'], 'suggestions': [
        {'field': 'stack', 'value': 'Python', 'rationale': 'The brief describes Python.', 'evidence': ['README.md']}]}
    imported = config.import_proposals(str(tmp_path), d, proposal)
    assert imported['decisions']['stack']['status'] == 'proposed'
    assert preview(tmp_path, imported, 'apply')['blockers']
    imported['decisions']['stack']['status'] = 'accepted'
    assert not preview(tmp_path, imported, 'apply')['blockers']
    (tmp_path / 'README.md').write_text('# Changed\nA Rust tool.\n')
    with pytest.raises(config.ConfigurationError, match='stale_analysis'):
        config.import_proposals(str(tmp_path), d, proposal)
    assert set(files(tmp_path)) == {'README.md'}


def test_ai_import_cannot_modify_direct_choices(tmp_path):
    d = config.default_draft(tmp_path)
    packet = config.analysis_packet(str(tmp_path), d)
    proposal = {'schema_version': 1, 'request_id': packet['request_id'], 'suggestions': [
        {'field': 'stack', 'value': 'Python', 'rationale': 'Reason', 'evidence': ['README.md']}]}
    with pytest.raises(config.ConfigurationError, match='invalid_proposals'):
        config.import_proposals(str(tmp_path), d, proposal)


@pytest.mark.skipif(os.name != 'nt', reason='Windows writer')
def test_owned_context_update_and_external_edit_conflict(tmp_path):
    apply(tmp_path, preview(tmp_path, operation='apply'))
    d = config.read_draft(str(tmp_path))['draft']
    d['goal'] = 'A revised purpose'
    p = preview(tmp_path, d, 'apply')
    assert not p['blockers']
    assert any(f['path'] == 'CONTROLCODING.md' and f['action'] == 'update' for f in p['files'])
    apply(tmp_path, p)
    assert 'A revised purpose' in (tmp_path / 'CONTROLCODING.md').read_text(encoding='utf-8')
    (tmp_path / 'CONTROLCODING.md').write_text('External context edit')
    before = files(tmp_path)
    assert preview(tmp_path, operation='apply')['blockers']
    assert files(tmp_path) == before


@pytest.mark.skipif(os.name != 'nt', reason='Windows writer')
def test_config_unknown_keys_and_existing_protections_survive(tmp_path):
    (tmp_path / '.controlcoding').mkdir()
    original = {'hooks_location': 'local', 'custom': {'retain': 123}, 'protected_zones': [{'path': 'old/', 'description': 'Keep', 'level': 'deny'}]}
    (tmp_path / '.controlcoding/cc_config.json').write_text(json.dumps(original))
    d = config.default_draft(tmp_path)
    d['documentation_mode'] = 'project_managed'
    apply(tmp_path, preview(tmp_path, d, 'apply'))
    actual = json.loads((tmp_path / '.controlcoding/cc_config.json').read_text())
    assert actual['custom'] == original['custom'] and actual['protected_zones'] == original['protected_zones']
    assert actual['documentation_mode'] == 'project_managed'
    assert not preview(tmp_path, operation='apply')['blockers']


@pytest.mark.skipif(os.name != 'nt', reason='Windows writer')
def test_process_interruption_leaves_recovery_journal(tmp_path):
    script = '''import os,sys
sys.path.insert(0,sys.argv[1])
import cc_panel_configuration as c, cc_panel_transaction as t
root=sys.argv[2];d=c.read_draft(root);p,_=c.prepare(root,d['draft'],d['revision'],'apply')
original=t._write
count=0
def interrupted(fd,data):
 global count
 count+=1
 if count==2: os._exit(77)
 original(fd,data)
t._write=interrupted
c.save_or_apply(root,d['draft'],d['revision'],'apply',p['approval_id'])
'''
    result = subprocess.run([sys.executable, '-I', '-B', '-c', script, str(Path(config.__file__).parent), str(tmp_path)], timeout=20, capture_output=True)
    assert result.returncode == 77, result.stderr
    journal = json.loads((tmp_path / transaction.JOURNAL).read_text(encoding='utf-8'))
    assert journal['state'] == 'prepared' and journal['files']
    with pytest.raises(config.ConfigurationError, match='recovery_required'):
        config.read_draft(str(tmp_path))


def test_bridge_configuration_envelope_and_errors(tmp_path):
    from cc_panel_bridge import dispatch
    request = {'version': 1, 'id': 'test', 'operation': 'config_read_v1', 'project_root': str(tmp_path)}
    result = dispatch(request)
    assert result['status'] == 'ok' and result['result']['configuration']['revision'] == 'absent'
    assert dispatch({**request, 'arbitrary_path': 'escape'})['status'] == 'error'
    result = dispatch({**request, 'operation': 'config_preview_v1', 'draft': {}, 'revision': 'absent', 'intent': 'save'})
    assert result['error']['code'] == 'invalid_draft'


@pytest.mark.skipif(os.name != 'nt', reason='Windows writer')
@pytest.mark.parametrize('location', ['.claude', '.controlcoding'])
@pytest.mark.parametrize('zones', [['shared/', {'path': 'private/', 'level': 'deny', 'custom': 'retain'}],
                                  {'deny': ['private/'], 'warn': ['shared/'], 'custom': {'retain': True}}])
def test_legacy_zone_shapes_preserved(tmp_path, location, zones):
    (tmp_path / location).mkdir()
    (tmp_path / location / 'cc_config.json').write_text(json.dumps({'protected_zones': zones, 'custom': 'retain'}))
    d = config.default_draft(tmp_path)
    d['decisions']['boundaries'].update(mode='manual', status='accepted', value='new/area | stable | deny')
    apply(tmp_path, preview(tmp_path, d, 'apply'))
    actual = json.loads((tmp_path / '.controlcoding/cc_config.json').read_text())
    assert actual['custom'] == 'retain'
    if isinstance(zones, dict):
        assert actual['protected_zones']['deny'][:-1] == zones['deny']
        assert actual['protected_zones']['warn'] == zones['warn'] and actual['protected_zones']['custom'] == zones['custom']
    else:
        assert actual['protected_zones'][:-1] == zones
