"""Optional topic tags must remain reviewed, version-bound, and reversible."""
import pytest

from test_cc_knowledge import project
from cc_memory_lib import knowledge_service as service
from cc_memory_lib.knowledge_store import KnowledgeError, database, get
from cc_memory_lib.knowledge_tags import ASSIGN


def view(root, **options):
    return service.dispatch(root, 'tag-view', dict(query='', section='', area='', document_type='',
                                                   tags=[], tag_mode='any', offset=0) | options)


def save(root, row, names, reason='Reviewed source and topic assignment.', origin='human', proposal=None):
    return service.dispatch(root, 'tag-save', dict(source=row['id'], revision=row['revision'],
        expected=row['tags']['token'], registry=view(root)['registry'], names=names, reason=reason, origin=origin,
        **({'proposal': proposal} if origin == 'accepted_ai' else {})))


def test_tag_is_optional_original_untouched_and_library_filter(project):
    original = (project / 'docs/design.md').read_bytes()
    row = next(r for r in view(project)['rows'] if r['path'] == 'docs/design.md')
    assert save(project, row, ['Authentication', 'Security'])['tags']['confirmed']
    assert (project / 'docs/design.md').read_bytes() == original
    catalog = view(project)
    ids = {t['name']: t['id'] for t in catalog['tags']}
    assert view(project, tags=[ids['Security']])['total'] == 1
    library = service.dispatch(project, 'library', dict(kind='sources', query='', offset=0, snapshot=None,
                                                        tags=[ids['Security']], tag_mode='all'))
    assert library['total'] == 1 and library['rows'][0]['id'] == row['id']
    assert library['rows'][0]['document_type']['tags']['names'] == ['Authentication', 'Security']
    from cc_memory_lib.knowledge_organization import blank
    organized = next(r for r in service.dispatch(project, 'organization-view',
        dict(query='', section='', area='', offset=0))['rows'] if r['id'] == row['id'])
    service.dispatch(project, 'organization-save', dict(source=row['id'], revision=row['revision'],
        expected=organized['organization']['token'], fields=blank() | {'section': 'areas', 'areas': ['Backend']},
        reason='Reviewed the backend design source.'))
    combined = service.dispatch(project, 'library', dict(kind='sources', query='design', offset=0, snapshot=None,
        tags=[ids['Security']], tag_mode='all', section='areas', area='Backend',
        document_type=library['rows'][0]['document_type']['key']))
    assert combined['total'] == 1
    assert service.dispatch(project, 'library', dict(kind='sources', query='design', offset=0, snapshot=None,
        tags=[ids['Security']], tag_mode='all', section='areas', area='Frontend'))['total'] == 0
    assert service.query(project, 'passphrase', False, retrieval={'tags': [ids['Security']]})['citations']
    assert not service.query(project, 'passphrase', False, retrieval={'tags': [ids['Authentication']], 'paths': ['README.md']})['citations']


def test_source_change_invalidates_tag_and_cursor(project):
    row = next(r for r in view(project)['rows'] if r['path'] == 'docs/design.md')
    save(project, row, ['Security'])
    tag = view(project)['tags'][0]['id']
    page = service.dispatch(project, 'library', dict(kind='sources', query='', offset=0, snapshot=None))
    (project / 'docs/design.md').write_text('# Changed\nnew material', encoding='utf-8')
    with pytest.raises(KnowledgeError, match='tag_assignment_changed'):
        save(project, row, ['Security'])
    changed = next(r for r in view(project)['rows'] if r['id'] == row['id'])
    assert changed['tags']['stale'] and not changed['tags']['confirmed']
    assert view(project, tags=[tag])['total'] == 0
    with pytest.raises(KnowledgeError, match='library_snapshot_changed'):
        service.dispatch(project, 'library', dict(kind='sources', query='', offset=0, snapshot=page['snapshot']))


