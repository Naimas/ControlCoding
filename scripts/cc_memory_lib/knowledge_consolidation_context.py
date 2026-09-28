"""Bounded, original-source projections of reviewed consolidation claims.

This is a read model. A claim is current only while its reviewed wiki section and
every original anchor still agree with the reconciled archive. Generated claim
prose is never returned as a citation or inserted into the source index.
"""
import json

from .knowledge_store import KnowledgeError, database, get
from .knowledge_wiki import freshness
from . import knowledge_wiki_review as review

WINDOW = 20
MAX_WINDOW = 40
MAX_REPLY = 256 * 1024
EPISTEMIC = frozenset(('observed', 'decided', 'planned', 'inferred', 'disputed', 'unknown'))


def _request(value):
    if value is None:
        value = {}
    if type(value) is not dict or not set(value) <= {'query', 'limit', 'offset', 'source', 'history'}:
        raise KnowledgeError('invalid_consolidation_context_request')
    query = value.get('query', '')
    source = value.get('source')
    history = value.get('history')
    limit = value.get('limit', WINDOW)
    offset = value.get('offset', 0)
    if (type(query) is not str or len(query) > 500 or type(limit) is not int
            or not 1 <= limit <= MAX_WINDOW or type(offset) is not int or not 0 <= offset <= 100000
            or source is not None and (type(source) is not str or not 0 < len(source) <= 160)
            or history is not None and (type(history) is not str or not history.startswith('memory:')
                                        or len(history) != 71)):
        raise KnowledgeError('invalid_consolidation_context_request')
    return query.casefold().strip(), limit, offset, source, history


def _metadata(db):
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='consolidation_claim_metadata'").fetchone():
        return {r['claim_id']: (r['epistemic_status'], r['scope']) for r in
                db.execute('SELECT claim_id,epistemic_status,scope FROM consolidation_claim_metadata')}
    return {}


def _section(approved, target):
    if type(target) is not str or target.count('/') != 1:
        return None
    page, key = target.split('/', 1)
    return approved.get(page, {}).get(key)


def _claim_status(db, row, approved, deps):
    if row['state'] != 'approved':
        return row['state']
    section = _section(approved, row['target'])
    if (type(section) is not dict or section.get('claim_id') != row['id']
            or section.get('title') != row['title'] or section.get('body') != row['body']
            or section.get('dependencies') != deps):
        return 'replaced'
    if get(db, 'needs_reconcile', True) or freshness(db, deps):
        return 'stale'
    return 'current'


def _hydrate(db, dep):
    """Return a passage copied from the current original, never from claim text."""
    row = db.execute('SELECT path,title,revision,kind,body,deleted FROM sources WHERE id=?',
                     (dep['source'],)).fetchone()
    if row is None or row['deleted'] or row['revision'] != dep['revision'] or row['path'] != dep['path']:
        return None
    lines = row['body'].splitlines()
    if not 1 <= dep['line'] <= dep['end_line'] <= len(lines):
        return None
    passage = '\n'.join(lines[dep['line'] - 1:dep['end_line']])
    excerpt = dep['excerpt']
    if excerpt not in passage:
        # Long source lines may be chunked. A chunk is still original source
        # material but must match the exact current source revision and anchor.
        chunk = db.execute('SELECT id,text FROM chunks WHERE source=? AND revision=? AND line=? '
                           'AND end_line=? AND text=?', (dep['source'], dep['revision'],
                           dep['line'], dep['end_line'], excerpt)).fetchone()
        if chunk is None:
            return None
        from .knowledge_sources import passages
        original = {'id': dep['source'], 'revision': dep['revision'], 'body': row['body']}
        if not any(part['id'] == chunk['id'] and part['text'] == chunk['text']
                   for part in passages(original)):
            return None
        passage = chunk['text']
    return {'source': dep['source'], 'path': row['path'], 'title': row['title'],
            'revision': row['revision'], 'line': dep['line'], 'end_line': dep['end_line'],
            'excerpt': excerpt, 'kind': row['kind'], 'origin': 'original_source'}


def _row(db, row, approved, metadata, hydrate):
    try:
        deps = json.loads(row['dependencies'])
    except (ValueError, TypeError):
        return None
    if type(deps) is not list or not deps:
        return None
    status = _claim_status(db, row, approved, deps)
    epistemic, scope = metadata.get(row['id'], ('unknown', 'project'))
    if epistemic not in EPISTEMIC:
        epistemic = 'unknown'
    if type(scope) is not str or len(scope) > 160:
        scope = 'project'
    original = []
    if hydrate and status == 'current':
        for dep in deps:
            citation = _hydrate(db, dep)
            if citation is None:
                status = 'stale'
                original = []
                break
            original.append(citation)
    return {'id': row['id'], 'target': row['target'], 'kind': row['kind'],
            'title': row['title'], 'body': row['body'], 'revision': row['revision'],
            'review_status': row['state'], 'current_status': status,
            'epistemic_status': epistemic, 'scope': scope, 'updated': row['updated'],
            'evidence': original if hydrate else [],
            'source_ids': [dep['source'] for dep in deps]}


