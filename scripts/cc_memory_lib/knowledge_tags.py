"""Optional private topic tags; source revisions and human decisions remain explicit."""
import json
import re
import unicodedata
from uuid import uuid4

from .knowledge_store import database, get, put, now, KnowledgeError
from .knowledge_sources import digest

REGISTRY = 'document_tags:registry:v1'
ASSIGN = 'document_tags:source:'
PROPOSAL = 'document_tags:proposal:'
HISTORY = 'document_tags:history:'
RECEIPT = 'document_tags:last_change:v1'
MAX_TAGS = 1000
MAX_PER_SOURCE = 24
MAX_HISTORY = 20
MAX_PROPOSALS = 8


def label(value):
    if type(value) is not str:
        raise KnowledgeError('invalid_tag_name')
    value = ' '.join(unicodedata.normalize('NFC', value).split())
    if (not value or len(value) > 64 or any(unicodedata.category(ch).startswith('C') for ch in value)
            or value[0] in '#@' or '\\' in value):
        raise KnowledgeError('invalid_tag_name')
    return value


def normalized(value):
    return unicodedata.normalize('NFKC', label(value)).casefold()


def registry(db):
    value = get(db, REGISTRY, {'version': 1, 'tags': {}})
    if (type(value) is not dict or value.get('version') != 1 or type(value.get('tags')) is not dict
            or len(value['tags']) > MAX_TAGS):
        raise KnowledgeError('invalid_tag_state')
    seen = set()
    for identifier, item in value['tags'].items():
        if not re.fullmatch('[a-f0-9]{16}', identifier) or type(item) is not str:
            raise KnowledgeError('invalid_tag_state')
        key = normalized(item)
        if key in seen:
            raise KnowledgeError('invalid_tag_state')
        seen.add(key)
    return value


def registry_token(value):
    return digest(json.dumps(value, sort_keys=True))


def source(db, identifier):
    row = db.execute('SELECT id,path,title,kind,revision FROM sources WHERE id=? AND deleted=0', (identifier,)).fetchone()
    if row is None:
        raise KnowledgeError('source_unavailable')
    return dict(row)


def assignment(db, row, tags=None):
    tags = tags or registry(db)['tags']
    saved = get(db, ASSIGN + row['id'])
    if saved:
        if (type(saved) is not dict or set(saved) != {'path', 'revision', 'ids', 'reason', 'origin', 'updated'}
                or saved['origin'] not in ('human', 'accepted_ai') or type(saved['ids']) is not list
                or len(saved['ids']) > MAX_PER_SOURCE or len(set(saved['ids'])) != len(saved['ids'])
                or any(identifier not in tags for identifier in saved['ids'])
                or type(saved['reason']) is not str or len(saved['reason']) > 600):
            raise KnowledgeError('invalid_tag_state')
    current = bool(saved and saved['path'] == row['path'] and saved['revision'] == row['revision'])
    return dict(ids=saved['ids'] if saved else [], names=[tags[i] for i in saved['ids']] if saved else [],
                confirmed=current, stale=bool(saved and not current), origin=saved['origin'] if saved else None,
                reason=saved['reason'] if saved else '',
                token=digest(json.dumps([row['id'], row['path'], row['revision'], saved], sort_keys=True)))


def proposals(db, row, tags=None):
    tags = tags or registry(db)['tags']
    saved = get(db, PROPOSAL + row['id'], [])
    if type(saved) is not list or len(saved) > MAX_PROPOSALS:
        raise KnowledgeError('invalid_tag_state')
    return [dict(item, stale=item['revision'] != row['revision'] or item['path'] != row['path']
                 or item['registry'] != registry_token({'version': 1, 'tags': tags})) for item in saved]


def public_tags(db, row, tags=None):
    tags = tags or registry(db)['tags']
    record = assignment(db, row, tags)
    return {**record, 'proposals': proposals(db, row, tags)}


def usage(db, tags):
    counts = {identifier: 0 for identifier in tags}
    for row in db.execute('SELECT id,path,revision FROM sources WHERE deleted=0'):
        saved = assignment(db, row, tags)
        if saved['confirmed']:
            for identifier in saved['ids']:
                counts[identifier] += 1
    return counts


