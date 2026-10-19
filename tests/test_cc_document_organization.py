import pytest
from test_cc_knowledge import project
from cc_memory_lib import knowledge_service as service
from cc_memory_lib.knowledge_store import KnowledgeError
from cc_memory_lib.knowledge_organization import blank, SECTIONS


def view(root, **kw):
    return service.dispatch(root, 'organization-view', dict(query='', section='', area='', offset=0) | kw)


def request(row, **fields):
    return dict(source=row['id'], revision=row['revision'], expected=row['organization']['token'],
                fields=blank() | fields, reason='Reviewed against the original document.')


def test_optional_organization_does_not_change_original_or_classification(project):
    before = (project / 'docs/design.md').read_bytes()
    original = service.catalog(project)
    row = next(r for r in view(project)['rows'] if r['path'] == 'docs/design.md')
    result = service.dispatch(project, 'organization-save', request(row, section='areas', areas=['Security', 'Backend']))
    assert result['organization']['confirmed']
    assert view(project, area='Security')['total'] == 1
    assert view(project, section='areas')['total'] == 1
    assert len(SECTIONS) == 10
    assert (project / 'docs/design.md').read_bytes() == before
    assert [(r['id'], r['revision']) for r in service.catalog(project)['sources']] == [(r['id'], r['revision']) for r in original['sources']]
    assert service.query(project, 'passphrase', False, retrieval={'paths': ['docs/design.md'], 'graph': True})['citations']


def test_source_drift_invalidates_without_manual_refresh(project):
    row = view(project)['rows'][0]
    service.dispatch(project, 'organization-save', request(row, section='direction'))
    current = view(project)['rows'][0]
    (project / current['path']).write_text('# Changed source\nNew version.', encoding='utf-8')
    with pytest.raises(KnowledgeError, match='document_organization_changed'):
        service.dispatch(project, 'organization-save', request(current))
    changed = view(project)['rows'][0]
    assert changed['organization']['stale'] and not changed['organization']['confirmed']
    assert view(project, section='direction')['total'] == 0
    assert view(project, section='unassigned')['total'] == 2


def test_related_drift_and_concurrent_review(project):
    (project / 'README.md').write_text('# Entry point', encoding='utf-8')
    a, b = view(project)['rows']
    service.dispatch(project, 'organization-save', request(a, related=[b['id']]))
    from cc_memory_lib.knowledge_store import database
    from cc_memory_lib.knowledge_retrieval import expand, options
    with database(project) as db:
        additions, trace = expand(db, project, '', [a['id']], options({'wiki': False}, ''))
        assert b['id'] in trace['paths']
        assert any(e['kind'] == 'human_reviewed:document_related' for e in trace['edges'])
        _, filtered = expand(db, project, '', [a['id']], options({'wiki': False, 'paths': [a['path']]}, ''))
        assert b['id'] not in filtered['paths']
    with pytest.raises(KnowledgeError, match='document_organization_changed'):
        service.dispatch(project, 'organization-save', request(a))
    (project / b['path']).write_text('# Changed linked source', encoding='utf-8')
    assert view(project)['rows'][0]['organization']['stale']
    with database(project) as db:
        from cc_memory_lib.knowledge_organization import related_edges
        assert related_edges(db, a['id']) == []


@pytest.mark.parametrize('fields', [{'areas': ['a'] * 13}, {'section': 'fake'}, {'status': 'complete'}, {'related': ['missing']}, {'phase': 'x' * 121}])
def test_invalid_save_keeps_original_state(project, fields):
    row = view(project)['rows'][0]
    with pytest.raises(KnowledgeError):
        service.dispatch(project, 'organization-save', request(row, **fields))
    assert not view(project)['rows'][0]['organization']['confirmed']


def test_reset_and_bounded_read(project):
    row = view(project)['rows'][0]
    service.dispatch(project, 'organization-save', request(row, section='direction'))
    row = view(project)['rows'][0]
    saved = service.dispatch(project, 'organization-save', request(row) | {'fields': None})
    assert not saved['organization']['confirmed']
    row = view(project)['rows'][0]
    packet = service.dispatch(project, 'organization-prepare', {k: request(row)[k] for k in ('source', 'revision', 'expected')})
    assert packet['text'] and len(packet['text']) <= 24000
    for i in range(30):
        (project / 'docs' / f'area-{i}.md').write_text('# Area', encoding='utf-8')
    first = view(project)
    second = view(project, offset=25)
    assert len(first['rows']) == 25 and len(second['rows']) == 7
    assert not {r['id'] for r in first['rows']} & {r['id'] for r in second['rows']}


def test_local_ai_input_discloses_truncation(project):
    (project / 'docs/design.md').write_text('# Long design\n' + 'Document content.\n' * 2000, encoding='utf-8')
    row = next(r for r in view(project)['rows'] if r['path'] == 'docs/design.md')
    packet = service.dispatch(project, 'organization-prepare', {k: request(row)[k] for k in ('source', 'revision', 'expected')})
    assert packet['truncated'] and len(packet['text']) == 24000


def test_forgetting_conversation_removes_organization_and_linked_notes(project):
    from test_cc_knowledge import record
    from cc_memory_lib.knowledge_store import database, get
    from cc_memory_lib.knowledge_organization import PREFIX
    service.conversation(project, record())
    rows = view(project)['rows']
    chat = next(r for r in rows if r['kind'] == 'conversation')
    doc = next(r for r in rows if r['path'] == 'README.md')
    service.dispatch(project, 'organization-save', request(chat, section='history'))
    service.dispatch(project, 'organization-save', request(doc, related=[chat['id']]))
    service.dispatch(project, 'forget', 'chat-1')
    with database(project) as db:
        assert get(db, PREFIX + chat['id']) is None
        assert get(db, PREFIX + doc['id']) is None
