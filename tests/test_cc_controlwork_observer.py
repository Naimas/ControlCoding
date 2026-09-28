"""ControlWork desktop reads use real Core records without changing the adopter."""
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cc_controlwork_observer import WorkObserverError, observe
from cc_memory_lib import work_features as work
from cc_panel_bridge import dispatch


def write(root, name, value):
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value) if isinstance(value, dict) else value, encoding='utf-8')
    return p


def read(root, query=''):
    scope = observe(str(root), preview=True)['work_scope']
    return observe(str(root), scope_id=scope['scope_id'], query=query)['work']


def identity(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def memory(tmp_path):
    write(tmp_path, 'CONTROLWORK.md', '# Example project\nRecorded project knowledge.\n')
    for name, lifecycle in [('Architecture', 'active'), ('OldArchitecture', 'legacy')]:
        write(tmp_path, f'.controlwork/memory/decisions/{name}.md', f'# {name}\n\n- **Area**: decisions\n- **Lifecycle**: {lifecycle}\n- **Category**: architecture\n\n## Body\nArchitecture interface decision.\n')
    write(tmp_path, '.controlwork/sessions/session-one.json', {
        'schemaVersion': 1, 'id': 'session-one', 'topic': 'Architecture review', 'summary': 'Reviewed module ownership.',
        'status': 'completed', 'updatedAt': '2026-09-22T00:00:00Z',
        'decisions': ['Keep one authoritative store'], 'followups': ['Verify the interface'],
        'links': [{'type': 'references', 'target': '.controlwork/memory/decisions/Architecture.md', 'targetType': 'decision'}]})
    return tmp_path


def test_absence_does_not_initialize(tmp_path):
    assert read(tmp_path)['state'] == 'absent'
    assert list(tmp_path.iterdir()) == []


def test_real_core_entries_graph_query_and_packet(memory):
    before = identity(memory)
    result = read(memory, 'Architecture')
    assert identity(memory) == before
    assert len(result['documents']) == 3 and len(result['sessions']) == 1
    assert result['sessions'][0]['followups'] == ['Verify the interface']
    assert any(e['confidence'] == 'explicit_link' for e in result['graph']['edges'])
    assert result['packet']['citations']
    assert all(c['lifecycle'] != 'legacy' for c in result['packet']['citations'])
    assert 'GraphRAG Packet' in result['packet']['packetMarkdown']
    assert result['graph'] == work.portable_graph(memory)
    cli = work.retrieve_matches(memory, 'Architecture')
    assert result['packet']['citations'][0]['id'] == cli['matches'][0]['id']


def test_core_pure_extracts_preserve_wrappers(memory, monkeypatch):
    path = memory / '.controlwork/memory/decisions/Architecture.md'
    assert work.entry_from_text(memory, path, path.read_text()) == work.entry_from_path(memory, path)
    entries = work.portable_graph_entries(memory)
    assert work.graph_from_entries(entries, work.read_suggestion_state(memory)) == work.portable_graph(memory)
    monkeypatch.setattr(work, 'utc_iso', lambda: 'fixed')
    retrieval = work.retrieve_matches(memory, 'Architecture')
    assert work.rag_pack_from_retrieval(retrieval, 'Architecture') == work.build_rag_pack_payload(memory, 'Architecture')


def test_rejected_suggestions_not_used_for_ui_retrieval(memory):
    graph = work.portable_graph(memory)
    suggestion = graph['suggestions'][0]
    write(memory, '.controlwork/graph-suggestions.json', {'schemaVersion': 1, 'suggestions': {suggestion['id']: {'status': 'rejected', 'reason': 'Unrelated meaning', 'original': suggestion}}})
    result = read(memory, 'Architecture')
    assert any(s['status'] == 'rejected' for s in result['graph']['suggestions'])
    assert all(e.get('status') != 'rejected' for c in result['packet']['citations'] for e in c['edgesUsed'])


def test_external_reference_and_unselected_private_files_not_read(memory):
    write(memory, '.env', 'PRIVATE_CANARY')
    write(memory, '.controlwork/secret.txt', 'PRIVATE_CANARY')
    write(memory, '.controlwork/memory/notes/reference.md', '# Reference\n- **Source**: ../../outside/private.txt\n## Body\nA reference only.')
    result = read(memory)
    assert 'PRIVATE_CANARY' not in json.dumps(result)
    assert any(d['source'] == '../../outside/private.txt' for d in result['documents'])


@pytest.mark.parametrize('value', ['{broken', '{"schemaVersion":1,"schemaVersion":1}', '{"schemaVersion":2,"id":"a"}', '{"schemaVersion":1,"id":"a","links":[42]}'])
def test_malformed_session_fails_without_body_leak(tmp_path, value):
    write(tmp_path, '.controlwork/sessions/bad.json', value)
    with pytest.raises(WorkObserverError, match='invalid_memory'):
        read(tmp_path)


def test_scope_is_bound_to_selected_root(tmp_path):
    other = tmp_path / 'other'; other.mkdir()
    scope = observe(str(tmp_path), preview=True)['work_scope']
    with pytest.raises(WorkObserverError, match='scope_mismatch'):
        observe(str(other), scope_id=scope['scope_id'])


@pytest.mark.parametrize('query', ['x'*241, '\x00', 12])
def test_bad_query_rejected(tmp_path, query):
    with pytest.raises(WorkObserverError, match='invalid_query'):
        read(tmp_path, query)


def test_large_record_remains_fatal(tmp_path):
    write(tmp_path, 'CONTROLWORK.md', 'x'*65537)
    with pytest.raises(WorkObserverError, match='memory_limit'):
        read(tmp_path)


def test_record_count_remains_bounded(tmp_path):
    for i in range(97):
        write(tmp_path, f'.controlwork/memory/notes/{i}.md', '# Note\n## Body\nNote')
    with pytest.raises(WorkObserverError, match='memory_limit'):
        read(tmp_path)


def test_symlink_never_followed(tmp_path):
    outside = write(tmp_path, 'private.txt', 'PRIVATE_CANARY')
    link = tmp_path / 'CONTROLWORK.md'
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip('Symlink privilege unavailable')
    with pytest.raises(WorkObserverError, match='unsupported_path'):
        read(tmp_path)


def test_hardlink_never_followed(tmp_path):
    outside = write(tmp_path, 'private.txt', 'PRIVATE_CANARY')
    os.link(outside, tmp_path / 'CONTROLWORK.md')
    with pytest.raises(WorkObserverError, match='unsupported_path'):
        read(tmp_path)


def test_source_change_during_read_is_rejected(memory, monkeypatch):
    from cc_setup_service import _Snapshots
    original = _Snapshots.recheck
    def changed(self, *args):
        # Directory membership must be rechecked even when the OS permits new files.
        if memory / '.controlwork/memory/decisions/Architecture.md' in self.entries:
            write(memory, '.controlwork/memory/decisions/Added.md', '# Added')
        return original(self, *args)
    monkeypatch.setattr(_Snapshots, 'recheck', changed)
    with pytest.raises(WorkObserverError, match='changed_input'):
        read_scope = observe(str(memory), preview=True)['work_scope']
        observe(str(memory), scope_id=read_scope['scope_id'])


def test_bridge_rejects_arbitrary_fields_and_exposes_safe_errors(tmp_path):
    base = {'version': 1, 'id': 'test', 'operation': 'work_preview_v1', 'project_root': str(tmp_path)}
    assert dispatch({**base, 'path': '/private'})['status'] == 'error'
    scope = dispatch(base)['result']['work_scope']
    response = dispatch({**base, 'operation': 'work_read_v1', 'scope_id': scope['scope_id'], 'query': ''})
    assert response['result']['work']['state'] == 'absent'
