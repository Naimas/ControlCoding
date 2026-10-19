"""Human-reviewed succession between current, classified source documents."""
import json

from .knowledge_store import database, get, put, now, KnowledgeError
from .knowledge_sources import digest

LINK = 'document_codes:supersedes:'
REVERSE = 'document_codes:successor:'


def _token(value):
    return digest(json.dumps(value, sort_keys=True))


def _endpoint(db, identifier):
    from .knowledge_document_codes import _state, _display
    row = db.execute('SELECT id,path,title,kind,revision,deleted FROM sources WHERE id=?', (identifier,)).fetchone()
    if row is None or row['deleted']:
        return None
    row = dict(row)
    state = _state(db, row)
    assignment = state['assignment']
    return {**row, 'code': _display(assignment) if state['confirmed'] and assignment and assignment['kind'] != 'NONE' else '',
            'identity': assignment['id'] if state['confirmed'] and assignment and assignment['kind'] != 'NONE' else '',
            'token': state['token']}


def _relation(db, successor, record):
    if (type(record) is not dict
            or not {'target', 'source_revision', 'target_revision', 'source_identity', 'target_identity', 'reason', 'updated'} <= set(record)
            or type(record['target']) is not str or not 0 < len(record['target']) <= 180
            or any(type(record[k]) is not str for k in ('source_revision', 'target_revision', 'source_identity', 'target_identity', 'reason', 'updated'))):
        raise KnowledgeError('invalid_document_code_lineage')
    origin, target = _endpoint(db, successor), _endpoint(db, record['target'])
    effective = bool(origin and target and origin['revision'] == record['source_revision']
                     and target['revision'] == record['target_revision']
                     and origin['identity'] == record['source_identity']
                     and target['identity'] == record['target_identity']
                     and get(db, REVERSE + record['target']) == successor)
    return {'source': successor, 'target': record['target'], 'title': target['title'] if target else 'Missing source',
            'code': target['code'] if target else '', 'reason': record['reason'], 'updated': record['updated'],
            'effective': effective}


def read_for(db, identifier):
    previous = get(db, LINK + identifier)
    successor = get(db, REVERSE + identifier)
    if successor is not None and (type(successor) is not str or not 0 < len(successor) <= 180):
        raise KnowledgeError('invalid_document_code_lineage')
    next_link = get(db, LINK + successor) if isinstance(successor, str) else None
    if next_link is not None and type(next_link) is not dict:
        raise KnowledgeError('invalid_document_code_lineage')
    result = {'supersedes': _relation(db, identifier, previous) if previous else None,
              'superseded_by': None, 'token': _token([previous, successor, next_link])}
    if next_link and next_link.get('target') == identifier:
        relation = _relation(db, successor, next_link)
        origin = _endpoint(db, successor)
        result['superseded_by'] = {**relation, 'title': origin['title'] if origin else 'Missing source',
                                   'code': origin['code'] if origin else ''}
    return result


def edges_for(db, identifier):
    state = read_for(db, identifier)
    records = [state['supersedes'], state['superseded_by']]
    return [{'source': r['source'], 'target': r['target'], 'kind': 'human_reviewed:document_supersedes',
             'revision': db.execute('SELECT revision FROM sources WHERE id=?', (r['source'],)).fetchone()[0],
             'effective': True} for r in records if r and r['effective']]


def _current(db, identifier, revision, expected):
    endpoint = _endpoint(db, identifier)
    if endpoint is None or not endpoint['code'] or endpoint['revision'] != revision or endpoint['token'] != expected:
        raise KnowledgeError('document_code_link_changed')
    return endpoint


