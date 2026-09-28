"""Isolated query scale, bounded vector memory and snapshot isolation.

Synthetic pre-indexed rows test retrieval, not the public ingestion capacity.
"""
from pathlib import Path
import sys
import tracemalloc

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cc_memory_lib import knowledge_service as service
from cc_memory_lib.knowledge_store import database, put, KnowledgeError
from cc_memory_lib.knowledge_semantic import encode_vector


class Embedding:
    def __init__(self, dimensions=2):
        self.vector = [1.] + [0.] * (dimensions - 1)

    def pin(self):
        return 'fixture@one'

    def embed(self, texts):
        return [self.vector for _ in texts]


def seed(root, count, dimensions):
    service.configure(root, {**service.DEFAULT, 'scopes': ['project']})
    vector = encode_vector([1.] + [0.] * (dimensions - 1))
    with database(root) as db, db:
        db.execute("INSERT INTO sources VALUES('fixture','docs/fixture.md','Fixture','rev','fixture','document',0,'now')")
        db.executemany('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?)',
                       ((f'{i:08d}', 'fixture', 'rev', i + 1, i + 1,
                         'common evidence ' + ('needlexylophone' if i == count - 1 else 'ordinary'),
                         vector, 'fixture@one') for i in range(count)))
        put(db, 'needs_reconcile', False)
        put(db, 'generation', 1)
        put(db, 'embedding_identity', 'fixture@one')


def test_50000_passages_can_find_final_record_without_loading_vectors(tmp_path):
    seed(tmp_path, 50000, 128)
    result = service.query(tmp_path, 'needlexylophone', False)
    assert len(result['citations']) == 1
    assert result['citations'][0]['line'] == 50000
    assert result['warning'] is None


def test_high_dimensional_query_does_not_materialize_vector_corpus(tmp_path):
    seed(tmp_path, 5000, 2048)  # 39 MiB of vectors alone.
    tracemalloc.start()
    try:
        result = service.query(tmp_path, 'common', True, Embedding(2048))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 20 * 1024 * 1024
    assert result['mode'].startswith('neural') and result['warning'] is None
    assert [c['line'] for c in result['citations']] == list(range(1, 11))


@pytest.mark.parametrize('change', ['generation', 'policy', 'dirty', 'model'])
def test_inference_releases_lease_but_rejects_changed_snapshot(tmp_path, change):
    seed(tmp_path, 2, 2)

    class Mutating(Embedding):
        def embed(self, texts):
            with database(tmp_path) as db, db:
                if change == 'generation':
                    put(db, 'generation', 2)
                elif change == 'policy':
                    put(db, 'policy', {**service.DEFAULT, 'scopes': ['work']})
                elif change == 'dirty':
                    put(db, 'needs_reconcile', True)
                else:
                    put(db, 'embedding_identity', 'other@two')
            return super().embed(texts)

    with pytest.raises(KnowledgeError, match='query_context_changed'):
        service.query(tmp_path, 'common', True, Mutating())


def test_dimension_failure_keeps_exact_lexical_ranking(tmp_path):
    seed(tmp_path, 20, 2)
    with database(tmp_path) as db, db:
        db.execute('UPDATE chunks SET vector=? WHERE id=?', (encode_vector([1., 0., 0.]), '00000019'))
    expected = service.query(tmp_path, 'needlexylophone', False)
    result = service.query(tmp_path, 'needlexylophone', True, Embedding())
    assert result['citations'] == expected['citations']
    assert result['mode'].startswith('lexical')
    assert 'embedding_dimension_changed' in result['warning']


def test_deleted_and_old_revision_rows_are_never_evidence(tmp_path):
    seed(tmp_path, 5, 2)
    with database(tmp_path) as db, db:
        db.execute("UPDATE chunks SET revision='old' WHERE id='00000004'")
    assert not service.query(tmp_path, 'needlexylophone', False)['citations']
    with database(tmp_path) as db, db:
        db.execute('UPDATE sources SET deleted=1')
    assert not service.query(tmp_path, 'common', True, Embedding())['citations']
