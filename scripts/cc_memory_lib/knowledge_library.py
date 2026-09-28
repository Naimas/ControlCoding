"""Server-side library windows with explicit counts and snapshot-bound navigation."""
import json
from pathlib import Path

from .knowledge_store import database, get, KnowledgeError
from .knowledge_sources import digest
from .knowledge_wiki import freshness

PAGE_SIZE = 50


def read(root, value):
    if (type(value) is not dict or set(value) != {'kind', 'query', 'offset', 'snapshot'}
            or value['kind'] not in ('sources', 'wiki', 'conversations')
            or type(value['query']) is not str or len(value['query']) > 200
            or type(value['offset']) is not int or not 0 <= value['offset'] <= 100000
            or (value['snapshot'] is not None and type(value['snapshot']) is not str)):
        raise KnowledgeError('invalid_library_request')
    with database(root) as db:
        if db is None or get(db, 'policy') is None:
            raise KnowledgeError('knowledge_not_enabled')
        query = value['query'].strip().casefold()
        identity = digest(json.dumps({'project': str(Path(root).resolve()), 'kind': value['kind'], 'query': query,
                                      'epoch': get(db, 'library_epoch', 0), 'generation': get(db, 'generation', 0),
                                      'policy': get(db, 'policy'), 'dirty': get(db, 'needs_reconcile', True)}, sort_keys=True))
        if (value['snapshot'] is not None and value['snapshot'] != identity) or (value['offset'] and value['snapshot'] is None):
            raise KnowledgeError('library_snapshot_changed')
        db.create_function('casefold', 1, lambda text: (text or '').casefold(), deterministic=True)
        table = value['kind']
        if table == 'sources':
            columns = 'id,path,title,revision,kind,updated,substr(body,1,300) AS excerpt'
            condition, searchable, ordering = 'deleted=0', "title||' '||path", 'path,id'
        elif table == 'wiki':
            columns = 'id,title,revision,updated,dependencies'
            condition, searchable, ordering = "id LIKE 'wiki:%'", 'title', 'title,id'
        else:
            columns = 'id,title,retention,status,updated,substr(summary,1,300) AS summary'
            condition, searchable, ordering = '1=1', "title||' '||summary", 'updated DESC,id'
        where = condition + ' AND instr(casefold(' + searchable + '),?)>0'
        total = db.execute('SELECT count(*) FROM ' + table + ' WHERE ' + where, (query,)).fetchone()[0]
        rows = [dict(row) for row in db.execute('SELECT ' + columns + ' FROM ' + table + ' WHERE ' + where
                                               + ' ORDER BY ' + ordering + ' LIMIT ? OFFSET ?', (query, PAGE_SIZE, value['offset']))]
        if table == 'wiki':
            for row in rows:
                dependencies = json.loads(row.pop('dependencies'))
                row.update(kind='draft' if row['id'].startswith('wiki:draft:') else ('reviewed' if row['id'].startswith('wiki:review:') else 'topic'),
                           source_count=len(dependencies), stale=bool(freshness(db, dependencies)))
        return {'kind': table, 'query': value['query'], 'offset': value['offset'], 'page_size': PAGE_SIZE,
                'snapshot': identity, 'total': total, 'rows': rows,
                'next_offset': value['offset'] + len(rows) if value['offset'] + len(rows) < total else None}
