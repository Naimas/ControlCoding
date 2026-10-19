"""Snapshot-bound graph windows; never materialize all document bodies or nodes."""
import json
import re
from urllib.parse import unquote
from pathlib import Path
from .knowledge_store import database, get, KnowledgeError
from .knowledge_sources import digest, physical_source_path
from .knowledge_wiki import classify, TOPICS
from .knowledge_document_review import read_type

FIELDS = 'id,path,title,revision,kind,updated,substr(body,1,300) AS excerpt'


def source(root, identifier):
    if type(identifier) is dict:
        return linked_source(root, identifier)
    if type(identifier) is not str or len(identifier) > 180:
        raise KnowledgeError('invalid_graph_request')
    with database(root) as db:
        if db is None:
            raise KnowledgeError('knowledge_not_enabled')
        row = db.execute('SELECT '+FIELDS+' FROM sources WHERE id=? AND deleted=0', (identifier,)).fetchone()
        if row is None:
            raise KnowledgeError('source_unavailable')
        result = dict(row)
        result['physicalPath'] = physical_source_path(root, result['path'], result['kind'])
        result['document_type'] = read_type(db, result)
        result['tags'] = result['document_type']['tags']
        return result


def linked_source(root, request):
    """Resolve one relative link against authorized rows, never discover new files."""
    if (set(request) != {'from', 'href'} or type(request['from']) is not str
            or len(request['from']) > 180 or type(request['href']) is not str
            or not 0 < len(request['href']) <= 4096):
        raise KnowledgeError('invalid_graph_request')
    try:
        href = unquote(request['href'].split('#', 1)[0], errors='strict')
    except UnicodeError as error:
        raise KnowledgeError('unsupported_link') from error
    if (not href or re.search(r'[:\\?\x00-\x1f]', href) or href.startswith('/')):
        raise KnowledgeError('unsupported_link')
    parent = source(root, request['from'])
    if not parent['physicalPath']:
        raise KnowledgeError('unsupported_link')
    parts = parent['physicalPath'].split('/')[:-1]
    for part in href.split('/'):
        if part == '..':
            if not parts:
                raise KnowledgeError('unsupported_link')
            parts.pop()
        elif part and part != '.':
            parts.append(part)
    target = '/'.join(parts)
    candidates = {target}
    for logical in ('.controlcoding', '.controlwork'):
        physical = physical_source_path(root, logical)
        if physical and target.startswith(physical + '/'):
            candidates.add(logical + target[len(physical):])
    with database(root) as db:
        rows = db.execute('SELECT id FROM sources WHERE deleted=0 AND path IN ('
                          + ','.join('?' for _ in candidates) + ') LIMIT 2', tuple(candidates)).fetchall()
    if len(rows) != 1:
        raise KnowledgeError('source_unavailable')
    return source(root, rows[0]['id'])


