"""Real source mutation, atomic review, replay, recovery and privacy boundaries."""
import json
import pytest

from test_cc_knowledge import project, record
from cc_memory_lib import knowledge_service as service
from cc_memory_lib import knowledge_consolidation as mc
from cc_memory_lib import knowledge_wiki_review as wiki_review
from cc_memory_lib.knowledge_consolidation_store import migrate
from cc_memory_lib.knowledge_store import KnowledgeError, database, get


@pytest.fixture
def migrated(project, tmp_path):
    migrate(project, tmp_path / 'before.ccmemory')
    return project


def create(root):
    return mc.dispatch(root, 'consolidation-create', {'title': 'Review authentication'})


def propose(root, view, key='authentication', page='overview', citation=None, body=None):
    citation = citation or next(c for c in view['sources'] if c['path'] == 'docs/design.md')
    value = {'job': view['job']['id'], 'page_type': page, 'key': key, 'kind': 'summary',
             'title': 'Authentication summary', 'body': body or 'A passphrase is required [S1].',
             'reason': 'Record the source-supported design', 'citations': [citation]}
    return mc.dispatch(root, 'consolidation-propose', value)


def decide(root, view, indices=(0,), action='accept', request='request-1'):
    return mc.dispatch(root, 'consolidation-decide', {'job': view['job']['id'],
                       'ids': [view['job']['proposals'][i]['id'] for i in indices],
                       'decision': action, 'request_id': request})


def sections(root):
    with database(root) as db:
        return get(db, wiki_review.STATE, {})


def test_no_implicit_migration(project):
    assert mc.dispatch(project, 'consolidation-view')['schema'] == 1
    with database(project) as db:
        before = list(map(tuple, db.execute('SELECT * FROM jobs')))
    with pytest.raises(KnowledgeError, match='migration_required'):
        create(project)
    with database(project) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
        assert before == list(map(tuple, db.execute('SELECT * FROM jobs')))


def test_read_absent_does_not_create(tmp_path):
    assert not mc.dispatch(tmp_path, 'consolidation-view')['enabled']
    assert not (tmp_path / '.controlcoding').exists()


def test_atomic_selection_replay_and_originals_unchanged(migrated):
    before = (migrated / 'docs/design.md').read_bytes()
    view = propose(migrated, create(migrated))
    view = propose(migrated, view, key='second')
    assert all(p['status'] == 'pending' for p in view['job']['proposals'])
    accepted = decide(migrated, view, (0, 1))
    assert len(accepted['job']['receipt']['patches']) == 2
    assert all(p['status'] == 'accepted' for p in accepted['job']['proposals'])
    with database(migrated) as db:
        epoch = get(db, wiki_review.EPOCH)
        assert db.execute('SELECT count(*) FROM memory_claims').fetchone()[0] == 2
    decide(migrated, view, (0, 1))
    with database(migrated) as db:
        assert get(db, wiki_review.EPOCH) == epoch
        assert db.execute('SELECT count(*) FROM consolidation_events').fetchone()[0] == 1
    with pytest.raises(KnowledgeError, match='request_conflict'):
        decide(migrated, view, (0,))
    assert (migrated / 'docs/design.md').read_bytes() == before
    assert not service.query(migrated, 'passphrase required summary', False)['citations'][0]['source'].startswith('wiki:')


def test_pending_persists_after_new_connection_and_rejection(migrated):
    view = propose(migrated, create(migrated))
    restored = service.dispatch(migrated, 'consolidation-view', {'job': view['job']['id']})
    assert restored['job']['proposals'] == view['job']['proposals']
    rejected = decide(migrated, restored, action='reject')
    assert rejected['job']['state'] == 'rejected'
    assert not sections(migrated)


def test_source_change_without_explicit_refresh_rejects_accept(migrated):
    view = propose(migrated, create(migrated))
    (migrated / 'docs/design.md').write_text('# New design\nUse a token instead.\n', encoding='utf-8')
    with pytest.raises(KnowledgeError, match='context_changed'):
        decide(migrated, view)
    assert not sections(migrated)
    current = mc.dispatch(migrated, 'consolidation-view', {'job': view['job']['id']})
    assert current['job']['stale'] and not current['job']['proposals'][0]['eligible']
    assert decide(migrated, current, action='reject')['job']['state'] == 'rejected'


