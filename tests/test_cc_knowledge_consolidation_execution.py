"""Durable sends, strict batch import, queue coalescing and recovery scenarios."""
import json

import pytest

from test_cc_knowledge import project, record
from cc_memory_lib import knowledge_service as service
from cc_memory_lib.knowledge_consolidation_store import migrate
from cc_memory_lib.knowledge_store import KnowledgeError, database


CONFIG = {'provider': 'ollama', 'model': 'fixture', 'generation': {
    'effort': 'default', 'thinking': None, 'reasoningMode': 'default',
    'verbosity': 'default', 'temperature': None, 'format': 'json', 'summary': False},
    'maxContextChars': 12000, 'maxOutputTokens': 2048, 'timeoutSeconds': 120,
    'maxRequests': 3, 'roleRevision': 'fixture-revision'}


@pytest.fixture
def ready(project, tmp_path):
    migrate(project, tmp_path / 'preflight.ccmemory')
    return project


def prepare(root, mode='manual', config=CONFIG):
    job = service.dispatch(root, 'consolidation-create', {'title': 'Review source evidence'})['job']['id']
    packet = service.dispatch(root, 'consolidation-prepare', {
        'job': job, 'prompt': 'Summarize the pinned original evidence.',
        'config': config, 'mode': mode})
    return packet


def reply(packet, outcome='no_change', proposals=None, analyzed=None):
    count = len(packet['input_manifest']) if analyzed is None else analyzed
    return json.dumps({'protocol': 1, 'job': packet['job'],
                       'request_id': packet['request_id'],
                       'manifest_digest': packet['manifest_digest'], 'outcome': outcome,
                       'proposals': proposals or [],
                       'used_evidence_ids': ([packet['input_manifest'][0]['evidence_id']]
                                             if proposals else []),
                       'unresolved_questions': [],
                       'coverage': {'inspected': count, 'analyzed': count,
                                    'deferred': len(packet['input_manifest']) - count}})


def candidate(packet, evidence_index=0, key='summary'):
    anchor = packet['input_manifest'][evidence_index]
    citation = {k: anchor[k] for k in ('source', 'path', 'revision', 'line',
                                       'end_line', 'excerpt', 'evidence_id')}
    citation['citation'] = citation.pop('evidence_id')
    return {'page_type': 'overview', 'key': key, 'title': 'Evidence summary',
            'body': 'The selected original source records this statement [' + citation['citation'] + '].',
            'kind': 'summary', 'epistemic_status': 'observed', 'scope': 'project',
            'citations': [citation], 'conflicting_evidence_ids': [],
            'reason': 'Retain a traceable source summary', 'prerequisite_ids': []}


def attempt(root, packet, operation, owner='runner-a'):
    return service.dispatch(root, 'consolidation-attempt', {
        'job': packet['job'], 'request_id': packet['request_id'],
        'operation': operation, 'owner': owner})


def test_manual_packet_survives_restart_and_import_is_replay_safe(ready):
    packet = prepare(ready)
    viewed = service.dispatch(ready, 'consolidation-view', {'job': packet['job']})
    assert viewed['job']['packet'] == packet
    response = reply(packet, 'proposals', [candidate(packet)])
    first = service.dispatch(ready, 'consolidation-import', {
        'job': packet['job'], 'request_id': packet['request_id'], 'text': response})
    assert first['count'] == 1 and not first['replay']
    again = service.dispatch(ready, 'consolidation-import', {
        'job': packet['job'], 'request_id': packet['request_id'], 'text': response})
    assert again['replay']
    viewed = service.dispatch(ready, 'consolidation-view', {'job': packet['job']})
    assert viewed['job']['proposals'][0]['status'] == 'pending'
    assert viewed['job']['proposals'][0]['epistemic_status'] == 'observed'
    assert not viewed['job']['receipt']
    with pytest.raises(KnowledgeError, match='reply_conflict'):
        service.dispatch(ready, 'consolidation-import', {
            'job': packet['job'], 'request_id': packet['request_id'],
            'text': reply(packet, 'insufficient_evidence')})


