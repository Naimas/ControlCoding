"""Human document categories are bounded, private and bound to source versions."""
import pytest
from test_cc_knowledge import project, record
from cc_memory_lib import knowledge_service as service
from cc_memory_lib.knowledge_store import KnowledgeError, database, get, put
from cc_memory_lib.knowledge_document_types import classify
from cc_memory_lib.knowledge_document_review import PREFIX, reviews


def library(root, role=None, snapshot=None):
    return service.dispatch(root, 'library', dict(kind='sources', query='', offset=0, snapshot=snapshot, document_type=role))


def source(root):
    return next(r for r in library(root)['rows'] if r['path'] == 'docs/design.md')


def request(row, role='implementation_plan'):
    return dict(source=row['id'], revision=row['revision'], expected=row['document_type']['review_token'],
                type=role, reason='The document describes the implementation sequence.')


def test_collection_is_not_purpose_and_work_session_is_not_chat():
    value = classify(dict(kind='sources', path='.controlwork/memory/sources/evidence.md', title='Verification evidence'))
    assert value['key'] == 'verification' and value['basis'] == 'suggested'
    assert classify(dict(kind='session', path='sessions/work.json', title='Session'))['key'] == 'session_record'
    assert classify(dict(kind='conversation', path='conversation/x', title='Session'))['key'] == 'conversation'


def test_confirmation_changes_all_read_views_and_counts_without_source_writes(project):
    before = (project / 'docs/design.md').read_bytes()
    old_library = library(project)
    row = source(project)
    saved = service.dispatch(project, 'document-classify', request(row))
    assert saved['document_type']['basis'] == 'confirmed'
    assert saved['document_type']['key'] == 'implementation_plan'
    assert (project / 'docs/design.md').read_bytes() == before
    assert library(project, 'implementation_plan')['total'] == 1
    assert library(project, 'design')['total'] == 0
    with pytest.raises(KnowledgeError, match='library_snapshot_changed'):
        library(project, snapshot=old_library['snapshot'])
    assert service.page(project, row['id'])['reading']['sources'][0]['document_type'] == saved['document_type']
    assert service.dispatch(project, 'graph-source', row['id'])['document_type'] == saved['document_type']
    with pytest.raises(KnowledgeError, match='document_classification_changed'):
        service.dispatch(project, 'document-classify', request(row))


def test_changed_source_expires_confirmation_and_reset_restores_automatic(project):
    row = source(project)
    service.notes(project, row['id'], 'Keep my note')
    service.dispatch(project, 'document-classify', request(row))
    approved = source(project)
    (project / 'docs/design.md').write_text('# Authentication\nChanged design text.', encoding='utf-8')
    service.reconcile(project)
    current = source(project)
    assert current['document_type']['key'] == 'design'
    assert current['document_type']['review_stale'] and current['document_type']['previous_label'] == 'Implementation plan'
    assert service.page(project, row['id'])['notes'] == 'Keep my note'
    with pytest.raises(KnowledgeError, match='document_classification_changed'):
        service.dispatch(project, 'document-classify', request(approved))
    service.dispatch(project, 'document-classify', request(current, None))
    assert not source(project)['document_type']['review_stale']
    with database(project) as db:
        assert get(db, PREFIX + row['id']) is None


@pytest.mark.parametrize('change', [{'reason': ''}, {'reason': 'x'*601}, {'type': 'unknown'}, {'type': []}, {'expected': 'bad'}])
def test_invalid_review_is_atomic(project, change):
    row = source(project)
    with pytest.raises(KnowledgeError, match='invalid_document_classification'):
        service.dispatch(project, 'document-classify', {**request(row), **change})
    assert source(project)['document_type']['basis'] == 'suggested'


def test_forget_purges_private_review_and_chat_kind_cannot_be_relabelled(project):
    service.conversation(project, record())
    service.reconcile(project)
    row = next(r for r in library(project)['rows'] if r['kind'] == 'conversation')
    with pytest.raises(KnowledgeError, match='document_classification_record_kind'):
        service.dispatch(project, 'document-classify', request(row))
    service.dispatch(project, 'document-classify', request(row, 'conversation'))
    service.dispatch(project, 'forget', 'chat-1')
    with database(project) as db:
        assert get(db, PREFIX + row['id']) is None


def test_missing_source_and_invalid_restored_metadata_fail_closed(project):
    row = source(project)
    (project / 'docs/design.md').unlink()
    service.reconcile(project)
    with pytest.raises(KnowledgeError, match='source_unavailable'):
        service.dispatch(project, 'document-classify', request(row))
    with database(project) as db:
        with db:
            put(db, PREFIX + 'invalid', {'reason': 'unvalidated value'})
        with pytest.raises(KnowledgeError, match='invalid_document_classification_state'):
            reviews(db)


def test_backup_restore_retains_category_and_requires_reconciliation(project, tmp_path):
    from cc_memory_lib.knowledge_backup import backup, restore
    row = source(project)
    service.dispatch(project, 'document-classify', request(row))
    archive = tmp_path / 'categories.ccmemory'
    backup(project, archive)
    target = tmp_path / 'restored'
    (target / 'docs').mkdir(parents=True)
    (target / 'README.md').write_bytes((project / 'README.md').read_bytes())
    (target / 'docs/design.md').write_bytes((project / 'docs/design.md').read_bytes())
    restore(target, archive)
    with pytest.raises(KnowledgeError, match='document_classification_refresh_required'):
        service.dispatch(target, 'document-classify', request(source(project)))
    service.reconcile(target)
    assert source(target)['document_type']['basis'] == 'confirmed'
    assert source(target)['document_type']['key'] == 'implementation_plan'


def test_review_count_is_bounded(project, monkeypatch):
    from cc_memory_lib import knowledge_document_review as review
    monkeypatch.setattr(review, 'MAX_REVIEWS', 1)
    service.dispatch(project, 'document-classify', request(source(project)))
    other = next(r for r in library(project)['rows'] if r['path'] == 'README.md')
    with pytest.raises(KnowledgeError, match='document_classification_limit'):
        service.dispatch(project, 'document-classify', request(other, 'usage_guide'))
    assert source(project)['document_type']['basis'] == 'confirmed'