def shared_hint_edges(db, identifier):
    """A few current shared tags suggest originals, never a fact."""
    try:
        seed = source(db, identifier)
    except KnowledgeError:
        return []
    tags = registry(db)['tags']
    current = assignment(db, seed, tags)
    if not current['confirmed'] or not current['ids']:
        return []
    edges = []
    # Fixed cap, indexed assignment lookup and no source bodies.
    for item in db.execute('SELECT id,path,revision FROM sources WHERE deleted=0 AND id<>? ORDER BY id LIMIT 5000', (identifier,)):
        other = assignment(db, item, tags)
        if other['confirmed'] and set(other['ids']) & set(current['ids']):
            edges.append(dict(source=identifier, target=item['id'], revision=seed['revision'], kind='topic_tag_hint'))
            if len(edges) == 8:
                break
    return edges


def _record_history(db, row, before, after, event):
    key = HISTORY + row['id']
    history = get(db, key, [])
    history.append(dict(at=now(), event=event, revision=row['revision'], before=before, after=after))
    put(db, key, history[-MAX_HISTORY:])


def _validate_names(names):
    if type(names) is not list or len(names) > MAX_PER_SOURCE:
        raise KnowledgeError('invalid_tag_assignment')
    result = [label(name) for name in names]
    if len({normalized(name) for name in result}) != len(result):
        raise KnowledgeError('duplicate_tag_name')
    return result


def _save(db, value):
    base = {'source', 'revision', 'expected', 'registry', 'names', 'reason', 'origin'}
    if (type(value) is not dict or set(value) != (base | {'proposal'} if value.get('origin') == 'accepted_ai' else base)
            or any(type(value[k]) is not str for k in ('source', 'revision', 'expected', 'registry', 'reason'))
            or value['origin'] not in ('human', 'accepted_ai')
            or not 5 <= len(value['reason'].strip()) <= 600):
        raise KnowledgeError('invalid_tag_assignment')
    names = _validate_names(value['names'])
    row = source(db, value['source'])
    tagset = registry(db)
    prior = assignment(db, row, tagset['tags'])
    if (row['revision'] != value['revision'] or prior['token'] != value['expected']
            or registry_token(tagset) != value['registry']):
        raise KnowledgeError('tag_assignment_changed')
    if value['origin'] == 'accepted_ai':
        proposed = next((p for p in proposals(db, row, tagset['tags'])
                         if p['id'] == value['proposal'] and p['status'] == 'pending' and not p['stale']), None)
        if not proposed or normalized(proposed['name']) not in {normalized(name) for name in names}:
            raise KnowledgeError('tag_proposal_changed')
    by_name = {normalized(name): identifier for identifier, name in tagset['tags'].items()}
    ids = []
    for name in names:
        key = normalized(name)
        if key not in by_name:
            if len(tagset['tags']) >= MAX_TAGS:
                raise KnowledgeError('tag_registry_limit')
            identifier = uuid4().hex[:16]
            tagset['tags'][identifier] = name
            by_name[key] = identifier
        ids.append(by_name[key])
    with db:
        if ids:
            put(db, ASSIGN + row['id'], dict(path=row['path'], revision=row['revision'], ids=ids,
                                             reason=value['reason'].strip(), origin=value['origin'], updated=now()))
        else:
            db.execute('DELETE FROM meta WHERE key=?', (ASSIGN + row['id'],))
        put(db, REGISTRY, tagset)
        if value['origin'] == 'accepted_ai':
            pending = get(db, PROPOSAL + row['id'], [])
            for item in pending:
                if item['id'] == value['proposal']:
                    item['status'], item['decided'] = 'accepted', now()
            put(db, PROPOSAL + row['id'], pending)
        _record_history(db, row, prior['ids'], ids, 'assigned' if ids else 'cleared')
        put(db, 'library_epoch', get(db, 'library_epoch', 0) + 1)
    return dict(saved=True, source=row['id'], tags=public_tags(db, row, tagset['tags']))


