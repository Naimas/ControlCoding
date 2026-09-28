import pytest
import json
from test_cc_knowledge import project
from cc_memory_lib import knowledge_service as service
from cc_memory_lib.knowledge_store import KnowledgeError


def view(root):
    return service.dispatch(root, 'work-view', None)


def propose(root, source, target, kind='part_of'):
    return service.dispatch(root, 'work-propose', dict(snapshot=view(root)['snapshot'], source=source,
        target=target, source_role='activity', target_role='objective', kind=kind, reason='Explicit owner rationale'))


def review(root, row, status='approved', snapshot=None):
    return service.dispatch(root, 'work-review', dict(snapshot=snapshot or view(root)['snapshot'],
        id=row['id'], status=status, reason='Reviewed against recorded sources'))


def test_review_is_persistent_revision_bound_and_cycles_are_rejected(project):
    a, b = [s['id'] for s in service.catalog(project)['sources']]
    state = propose(project, a, b)
    assert not state['relations'][0]['effective']
    row = review(project, state['relations'][0])['relations'][0]
    assert row['effective'] and len(row['history']) == 1
    graph = service.dispatch(project, 'graph-view', dict(topic=None, query='', focus=None, offset=0, snapshot=None))
    assert any(e['kind'] == 'human_reviewed:part_of' for e in graph['edges'])
    assert any(e['kind'] == 'human_reviewed:part_of' for e in service.query(project, 'passphrase', False)['edges'])
    reverse = propose(project, b, a)['relations'][1]
    with pytest.raises(KnowledgeError, match='work_dependency_cycle'):
        review(project, reverse)
    (project/'README.md').write_text('# Changed\nnew evidence', encoding='utf-8')
    service.reconcile(project)
    assert all(r['stale'] and not r['effective'] for r in view(project)['relations'])
    with pytest.raises(KnowledgeError, match='work_evidence_changed'):
        review(project, row)
    rebound = review(project, row, 'proposed')['relations'][0]
    assert not rebound['stale'] and not rebound['effective']
    assert rebound['history'][-1]['source_revision'] == row['source_revision']
    assert review(project, rebound)['relations'][0]['effective']


def test_conflict_resolution_and_concurrent_review(project):
    a, b = [s['id'] for s in service.catalog(project)['sources']]
    state = propose(project, a, b, 'conflicts')
    row = state['relations'][0]
    review(project, row)
    with pytest.raises(KnowledgeError, match='work_snapshot_changed'):
        review(project, row, snapshot=state['snapshot'])
    state = review(project, row, 'resolved')
    assert state['relations'][0]['status'] == 'resolved'
    assert not state['relations'][0]['effective']
    assert len(state['relations'][0]['history']) == 2


def test_feature_projection_reuses_canonical_owner_without_editing_registry(project):
    path = project/'.controlcoding/features/features.json'
    path.parent.mkdir(parents=True)
    registry = {'schemaVersion': 'cc-feature-state/v1', 'wipLimit': 1, 'features': [
        {'id': 'login', 'title': 'Login feature', 'state': 'blocked', 'acceptanceCriteria': ['A signed credential is required.']}]}
    path.write_text(json.dumps(registry), encoding='utf-8')
    before = path.read_bytes()
    service.configure(project, {**service.DEFAULT, 'embedding': '', 'scopes': ['project', 'dev-memory']})
    service.reconcile(project)
    state = view(project)
    feature = next(n for n in state['canonical'] if n['record_type'] == 'feature')
    criterion = next(n for n in state['canonical'] if n['record_type'] == 'criterion')
    assert feature['state'] == 'blocked' and feature['owner'] == 'Not recorded'
    assert criterion['state'] == 'unknown'
    assert any(e['source'] == feature['id'] and e['target'] == criterion['id'] and e['kind'] == 'canonical_work:contains_work' for e in state['recorded_edges'])
    assert path.read_bytes() == before
    assert 'cc-project-map-controls/v1' in service.page(project, feature['id'])['body']
    assert 'A signed credential is required.' in service.page(project, criterion['id'])['body']


def test_review_overlay_survives_backup_but_restored_evidence_is_stale(project, tmp_path):
    from cc_memory_lib.knowledge_backup import backup, restore
    a, b = [s['id'] for s in service.catalog(project)['sources']]
    row = propose(project, a, b)['relations'][0]
    reviewed = review(project, row)['relations'][0]
    archive = tmp_path/'reviewed.ccmemory'
    backup(project, archive)
    target = tmp_path/'restored-reviewed'
    target.mkdir()
    restore(target, archive)
    restored = view(target)['relations'][0]
    assert restored['id'] == reviewed['id'] and restored['history'] == reviewed['history']
    assert restored['status'] == 'approved' and restored['stale'] and not restored['effective']
