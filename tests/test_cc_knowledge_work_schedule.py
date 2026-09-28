"""Schedule projection uses observed source revisions and reviewed relations."""
import json

import pytest

from test_cc_knowledge import project  # noqa: F401 - shared isolated project fixture
from cc_memory_lib import knowledge_work_schedule as schedule
from cc_memory_lib import knowledge_service as service
from cc_memory_lib.knowledge_store import database, put, KnowledgeError


def seed(root, names, edges=(), states=None, owners=None, relations=()):
    states, owners = states or {}, owners or {}
    with database(root) as db:
        with db:
            for key in names:
                body = '# ' + key + '\n\n```json\n' + json.dumps({
                    'type': 'task', 'lifecycle': states.get(key, 'draft'),
                    'owner': owners.get(key, 'Not recorded')
                }) + '\n```'
                db.execute('INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?,?,0,?)',
                           (key, 'dev-memory/' + key, key, 'rev-' + key, body,
                            'dev-record:draft', '2026-09-28'))
            for source, target, kind in edges:
                db.execute('INSERT OR REPLACE INTO edges VALUES(?,?,?,?)',
                           (source, target, 'rev-' + source, 'recorded_dev:' + kind))
            rows = []
            for source, target, kind in relations:
                rows.append({'source': source, 'target': target, 'kind': kind,
                             'source_revision': 'rev-' + source,
                             'target_revision': 'rev-' + target, 'status': 'approved',
                             'source_role': 'activity', 'target_role': 'activity'})
            put(db, 'work_relations_v1', rows)
            put(db, 'needs_reconcile', False)
            put(db, 'generation', 5)


def view(root):
    with database(root) as db:
        return schedule.read_db(db, root)


def save(root, state, key=None, duration=None, buffer=0, anchor=None, earliest=None,
         deadline=None):
    task = None if key is None else dict(id=key, revision='rev-' + key,
                                         duration_days=duration, buffer_days=buffer,
                                         earliest_start=earliest, deadline=deadline)
    return schedule.dispatch(root, 'work-schedule-save', dict(snapshot=state['snapshot'],
        start_date=anchor, task=task, reason='Planning estimate reviewed'))


def test_branch_join_cpm_dates_buffer_float_and_owner_warning(project):
    # A -> B/C -> D. Buffer belongs to B's estimated elapsed time.
    seed(project, 'ABCD', [('A', 'B', 'precedes'), ('A', 'C', 'precedes'),
                           ('B', 'D', 'precedes'), ('C', 'D', 'precedes')],
         owners={'B': 'Pat', 'C': 'Pat'})
    state = view(project)
    for key, days, buffer in [('A', 2, 0), ('B', 3, 1), ('C', 2, 0), ('D', 1, 0)]:
        state = save(project, state, key, days, buffer, '2026-10-01')
    by_id = {n['id']: n for n in state['nodes']}
    assert state['complete'] and state['critical_available']
    assert state['duration_days'] == 7
    assert [[*wave['ids']] for wave in state['waves']] == [['A'], ['B', 'C'], ['D']]
    assert (by_id['B']['earliest_start'], by_id['B']['earliest_finish'],
            by_id['B']['float_days'], by_id['B']['critical']) == (2, 6, 0, True)
    assert (by_id['C']['float_days'], by_id['C']['critical']) == (2, False)
    assert by_id['D']['finish_date'] == '2026-10-08'
    assert any(w['code'] == 'owner_overlap' for w in state['warnings'])


def test_group_dependencies_expand_to_leaves_without_sequencing_containment(project):
    seed(project, 'GABC', [('G', 'A', 'contains'), ('G', 'B', 'contains'),
                           ('G', 'C', 'precedes')])
    state = view(project)
    assert {(e['source'], e['target']) for e in state['dependencies']} == {('A', 'C'), ('B', 'C')}
    assert {(e['parent'], e['child']) for e in state['hierarchy']} == {('G', 'A'), ('G', 'B')}
    assert set(state['waves'][0]['ids']) == {'A', 'B'}
    assert next(n for n in state['nodes'] if n['id'] == 'G')['leaf'] is False


