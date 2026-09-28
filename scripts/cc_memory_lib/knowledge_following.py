"""Bounded operator ledger from saved policy and coherent stored observations."""
from .knowledge_store import get


def read(db):
    policy = get(db, 'policy')
    enabled = 'rich-documents' in policy['scopes']
    selected = policy.get('rich_paths')
    dirty = get(db, 'needs_reconcile', True)
    # Sources are bounded by the existing archive contract, but retained deleted
    # sources may grow. Count and fetch in SQL rather than materializing history.
    known_count = db.execute("SELECT count(*) FROM sources WHERE kind='rich-document'").fetchone()[0]
    if selected is not None:
        paths = sorted(selected)
        total = len(paths)
    else:
        paths = [r[0] for r in db.execute("SELECT path FROM sources WHERE kind='rich-document' ORDER BY path LIMIT 128")]
        total = known_count
    rows = []
    for path in paths[:128]:
        source = db.execute("SELECT id,revision,updated,deleted FROM sources WHERE path=? AND kind='rich-document'", (path,)).fetchone()
        state = ('not_followed' if not enabled else 'refresh_required' if dirty else
                 'indexed' if source and not source['deleted'] else 'absent_at_last_scan')
        rows.append({'path': path, 'state': state, 'id': source['id'] if source else None,
                     'revision': source['revision'] if source else None,
                     'observed': source['updated'] if source else None,
                     'passages': 0, 'embedded': 0})
    by_id = {row['id']: row for row in rows if row['id']}
    if by_id:
        for identifier, passages, embedded in db.execute('SELECT source,count(*),count(vector) FROM chunks GROUP BY source'):
            if identifier in by_id:
                by_id[identifier].update(passages=passages, embedded=embedded)
    return {'mode': 'selected' if selected is not None else 'scope', 'enabled': enabled,
            'last_scan': get(db, 'last_reconcile'), 'total': total, 'rows': rows,
            'limited': total > len(rows)}