def _proposal_save(db, value):
    if (type(value) is not dict or set(value) != {'source', 'revision', 'registry', 'model', 'items', 'truncated'}
            or any(type(value[k]) is not str for k in ('source', 'revision', 'registry', 'model'))
            or type(value['truncated']) is not bool or type(value['items']) is not list
            or not 1 <= len(value['items']) <= 8):
        raise KnowledgeError('invalid_tag_proposal')
    row = source(db, value['source'])
    tagset = registry(db)
    if row['revision'] != value['revision'] or registry_token(tagset) != value['registry']:
        raise KnowledgeError('tag_proposal_changed')
    body = db.execute('SELECT substr(body,1,24000) FROM sources WHERE id=?', (row['id'],)).fetchone()[0]
    existing = get(db, PROPOSAL + row['id'], [])
    items = []
    for item in value['items']:
        if type(item) is not dict or set(item) != {'name', 'quote', 'reason'}:
            raise KnowledgeError('invalid_tag_proposal')
        name = label(item['name'])
        quote, reason = item['quote'], item['reason']
        if (type(quote) is not str or not 8 <= len(quote) <= 200 or quote not in body
                or type(reason) is not str or not 5 <= len(reason.strip()) <= 400):
            raise KnowledgeError('invalid_tag_proposal')
        items.append(dict(id=uuid4().hex[:16], name=name, quote=quote, reason=reason.strip(),
                          line=body.count('\n', 0, body.index(quote)) + 1, status='pending',
                          source=row['id'], path=row['path'], revision=row['revision'],
                          registry=value['registry'], model=value['model'], truncated=value['truncated'], created=now()))
    if len({normalized(x['name']) for x in items}) != len(items):
        raise KnowledgeError('duplicate_tag_name')
    with db:
        put(db, PROPOSAL + row['id'], (existing + items)[-MAX_PROPOSALS:])
        put(db, 'library_epoch', get(db, 'library_epoch', 0) + 1)
    return dict(saved=True, source=row['id'], proposals=proposals(db, row, tagset['tags']))


def _proposal_decide(db, value):
    if type(value) is not dict or set(value) != {'source', 'id', 'decision', 'revision', 'registry'} or value['decision'] != 'reject':
        raise KnowledgeError('invalid_tag_proposal')
    row = source(db, value['source'])
    tagset = registry(db)
    items = get(db, PROPOSAL + row['id'], [])
    current = next((p for p in items if p['id'] == value['id']), None)
    if (not current or current['status'] != 'pending' or current['revision'] != row['revision']
            or value['revision'] != row['revision'] or current['registry'] != registry_token(tagset)
            or value['registry'] != current['registry']):
        raise KnowledgeError('tag_proposal_changed')
    with db:
        current['status'] = 'rejected'
        current['decided'] = now()
        put(db, PROPOSAL + row['id'], items)
        put(db, 'library_epoch', get(db, 'library_epoch', 0) + 1)
    return dict(saved=True, source=row['id'], proposals=proposals(db, row, tagset['tags']))


def _matches(db, row, tagset, value):
    tags = assignment(db, row, tagset['tags'])
    selected = value['tags']
    if not selected:
        return True, tags
    found = set(tags['ids']) if tags['confirmed'] else set()
    return (bool(found.intersection(selected)) if value['tag_mode'] == 'any' else set(selected) <= found), tags


def _view(db, value):
    from .knowledge_organization import effective as organization, SECTIONS
    from .knowledge_document_review import read_type
    from .knowledge_document_types import TYPES
    if (type(value) is not dict or set(value) != {'query', 'section', 'area', 'document_type', 'tags', 'tag_mode', 'offset'}
            or any(type(value[k]) is not str or len(value[k]) > 200 for k in ('query', 'section', 'area', 'document_type'))
            or value['section'] not in ('', 'unassigned', *SECTIONS)
            or value['document_type'] not in ('', *TYPES)
            or type(value['offset']) is not int or not 0 <= value['offset'] <= 100000
            or value['tag_mode'] not in ('any', 'all') or type(value['tags']) is not list
            or len(value['tags']) > 8 or len(set(value['tags'])) != len(value['tags'])):
        raise KnowledgeError('invalid_tag_view')
    tagset = registry(db)
    if any(type(identifier) is not str or identifier not in tagset['tags'] for identifier in value['tags']):
        raise KnowledgeError('invalid_tag_view')
    total, rows, areas = 0, [], set()
    counts = {identifier: 0 for identifier in tagset['tags']}
    for raw in db.execute('SELECT id,path,title,kind,revision FROM sources WHERE deleted=0 ORDER BY path,id'):
        row = dict(raw)
        match, tags = _matches(db, row, tagset, value)
        if tags['confirmed']:
            for identifier in tags['ids']:
                counts[identifier] += 1
        org = organization(db, row)
        fields = org['fields'] if org['confirmed'] else None
        if fields:
            areas.update(fields['areas'])
        if (not match or value['query'].casefold() not in (row['title'] + ' ' + row['path']).casefold()
                or value['section'] and ((fields or {}).get('section') or 'unassigned') != value['section']
                or value['area'] and value['area'] not in (fields or {}).get('areas', [])):
            continue
        if value['document_type'] and read_type(db, row)['key'] != value['document_type']:
            continue
        if value['offset'] <= total < value['offset'] + 25:
            rows.append({**row, 'organization': org, 'tags': {**tags, 'proposals': proposals(db, row, tagset['tags'])},
                         'document_type': read_type(db, row)})
        total += 1
    return dict(rows=rows, total=total, offset=value['offset'], next_offset=value['offset'] + 25 if value['offset'] + 25 < total else None,
                query=value['query'], section=value['section'], area=value['area'], document_type=value['document_type'],
                selected_tags=value['tags'], tag_mode=value['tag_mode'],
                tags=[dict(id=i, name=name, count=counts[i]) for i, name in sorted(tagset['tags'].items(), key=lambda x: x[1].casefold())],
                registry=registry_token(tagset), areas=sorted(areas), sections=SECTIONS,
                document_types=[dict(key=i, label=v[0]) for i, v in TYPES.items()])