def read(root, value):
    keys = {'topic', 'query', 'offset', 'snapshot', 'focus'}
    if (type(value) is not dict or not keys <= set(value) <= keys | {'tag'} or value['topic'] not in (None, *[t[0] for t in TOPICS])
            or type(value['query']) is not str or len(value['query']) > 200
            or type(value['offset']) is not int or not 0 <= value['offset'] <= 1000000
            or value['snapshot'] is not None and type(value['snapshot']) is not str
            or value['focus'] is not None and (type(value['focus']) is not str or len(value['focus']) > 180)
            or value.get('tag') is not None and (type(value['tag']) is not str or not re.fullmatch('[a-f0-9]{16}', value['tag']))):
        raise KnowledgeError('invalid_graph_request')
    with database(root) as db:
        if db is None or get(db, 'policy') is None:
            raise KnowledgeError('knowledge_not_enabled')
        identity = digest(json.dumps([str(Path(root).resolve()), get(db, 'library_epoch', 0),
                         get(db, 'generation', 0), get(db, 'needs_reconcile', True), get(db, 'policy'),
                         value['topic'], value['query'], value['focus'], value.get('tag'), get(db, 'work_relations_v1', [])], sort_keys=True))
        if value['snapshot'] not in (None, identity) or value['offset'] and value['snapshot'] is None:
            raise KnowledgeError('graph_snapshot_changed')
        db.create_function('graph_topic', 3, lambda kind, path, title: classify(dict(kind=kind, path=path, title=title))[0], deterministic=True)
        db.create_function('casefold', 1, lambda text: text.casefold(), deterministic=True)
        counts = dict(db.execute("SELECT graph_topic(kind,path,title),count(*) FROM sources WHERE deleted=0 GROUP BY 1"))
        topics = [dict(id=key, title=title, count=counts.get(key, 0)) for key, title, _ in TOPICS if counts.get(key)]
        where, params = 'deleted=0', []
        if value.get('tag'):
            from .knowledge_tags import assignment, registry
            tag_names = registry(db)['tags']
            if value['tag'] not in tag_names:
                raise KnowledgeError('tag_unavailable')
            eligible = [row['id'] for row in db.execute('SELECT id,path,revision FROM sources WHERE deleted=0')
                        if (record := assignment(db, row, tag_names))['confirmed'] and value['tag'] in record['ids']]
            where += ' AND id IN (' + ','.join('?' for _ in eligible) + ')' if eligible else ' AND 0'
            params.extend(eligible)
        if value['topic']:
            where += ' AND graph_topic(kind,path,title)=?'
            params.append(value['topic'])
        if value['query'].strip():
            where += " AND instr(casefold(title||' '||path),?)>0"
            params.append(value['query'].strip().casefold())
        total = db.execute('SELECT count(*) FROM sources WHERE '+where, params).fetchone()[0]
        sources = [dict(r) for r in db.execute('SELECT '+FIELDS+' FROM sources WHERE '+where+' ORDER BY path,id LIMIT 200 OFFSET ?', (*params, value['offset']))]
        next_offset = value['offset'] + len(sources)
        from .knowledge_work import read_db
        reviewed = [r for r in read_db(db, root)['relations'] if r['effective']]
        if value['focus']:
            adjacent = db.execute('SELECT source,target FROM edges WHERE source=? OR target=? ORDER BY source,target LIMIT 80', (value['focus'], value['focus']))
            ids = {value['focus']}
            for row in adjacent:
                ids.update(row)
            for row in reviewed:
                if value['focus'] in (row['source'], row['target']) and len(ids) < 81:
                    ids.update((row['source'], row['target']))
            known = {s['id'] for s in sources}
            for identifier in sorted(ids-known):
                row = db.execute('SELECT '+FIELDS+' FROM sources WHERE deleted=0 AND id=?', (identifier,)).fetchone()
                if row:
                    sources.append(dict(row))
        from .knowledge_tags import assignment, registry
        tag_names = registry(db)['tags']
        for source_row in sources:
            source_row['physicalPath'] = physical_source_path(root, source_row['path'], source_row['kind'])
            source_row['tags'] = assignment(db, source_row, tag_names)
        ids = {s['id'] for s in sources}
        edges, relevant = [], 0
        for row in db.execute('SELECT source,target,kind,revision FROM edges ORDER BY source,target,kind'):
            if row['source'] in ids or row['target'] in ids:
                relevant += 1
                if row['source'] in ids and row['target'] in ids and len(edges) < 1000:
                    edges.append(dict(row))
        for row in reviewed:
            if row['source'] in ids or row['target'] in ids:
                relevant += 1
                if row['source'] in ids and row['target'] in ids and len(edges) < 1000:
                    edges.append({'source': row['source'], 'target': row['target'],
                                  'revision': row['source_revision'], 'kind': 'human_reviewed:'+row['kind']})
        # Legacy small-catalog consumers get bounded metadata; full lists use the
        # existing server-side library. These arrays are never accumulated.
        wiki = [dict(r) for r in db.execute("SELECT id,title,revision,updated,CASE WHEN id LIKE 'wiki:draft:%' THEN 'draft' ELSE 'topic' END AS kind FROM wiki WHERE id LIKE 'wiki:%' ORDER BY id LIMIT 200")]
        conversations = [dict(r) for r in db.execute('SELECT id,title,retention,status,updated FROM conversations ORDER BY updated DESC,id LIMIT 200')]
        result = {'sources': sources, 'edges': edges, 'wiki': wiki, 'conversations': conversations,
                'graph': {'snapshot': identity, 'topics': topics, 'topic': value['topic'], 'query': value['query'], 'tag': value.get('tag'),
                          'focus': value['focus'], 'offset': value['offset'], 'total': total,
                          'source_total': sum(counts.values()), 'next_offset': next_offset if next_offset < total else None,
                          'external_edges': relevant-len(edges), 'window_size': 200}}
        # Metadata extras are convenience previews; the library owns complete
        # lists. Trim previews before refusing an oversized source window.
        while len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > 900000:
            if edges:
                removed = min(64, len(edges))
                del edges[-removed:]
                result['graph']['external_edges'] += removed
            elif conversations:
                del conversations[-32:]
            elif wiki:
                del wiki[-32:]
            else:
                raise KnowledgeError('graph_response_budget')
        return result
