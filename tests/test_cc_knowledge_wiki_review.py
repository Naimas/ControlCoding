"""Reviewed wiki pages retain human decisions and source revision boundaries."""
import json
import pytest

from test_cc_knowledge import project, record
from cc_memory_lib import knowledge_service as service
from cc_memory_lib import knowledge_wiki_review as review
from cc_memory_lib import knowledge_backup
from cc_memory_lib.knowledge_store import KnowledgeError, database, get, put


def proposal(project, page_type='overview', key='purpose', body='People use a passphrase [S1].'):
    review.dispatch(project, 'wiki-review-view')
    with database(project) as db:
        source = db.execute("SELECT * FROM sources WHERE path='docs/design.md'").fetchone()
    page = service.page(project, 'wiki:review:' + page_type)
    return {'page_type': page_type, 'key': key, 'title': 'Purpose', 'body': body,
            'base_revision': page['revision'],
            'citations': [{'source': source['id'], 'path': source['path'],
                           'revision': source['revision'], 'line': 2, 'end_line': 2,
                           'excerpt': source['body'].splitlines()[1]}]}


def test_five_pages_are_bounded_source_projections(project):
    assert not any(item['id'].startswith('wiki:review:') for item in service.catalog(project)['wiki'])
    view = review.dispatch(project, 'wiki-review-view')
    assert {p['type'] for p in view['pages']} == set(review.PAGE_TYPES)
    for item in view['pages']:
        page = service.page(project, item['id'])
        assert page['history'] and page['revision'] == item['revision']
        assert all(not d['source'].startswith('wiki:') for d in page['dependencies'])
    assert 'People enter with a secret passphrase.' in service.page(project, 'wiki:review:overview')['body']
    assert not service.dispatch(project, 'wiki-lint')['issues']


def test_accept_preserves_prose_and_marks_source_drift(project):
    candidate = review.dispatch(project, 'wiki-review-propose', proposal(project))
    review.dispatch(project, 'wiki-review-decide', {'id': candidate['id'], 'decision': 'accept'})
    accepted = service.page(project, 'wiki:review:overview')
    assert 'People use a passphrase [S1].' in accepted['body']
    assert len(accepted['history']) == 2
    (project / 'docs/design.md').write_text('# Authentication\nOnly tokens are accepted.\n', encoding='utf-8')
    service.reconcile(project)
    changed = service.page(project, 'wiki:review:overview')
    assert 'People use a passphrase [S1].' in changed['body']
    assert 'Only tokens are accepted.' in changed['body']
    assert changed['stale']
    assert any(issue['code'] == 'source_changed' for issue in changed['issues'])
    assert service.page(project, changed['id'], accepted['revision'])['historical']
    (project / 'docs/design.md').unlink()
    service.reconcile(project)
    missing = service.page(project, 'wiki:review:overview')
    assert 'People use a passphrase [S1].' in missing['body']
    assert any(issue['code'] == 'source_missing' for issue in missing['issues'])


def test_revision_bound_proposals_and_rejection(project):
    first = review.dispatch(project, 'wiki-review-propose', proposal(project))
    second = review.dispatch(project, 'wiki-review-propose', proposal(project, key='alternative'))
    review.dispatch(project, 'wiki-review-decide', {'id': first['id'], 'decision': 'accept'})
    with pytest.raises(KnowledgeError, match='wiki_review_page_changed'):
        review.dispatch(project, 'wiki-review-decide', {'id': second['id'], 'decision': 'accept'})
    stale = next(item for item in review.dispatch(project, 'wiki-review-view')['proposals'] if item['id'] == second['id'])
    assert not stale['eligible'] and any(issue['code'] == 'page_changed' for issue in stale['issues'])
    review.dispatch(project, 'wiki-review-decide', {'id': second['id'], 'decision': 'reject'})
    view = review.dispatch(project, 'wiki-review-view')
    assert [p['status'] for p in view['proposals']] == ['accepted', 'rejected']
    assert len(view['pages'][0]['sections']) == 1
    with pytest.raises(KnowledgeError, match='wiki_review_page_changed'):
        review.dispatch(project, 'wiki-review-propose', proposal(project) | {'base_revision': first['base_revision']})


