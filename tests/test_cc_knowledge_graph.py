"""Large archive graph is a bounded server projection, not a client catalog."""
import pytest
from test_cc_knowledge import project, record
from cc_memory_lib import knowledge_service as service
from cc_memory_lib.knowledge_store import database, KnowledgeError


def read(root, **changes):
    return service.dispatch(root, 'graph-view', dict(dict(topic=None, query='', focus=None,
                            offset=0, snapshot=None), **changes))


def test_large_graph_search_neighbors_and_snapshot(project):
    with database(project) as db:
        db.executemany('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?)',
                       [(f's{i}', f'large/{i:04}.md', f'Plan {i}', 'a'*64, 'body', 'document', 0, 'now') for i in range(5000)])
        db.execute('INSERT INTO edges VALUES(?,?,?,?)', ('s4999', 's4000', 'a'*64, 'reference'))
        db.commit()
    first = read(project)
    assert len(first['sources']) == 200 and first['graph']['source_total'] == 5002
    second = service.dispatch(project, 'graph-view', dict(topic=None, query='', focus=None,
                              offset=200, snapshot=first['graph']['snapshot']))
    assert not {r['id'] for r in first['sources']} & {r['id'] for r in second['sources']}
    result = read(project, query='Plan 4999')
    assert [r['id'] for r in result['sources']] == ['s4999']
    neighbors = read(project, query='Plan 4999', focus='s4999')
    assert {r['id'] for r in neighbors['sources']} == {'s4999', 's4000'}
    assert len(neighbors['edges']) == 1
    assert service.dispatch(project, 'graph-source', 's4999')['path'] == 'large/4999.md'
    service.conversation(project, record())
    with pytest.raises(KnowledgeError, match='graph_snapshot_changed'):
        service.dispatch(project, 'graph-view', dict(topic=None, query='', focus=None,
                         offset=200, snapshot=first['graph']['snapshot']))


def test_additive_indexes_exist_and_support_source_deletion(project):
    with database(project) as db:
        names = {r[1] for r in db.execute('PRAGMA index_list(chunks)')}
        assert {'cc_chunks_source', 'cc_chunks_pending'} <= names
        assert 'cc_chunks_source' in str(list(map(tuple, db.execute(
            'EXPLAIN QUERY PLAN DELETE FROM chunks WHERE source=?', ('absent',)))))


def test_storage_page_limit_rolls_back_without_stranding_archive(project, monkeypatch):
    from cc_memory_lib import knowledge_store as storage
    path = project/'.controlcoding/knowledge/knowledge.db'
    before = service.catalog(project)
    monkeypatch.setattr(storage, 'MAX_DATABASE_BYTES', path.stat().st_size + 8192)
    with pytest.raises(KnowledgeError, match='knowledge_storage_limit'):
        with database(project) as db:
            with db:
                db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?)',
                           ('large', 'large.md', 'Too large', 'a'*64, 'x'*1048576, 'document', 0, 'now'))
    assert service.catalog(project) == before