def test_batch_import_rolls_back_all_when_second_proposal_has_foreign_anchor(ready):
    packet = prepare(ready)
    bad = candidate(packet, key='bad')
    bad['citations'][0]['revision'] = 'forged'
    with pytest.raises(KnowledgeError, match='reply_mismatch'):
        service.dispatch(ready, 'consolidation-import', {
            'job': packet['job'], 'request_id': packet['request_id'],
            'text': reply(packet, 'proposals', [candidate(packet), bad])})
    viewed = service.dispatch(ready, 'consolidation-view', {'job': packet['job']})
    assert viewed['job']['proposals'] == []
    assert viewed['job']['attempts'][0]['state'] == 'prepared'


def test_two_consumers_and_unknown_started_send_requires_explicit_retry(ready):
    packet = prepare(ready, mode='local')
    assert attempt(ready, packet, 'reserve')['state'] == 'reserved'
    with pytest.raises(KnowledgeError, match='attempt_state'):
        attempt(ready, packet, 'reserve', owner='runner-b')
    assert attempt(ready, packet, 'start')['state'] == 'started'
    with database(ready) as db:
        db.execute("UPDATE consolidation_attempts SET lease_until='2000-01-01T00:00:00+00:00' WHERE request_id=?",
                   (packet['request_id'],))
        db.commit()
    with pytest.raises(KnowledgeError, match='lease_required'):
        # Expired started transport has an unknown outcome and cannot restart.
        attempt(ready, packet, 'start', owner='runner-b')
    recovered = service.dispatch(ready, 'consolidation-view', {'job': packet['job']})
    assert recovered['job']['attempts'][0]['state'] in ('started', 'interrupted')
    retried = attempt(ready, packet, 'retry', owner='runner-b')
    assert retried['request_id'] != packet['request_id']
    assert retried['state'] == 'prepared'
    with pytest.raises(KnowledgeError, match='attempt_state'):
        service.dispatch(ready, 'consolidation-import', {
            'job': packet['job'], 'request_id': packet['request_id'],
            'text': reply(packet)})


def test_opt_in_queue_coalesces_changed_generation_and_noop(ready):
    settings = {'enabled': True, 'triggers': ['source', 'daily'], 'mode': 'local',
                'timezone': 'Europe/Rome', 'daily_hour': 0, 'send_policy': 'local',
                'daily_request_cap': 0, 'prompt': 'Review pinned original evidence.',
                'config': CONFIG}
    service.dispatch(ready, 'consolidation-settings', settings)
    (ready / 'docs/design.md').write_text('# Authentication\nUse a new signed token.\n', encoding='utf-8')
    service.reconcile(ready, 'source-change')
    first = service.dispatch(ready, 'consolidation-queue', None)
    assert first['queued'] and len(first['items']) == 1
    service.reconcile(ready, 'source-change')
    assert len(service.dispatch(ready, 'consolidation-queue', None)['items']) == 1
    service.dispatch(ready, 'consolidation-queue', {'trigger': 'daily', 'event_id': 'daily-one'})
    service.dispatch(ready, 'consolidation-queue', {'trigger': 'daily', 'event_id': 'daily-two'})
    assert len(service.dispatch(ready, 'consolidation-queue', None)['items']) == 2