def test_ai_proposal_requires_exact_evidence_and_human_acceptance(project):
    row = next(r for r in view(project)['rows'] if r['path'] == 'docs/design.md')
    request = dict(source=row['id'], revision=row['revision'], expected=row['tags']['token'], registry=view(project)['registry'])
    packet = service.dispatch(project, 'tag-prepare', request)
    quote = 'People enter with a secret passphrase.'
    with pytest.raises(KnowledgeError, match='invalid_tag_proposal'):
        service.dispatch(project, 'tag-proposal-save', dict(source=row['id'], revision=row['revision'],
            registry=packet['registry'], model='local-test', items=[dict(name='Security', quote='unsupported fact', reason='Describes authentication.')], truncated=False))
    proposed = service.dispatch(project, 'tag-proposal-save', dict(source=row['id'], revision=row['revision'],
        registry=packet['registry'], model='local-test', items=[dict(name='Security', quote=quote, reason='Describes authentication.'),
            dict(name='Login', quote='secret passphrase', reason='Describes credential entry.')], truncated=False))
    assert all(p['status'] == 'pending' for p in proposed['proposals'])
    assert view(project)['tags'] == []
    with pytest.raises(KnowledgeError, match='tag_proposal_changed'):
        save(project, row, ['Security'], origin='accepted_ai', proposal='not-a-proposal')
    accepted = save(project, row, ['Security'], origin='accepted_ai', proposal=proposed['proposals'][0]['id'])
    assert accepted['tags']['confirmed']
    assert [p['status'] for p in accepted['tags']['proposals']] == ['accepted', 'pending']


def test_rename_merge_remove_preview_and_one_step_undo(project):
    rows = view(project)['rows']
    save(project, rows[0], ['Security', 'Login'])
    rows = view(project)['rows']
    save(project, rows[1], ['Login'])
    tags = {t['name']: t['id'] for t in view(project)['tags']}
    command = dict(operation='merge', source=tags['Login'], target=tags['Security'], name='')
    preview = service.dispatch(project, 'tag-manage-preview', command)
    assert preview['count'] == 2 and len(preview['examples']) == 2
    result = service.dispatch(project, 'tag-manage-apply', command | dict(registry=preview['registry'],
        review=preview['review'], reason='One reviewed topic replaces the duplicate.'))
    assert result['affected'] == 2 and len(view(project)['tags']) == 1
    assert view(project, tags=[tags['Security']])['total'] == 2
    assert service.dispatch(project, 'tag-undo', {'receipt': result['receipt']})['undone']
    assert len(view(project)['tags']) == 2


def test_tag_name_collision_and_privacy_erasure(project):
    from test_cc_knowledge import record
    service.conversation(project, record())
    row = next(r for r in view(project)['rows'] if r['kind'] == 'conversation')
    save(project, row, ['Context'])
    with pytest.raises(KnowledgeError, match='duplicate_tag_name'):
        save(project, next(r for r in view(project)['rows'] if r['id'] == row['id']), ['Context', 'context'])
    service.dispatch(project, 'forget', 'chat-1')
    with database(project) as db:
        assert get(db, ASSIGN + row['id']) is None
    assert not view(project)['tags']


def test_many_documents_pagination_any_all_and_graph_hint(project):
    for number in range(65):
        (project / 'docs' / f'topic-{number:03}.md').write_text(f'# Topic {number}\nShared domain material.', encoding='utf-8')
    first = view(project)
    assert first['total'] == 67 and len(first['rows']) == 25
    second = view(project, offset=25)
    assert len(second['rows']) == 25 and not {r['id'] for r in first['rows']} & {r['id'] for r in second['rows']}
    source_rows = [next(r for r in view(project, offset=offset)['rows'] if r['path'] == f'docs/topic-{number:03}.md')
                   for number, offset in [(0, 0), (50, 50)]]
    save(project, source_rows[0], ['Shared', 'Design'])
    source_rows[1] = next(r for r in view(project, offset=50)['rows'] if r['id'] == source_rows[1]['id'])
    save(project, source_rows[1], ['Shared'])
    tags = {t['name']: t['id'] for t in view(project)['tags']}
    assert view(project, tags=[tags['Shared']])['total'] == 2
    assert view(project, tags=list(tags.values()), tag_mode='any')['total'] == 2
    assert view(project, tags=list(tags.values()), tag_mode='all')['total'] == 1
    from cc_memory_lib.knowledge_retrieval import expand, options
    with database(project) as db:
        _, trace = expand(db, project, 'topic', [source_rows[0]['id']], options({'graph': True, 'wiki': False}, 'topic'))
    assert source_rows[1]['id'] in trace['paths']
    assert any(edge['kind'] == 'topic_tag_hint' for edge in trace['edges'])
    with database(project) as db:
        _, scoped = expand(db, project, 'topic', [source_rows[0]['id']], options({'graph': True, 'wiki': False,
            'paths': [source_rows[0]['path']]}, 'topic'))
    assert source_rows[1]['id'] not in scoped['paths']


