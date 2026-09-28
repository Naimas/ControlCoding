"""Bounded server-side search and navigation never mix library revisions."""
import pytest
from test_cc_knowledge import project, record
from cc_memory_lib import knowledge_service as service
from cc_memory_lib.knowledge_store import KnowledgeError


def read(root, kind='sources', query='', offset=0, snapshot=None):
    return service.dispatch(root, 'library', dict(kind=kind, query=query, offset=offset, snapshot=snapshot))


def test_three_windows_have_explicit_totals_and_no_missing_or_duplicate_sources(project):
    for number in range(123):
        (project / 'docs' / f'item-{number:03}.md').write_text(f'# Document {number}\nContent.', encoding='utf-8')
    service.reconcile(project)
    first = read(project)
    assert first['total'] == 125 and len(first['rows']) == 50
    second = read(project, offset=first['next_offset'], snapshot=first['snapshot'])
    third = read(project, offset=second['next_offset'], snapshot=second['snapshot'])
    ids = [row['id'] for page in (first, second, third) for row in page['rows']]
    assert len(ids) == len(set(ids)) == 125
    assert len(third['rows']) == 25 and third['next_offset'] is None
    assert {r['id'] for r in service.catalog(project)['sources']} == set(ids)


def test_literal_unicode_search_and_query_bound_cursor(project):
    (project / 'docs/extra.md').write_text('# Stra\u00dfe \u00c9TUDES 100%\nContent.', encoding='utf-8')
    service.reconcile(project)
    assert read(project, query='STRASSE')['total'] == 1
    assert read(project, query='\u00e9tudes')['total'] == 1
    assert read(project, query='%')['total'] == 1
    assert read(project, query="' OR 1=1 --")['total'] == 0
    page = read(project)
    with pytest.raises(KnowledgeError, match='library_snapshot_changed'):
        read(project, query='changed query', snapshot=page['snapshot'])


def test_source_changes_and_project_changes_reject_old_navigation(project, tmp_path):
    page = read(project)
    (project / 'docs/design.md').write_text('# Updated design\nChanged.', encoding='utf-8')
    service.reconcile(project)
    with pytest.raises(KnowledgeError, match='library_snapshot_changed'):
        read(project, offset=50, snapshot=page['snapshot'])
    other = tmp_path / 'other'
    other.mkdir()
    service.configure(other, service.DEFAULT.copy())
    service.reconcile(other)
    with pytest.raises(KnowledgeError, match='library_snapshot_changed'):
        read(other, snapshot=page['snapshot'])


def test_chat_edits_invalidate_cursor_without_waiting_for_reconciliation(project):
    value = record()
    service.conversation(project, value)
    page = read(project, 'conversations')
    assert page['rows'][0]['id'] == value['id']
    value['summary'] = 'New summary'
    service.conversation(project, value)
    with pytest.raises(KnowledgeError, match='library_snapshot_changed'):
        read(project, 'conversations', snapshot=page['snapshot'])
    assert read(project, 'conversations', 'New summary')['total'] == 1


@pytest.mark.parametrize('value', [None, {}, {'kind': 'sources', 'query': '', 'offset': True, 'snapshot': None},
                                  {'kind': 'sources', 'query': '', 'offset': 1, 'snapshot': None}])
def test_invalid_navigation_is_rejected(project, value):
    with pytest.raises(KnowledgeError):
        service.dispatch(project, 'library', value)
