"""Persist bounded acquisition diagnostics, never retrieval evidence."""
from .knowledge_store import get, put, now, KnowledgeError

KEY = 'source_diagnostics'


def failure(db, path, identity, exc):
    if db is None:
        return
    rows = get(db, KEY, {})
    previous = rows.get(path, {})
    code = str(exc)[:160] if isinstance(exc, KnowledgeError) else 'source_read_failed'
    rows[path] = {'path': path, 'input_identity': identity, 'error': code,
                  'attempts': previous.get('attempts', 0) + 1, 'observed': now(),
                  'state': 'failed'}
    with db:
        put(db, KEY, dict(sorted(rows.items())[:128]))


def success(db, path):
    if db is None:
        return
    rows = get(db, KEY, {})
    if path in rows:
        rows[path]['state'] = 'recovered_pending_publication'
        with db:
            put(db, KEY, rows)


def published(db):
    rows = get(db, KEY, {})
    for row in rows.values():
        row['state'] = 'resolved'
    put(db, KEY, rows)


def retry(root, path):
    from .knowledge_store import database
    from .knowledge_service import reconcile
    with database(root) as db:
        if type(path) is not str or db is None or path not in get(db, KEY, {}):
            raise KnowledgeError('unknown_source_failure')
    # Reuse immutable successful checkpoints; publish only a coherent generation.
    return reconcile(root, 'manual')