def test_missing_estimates_keep_process_readiness_and_group_rollup(project):
    seed(project, 'GABC', [('G', 'A', 'contains'), ('G', 'B', 'contains'),
                           ('A', 'C', 'precedes'), ('B', 'C', 'precedes')],
         states={'G': 'blocked'}, owners={'A': 'Pat', 'B': 'Pat'})
    state = view(project)
    by_id = {n['id']: n for n in state['nodes']}
    assert state['complete'] and not state['critical_available']
    assert by_id['A']['readiness'] == 'blocked' and by_id['B']['readiness'] == 'blocked'
    assert by_id['C']['readiness'] == 'blocked' and by_id['C']['waiting_on'] == ['A', 'B']
    assert any(w['code'] == 'owner_parallel_conflict' for w in state['warnings'])
    for key, days in [('A', 2), ('B', 1), ('C', 1)]:
        state = save(project, state, key, days, anchor='2026-10-01')
    by_id = {n['id']: n for n in state['nodes']}
    assert by_id['G']['earliest_start'] == 0 and by_id['G']['earliest_finish'] == 2
    assert by_id['G']['start_date'] == '2026-10-01'
    assert by_id['G']['predecessors'] == [] and by_id['G']['successors'] == ['C']
    assert by_id['G']['critical']


def test_nonwork_dev_records_are_not_tasks(project):
    seed(project, 'A')
    with database(project) as db:
        with db:
            db.execute("UPDATE sources SET body=replace(body, '\"type\": \"task\"', "
                       "'\"type\": \"decision\"') WHERE id='A'")
            db.execute('INSERT INTO edges VALUES(?,?,?,?)',
                       ('A', 'A', 'rev-A', 'recorded_dev:precedes'))
    state = view(project)
    assert state['nodes'] == []
    assert not state['warnings']


def test_fractional_start_date_uses_containing_calendar_day(project):
    seed(project, 'AB', [('A', 'B', 'precedes')])
    state = save(project, view(project), 'A', .25, anchor='2026-10-01')
    state = save(project, state, 'B', .5, anchor='2026-10-01')
    node = next(n for n in state['nodes'] if n['id'] == 'B')
    assert node['earliest_start'] == .25
    assert node['start_date'] == '2026-10-01'
    assert node['finish_date'] == '2026-10-02'


def test_blockers_waiting_stale_revision_and_concurrent_save(project):
    seed(project, 'ABC', [('A', 'B', 'precedes'), ('B', 'C', 'precedes')],
         states={'A': 'blocked'}, relations=[('A', 'C', 'blocks')])
    first = view(project)
    assert not first['critical_available']
    assert next(n for n in first['nodes'] if n['id'] == 'C')['blocked_by'] == ['A']
    updated = save(project, first, 'A', 1)
    with pytest.raises(KnowledgeError, match='work_schedule_snapshot_changed'):
        save(project, first, 'B', 1)
    with database(project) as db:
        with db:
            db.execute("UPDATE sources SET revision='changed' WHERE id='A'")
    stale = view(project)
    assert any(w['code'] == 'stale_estimate' for w in stale['warnings'])
    assert any(w['code'] == 'stale_relation' for w in stale['warnings'])
    assert not stale['complete']
    assert all(not n['critical'] for n in stale['nodes'])
    assert updated['snapshot'] != stale['snapshot']


def test_missing_endpoint_cycles_and_multiple_parents_fail_closed(project):
    seed(project, 'ABC', [('A', 'B', 'precedes'), ('B', 'A', 'precedes'),
                           ('A', 'C', 'contains'), ('B', 'C', 'contains'),
                           ('C', 'missing', 'precedes')])
    state = view(project)
    codes = {w['code'] for w in state['warnings']}
    assert {'dependency_cycle', 'multiple_parents', 'missing_dependency_endpoint'} <= codes
    assert not state['complete'] and not state['critical_available']


@pytest.mark.parametrize('duration,buffer', [(True, 0), (float('nan'), 0),
                                               (-1, 0), (3651, 0), (1, 366)])
def test_invalid_estimate_numbers_rejected(project, duration, buffer):
    seed(project, 'A')
    with pytest.raises(KnowledgeError, match='invalid_schedule_number'):
        save(project, view(project), 'A', duration, buffer)


def test_invalid_dates_and_read_only_projection(project):
    seed(project, 'A')
    before = view(project)
    assert view(project) == before
    with pytest.raises(KnowledgeError, match='invalid_schedule_date'):
        save(project, before, 'A', 1, anchor='2026-02-30')
    with database(project) as db:
        assert db.execute("SELECT value FROM meta WHERE key='work_schedule_v1'").fetchone() is None


