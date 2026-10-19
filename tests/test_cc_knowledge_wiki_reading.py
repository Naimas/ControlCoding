"""Readable wiki keeps exact source identity, historical excerpts and scope."""
import pytest
from test_cc_knowledge import project
from cc_memory_lib import knowledge_service as service
from cc_memory_lib.knowledge_store import KnowledgeError, database


def test_reading_projection_preserves_body_and_historical_citation(project):
    old = service.page(project, 'wiki:topic:architecture')
    card = old['reading']['sources'][0]
    assert old['reading']['kind'] == 'topic' and 'contract' in old['reading']['purpose']
    assert card['title'] == 'Authentication' and card['readable']
    assert card['availability'] == 'current' and card['physicalPath'] == 'docs/design.md'
    assert card['excerpt'] in old['body'] and 'Matched "design"' in card['reason']
    service.notes(project, old['id'], 'Keep this note')
    (project / 'docs/design.md').write_text('# New title\nNew authentication rules.\n', encoding='utf-8')
    service.reconcile(project)
    historical = service.page(project, old['id'], old['revision'])
    after = historical['reading']['sources'][0]
    assert historical['body'] == old['body'] and historical['notes'] == 'Keep this note'
    assert after['revision'] == card['revision'] and after['excerpt'] == card['excerpt']
    assert after['availability'] == 'source_changed' and after['title'] != 'New title'
    (project / 'docs/design.md').unlink()
    service.reconcile(project)
    # A retained AI draft still displays its source as missing.
    with database(project) as db:
        from cc_memory_lib.knowledge_wiki_reading import presentation
        historical['issues'] = [{'source': card['id'], 'code': 'source_missing'}]
        assert presentation(project, db, historical)['sources'][0]['availability'] == 'source_missing'


def test_relative_link_lookup_uses_index_not_catalog_window(project):
    source = next(s for s in service.catalog(project)['sources'] if s['path'] == 'README.md')
    result = service.dispatch(project, 'graph-source', {'from': source['id'], 'href': 'docs/design.md#authentication'})
    assert result['path'] == 'docs/design.md' and result['title'] == 'Authentication'
    with database(project) as db:
        db.executemany('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?)',
                       [(f's{i}', f'aaa/{i:04}.md', f'Plan {i}', 'a'*64, 'body', 'document', 0, 'now') for i in range(230)])
        db.commit()
    window = service.dispatch(project, 'graph-view', dict(topic=None, query='', offset=0, snapshot=None, focus=None))
    assert result['id'] not in {s['id'] for s in window['sources']}
    assert service.dispatch(project, 'graph-source', {'from': source['id'], 'href': './docs/design.md'})['id'] == result['id']
    (project / 'private.md').write_text('Not selected', encoding='utf-8')
    with pytest.raises(KnowledgeError, match='source_unavailable'):
        service.dispatch(project, 'graph-source', {'from': source['id'], 'href': 'private.md'})


def test_current_and_older_passages_of_same_source_have_separate_status(project):
    old = service.page(project, 'wiki:topic:architecture')
    (project / 'docs/design.md').write_text('# Authentication\nUpdated rules.\n', encoding='utf-8')
    service.reconcile(project)
    current = service.page(project, old['id'])
    current['dependencies'] += old['dependencies']
    with database(project) as db:
        from cc_memory_lib.knowledge_wiki_reading import presentation
        cards = presentation(project, db, current)['sources']
    assert cards[0]['id'] == cards[1]['id']
    assert [c['availability'] for c in cards] == ['current', 'source_changed']


@pytest.mark.parametrize('href', ['https://example.org', 'file:///secret.md', '//server/share.md',
    'C:/secret.md', '../../secret.md', '%2e%2e/secret.md', '%2fsecret.md', '%5csecret.md', 'docs/design.md?raw=1',
    'data:text/plain,hello', 'javascript:alert(1)', '%00bad.md'])
def test_links_cannot_expand_scope(project, href):
    source = next(s for s in service.catalog(project)['sources'] if s['path'] == 'README.md')
    with pytest.raises(KnowledgeError, match='unsupported_link'):
        service.dispatch(project, 'graph-source', {'from': source['id'], 'href': href})


def test_contained_physical_link_resolution(project, monkeypatch):
    import cc_memory_lib.knowledge_graph as graph
    original = graph.physical_source_path
    monkeypatch.setattr(graph, 'physical_source_path', lambda root, name, kind='': 'cc/' + name if name.startswith('.controlwork') else original(root, name, kind))
    with database(project) as db:
        db.executemany('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?)', [
            ('work1', '.controlwork/docs/one.md', 'One', 'a'*64, 'one', 'document', 0, 'now'),
            ('work2', '.controlwork/docs/two.md', 'Two', 'b'*64, 'two', 'document', 0, 'now')])
        db.commit()
    assert graph.source(project, {'from': 'work1', 'href': 'two.md'})['id'] == 'work2'
    assert graph.source(project, {'from': 'work1', 'href': '../../../docs/design.md'})['path'] == 'docs/design.md'
