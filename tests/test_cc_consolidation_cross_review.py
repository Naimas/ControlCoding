"""Cross-boundary regressions for durable consolidation execution."""
import json
from datetime import datetime, timezone

import pytest

from test_cc_knowledge import project
from test_cc_knowledge_consolidation_execution import CONFIG
from cc_memory_lib import knowledge_service as service
from cc_memory_lib.knowledge_consolidation_store import migrate
from cc_memory_lib.knowledge_store import KnowledgeError
from cc_memory_lib import knowledge_consolidation_execution as execution


@pytest.fixture
def ready(project, tmp_path):
    migrate(project, tmp_path / 'preflight.ccmemory')
    return project


def prepared(root, mode='local'):
    job = service.dispatch(root, 'consolidation-create', {'title': 'Review evidence'})['job']['id']
    return service.dispatch(root, 'consolidation-prepare', {
        'job': job, 'prompt': 'Review exact original source evidence.',
        'config': CONFIG, 'mode': mode})


def attempt(root, packet, operation, owner):
    return service.dispatch(root, 'consolidation-attempt', {
        'job': packet['job'], 'request_id': packet['request_id'],
        'operation': operation, 'owner': owner})


def no_change(packet):
    count = len(packet['input_manifest'])
    return json.dumps({'protocol': 1, 'job': packet['job'],
                       'request_id': packet['request_id'],
                       'manifest_digest': packet['manifest_digest'],
                       'outcome': 'no_change', 'proposals': [],
                       'used_evidence_ids': [], 'unresolved_questions': [],
                       'coverage': {'inspected': count, 'analyzed': count, 'deferred': 0}})


def test_only_one_live_inference_per_project_across_distinct_jobs(ready):
    first, second = prepared(ready), prepared(ready)
    assert attempt(ready, first, 'reserve', 'runner-a')['state'] == 'reserved'
    with pytest.raises(KnowledgeError, match='consolidation_job_busy'):
        attempt(ready, second, 'reserve', 'runner-b')


def test_manual_import_revalidates_unscanned_source_change(ready):
    packet = prepared(ready, 'manual')
    (ready / 'docs/design.md').write_text('# Authentication\nA token replaces the passphrase.\n', encoding='utf-8')
    with pytest.raises(KnowledgeError, match='consolidation_context_changed'):
        service.dispatch(ready, 'consolidation-import', {
            'job': packet['job'], 'request_id': packet['request_id'], 'text': no_change(packet)})


def test_daily_window_deduplicates_same_local_date_after_source_generation(ready):
    settings = {'enabled': True, 'triggers': ['daily'], 'mode': 'local',
                'timezone': 'Europe/Rome', 'daily_hour': 0, 'send_policy': 'local',
                'daily_request_cap': 0, 'prompt': 'Review pinned original evidence.',
                'config': CONFIG}
    service.dispatch(ready, 'consolidation-settings', settings)
    service.dispatch(ready, 'consolidation-queue', {'trigger': 'daily', 'event_id': 'first-daily'})
    (ready / 'docs/design.md').write_text('# Authentication\nA token replaces the passphrase.\n', encoding='utf-8')
    service.reconcile(ready, 'source-change')
    service.dispatch(ready, 'consolidation-queue', {'trigger': 'daily', 'event_id': 'second-daily'})
    items = service.dispatch(ready, 'consolidation-queue', None)['items']
    assert len([item for item in items if item['trigger'] == 'daily']) == 1


def test_repeated_dst_hour_is_one_daily_window(ready, monkeypatch):
    settings = {'enabled': True, 'triggers': ['daily'], 'mode': 'local',
                'timezone': 'Europe/Rome', 'daily_hour': 2, 'send_policy': 'local',
                'daily_request_cap': 0, 'prompt': 'Review pinned original evidence.',
                'config': CONFIG}
    service.dispatch(ready, 'consolidation-settings', settings)

    class Clock(datetime):
        utc = datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.utc.astimezone(tz or timezone.utc)

    monkeypatch.setattr(execution, 'datetime', Clock)
    service.dispatch(ready, 'consolidation-queue', {'trigger': 'daily', 'event_id': 'summer-hour'})
    Clock.utc = datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc)
    service.dispatch(ready, 'consolidation-queue', {'trigger': 'daily', 'event_id': 'winter-hour'})
    items = service.dispatch(ready, 'consolidation-queue', None)['items']
    assert len([item for item in items if item['trigger'] == 'daily']) == 1


def test_new_generation_does_not_starve_behind_stale_queued_job(ready):
    settings = {'enabled': True, 'triggers': ['source'], 'mode': 'local',
                'timezone': 'Europe/Rome', 'daily_hour': 0, 'send_policy': 'local',
                'daily_request_cap': 0, 'prompt': 'Review pinned original evidence.',
                'config': CONFIG}
    service.dispatch(ready, 'consolidation-settings', settings)
    source = ready / 'docs/design.md'
    source.write_text('# Authentication\nFirst changed design.\n', encoding='utf-8')
    service.reconcile(ready, 'source-change')
    source.write_text('# Authentication\nSecond changed design.\n', encoding='utf-8')
    service.reconcile(ready, 'source-change')
    items = service.dispatch(ready, 'consolidation-queue', None)['items']
    assert len(items) == 2
    assert items[0]['state'] != 'queued' and items[1]['state'] == 'queued'


