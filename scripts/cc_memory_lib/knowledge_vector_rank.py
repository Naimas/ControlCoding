"""Exact bounded-batch vector scores with optional installed NumPy acceleration."""
from itertools import islice
from .knowledge_store import KnowledgeError
from .knowledge_semantic import cosine, decode_vector


def scores(rows, vector):
    try:
        import numpy as np
    except ImportError:
        for row in rows:
            yield row['id'], cosine(vector, decode_vector(row['vector']))
        return
    query = np.asarray(vector, dtype=np.float64)
    iterator = iter(rows)
    while batch := list(islice(iterator, 128)):
        values = []
        for row in batch:
            raw = row['vector']
            if isinstance(raw, bytes):
                if not raw or len(raw) % 4 or len(raw) > 65536:
                    raise KnowledgeError('invalid_embeddings')
                current = np.frombuffer(raw, dtype='<f4')
            else:
                current = np.asarray(decode_vector(raw), dtype=np.float64)
            if len(current) != len(query):
                raise KnowledgeError('embedding_dimension_changed')
            values.append(current)
        matrix = np.stack(values)
        if not np.isfinite(matrix).all():
            raise KnowledgeError('invalid_embeddings')
        # einsum avoids a per-batch multithreaded BLAS startup and retains exact
        # exhaustive scoring; no approximate search or corpus-sized vector cache.
        ranked = np.einsum('ij,j->i', matrix, query, dtype=np.float64)
        for row, score in zip(batch, ranked):
            yield row['id'], float(score)