def test_duplicate_targets_and_foreign_ids_are_not_partially_applied(migrated):
    view = propose(migrated, create(migrated))
    view = propose(migrated, view)
    with pytest.raises(KnowledgeError, match='target_conflict'):
        decide(migrated, view, (0, 1))
    other = propose(migrated, create(migrated))
    with pytest.raises(KnowledgeError, match='proposal_unavailable'):
        mc.dispatch(migrated, 'consolidation-decide', {'job': view['job']['id'],
                    'ids': [view['job']['proposals'][0]['id'], other['job']['proposals'][0]['id']],
                    'decision': 'accept', 'request_id': 'mixed-jobs'})
    assert not sections(migrated)


def test_selected_batch_rolls_back_on_section_quota(migrated):
    view = create(migrated)
    with database(migrated) as db:
        with db:
            wiki_review.commit_sections(db, {'overview': {str(i): {'title': 'Existing', 'body': 'Keep', 'dependencies': []}
                                                        for i in range(19)}}, [])
    view = create(migrated)
    view = propose(migrated, view)
    view = propose(migrated, view, key='second')
    before = sections(migrated)
    with pytest.raises(KnowledgeError, match='section_limit'):
        decide(migrated, view, (0, 1))
    assert sections(migrated) == before
    with database(migrated) as db:
        assert db.execute('SELECT count(*) FROM memory_claims').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM consolidation_events').fetchone()[0] == 0


def test_undo_restores_all_cumulative_published_targets(migrated):
    view = propose(migrated, create(migrated))
    view = propose(migrated, view, key='timeline', page='timeline')
    first = decide(migrated, view, (0,))
    second = decide(migrated, first, (1,), request='request-2')
    assert len(second['job']['receipt']['patches']) == 2
    value = {'job': view['job']['id'], 'request_id': 'undo-1'}
    undone = mc.dispatch(migrated, 'consolidation-undo', value)
    assert undone['job']['receipt']['undone']
    assert not sections(migrated)['overview'] and not sections(migrated)['timeline']
    assert mc.dispatch(migrated, 'consolidation-undo', value)['job']['receipt']['undone']
    with database(migrated) as db:
        assert not db.execute('SELECT * FROM memory_claims').fetchall()


def test_repeated_target_keeps_original_before_and_undo_restores_human(migrated):
    view = create(migrated)
    cite = next(c for c in view['sources'] if c['path'] == 'docs/design.md')
    initial = wiki_review.dispatch(migrated, 'wiki-review-propose', {'page_type': 'overview', 'key': 'authentication',
        'title': 'Human text', 'body': 'Original human prose [S1].', 'citations': [cite],
        'base_revision': service.page(migrated, 'wiki:review:overview')['revision']})
    wiki_review.dispatch(migrated, 'wiki-review-decide', {'id': initial['id'], 'decision': 'accept'})
    baseline = sections(migrated)
    view = decide(migrated, propose(migrated, create(migrated)))
    view = propose(migrated, view, body='A revised manual summary [S1].')
    assert view['job']['proposals'][-1]['before']['body'] == 'A passphrase is required [S1].'
    view = decide(migrated, view, (1,), request='second-accept')
    mc.dispatch(migrated, 'consolidation-undo', {'job': view['job']['id'], 'request_id': 'undo'})
    assert sections(migrated) == baseline


def test_later_manual_edit_blocks_undo(migrated):
    view = decide(migrated, propose(migrated, create(migrated)))
    with database(migrated) as db:
        with db:
            approved = get(db, wiki_review.STATE)
            approved['overview']['authentication']['body'] = 'Later human correction'
            wiki_review.commit_sections(db, approved, [])
    with pytest.raises(KnowledgeError, match='context_changed'):
        mc.dispatch(migrated, 'consolidation-undo', {'job': view['job']['id'], 'request_id': 'undo'})
    assert sections(migrated)['overview']['authentication']['body'] == 'Later human correction'


