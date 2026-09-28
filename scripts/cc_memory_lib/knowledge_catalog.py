"""Bounded graph transport with one identity across all record categories."""
import json
from pathlib import Path

from .knowledge_store import database, get, KnowledgeError
from .knowledge_sources import digest, physical_source_path
from .knowledge_wiki import freshness

KINDS = {
    'sources': ('id,path,title,revision,kind,updated,substr(body,1,300) AS excerpt', 'deleted=0', 'path,id'),
    'wiki': ('id,title,revision,updated,dependencies', "id LIKE 'wiki:%'", 'title,id'),
    'edges': ('source,target,revision,kind', '1=1', 'source,target,revision,kind'),
    'conversations': ('id,title,retention,status,updated,substr(summary,1,300) AS summary', '1=1', 'updated DESC,id'),
}


def read(root, value):
    if (type(value) is not dict or set(value) != {'kind', 'offset', 'snapshot'}
            or type(value['kind']) is not str or value['kind'] not in KINDS
            or type(value['offset']) is not int or not 0 <= value['offset'] <= 1000000
            or (value['snapshot'] is not None and type(value['snapshot']) is not str)):
        raise KnowledgeError('invalid_catalog_request')
    with database(root) as db:
        if db is None or get(db, 'policy') is None:
            raise KnowledgeError('knowledge_not_enabled')
        identity = digest(json.dumps({'project': str(Path(root).resolve()),
                                     'epoch': get(db, 'library_epoch', 0),
                                     'generation': get(db, 'generation', 0),
                                     'policy': get(db, 'policy'),
                                     'dirty': get(db, 'needs_reconcile', True)}, sort_keys=True))
        if (value['snapshot'] is not None and value['snapshot'] != identity) or (value['offset'] and value['snapshot'] is None):
            raise KnowledgeError('catalog_snapshot_changed')
        kind = value['kind']
        columns, where, order = KINDS[kind]
        total = db.execute('SELECT count(*) FROM ' + kind + ' WHERE ' + where).fetchone()[0]
        rows, size = [], 0
        for raw in db.execute('SELECT ' + columns + ' FROM ' + kind + ' WHERE ' + where
                              + ' ORDER BY ' + order + ' LIMIT 200 OFFSET ?', (value['offset'],)):
            row = dict(raw)
            if kind == 'sources':
                row['physicalPath'] = physical_source_path(root, row['path'], row['kind'])
            if kind == 'wiki':
                dependencies = json.loads(row.pop('dependencies'))
                row.update(source_count=len(dependencies), stale=bool(freshness(db, dependencies)),
                           kind='draft' if row['id'].startswith('wiki:draft:') else ('reviewed' if row['id'].startswith('wiki:review:') else 'topic'))
            length = len(json.dumps(row, ensure_ascii=True).encode('utf-8')) + 2
            if size + length > 512000:
                if not rows:
                    raise KnowledgeError('catalog_record_limit')
                break
            rows.append(row)
            size += length
        following = value['offset'] + len(rows)
        return {'kind': kind, 'snapshot': identity, 'offset': value['offset'], 'total': total,
                'rows': rows, 'next_offset': following if following < total else None}