def test_backup_preserves_tags_and_pending_proposals(project, tmp_path):
    from cc_memory_lib.knowledge_backup import backup, restore
    from cc_memory_lib.knowledge_tags import REGISTRY, PROPOSAL
    row = next(r for r in view(project)['rows'] if r['path'] == 'docs/design.md')
    save(project, row, ['Authentication'])
    row = next(r for r in view(project)['rows'] if r['id'] == row['id'])
    service.dispatch(project, 'tag-proposal-save', dict(source=row['id'], revision=row['revision'],
        registry=view(project)['registry'], model='local-test', truncated=False,
        items=[dict(name='Security', quote='People enter with a secret passphrase.', reason='This describes authentication.')]))
    archive = tmp_path / 'tags.ccmemory'
    backup(project, archive)
    target = tmp_path / 'restored'
    target.mkdir()
    restore(target, archive)
    with database(target) as db:
        assert get(db, REGISTRY)['tags']
        assert get(db, ASSIGN + row['id'])['ids']
        assert get(db, PROPOSAL + row['id'])[0]['status'] == 'pending'


def test_tag_management_includes_deleted_source_assignments(project):
    row = next(r for r in view(project)['rows'] if r['path'] == 'docs/design.md')
    save(project, row, ['Legacy'])
    tag_id = view(project)['tags'][0]['id']
    (project / 'docs/design.md').unlink()
    service.reconcile(project)
    command = dict(operation='remove', source=tag_id, target='', name='')
    preview = service.dispatch(project, 'tag-manage-preview', command)
    assert preview['count'] == 1 and not preview['examples'][0]['current']
    result = service.dispatch(project, 'tag-manage-apply', command | dict(registry=preview['registry'],
        review=preview['review'], reason='Remove an obsolete topic from the registry.'))
    assert result['affected'] == 1
    assert service.dispatch(project, 'tag-undo', {'receipt': result['receipt']})['undone']


def test_public_cli_accepts_tag_view(project):
    import json
    from pathlib import Path
    import subprocess
    import sys
    program = Path(__file__).resolve().parents[1] / 'scripts' / 'cc_knowledge.py'
    request = dict(query='', section='', area='', document_type='', tags=[], tag_mode='any', offset=0)
    run = subprocess.run([sys.executable, str(program), '--project-root', str(project), 'tag-view'],
                         input=json.dumps(request), text=True, capture_output=True, check=False)
    assert run.returncode == 0
    assert json.loads(run.stdout)['total'] == 2


def test_tagged_graph_window_reaches_source_beyond_initial_200(project):
    for number in range(210):
        (project / 'docs' / f'late-{number:03}.md').write_text(f'# Late source {number}\nArchive text.', encoding='utf-8')
    late = next(r for r in view(project, offset=200)['rows'] if r['path'] == 'docs/late-209.md')
    save(project, late, ['Later topic'])
    tag_id = view(project)['tags'][0]['id']
    ordinary = service.dispatch(project, 'graph-view',
        dict(topic=None, query='', focus=None, offset=0, snapshot=None))
    assert ordinary['graph']['total'] == 212 and late['id'] not in {r['id'] for r in ordinary['sources']}
    focused = service.dispatch(project, 'graph-view',
        dict(topic=None, query='', focus=None, offset=0, snapshot=None, tag=tag_id))
    assert focused['graph']['total'] == 1 and focused['graph']['tag'] == tag_id
    assert [r['id'] for r in focused['sources']] == [late['id']]