def test_changed_and_forged_evidence_cannot_be_accepted(project):
    value = proposal(project)
    with pytest.raises(KnowledgeError, match='invalid_wiki_page_type'):
        review.dispatch(project, 'wiki-review-propose', {**value, 'page_type': []})
    forged = {**value, 'citations': [{**value['citations'][0], 'source': 'wiki:review:overview'}]}
    with pytest.raises(KnowledgeError, match='invalid_wiki_review_citation'):
        review.dispatch(project, 'wiki-review-propose', forged)
    pending = review.dispatch(project, 'wiki-review-propose', value)
    (project / 'docs/design.md').write_text('# Authentication\nOnly tokens are accepted.\n', encoding='utf-8')
    service.reconcile(project)
    stale = next(item for item in review.dispatch(project, 'wiki-review-view')['proposals'] if item['id'] == pending['id'])
    assert not stale['eligible'] and any(issue['code'] == 'source_changed' for issue in stale['issues'])
    with pytest.raises(KnowledgeError, match='wiki_review_page_changed|wiki_review_source_changed'):
        review.dispatch(project, 'wiki-review-decide', {'id': pending['id'], 'decision': 'accept'})
    with pytest.raises(KnowledgeError, match='wiki_review_source_changed'):
        fresh_page_stale_citation = {**value, 'base_revision': service.page(project, 'wiki:review:overview')['revision']}
        review.dispatch(project, 'wiki-review-propose', fresh_page_stale_citation)


def test_forget_removes_reviewed_transcript_text(project):
    service.conversation(project, record())
    service.reconcile(project)
    with database(project) as db:
        source = db.execute("SELECT * FROM sources WHERE kind='conversation' AND deleted=0").fetchone()
    assert source is not None
    lines = source['body'].splitlines()
    line = next(i for i, text in enumerate(lines, 1) if 'apricot' in text)
    value = proposal(project)
    value['body'] = 'Private apricot phrase [S1].'
    value['citations'] = [{'source': source['id'], 'path': source['path'], 'revision': source['revision'],
                           'line': line, 'end_line': line, 'excerpt': lines[line - 1]}]
    pending = review.dispatch(project, 'wiki-review-propose', value)
    review.dispatch(project, 'wiki-review-decide', {'id': pending['id'], 'decision': 'accept'})
    service.dispatch(project, 'forget', 'chat-1')
    with database(project) as db:
        assert all('apricot' not in row['body'] for row in db.execute('SELECT body FROM wiki_history'))
    assert not review.dispatch(project, 'wiki-review-view')['pages'][0]['sections']


def test_backup_retains_review_state_and_history(project, tmp_path):
    pending = review.dispatch(project, 'wiki-review-propose', proposal(project))
    review.dispatch(project, 'wiki-review-decide', {'id': pending['id'], 'decision': 'accept'})
    archive = tmp_path / 'review.ccmemory'
    knowledge_backup.backup(project, archive)
    restored = tmp_path / 'restored'
    restored.mkdir()
    knowledge_backup.restore(restored, archive)
    with database(restored) as db:
        assert get(db, review.ENABLED) is True
        assert get(db, review.STATE)['overview']['purpose']['body'] == 'People use a passphrase [S1].'
        assert get(db, review.PROPOSALS)[0]['status'] == 'accepted'
        assert db.execute("SELECT count(*) FROM wiki_history WHERE id='wiki:review:overview'").fetchone()[0] == 2


def test_accept_invalidates_library_navigation_snapshot(project):
    request = {'kind': 'wiki', 'query': '', 'offset': 0, 'snapshot': None}
    before = service.dispatch(project, 'library', request)
    pending = review.dispatch(project, 'wiki-review-propose', proposal(project))
    review.dispatch(project, 'wiki-review-decide', {'id': pending['id'], 'decision': 'accept'})
    with pytest.raises(KnowledgeError, match='library_snapshot_changed'):
        service.dispatch(project, 'library', {**request, 'snapshot': before['snapshot']})