def test_stale_queued_generation_does_not_starve_newer_work(ready):
    settings = {'enabled': True, 'triggers': ['source'], 'mode': 'local',
                'timezone': 'Europe/Rome', 'daily_hour': 0, 'send_policy': 'local',
                'daily_request_cap': 0, 'prompt': 'Review pinned original evidence.',
                'config': CONFIG}
    service.dispatch(ready, 'consolidation-settings', settings)
    (ready / 'docs/design.md').write_text('# Authentication\nRevision A.\n', encoding='utf-8')
    service.reconcile(ready, 'source-change')
    old = service.dispatch(ready, 'consolidation-queue', None)['items'][0]
    (ready / 'docs/design.md').write_text('# Authentication\nRevision B.\n', encoding='utf-8')
    service.reconcile(ready, 'source-change')
    queued = service.dispatch(ready, 'consolidation-queue', None)['items']
    assert len(queued) == 2
    assert queued[0]['id'] == old['id'] and queued[0]['state'] == 'stale'
    assert queued[1]['state'] == 'queued'
    packet = service.dispatch(ready, 'consolidation-view', {'job': queued[1]['job']})['job']['packet']
    assert attempt(ready, packet, 'reserve')['state'] == 'reserved'


def test_failure_is_not_successful_abstention_and_does_not_advance_progress(ready):
    packet = prepare(ready, mode='local')
    attempt(ready, packet, 'reserve')
    attempt(ready, packet, 'start')
    service.dispatch(ready, 'consolidation-fail', {
        'job': packet['job'], 'request_id': packet['request_id'],
        'code': 'provider_timeout', 'message': 'No final text', 'usage': None})
    viewed = service.dispatch(ready, 'consolidation-view', {'job': packet['job']})
    assert viewed['job']['attempts'][0]['state'] == 'failed'
    assert viewed['job']['attempts'][0]['outcome'] is None
    assert viewed['job']['proposals'] == []


def test_forget_erases_durable_manual_packet_and_rejects_late_reply(ready):
    service.conversation(ready, record())
    service.reconcile(ready, 'conversation')
    packet = prepare(ready)
    assert any(item['source'] == 'conversation:chat-1' for item in packet['input_manifest'])
    response = reply(packet)
    service.dispatch(ready, 'forget', 'chat-1')
    view = service.dispatch(ready, 'consolidation-view', {'job': packet['job']})
    assert view['job']['state'] == 'purged' and view['job']['packet'] is None
    with pytest.raises(KnowledgeError, match='attempt_unavailable'):
        service.dispatch(ready, 'consolidation-import', {
            'job': packet['job'], 'request_id': packet['request_id'], 'text': response})
    with database(ready) as db:
        for table in ('consolidation_jobs', 'consolidation_attempts', 'consolidation_queue'):
            assert 'Private apricot phrase' not in json.dumps([tuple(row) for row in db.execute('SELECT * FROM ' + table)])


def test_nonfirst_manifest_evidence_is_renumbered_for_review(ready):
    for number in range(12):
        (ready / 'docs' / f'file-{number:02}.md').write_text(
            f'# Observation {number}\nValue {number} is recorded.\n', encoding='utf-8')
    service.reconcile(ready)
    packet = prepare(ready)
    assert len(packet['input_manifest']) == 8
    proposal = candidate(packet, evidence_index=7)
    response = json.loads(reply(packet, 'proposals', [proposal]))
    response['used_evidence_ids'] = ['S8']
    service.dispatch(ready, 'consolidation-import', {
        'job': packet['job'], 'request_id': packet['request_id'], 'text': json.dumps(response)})
    view = service.dispatch(ready, 'consolidation-view', {'job': packet['job']})
    stored = view['job']['proposals'][0]
    assert '[S1]' in stored['body'] and '[S8]' not in stored['body']
    assert stored['dependencies'][0]['source'] == packet['input_manifest'][7]['source']


def test_partial_coverage_cannot_use_unanalyzed_evidence(ready):
    packet = prepare(ready)
    assert len(packet['input_manifest']) >= 2
    proposal = candidate(packet, evidence_index=1)
    response = json.loads(reply(packet, 'proposals', [proposal], analyzed=1))
    response['used_evidence_ids'] = ['S2']
    with pytest.raises(KnowledgeError, match='coverage'):
        service.dispatch(ready, 'consolidation-import', {
            'job': packet['job'], 'request_id': packet['request_id'], 'text': json.dumps(response)})
    response['protocol'] = True
    response['coverage']['analyzed'] = 2
    response['coverage']['inspected'] = 2
    response['coverage']['deferred'] = len(packet['input_manifest']) - 2
    with pytest.raises(KnowledgeError):
        service.dispatch(ready, 'consolidation-import', {
            'job': packet['job'], 'request_id': packet['request_id'], 'text': json.dumps(response)})