def _affected(db, identifier):
    items = {}
    for raw in db.execute('SELECT id,path,revision,deleted FROM sources ORDER BY path,id'):
        saved = get(db, ASSIGN + raw['id'])
        if saved and identifier in saved['ids']:
            items[raw['id']] = (dict(raw), saved)
    for key, value in db.execute('SELECT key,value FROM meta WHERE key LIKE ?', (ASSIGN + '%',)):
        source_id = key[len(ASSIGN):]
        if source_id in items:
            continue
        saved = json.loads(value)
        if identifier in saved['ids']:
            items[source_id] = (dict(id=source_id, path=saved['path'], revision=saved['revision'], deleted=1), saved)
    return items


def _manage_preview(db, value):
    if (type(value) is not dict or set(value) != {'operation', 'source', 'target', 'name'}
            or value['operation'] not in ('rename', 'merge', 'remove')
            or any(type(value[k]) is not str for k in ('source', 'target', 'name'))):
        raise KnowledgeError('invalid_tag_management')
    tags = registry(db)
    if value['source'] not in tags['tags']:
        raise KnowledgeError('tag_unavailable')
    if value['operation'] == 'rename':
        name = label(value['name'])
        if any(normalized(other) == normalized(name) and identifier != value['source'] for identifier, other in tags['tags'].items()):
            raise KnowledgeError('tag_merge_required')
    elif value['operation'] == 'merge':
        if value['target'] == value['source'] or value['target'] not in tags['tags']:
            raise KnowledgeError('invalid_tag_management')
    elif value['target'] or value['name']:
        raise KnowledgeError('invalid_tag_management')
    items = _affected(db, value['source'])
    fingerprint = digest(json.dumps([value, registry_token(tags), [(i, item) for i, (_, item) in items.items()]], sort_keys=True))
    return dict(**value, registry=registry_token(tags), review=fingerprint, count=len(items),
                examples=[dict(id=i, path=row['path'], current=not row['deleted'] and row['revision'] == saved['revision'])
                          for i, (row, saved) in list(items.items())[:25]],
                truncated=len(items) > 25)


def _manage_apply(db, value):
    if (type(value) is not dict or set(value) != {'operation', 'source', 'target', 'name', 'registry', 'review', 'reason'}
            or type(value['review']) is not str or type(value['registry']) is not str
            or type(value['reason']) is not str or not 5 <= len(value['reason'].strip()) <= 600):
        raise KnowledgeError('invalid_tag_management')
    request = {k: value[k] for k in ('operation', 'source', 'target', 'name')}
    preview = _manage_preview(db, request)
    if preview['review'] != value['review'] or preview['registry'] != value['registry']:
        raise KnowledgeError('tag_management_changed')
    tags = registry(db)
    previous = json.loads(json.dumps(tags))
    items = _affected(db, value['source'])
    old = {identifier: saved for identifier, (_, saved) in items.items()}
    with db:
        if value['operation'] == 'rename':
            tags['tags'][value['source']] = label(value['name'])
        else:
            for identifier, (row, saved) in items.items():
                ids = [value['target'] if value['operation'] == 'merge' and x == value['source'] else x
                       for x in saved['ids'] if value['operation'] != 'remove' or x != value['source']]
                ids = list(dict.fromkeys(ids))
                if ids:
                    put(db, ASSIGN + identifier, {**saved, 'ids': ids, 'updated': now()})
                else:
                    db.execute('DELETE FROM meta WHERE key=?', (ASSIGN + identifier,))
                _record_history(db, row, saved['ids'], ids, value['operation'])
            del tags['tags'][value['source']]
        put(db, REGISTRY, tags)
        after = {identifier: get(db, ASSIGN + identifier) for identifier in items}
        receipt = dict(id=uuid4().hex[:16], operation=value['operation'], reason=value['reason'].strip(),
                       at=now(), before_registry=previous, after_registry=registry_token(tags),
                       assignments=old, after_assignments=digest(json.dumps(after, sort_keys=True)))
        put(db, RECEIPT, receipt)
        put(db, 'library_epoch', get(db, 'library_epoch', 0) + 1)
    return dict(saved=True, receipt=receipt['id'], operation=value['operation'], affected=len(items),
                undo_available=True)