def test_automatic_api_daily_request_cap_includes_retry(ready):
    config = {**CONFIG, 'provider': 'openai'}
    settings = {'enabled': True, 'triggers': ['source'], 'mode': 'api',
                'timezone': 'Europe/Rome', 'daily_hour': 0, 'send_policy': 'api',
                'daily_request_cap': 1, 'prompt': 'Review pinned original evidence.',
                'config': config}
    service.dispatch(ready, 'consolidation-settings', settings)
    (ready / 'docs/design.md').write_text('# Authentication\nA token replaces the passphrase.\n', encoding='utf-8')
    service.reconcile(ready, 'source-change')
    item = service.dispatch(ready, 'consolidation-queue', None)['items'][0]
    packet = service.dispatch(ready, 'consolidation-view', {'job': item['job']})['job']['packet']
    assert attempt(ready, packet, 'reserve', 'runner-a')['state'] == 'reserved'
    service.dispatch(ready, 'consolidation-fail', {
        'job': packet['job'], 'request_id': packet['request_id'],
        'code': 'transport_failure', 'message': 'First send failed'})
    retried = attempt(ready, packet, 'retry', 'runner-a')
    retry_packet = retried['packet']
    with pytest.raises(KnowledgeError, match='consolidation_daily_request_budget'):
        attempt(ready, retry_packet, 'reserve', 'runner-a')


def test_episode_request_cap_survives_terminal_prune(ready):
    for number in range(26):
        (ready / 'docs' / f'episode-{number:02}.md').write_text(
            f'# Observation {number}\nOriginal value {number}.\n', encoding='utf-8')
    settings = {'enabled': True, 'triggers': ['source'], 'mode': 'local',
                'timezone': 'Europe/Rome', 'daily_hour': 0, 'send_policy': 'local',
                'daily_request_cap': 0, 'prompt': 'Review pinned original evidence.',
                'config': {**CONFIG, 'maxRequests': 3}}
    service.dispatch(ready, 'consolidation-settings', settings)
    service.reconcile(ready, 'source-change')
    for _ in range(3):
        item = next(item for item in service.dispatch(ready, 'consolidation-queue', None)['items']
                    if item['state'] == 'queued')
        packet = service.dispatch(ready, 'consolidation-view', {'job': item['job']})['job']['packet']
        attempt(ready, packet, 'reserve', 'runner-a')
        attempt(ready, packet, 'start', 'runner-a')
        service.dispatch(ready, 'consolidation-import', {
            'job': packet['job'], 'request_id': packet['request_id'],
            'text': no_change(packet)})
    assert service.dispatch(ready, 'consolidation-prune', {'max_terminal': 0})['pruned'] == 3
    result = service.dispatch(ready, 'consolidation-queue', {
        'trigger': 'source', 'event_id': 'same-generation-after-prune'})
    assert result['queued'] is False


def test_approved_prior_search_checks_currentness_before_bounding(ready):
    from cc_memory_lib.knowledge_store import database, put
    from cc_memory_lib.knowledge_consolidation import digest
    from cc_memory_lib.knowledge_consolidation_prior import select
    from cc_memory_lib.knowledge_consolidation_review import save_claim
    from cc_memory_lib.knowledge_consolidation_validation import validate
    from cc_memory_lib import knowledge_wiki_review as wiki_review

    with database(ready) as db, db:
        source = db.execute('SELECT c.source,c.revision,c.line,c.end_line,c.text,s.path,s.title '
                            'FROM chunks c JOIN sources s ON s.id=c.source '
                            "WHERE s.deleted=0 AND s.id NOT LIKE 'wiki:%' LIMIT 1").fetchone()
        assert source is not None
        anchor = {'source': source['source'], 'path': source['path'],
                  'revision': source['revision'], 'line': source['line'],
                  'end_line': source['end_line'], 'excerpt': source['text'], 'citation': 'S1'}
        section = {'title': 'Reviewed design', 'body': 'Reviewed decision [S1].',
                   'dependencies': [anchor], 'approved': '2026-09-28T00:00:00Z'}
        for number in range(100):
            target = f'overview/a{number:03}'
            save_claim(db, target, 'summary', {**section, 'claim_id': 'memory:' + digest(target)})
        current_target = 'workflow/zzzz'
        current = {**section, 'claim_id': 'memory:' + digest(current_target)}
        save_claim(db, current_target, 'summary', current)
        put(db, wiki_review.STATE, {'workflow': {'zzzz': current}})
        validate(db)
        selected = [{key: source[key] for key in ('source', 'path', 'title', 'revision',
                                                   'line', 'end_line')}
                    | {'excerpt': source['text']}]
        _, prior, claims = select(db, selected)
        assert claims and claims[0]['target'] == current_target
        assert prior == []
