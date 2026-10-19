"""AI meaning stays proposed; only reviewed, locked store transactions allocate IDs."""
import pytest
import multiprocessing
import time

from test_cc_knowledge import project
from cc_memory_lib import knowledge_service as service
from cc_memory_lib.knowledge_store import KnowledgeError, database, get
from cc_memory_lib.knowledge_document_codes import ASSIGN, PROPOSAL


def listing(root):
    return service.dispatch(root, 'code-view', {'offset': 0, 'query': ''})


def configure(root, project_code='PLAT', automatic=True):
    before = service.dispatch(root, 'code-settings', None)
    return service.dispatch(root, 'code-configure', {
        'project': project_code, 'automatic': automatic, 'kinds': before['settings']['kinds'],
        'expected': before['token']})


def proposed(root, row, kind='DEV', phase='F03', quote='People enter with a secret passphrase.'):
    request = {'source': row['id'], 'revision': row['revision'], 'expected': row['code']['token']}
    packet = service.dispatch(root, 'code-prepare', request)
    return service.dispatch(root, 'code-proposal-save', {**request,
        'settings_token': packet['settings_token'], 'kind': kind, 'phase': phase,
        'quote': quote, 'reason': 'The source describes a development plan.',
        'model': 'local-test', 'provider': 'ollama', 'read_chars': len(packet['text']),
        'truncated': packet['truncated']})


def decide(root, row, decision='accept', kind='DEV', phase='F03'):
    state = row['code']
    return service.dispatch(root, 'code-decide', {
        'source': row['id'], 'revision': row['revision'], 'expected': state['token'],
        'proposal': state['proposal']['id'], 'decision': decision, 'kind': kind,
        'phase': phase, 'reason': 'Reviewed against the cited source passage.'})


def _accept_concurrently(root, payload, start, output):
    if not start.wait(10):
        output.put(('error', 'start_timeout'))
        return
    for _ in range(40):
        try:
            code = service.dispatch(root, 'code-decide', payload)['code']['assignment']['display']
            output.put(('ok', code))
            return
        except KnowledgeError as error:
            if str(error) != 'knowledge_busy':
                output.put(('error', str(error)))
                return
            time.sleep(.05)
    output.put(('error', 'writer_lock_timeout'))


def test_codes_are_reviewed_unique_and_do_not_touch_originals(project):
    before = (project / 'docs/design.md').read_bytes()
    configure(project)
    design = next(r for r in listing(project)['rows'] if r['path'] == 'docs/design.md')
    proposal = proposed(project, design)
    assert proposal['code']['pending'] and not proposal['code']['confirmed']
    assert listing(project)['pending'] == 1
    accepted = decide(project, {**design, 'code': proposal['code']})['code']
    assert accepted['confirmed'] and accepted['assignment']['display'] == 'DEV-PLAT-F03-001'
    assert (project / 'docs/design.md').read_bytes() == before
    readme = next(r for r in listing(project)['rows'] if r['path'] == 'README.md')
    p = proposed(project, readme, kind='PROT', phase='F02', quote='See [design](docs/design.md).')
    second = decide(project, {**readme, 'code': p['code']}, kind='PROT', phase='F02')['code']
    assert second['assignment']['display'] == 'PROT-PLAT-F02-002'
    assert second['assignment']['id'] != accepted['assignment']['id']
    with pytest.raises(KnowledgeError, match='document_code_changed'):
        decide(project, {**design, 'code': proposal['code']})


def test_two_processes_allocate_distinct_plan_numbers(project):
    configure(project)
    first = next(r for r in listing(project)['rows'] if r['path'] == 'docs/design.md')
    second = next(r for r in listing(project)['rows'] if r['path'] == 'README.md')
    a = proposed(project, first)
    b = proposed(project, second, kind='PROT', phase='F02', quote='See [design](docs/design.md).')
    def payload(row, code, kind, phase):
        return {'source': row['id'], 'revision': row['revision'], 'expected': code['token'],
                'proposal': code['proposal']['id'], 'decision': 'accept', 'kind': kind, 'phase': phase,
                'reason': 'Reviewed against the cited source passage.'}
    context = multiprocessing.get_context('spawn')
    start, output = context.Event(), context.Queue()
    workers = [context.Process(target=_accept_concurrently, args=(project, item, start, output)) for item in (
        payload(first, a['code'], 'DEV', 'F03'), payload(second, b['code'], 'PROT', 'F02'))]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(12)
        if worker.is_alive():
            worker.terminate()
            worker.join(3)
        assert worker.exitcode == 0
    results = [output.get(timeout=3) for _ in workers]
    assert all(state == 'ok' for state, _ in results), results
    assert set(code for _, code in results) == {'DEV-PLAT-F03-001', 'PROT-PLAT-F02-002'} or set(code for _, code in results) == {'DEV-PLAT-F03-002', 'PROT-PLAT-F02-001'}