def dispatch(root, action, value):
    with database(root) as db:
        if db is None or get(db, 'policy') is None:
            raise KnowledgeError('knowledge_not_enabled')
        if get(db, 'needs_reconcile', True):
            raise KnowledgeError('document_code_refresh_required')
        if action == 'code-link-candidates':
            if (type(value) is not dict or set(value) != {'source', 'query', 'offset'}
                    or type(value['source']) is not str or type(value['query']) is not str or len(value['query']) > 200
                    or type(value['offset']) is not int or not 0 <= value['offset'] <= 100000):
                raise KnowledgeError('invalid_document_code_link')
            current = _endpoint(db, value['source'])
            if current is None or not current['code']:
                raise KnowledgeError('document_code_link_source_unavailable')
            rows, total = [], 0
            for raw in db.execute("SELECT id,path,title,kind,revision,deleted FROM sources WHERE deleted=0 AND kind NOT IN ('conversation','session','receipt','dev-view') ORDER BY path,id"):
                if raw['id'] == value['source']:
                    continue
                endpoint = _endpoint(db, raw['id'])
                if not endpoint or not endpoint['code'] or value['query'].casefold() not in (endpoint['title'] + ' ' + endpoint['path'] + ' ' + endpoint['code']).casefold():
                    continue
                if value['offset'] <= total < value['offset'] + 25:
                    rows.append({k: endpoint[k] for k in ('id', 'path', 'title', 'revision', 'code', 'token')})
                total += 1
            return {'source': value['source'], 'query': value['query'], 'rows': rows, 'total': total, 'offset': value['offset'],
                    'next_offset': value['offset'] + 25 if value['offset'] + 25 < total else None}
        if action not in ('code-link-save', 'code-link-remove') or type(value) is not dict:
            raise KnowledgeError('invalid_document_code_link')
        common = {'source', 'revision', 'expected', 'link_expected'}
        required = common | ({'target', 'target_revision', 'target_expected', 'reason'} if action == 'code-link-save' else set())
        if (set(value) != required or any(type(value[k]) is not str for k in required)
                or len(value['source']) > 180 or len(value['expected']) != 64 or len(value['link_expected']) != 64):
            raise KnowledgeError('invalid_document_code_link')
        source = _current(db, value['source'], value['revision'], value['expected']) if action == 'code-link-save' else _endpoint(db, value['source'])
        if source is None or source['revision'] != value['revision'] or source['token'] != value['expected']:
            raise KnowledgeError('document_code_link_changed')
        if read_for(db, source['id'])['token'] != value['link_expected']:
            raise KnowledgeError('document_code_link_changed')
        old = get(db, LINK + source['id'])
        if action == 'code-link-remove':
            if not old:
                raise KnowledgeError('document_code_link_unavailable')
            with db:
                db.execute('DELETE FROM meta WHERE key=?', (LINK + source['id'],))
                if get(db, REVERSE + old['target']) == source['id']:
                    db.execute('DELETE FROM meta WHERE key=?', (REVERSE + old['target'],))
                put(db, 'library_epoch', get(db, 'library_epoch', 0) + 1)
        else:
            if value['target'] == source['id'] or not 5 <= len(value['reason'].strip()) <= 600:
                raise KnowledgeError('invalid_document_code_link')
            target = _current(db, value['target'], value['target_revision'], value['target_expected'])
            owner = get(db, REVERSE + target['id'])
            existing = get(db, LINK + owner) if isinstance(owner, str) else None
            if owner and owner != source['id'] and existing and _relation(db, owner, existing)['effective']:
                raise KnowledgeError('document_code_duplicate_successor')
            seen, cursor = set(), target['id']
            while cursor:
                if cursor == source['id'] or cursor in seen:
                    raise KnowledgeError('document_code_link_cycle')
                seen.add(cursor)
                if len(seen) > 5000:
                    raise KnowledgeError('document_code_link_limit')
                relation = get(db, LINK + cursor)
                cursor = relation['target'] if relation else None
            record = {'target': target['id'], 'source_revision': source['revision'],
                      'target_revision': target['revision'], 'source_identity': source['identity'],
                      'target_identity': target['identity'], 'reason': value['reason'].strip(), 'updated': now()}
            with db:
                if old and old['target'] != target['id'] and get(db, REVERSE + old['target']) == source['id']:
                    db.execute('DELETE FROM meta WHERE key=?', (REVERSE + old['target'],))
                put(db, LINK + source['id'], record)
                put(db, REVERSE + target['id'], source['id'])
                put(db, 'library_epoch', get(db, 'library_epoch', 0) + 1)
        from .knowledge_document_codes import _public
        row = db.execute('SELECT id,path,title,kind,revision FROM sources WHERE id=?', (source['id'],)).fetchone()
        return {'saved': True, 'source': source['id'], 'code': _public(db, dict(row))}