def test_malformed_json_values_raise_typed_errors_without_partial_import(ready):
    settings = {'enabled': False, 'triggers': [{'unexpected': 'object'}], 'mode': 'manual',
                'timezone': 'UTC', 'daily_hour': 0, 'send_policy': 'none',
                'daily_request_cap': 0, 'prompt': '', 'config': None}
    with pytest.raises(KnowledgeError):
        service.dispatch(ready, 'consolidation-settings', settings)
    packet = prepare(ready)
    for body in ([], {'unexpected': 'object'}, None):
        invalid = candidate(packet)
        invalid['body'] = body
        with pytest.raises(KnowledgeError):
            service.dispatch(ready, 'consolidation-import', {
                'job': packet['job'], 'request_id': packet['request_id'],
                'text': reply(packet, 'proposals', [invalid])})
    assert service.dispatch(ready, 'consolidation-view', {'job': packet['job']})['job']['proposals'] == []


def test_automatic_api_reservation_uses_saved_daily_cap_without_credentials(ready):
    cloud = {**CONFIG, 'provider': 'openai', 'model': 'fixture-api'}
    settings = {'enabled': True, 'triggers': ['source'], 'mode': 'api',
                'timezone': 'Europe/Rome', 'daily_hour': 0, 'send_policy': 'api',
                'daily_request_cap': 1, 'prompt': 'Review pinned original evidence.',
                'config': cloud}
    service.dispatch(ready, 'consolidation-settings', settings)
    (ready / 'docs/design.md').write_text('# Authentication\nUse a new signed token.\n', encoding='utf-8')
    service.reconcile(ready, 'source-change')
    first = service.dispatch(ready, 'consolidation-queue', None)['items'][0]
    packet = service.dispatch(ready, 'consolidation-view', {'job': first['job']})['job']['packet']
    assert attempt(ready, packet, 'reserve')['state'] == 'reserved'
    assert attempt(ready, packet, 'cancel')['state'] == 'canceled'
    (ready / 'README.md').write_text('# Welcome\nThe next source revision.\n', encoding='utf-8')
    service.reconcile(ready, 'source-change')
    second = service.dispatch(ready, 'consolidation-queue', None)['items'][-1]
    packet2 = service.dispatch(ready, 'consolidation-view', {'job': second['job']})['job']['packet']
    with pytest.raises(KnowledgeError, match='daily_request_budget'):
        attempt(ready, packet2, 'reserve')
    assert service.dispatch(ready, 'consolidation-view', {'job': second['job']})['job']['attempts'][0]['state'] == 'prepared'


def test_imported_prerequisite_must_be_accepted_in_same_atomic_selection(ready):
    view = service.dispatch(ready, 'consolidation-create', {'title': 'Dependency review'})
    cite = view['sources'][0]
    view = service.dispatch(ready, 'consolidation-propose', {
        'job': view['job']['id'], 'page_type': 'overview', 'key': 'first',
        'title': 'First source note', 'body': 'Original evidence [S1].',
        'kind': 'summary', 'citations': [cite], 'reason': 'Source-backed'})
    first_id = view['job']['proposals'][0]['id']
    packet = service.dispatch(ready, 'consolidation-prepare', {
        'job': view['job']['id'], 'prompt': 'Review pinned source.',
        'config': CONFIG, 'mode': 'manual'})
    second = candidate(packet, key='second')
    second['prerequisite_ids'] = [first_id]
    service.dispatch(ready, 'consolidation-import', {
        'job': packet['job'], 'request_id': packet['request_id'],
        'text': reply(packet, 'proposals', [second])})
    view = service.dispatch(ready, 'consolidation-view', {'job': packet['job']})
    second_id = view['job']['proposals'][1]['id']
    with pytest.raises(KnowledgeError, match='prerequisite_unavailable'):
        service.dispatch(ready, 'consolidation-decide', {
            'job': packet['job'], 'ids': [second_id], 'decision': 'accept',
            'request_id': 'reject-missing-prereq'})
    accepted = service.dispatch(ready, 'consolidation-decide', {
        'job': packet['job'], 'ids': [first_id, second_id], 'decision': 'accept',
        'request_id': 'accept-together'})
    assert accepted['job']['state'] == 'published'