def test_earliest_start_deadline_and_hierarchy_cycle(project):
    seed(project, 'AB', [('A', 'B', 'precedes')])
    undated = save(project, view(project), 'A', 2, deadline='2026-10-05')
    assert any(w['code'] == 'date_anchor_required' for w in undated['warnings'])
    assert not undated['critical_available']
    state = save(project, undated, 'A', 2, anchor='2026-10-01',
                 earliest='2026-10-03')
    state = save(project, state, 'B', 2, anchor='2026-10-01',
                 deadline='2026-10-05')
    by_id = {n['id']: n for n in state['nodes']}
    assert by_id['A']['earliest_start'] == 2
    assert by_id['B']['finish_date'] == '2026-10-07'
    assert any(w['code'] == 'deadline_missed' and w['nodes'] == ['B']
               for w in state['warnings'])
    with database(project) as db:
        with db:
            db.execute('INSERT INTO edges VALUES(?,?,?,?)',
                       ('A', 'B', 'rev-A', 'recorded_dev:contains'))
            db.execute('INSERT INTO edges VALUES(?,?,?,?)',
                       ('B', 'A', 'rev-B', 'recorded_dev:contains'))
    cyclic = view(project)
    assert any(w['code'] == 'hierarchy_cycle' for w in cyclic['warnings'])
    assert not cyclic['critical_available']


def test_malformed_stored_metadata_fails_closed(project):
    seed(project, 'A')
    with database(project) as db:
        with db:
            put(db, schedule.KEY, {'version': 1, 'revision': 0,
                                   'start_date': None, 'tasks': {'A': {'title': 'private'}}})
    with pytest.raises(KnowledgeError, match='invalid_schedule_metadata'):
        view(project)


def test_stored_overlay_contains_no_source_text_and_is_revision_bound(project):
    seed(project, 'A')
    state = save(project, view(project), 'A', 4, anchor='2026-10-01')
    assert state['nodes'][0]['estimate']['duration_days'] == 4
    with database(project) as db:
        raw = db.execute("SELECT value FROM meta WHERE key='work_schedule_v1'").fetchone()[0]
        assert 'dev-memory/' not in raw and 'Planning estimate reviewed' not in raw
        with db:
            db.execute("UPDATE sources SET deleted=1 WHERE id='A'")
    after = view(project)
    assert not after['nodes'] and 'A' not in json.dumps(after['nodes'])


def test_service_refresh_rejects_unobserved_source_change(project):
    registry = project/'.controlcoding/features/features.json'
    registry.parent.mkdir(parents=True)
    content = {'schemaVersion': 'cc-feature-state/v1', 'wipLimit': 1, 'features': [
        {'id': 'one', 'title': 'One', 'state': 'blocked',
         'acceptanceCriteria': ['The result is verified.']}]}
    registry.write_text(json.dumps(content), encoding='utf-8')
    service.configure(project, {**service.DEFAULT, 'embedding': '',
                                'scopes': ['project', 'dev-memory']})
    first = service.dispatch(project, 'work-schedule-view', None)
    node = next(n for n in first['nodes'] if n['kind'] == 'dev-feature')
    content['features'][0]['title'] = 'Changed'
    registry.write_text(json.dumps(content), encoding='utf-8')
    task = dict(id=node['id'], revision=node['revision'], duration_days=1,
                buffer_days=0, earliest_start=None, deadline=None)
    with pytest.raises(KnowledgeError, match='work_schedule_snapshot_changed'):
        service.dispatch(project, 'work-schedule-save', dict(snapshot=first['snapshot'],
            start_date=None, task=task, reason='Observed estimate changed'))
    refreshed = service.dispatch(project, 'work-schedule-view', None)
    assert refreshed['snapshot'] != first['snapshot']
    assert any(n['title'] == 'Changed' for n in refreshed['nodes'])


def test_backup_restores_overlay_but_requires_current_sources(project, tmp_path):
    from cc_memory_lib.knowledge_backup import backup, restore
    seed(project, 'A')
    save(project, view(project), 'A', 4, anchor='2026-10-01')
    archive = tmp_path/'schedule.ccmemory'
    backup(project, archive)
    target = tmp_path/'restored-schedule'
    target.mkdir()
    restore(target, archive)
    with database(target) as db:
        stored = schedule.metadata(db)
        assert stored['tasks']['A']['duration_days'] == 4
        assert db.execute("SELECT deleted FROM sources WHERE id='A'").fetchone()[0] == 1
    restored = view(target)
    assert not restored['nodes'] and not restored['critical_available']
