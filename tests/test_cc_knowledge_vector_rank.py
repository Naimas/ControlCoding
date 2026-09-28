"""Bounded vector scoring retains validation before any batch result is emitted."""
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from cc_memory_lib.knowledge_semantic import cosine, decode_vector
from cc_memory_lib.knowledge_store import KnowledgeError
from cc_memory_lib.knowledge_vector_rank import scores


def binary(*values):
    return struct.pack('<' + 'f' * len(values), *values)


@pytest.mark.parametrize('bad', [binary(float('nan'), 0), binary(float('inf'), 0),
                                      binary(float('-inf'), 0)])
def test_numpy_rejects_nonfinite_vector_before_first_batch_result(bad):
    pytest.importorskip('numpy')
    rows = [{'id': 'valid', 'vector': binary(1, 0)}, {'id': 'invalid', 'vector': bad}]
    with pytest.raises(KnowledgeError, match='invalid_embeddings'):
        next(scores(rows, [1., 0.]))


def test_numpy_rejects_dimension_change_before_first_batch_result():
    pytest.importorskip('numpy')
    rows = [{'id': 'valid', 'vector': binary(1, 0)},
            {'id': 'wrong-dimension', 'vector': binary(1, 0, 0)}]
    with pytest.raises(KnowledgeError, match='embedding_dimension_changed'):
        next(scores(rows, [1., 0.]))


def test_numpy_and_scalar_fallback_preserve_exact_scores(monkeypatch):
    pytest.importorskip('numpy')
    rows = [{'id': 'first', 'vector': binary(1, 0)},
            {'id': 'second', 'vector': binary(0, 1)},
            {'id': 'legacy', 'vector': '[1, 0]'}]
    query = [.6, .8]
    expected = [(row['id'], cosine(query, decode_vector(row['vector']))) for row in rows]
    assert list(scores(rows, query)) == expected
    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, 'numpy', None)
        assert list(scores(rows, query)) == expected
        with pytest.raises(KnowledgeError, match='embedding_dimension_changed'):
            list(scores([{'id': 'wrong', 'vector': binary(1, 0, 0)}], query))