def test_conflicting_evidence_requires_disputed_status_and_survives_review(ready):
    packet = prepare(ready)
    assert len(packet['input_manifest']) >= 2
    first = candidate(packet)
    second_anchor = packet['input_manifest'][1]
    second = {k: second_anchor[k] for k in ('source', 'path', 'revision', 'line', 'end_line', 'excerpt')}
    second['citation'] = 'S2'
    first['citations'].append(second)
    first['body'] = 'The records disagree [S1] [S2].'
    first['conflicting_evidence_ids'] = ['S2']
    first['epistemic_status'] = 'decided'
    response = json.loads(reply(packet, 'proposals', [first]))
    response['used_evidence_ids'] = ['S1', 'S2']
    with pytest.raises(KnowledgeError):
        service.dispatch(ready, 'consolidation-import', {
            'job': packet['job'], 'request_id': packet['request_id'], 'text': json.dumps(response)})
    first['epistemic_status'] = 'disputed'
    response['proposals'] = [first]
    service.dispatch(ready, 'consolidation-import', {
        'job': packet['job'], 'request_id': packet['request_id'], 'text': json.dumps(response)})
    view = service.dispatch(ready, 'consolidation-view', {'job': packet['job']})
    assert view['job']['proposals'][0]['epistemic_status'] == 'disputed'
    accepted = service.dispatch(ready, 'consolidation-decide', {
        'job': packet['job'], 'ids': [view['job']['proposals'][0]['id']],
        'decision': 'accept', 'request_id': 'accept-dispute'})
    assert accepted['job']['state'] == 'published'
    with database(ready) as db:
        metadata = db.execute('SELECT epistemic_status FROM consolidation_claim_metadata').fetchone()
        assert metadata[0] == 'disputed'