def test_review_payload_budget_preserves_state_and_reply_limit(project):
    review.dispatch(project, 'wiki-review-view')
    first = None
    for number in range(100):
        value = proposal(project, key='candidate-' + str(number), body=('Z' * 7900) + ' [S1].')
        try:
            item = review.dispatch(project, 'wiki-review-propose', value)
        except KnowledgeError as error:
            assert str(error) == 'wiki_review_budget'
            break
        first = first or item
    else:
        pytest.fail('review budget was never reached')
    before = review.dispatch(project, 'wiki-review-view')
    assert first and len(before['proposals']) < 100
    assert len(json.dumps(before, ensure_ascii=True).encode('utf-8')) < 1024 * 1024
    with pytest.raises(KnowledgeError, match='wiki_review_budget'):
        review.dispatch(project, 'wiki-review-decide', {'id': first['id'], 'decision': 'accept'})
    after = review.dispatch(project, 'wiki-review-view')
    assert after == before
    assert not after['pages'][0]['sections']
    review.dispatch(project, 'wiki-review-decide', {'id': first['id'], 'decision': 'reject'})
    assert review.dispatch(project, 'wiki-review-view')['proposals'][0]['status'] == 'rejected'


def test_oversized_restored_review_state_has_explicit_error(project):
    review.dispatch(project, 'wiki-review-view')
    with database(project) as db:
        put(db, review.PROPOSALS, [{'id': str(i), 'status': 'rejected', 'body': 'Q' * 8000}
                                   for i in range(50)])
        db.commit()
    with pytest.raises(KnowledgeError, match='wiki_review_budget'):
        review.dispatch(project, 'wiki-review-view')


def test_oversized_review_backup_rejected_before_target_and_sync_rolls_back(project, tmp_path):
    review.dispatch(project, 'wiki-review-view')
    with database(project) as db:
        source = db.execute("SELECT * FROM sources WHERE path='docs/design.md'").fetchone()
        dependency = {'source': source['id'], 'path': source['path'], 'revision': source['revision'],
                      'line': 2, 'end_line': 2, 'excerpt': source['body'].splitlines()[1], 'citation': 'S1'}
        section = {'title': 'Earlier approved text', 'body': 'A' * 7895 + '[S1]',
                   'dependencies': [dependency], 'approved': '2026-09-24T00:00:00+00:00'}
        approved = {kind: {f'earlier-{i}': section for i in range(17)}
                    for kind in ('overview', 'workflow', 'timeline')}
        put(db, review.STATE, approved)
        db.commit()
        generation = get(db, 'generation')
        history = db.execute('SELECT count(*) FROM wiki_history').fetchone()[0]
        original_revision = source['revision']
    assert len(json.dumps({'sections': approved, 'proposals': []}, ensure_ascii=True,
                          separators=(',', ':')).encode('utf-8')) > review.MAX_REVIEW_BYTES
    archive = tmp_path / 'older-review.ccmemory'
    knowledge_backup.backup(project, archive)
    archive_bytes = archive.read_bytes()
    target = tmp_path / 'restored'
    target.mkdir()
    with pytest.raises(KnowledgeError, match='wiki_review_budget'):
        knowledge_backup.restore(target, archive)
    assert not (target / '.controlcoding').exists()
    assert archive.read_bytes() == archive_bytes
    (project / 'docs/design.md').write_text('# Authentication\nChanged current evidence.\n', encoding='utf-8')
    with pytest.raises(KnowledgeError, match='wiki_review_budget'):
        service.reconcile(project)
    with database(project) as db:
        assert get(db, 'generation') == generation
        assert get(db, review.STATE) == approved
        assert db.execute('SELECT count(*) FROM wiki_history').fetchone()[0] == history
        assert db.execute("SELECT revision FROM sources WHERE path='docs/design.md'").fetchone()[0] == original_revision