def _empty(schema, query=''):
    return {'schema': schema, 'query': query, 'generation': None, 'consolidation_epoch': 0,
            'claims': [], 'backlinks': [], 'history': [], 'total': 0, 'offset': 0,
            'next_offset': None, 'history_total': 0, 'history_next_offset': None,
            'notice': 'Approved memory is unavailable until explicit migration and review.'}


def read(root, value=None):
    """Read current claims, source backlinks, or an explicitly selected history.

    ``history`` is a claim ID. Its old revisions are never included in default
    operational retrieval. ``source`` narrows reverse links to one original ID.
    Every response is windowed and bounded under the bridge's 1 MiB ceiling.
    """
    query, limit, offset, source, history = _request(value)
    with database(root) as db:
        if db is None:
            return _empty(0, query)
        schema = db.execute('PRAGMA user_version').fetchone()[0]
        if schema < 2:
            return _empty(schema, query)
    # Catch on-disk edits that have not yet entered the archive. Reconciliation
    # has its own writer lease; no context DB connection is held across it.
    from .knowledge_service import reconcile
    reconcile(root)
    with database(root) as db:
        if db is None or get(db, 'policy') is None:
            return _empty(0, query)
        if get(db, 'needs_reconcile', True):
            result = _empty(db.execute('PRAGMA user_version').fetchone()[0], query)
            result['notice'] = 'Source reconciliation is required before approved memory can be used.'
            return result
        approved = get(db, review.STATE, {})
        metadata = _metadata(db)
        from .knowledge_ranking import tokenize
        from .knowledge_query import STOPWORDS
        terms = set(tokenize(query)) - STOPWORDS if query else set()
        candidates = []
        # The reviewed wiki quota caps current claims at 100, so this scans
        # metadata only and hydrates original bodies for the returned window.
        for row in db.execute('SELECT * FROM memory_claims ORDER BY updated DESC,id'):
            projected = _row(db, row, approved, metadata, False)
            if projected is None or projected['current_status'] != 'current':
                continue
            if source is not None and source not in projected['source_ids']:
                continue
            haystack = (projected['title'] + ' ' + projected['body']).casefold()
            score = sum(term in haystack for term in terms)
            if query and (not terms or not score):
                continue
            candidates.append((score, row, projected))
        if terms:
            candidates.sort(key=lambda item: -item[0])
        total = len(candidates)
        claims = []
        current_offset = 0 if history is not None else offset
        for _, row, _ in candidates[current_offset:current_offset + limit]:
            projected = _row(db, row, approved, metadata, True)
            if projected and projected['current_status'] == 'current':
                claims.append(projected)
        backlinks = []
        for claim in claims:
            for citation in claim['evidence']:
                backlinks.append({'source': citation['source'], 'claim': claim['id'],
                                  'target': claim['target'], 'line': citation['line'],
                                  'end_line': citation['end_line'], 'revision': citation['revision']})
        revisions = []
        history_total = 0
        if history is not None:
            history_total = db.execute('SELECT count(*) FROM memory_claim_revisions WHERE id=?',
                                       (history,)).fetchone()[0]
            for row in db.execute('SELECT * FROM memory_claim_revisions WHERE id=? '
                                  'ORDER BY updated DESC,revision DESC LIMIT ? OFFSET ?',
                                  (history, limit, offset)):
                projected = _row(db, row, approved, metadata, False)
                if projected:
                    # A historical revision is always a historical view, even
                    # when it has the same text as a current claim.
                    projected['current_status'] = 'historical'
                    revisions.append(projected)
        result = {'schema': schema, 'query': query, 'generation': get(db, 'generation', 0),
                  'consolidation_epoch': get(db, 'consolidation_epoch', 0),
                  'claims': claims, 'backlinks': backlinks, 'history': revisions,
                  'total': total, 'offset': current_offset,
                  'next_offset': current_offset + limit if current_offset + limit < total else None,
                  'history_total': history_total,
                  'history_next_offset': offset + limit if history is not None and offset + limit < history_total else None,
                  'notice': 'Claims are reviewed derived memory. Citations are hydrated from current original sources.'}
        if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > MAX_REPLY:
            raise KnowledgeError('consolidation_context_budget')
        return result