def test_approved_prior_context_uses_originals_and_survives_human_override(ready, tmp_path):
    from cc_memory_lib.knowledge_backup import backup

    (ready / 'docs' / 'database.md').write_text(
        '# Production datastore decision\nPostgreSQL is the approved production datastore.\n', encoding='utf-8')
    service.reconcile(ready)
    first = prepare(ready)
    pg_index = next(i for i, item in enumerate(first['input_manifest'])
                    if item['path'] == 'docs/database.md')
    proposal = candidate(first, evidence_index=pg_index, key='production-datastore')
    proposal.update(title='Approved production datastore',
                    body=f"PostgreSQL is the approved production datastore [S{pg_index + 1}].",
                    kind='decision_summary', epistemic_status='decided', scope='production')
    seeded = json.loads(reply(first, 'proposals', [proposal]))
    seeded['used_evidence_ids'] = [f'S{pg_index + 1}']
    service.dispatch(ready, 'consolidation-import', {
        'job': first['job'], 'request_id': first['request_id'], 'text': json.dumps(seeded)})
    view = service.dispatch(ready, 'consolidation-view', {'job': first['job']})
    service.dispatch(ready, 'consolidation-decide', {
        'job': first['job'], 'ids': [view['job']['proposals'][0]['id']],
        'decision': 'accept', 'request_id': 'accept-pg'})

    (ready / 'docs' / 'redis.md').write_text(
        '# Production datastore idea\nCould Redis replace PostgreSQL someday? This is speculative.\n',
        encoding='utf-8')
    service.reconcile(ready)
    second = prepare(ready)
    assert [item['path'] for item in second['input_manifest']] == ['docs/redis.md']
    assert len(second['approved_context']) == len(second['approved_evidence']) == 1
    claim = second['approved_context'][0]
    anchor = second['approved_evidence'][0]
    assert claim['epistemic_status'] == 'decided' and claim['scope'] == 'production'
    assert 'PostgreSQL' in claim['body'] and claim['evidence_ids'] == [anchor['evidence_id']]
    assert anchor['path'] == 'docs/database.md' and anchor['excerpt'] in (
        ready / 'docs' / 'database.md').read_text(encoding='utf-8')
    assert anchor['evidence_id'] not in {item['evidence_id'] for item in second['input_manifest']}
    no_change = reply(second)
    service.dispatch(ready, 'consolidation-import', {
        'job': second['job'], 'request_id': second['request_id'], 'text': no_change})
    assert service.dispatch(ready, 'consolidation-import', {
        'job': second['job'], 'request_id': second['request_id'], 'text': no_change})['replay']

    page = next(page for page in service.dispatch(ready, 'wiki-review-view', None)['pages']
                if page['type'] == 'overview')
    citation = {k: anchor[k] for k in ('source', 'path', 'revision', 'line', 'end_line', 'excerpt')}
    citation['citation'] = 'S1'
    override = service.dispatch(ready, 'wiki-review-propose', {
        'page_type': 'overview', 'key': 'production-datastore',
        'title': 'Human override', 'body': 'A human replaced the derived wording [S1].',
        'citations': [citation], 'base_revision': page['revision']})
    service.dispatch(ready, 'wiki-review-decide', {'id': override['id'], 'decision': 'accept'})
    # The frozen historical packet is still verifiable, but the replaced claim
    # cannot enter the next prompt as current approved context.
    assert backup(ready, tmp_path / 'overridden.ccmemory')['saved']
    (ready / 'docs' / 'redis.md').write_text(
        '# Production datastore idea\nCould Redis replace PostgreSQL someday? Still speculative.\n',
        encoding='utf-8')
    service.reconcile(ready)
    third = prepare(ready)
    assert third['approved_context'] == [] and third['approved_evidence'] == []


def test_prior_long_line_anchor_and_unicode_budget_are_exact(ready, tmp_path):
    from cc_memory_lib.knowledge_backup import backup

    long_line = '界' * 1600 + 'PostgreSQL is the approved production datastore.'
    (ready / 'docs' / 'database.md').write_text('# Production datastore decision\n' + long_line + '\n',
                                                encoding='utf-8')
    service.reconcile(ready)
    first = prepare(ready)
    pg_index = next(i for i, item in enumerate(first['input_manifest'])
                    if item['path'] == 'docs/database.md')
    assert '\n' in first['input_manifest'][pg_index]['excerpt']
    proposal = candidate(first, evidence_index=pg_index, key='production-datastore')
    proposal.update(title='Approved production datastore',
                    body=f"PostgreSQL is approved [S{pg_index + 1}].",
                    kind='decision_summary', epistemic_status='decided')
    response = json.loads(reply(first, 'proposals', [proposal]))
    response['used_evidence_ids'] = [f'S{pg_index + 1}']
    service.dispatch(ready, 'consolidation-import', {
        'job': first['job'], 'request_id': first['request_id'], 'text': json.dumps(response)})
    view = service.dispatch(ready, 'consolidation-view', {'job': first['job']})
    service.dispatch(ready, 'consolidation-decide', {
        'job': first['job'], 'ids': [view['job']['proposals'][0]['id']],
        'decision': 'accept', 'request_id': 'accept-long-line'})
    for number in range(3):
        (ready / 'docs' / f'redis-{number}.md').write_text(
            f'# Production datastore idea {number}\nRedis might replace PostgreSQL someday. '
            + '界' * 1400 + '\n', encoding='utf-8')
    service.reconcile(ready)
    second = prepare(ready)
    assert len(second['input_manifest']) == 2
    assert second['approved_context'] and second['approved_evidence'][0]['excerpt'] == (
        first['input_manifest'][pg_index]['excerpt'])
    assert backup(ready, tmp_path / 'unicode-prior.ccmemory')['saved']


