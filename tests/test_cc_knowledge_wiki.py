"""Topic wiki, source-bound drafts, privacy and concurrent inference regressions."""
import json

import pytest

from test_cc_knowledge import project, record, NeuralFixture
from cc_memory_lib import knowledge_service as service
from cc_memory_lib.knowledge_store import database, KnowledgeError


def draft(project):
    packet = service.query(project, 'passphrase', False)
    return {'title': 'Authentication summary', 'body': 'A secret passphrase is used [S1].',
            'citations': packet['citations'], 'generation': packet['generation'],
            'provider': 'fixture', 'model': 'fixture'}


def test_save_current_ten_passage_query_as_draft(project):
    for i in range(10):
        (project / 'docs' / f'cap-{i}.md').write_text(f'# Evidence {i}\nCapword claim {i}.', encoding='utf-8')
    service.reconcile(project)
    packet = service.query(project, 'Capword', False)
    assert len(packet['citations']) == 10
    value = {'title': 'Ten anchors', 'body': 'A recorded claim [S10].',
             'citations': packet['citations'], 'generation': packet['generation'],
             'provider': 'fixture', 'model': 'fixture'}
    saved = service.dispatch(project, 'wiki-draft', value)
    assert len(saved['dependencies']) == 10 and not saved['stale']


def test_topics_are_explained_cited_and_excluded_from_retrieval(project):
    catalog = service.catalog(project)
    topics = catalog['wiki']
    assert topics and all(t['kind'] == 'topic' and not t['stale'] for t in topics)
    page = service.page(project, 'wiki:topic:architecture')
    assert 'Matched "design"' in page['body']
    assert page['dependencies'][0]['path'] == 'docs/design.md'
    assert page['dependencies'][0]['line'] == 2
    assert not service.dispatch(project, 'wiki-lint')['issues']
    assert all(not c['source'].startswith('wiki:') for c in service.query(project, 'passphrase', False)['citations'])


def test_topic_rebuild_preserves_notes_and_historical_freshness(project):
    identifier = 'wiki:topic:architecture'
    old = service.page(project, identifier)
    service.notes(project, identifier, 'Human note survives')
    (project / 'docs/design.md').write_text('# Authentication\nToken-based design.\n', encoding='utf-8')
    service.reconcile(project)
    current = service.page(project, identifier)
    assert current['notes'] == 'Human note survives' and not current['stale']
    assert 'Token-based' in current['body'] and len(current['history']) == 2
    assert service.page(project, identifier, old['revision'])['stale']
    (project / 'docs/design.md').unlink()
    service.reconcile(project)
    (project / 'docs/design.md').write_text('# Authentication\nReturned source.\n', encoding='utf-8')
    service.reconcile(project)
    assert service.page(project, identifier)['notes'] == 'Human note survives'


def test_saved_draft_is_invalidated_not_silently_rewritten(project):
    value = draft(project)
    saved = service.dispatch(project, 'wiki-draft', value)
    assert saved['id'].startswith('wiki:draft:') and not saved['stale']
    assert 'Unreviewed AI synthesis' in saved['body']
    (project / 'docs/design.md').write_text('# Authentication\nOnly tokens are accepted.\n', encoding='utf-8')
    service.reconcile(project)
    after = service.page(project, saved['id'])
    assert after['stale'] and after['body'] == saved['body']
    assert any(i['page'] == saved['id'] for i in service.dispatch(project, 'wiki-lint')['issues'])
    with pytest.raises(KnowledgeError, match='wiki_context_changed'):
        service.dispatch(project, 'wiki-draft', value)
    assert not service.query(project, 'passphrase', False)['citations']


@pytest.mark.parametrize('mutation,code', [
    ('unknown', 'invalid_wiki_citation'), ('uncited', 'wiki_uncited_paragraph'),
    ('forged', 'wiki_context_changed'), ('duplicate', 'invalid_wiki_citation'),
    ('heading', 'wiki_uncited_paragraph'),
])
def test_draft_rejects_unbound_evidence(project, mutation, code):
    value = draft(project)
    if mutation == 'unknown':
        value['body'] = 'Claim [S99].'
    elif mutation == 'uncited':
        value['body'] += '\n\nAnother factual assertion without evidence.'
    elif mutation == 'forged':
        value['citations'][0]['excerpt'] = 'Invented evidence'
    elif mutation == 'heading':
        value['body'] += '\n\n# Heading\nAn unsupported statement below the heading.'
    else:
        value['citations'].append(value['citations'][0])
    with pytest.raises(KnowledgeError, match=code):
        service.dispatch(project, 'wiki-draft', value)


def test_forget_purges_topic_and_draft_transcript_history(project):
    service.conversation(project, record())
    service.reconcile(project)
    packet = service.query(project, 'apricot', False)
    saved = service.dispatch(project, 'wiki-draft', {**draft(project), 'body': 'Private apricot phrase [S1].',
                                                    'citations': packet['citations']})
    assert 'apricot' in service.page(project, 'wiki:topic:sessions')['body']
    service.dispatch(project, 'forget', 'chat-1')
    with database(project) as db:
        for table in ('wiki', 'wiki_history'):
            assert not any('apricot' in r['body'] for r in db.execute('SELECT body FROM ' + table))
        assert not db.execute('SELECT 1 FROM wiki WHERE id=?', (saved['id'],)).fetchone()


def test_query_inference_releases_lock_and_rejects_changed_snapshot(project):
    adapter = NeuralFixture()
    service.index(project, adapter)
    def change_while_embedding(texts):
        # This would fail with knowledge_busy if inference held the writer lock.
        service.conversation(project, record())
        return [[1., 0.]]
    adapter.embed = change_while_embedding
    with pytest.raises(KnowledgeError, match='query_context_changed'):
        service.query(project, 'login', True, adapter)
    assert service.read_conversation(project, 'chat-1')['turns']


def test_long_line_anchors_and_foreign_project_draft(project, tmp_path):
    (project / 'docs/long.md').write_text('# Long source\n' + 'passphrase ' * 500, encoding='utf-8')
    service.reconcile(project)
    assert not service.dispatch(project, 'wiki-lint')['issues']
    other = tmp_path / 'other'
    other.mkdir()
    service.configure(other, service.DEFAULT.copy())
    service.reconcile(other)
    with pytest.raises(KnowledgeError, match='wiki_context_changed'):
        service.dispatch(other, 'wiki-draft', draft(project))