def test_forged_source_anchor_cannot_be_proposed(migrated):
    view = create(migrated)
    cite = dict(view['sources'][0], excerpt='Invented evidence')
    with pytest.raises(KnowledgeError, match='source_changed'):
        propose(migrated, view, citation=cite)


def test_forget_purges_private_job_receipts_claims_and_prevents_replay(migrated):
    service.conversation(migrated, record())
    service.reconcile(migrated)
    view = create(migrated)
    cite = next(c for c in view['sources'] if c['source'] == 'conversation:chat-1')
    # Pin the actual private line, not only the introductory provenance sentence.
    with database(migrated) as db:
        source = db.execute("SELECT body FROM sources WHERE id='conversation:chat-1'").fetchone()[0]
    lines = source.splitlines()
    n = next(i for i, text in enumerate(lines) if 'Private apricot phrase' in text)
    cite.update(line=n+1, end_line=n+1, excerpt=lines[n])
    view = decide(migrated, propose(migrated, view, citation=cite, body='Private apricot phrase [S1].'))
    service.dispatch(migrated, 'forget', 'chat-1')
    purged = mc.dispatch(migrated, 'consolidation-view', {'job': view['job']['id']})
    assert purged['job']['state'] == 'purged' and not purged['job']['proposals']
    with database(migrated) as db:
        for table in ('consolidation_jobs', 'consolidation_events', 'consolidation_proposals',
                      'memory_claims', 'memory_claim_revisions', 'wiki', 'wiki_history'):
            assert 'Private apricot phrase' not in json.dumps([tuple(r) for r in db.execute('SELECT * FROM ' + table)])
    with pytest.raises(KnowledgeError):
        mc.dispatch(migrated, 'consolidation-undo', {'job': view['job']['id'], 'request_id': 'undo'})


def test_forget_removes_replaced_claim_lineage_and_backup_stays_usable(migrated, tmp_path):
    from cc_memory_lib import knowledge_backup as backups
    service.conversation(migrated, record())
    private = create(migrated)
    cite = next(c for c in private['sources'] if c['source'] == 'conversation:chat-1')
    decide(migrated, propose(migrated, private, citation=cite, page='workflow', body='Private summary [S1].'))
    # Same claim, now bound only to a document: historical privacy still matters.
    public = decide(migrated, propose(migrated, create(migrated), page='workflow'))
    assert sections(migrated)['workflow']['authentication']['dependencies'][0]['source'] != cite['source']
    service.dispatch(migrated, 'forget', 'chat-1')
    assert not sections(migrated)['workflow']
    with database(migrated) as db:
        assert not db.execute('SELECT * FROM memory_claims').fetchall()
        assert not db.execute('SELECT * FROM memory_claim_revisions').fetchall()
        assert not db.execute("SELECT * FROM wiki_history WHERE id='wiki:review:workflow'").fetchall()
    backup = tmp_path / 'forgotten.ccmemory'
    backups.backup(migrated, backup)
    restored = tmp_path / 'restored'
    restored.mkdir()
    backups.restore(restored, backup)
    view = mc.dispatch(restored, 'consolidation-view', {'job': public['job']['id']})
    assert view['job']['state'] == 'purged' and view['job']['receipt'] is None


def test_deleted_file_leaves_stale_review_exportable(migrated, tmp_path):
    from cc_memory_lib import knowledge_backup as backups
    view = propose(migrated, create(migrated))
    (migrated / 'docs/design.md').unlink()
    service.reconcile(migrated)
    path = tmp_path / 'stale.ccmemory'
    backups.backup(migrated, path)
    target = tmp_path / 'restored'
    target.mkdir()
    backups.restore(target, path)
    restored = mc.dispatch(target, 'consolidation-view', {'job': view['job']['id']})
    assert restored['job']['stale'] and not restored['job']['proposals'][0]['eligible']


def test_proposal_normalizes_padded_text_before_storage(migrated):
    view = propose(migrated, create(migrated), body=' ' * 1000 + 'A passphrase is required [S1].' + ' ' * 1000)
    assert view['job']['proposals'][0]['body'] == 'A passphrase is required [S1].'