def test_automatic_episode_continues_three_batches_then_stops_at_request_cap(ready):
    for number in range(26):
        (ready / 'docs' / f'episode-{number:02}.md').write_text(
            f'# Observation {number}\nOriginal value {number}.\n', encoding='utf-8')
    options = {**CONFIG, 'maxRequests': 3}
    settings = {'enabled': True, 'triggers': ['source'], 'mode': 'local',
                'timezone': 'Europe/Rome', 'daily_hour': 0, 'send_policy': 'local',
                'daily_request_cap': 0, 'prompt': 'Review pinned original evidence.',
                'config': options}
    service.dispatch(ready, 'consolidation-settings', settings)
    service.reconcile(ready, 'source-change')
    seen = []
    for index in range(3):
        items = service.dispatch(ready, 'consolidation-queue', None)['items']
        queued = [item for item in items if item['state'] == 'queued']
        assert len(queued) == 1
        packet = service.dispatch(ready, 'consolidation-view', {'job': queued[0]['job']})['job']['packet']
        assert packet['request_id'] not in seen
        seen.append(packet['request_id'])
        attempt(ready, packet, 'reserve')
        attempt(ready, packet, 'start')
        service.dispatch(ready, 'consolidation-import', {
            'job': packet['job'], 'request_id': packet['request_id'],
            'text': reply(packet)})
        assert service.dispatch(ready, 'consolidation-view', {'job': packet['job']})['job']['state'] == 'analyzed'
    final = service.dispatch(ready, 'consolidation-queue', None)
    assert len(final['items']) == 3 and not final['queued']
    with database(ready) as db:
        from cc_memory_lib.knowledge_consolidation_selection import eligible_count
        assert eligible_count(db) > 0
    service.reconcile(ready, 'source-change')
    assert len(service.dispatch(ready, 'consolidation-queue', None)['items']) == 3
    assert service.dispatch(ready, 'consolidation-prune', {'max_terminal': 0})['pruned'] == 3


def test_zero_analysis_and_policy_drift_do_not_continue_episode(ready):
    for number in range(10):
        (ready / 'docs' / f'pending-{number:02}.md').write_text(
            f'# Pending {number}\nEvidence {number}.\n', encoding='utf-8')
    settings = {'enabled': True, 'triggers': ['source'], 'mode': 'manual',
                'timezone': 'Europe/Rome', 'daily_hour': 0, 'send_policy': 'none',
                'daily_request_cap': 0, 'prompt': 'Review pinned original evidence.',
                'config': CONFIG}
    service.dispatch(ready, 'consolidation-settings', settings)
    service.reconcile(ready, 'source-change')
    queue = service.dispatch(ready, 'consolidation-queue', None)
    packet = service.dispatch(ready, 'consolidation-view', {'job': queue['items'][0]['job']})['job']['packet']
    service.dispatch(ready, 'consolidation-import', {
        'job': packet['job'], 'request_id': packet['request_id'],
        'text': reply(packet, analyzed=0)})
    assert len(service.dispatch(ready, 'consolidation-queue', None)['items']) == 1
    (ready / 'docs' / 'pending-00.md').write_text('# Pending 0\nNew evidence.\n', encoding='utf-8')
    service.reconcile(ready, 'source-change')
    queue = service.dispatch(ready, 'consolidation-queue', None)
    packet2 = service.dispatch(ready, 'consolidation-view', {'job': queue['items'][-1]['job']})['job']['packet']
    service.dispatch(ready, 'consolidation-settings', {**settings, 'prompt': 'New scoped prompt.'})
    service.dispatch(ready, 'consolidation-import', {
        'job': packet2['job'], 'request_id': packet2['request_id'],
        'text': reply(packet2)})
    assert len(service.dispatch(ready, 'consolidation-queue', None)['items']) == 2