def test_no_code_rejection_drift_and_phase_correction_keep_identity(project):
    configure(project)
    readme = next(r for r in listing(project)['rows'] if r['path'] == 'README.md')
    p = proposed(project, readme, kind='NONE', phase='', quote='See [design](docs/design.md).')
    result = decide(project, {**readme, 'code': p['code']}, kind='NONE', phase='')['code']
    assert result['confirmed'] and result['assignment']['display'] == ''
    assert result['assignment']['number'] == 0
    source = next(r for r in listing(project)['rows'] if r['path'] == 'docs/design.md')
    p = proposed(project, source)
    original = decide(project, {**source, 'code': p['code']})['code']['assignment']
    (project / 'docs/design.md').write_text('# Authentication\nPeople enter with a secret passphrase.\nPhase F04.', encoding='utf-8')
    changed = next(r for r in listing(project)['rows'] if r['path'] == 'docs/design.md')
    assert changed['code']['stale'] and not changed['code']['confirmed']
    with pytest.raises(KnowledgeError, match='document_code_changed'):
        proposed(project, source)
    p = proposed(project, changed, phase='F04')
    revised = decide(project, {**changed, 'code': p['code']}, phase='F04')['code']['assignment']
    assert revised['id'] == original['id'] and revised['number'] == original['number']
    assert revised['display'] == 'DEV-PLAT-F04-001'
    with database(project) as db:
        assert get(db, ASSIGN + source['id'])['history'][-1]['phase'] == 'F03'


def test_project_code_change_keeps_prior_code_and_failures_are_visible(project):
    configure(project)
    source = next(r for r in listing(project)['rows'] if r['path'] == 'docs/design.md')
    p = proposed(project, source)
    accepted = decide(project, {**source, 'code': p['code']})['code']['assignment']
    configure(project, 'NEXT')
    same = next(r for r in listing(project)['rows'] if r['path'] == 'docs/design.md')
    assert same['code']['assignment']['display'] == accepted['display']
    other = next(r for r in listing(project)['rows'] if r['path'] == 'README.md')
    request = {'source': other['id'], 'revision': other['revision'], 'expected': other['code']['token']}
    packet = service.dispatch(project, 'code-prepare', request)
    service.dispatch(project, 'code-fail', {**request, 'settings_token': packet['settings_token'], 'error': 'model_output_invalid'})
    failed = next(r for r in listing(project)['rows'] if r['path'] == 'README.md')
    assert failed['code']['proposal']['status'] == 'failed'
    assert service.dispatch(project, 'code-next', None)['reason'] == 'up_to_date'
    p = proposed(project, failed, kind='PROT', phase='F01', quote='See [design](docs/design.md).')
    newer = decide(project, {**failed, 'code': p['code']}, kind='PROT', phase='F01')['code']['assignment']
    assert newer['display'] == 'PROT-NEXT-F01-002'


def test_invalid_evidence_does_not_persist(project):
    configure(project)
    source = next(r for r in listing(project)['rows'] if r['path'] == 'docs/design.md')
    with pytest.raises(KnowledgeError, match='invalid_document_code_proposal'):
        proposed(project, source, quote='This passage was fabricated.')
    with database(project) as db:
        assert get(db, ASSIGN + source['id']) is None
        assert get(db, PROPOSAL + source['id']) is None


def test_automatic_toggle_preserves_reviewable_proposal(project):
    configure(project, automatic=False)
    source = next(r for r in listing(project)['rows'] if r['path'] == 'docs/design.md')
    pending = proposed(project, source)['code']
    configure(project, automatic=True)
    current = next(r for r in listing(project)['rows'] if r['path'] == 'docs/design.md')
    assert current['code']['pending']
    assert current['code']['proposal']['id'] == pending['proposal']['id']
    accepted = decide(project, current)['code']
    assert accepted['assignment']['display'] == 'DEV-PLAT-F03-001'


def test_backup_preserves_codes_and_disables_automatic_classification(project, tmp_path):
    from cc_memory_lib.knowledge_backup import backup, restore
    from cc_memory_lib.knowledge_document_codes import SETTINGS, NEXT
    configure(project)
    source = next(r for r in listing(project)['rows'] if r['path'] == 'docs/design.md')
    p = proposed(project, source)
    accepted = decide(project, {**source, 'code': p['code']})['code']['assignment']
    archive = tmp_path / 'codes.ccmemory'
    backup(project, archive)
    target = tmp_path / 'restored'
    target.mkdir()
    restore(target, archive)
    with database(target) as db:
        assert get(db, ASSIGN + source['id'])['id'] == accepted['id']
        assert get(db, NEXT) == 2
        assert get(db, SETTINGS)['automatic'] is False