def _undo(db, value):
    receipt = get(db, RECEIPT)
    if type(value) is not dict or set(value) != {'receipt'} or not receipt or value['receipt'] != receipt['id']:
        raise KnowledgeError('tag_undo_unavailable')
    if registry_token(registry(db)) != receipt['after_registry']:
        raise KnowledgeError('tag_undo_changed')
    current = {identifier: get(db, ASSIGN + identifier) for identifier in receipt['assignments']}
    if digest(json.dumps(current, sort_keys=True)) != receipt['after_assignments']:
        raise KnowledgeError('tag_undo_changed')
    with db:
        put(db, REGISTRY, receipt['before_registry'])
        for identifier, saved in receipt['assignments'].items():
            put(db, ASSIGN + identifier, saved)
            row = db.execute('SELECT id,path,revision FROM sources WHERE id=?', (identifier,)).fetchone()
            if row is None:
                row = dict(id=identifier, path=saved['path'], revision=saved['revision'])
            _record_history(db, row, current[identifier]['ids'] if current[identifier] else [], saved['ids'], 'undo')
        db.execute('DELETE FROM meta WHERE key=?', (RECEIPT,))
        put(db, 'library_epoch', get(db, 'library_epoch', 0) + 1)
    return dict(undone=True, receipt=value['receipt'])


def dispatch(root, action, value):
    with database(root) as db:
        if db is None or get(db, 'policy') is None:
            raise KnowledgeError('knowledge_not_enabled')
        if get(db, 'needs_reconcile', True):
            raise KnowledgeError('tag_refresh_required')
        if action == 'tag-view':
            return _view(db, value)
        if action == 'tag-prepare':
            if type(value) is not dict or set(value) != {'source', 'revision', 'expected', 'registry'}:
                raise KnowledgeError('invalid_tag_request')
            row = source(db, value['source'])
            tags = registry(db)
            current = assignment(db, row, tags['tags'])
            if row['revision'] != value['revision'] or current['token'] != value['expected'] or registry_token(tags) != value['registry']:
                raise KnowledgeError('tag_assignment_changed')
            body = db.execute('SELECT substr(body,1,24000),length(body) FROM sources WHERE id=?', (row['id'],)).fetchone()
            return dict(source=row, text=body[0], truncated=body[1] > 24000,
                        tags=[dict(id=i, name=n) for i, n in tags['tags'].items()], registry=value['registry'])
        if action == 'tag-save':
            return _save(db, value)
        if action == 'tag-proposal-save':
            return _proposal_save(db, value)
        if action == 'tag-proposal-decide':
            return _proposal_decide(db, value)
        if action == 'tag-history':
            row = source(db, value)
            return dict(source=row['id'], events=get(db, HISTORY + row['id'], []))
        if action == 'tag-manage-preview':
            return _manage_preview(db, value)
        if action == 'tag-manage-apply':
            return _manage_apply(db, value)
        if action == 'tag-undo':
            return _undo(db, value)
        raise KnowledgeError('invalid_tag_request')


def forget(db, identifier):
    source_id = 'conversation:' + identifier
    saved = get(db, ASSIGN + source_id)
    for prefix in (ASSIGN, PROPOSAL, HISTORY):
        db.execute('DELETE FROM meta WHERE key=?', (prefix + source_id,))
    if saved:
        tagset = registry(db)
        retained = set()
        for key, value in db.execute('SELECT key,value FROM meta WHERE key LIKE ?', (ASSIGN + '%',)):
            if key != ASSIGN + source_id:
                retained.update(json.loads(value)['ids'])
        for tag_id in saved['ids']:
            if tag_id not in retained:
                tagset['tags'].pop(tag_id, None)
        put(db, REGISTRY, tagset)
        put(db, 'library_epoch', get(db, 'library_epoch', 0) + 1)
    receipt = get(db, RECEIPT)
    if receipt and source_id in receipt.get('assignments', {}):
        db.execute('DELETE FROM meta WHERE key=?', (RECEIPT,))
