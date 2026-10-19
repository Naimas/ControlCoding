"""Server-side library windows with explicit counts and snapshot-bound navigation."""
import json
from pathlib import Path

from .knowledge_store import database, get, KnowledgeError
from .knowledge_sources import digest, physical_source_path
from .knowledge_wiki import freshness
from .knowledge_document_types import TYPES, VERSION, GUIDES
from .knowledge_document_review import reviews, effective

PAGE_SIZE = 50


def read(root, value):
    if (type(value) is not dict or not {'kind', 'query', 'offset', 'snapshot'} <= set(value)
            or set(value) - {'kind', 'query', 'offset', 'snapshot', 'document_type', 'tags', 'tag_mode', 'section', 'area'}
            or value['kind'] not in ('sources', 'wiki', 'conversations')
            or type(value['query']) is not str or len(value['query']) > 200
            or type(value['offset']) is not int or not 0 <= value['offset'] <= 100000
            or (value['snapshot'] is not None and type(value['snapshot']) is not str)):
        raise KnowledgeError('invalid_library_request')
    document_type = value.get('document_type')
    selected_tags, tag_mode = value.get('tags', []), value.get('tag_mode', 'any')
    section, area = value.get('section', ''), value.get('area', '')
    from .knowledge_organization import SECTIONS
    if (type(section) is not str or section not in ('', 'unassigned', *SECTIONS)
            or type(area) is not str or len(area) > 200
            or (section or area) and value['kind'] != 'sources'):
        raise KnowledgeError('invalid_library_request')
    if (type(selected_tags) is not list or len(selected_tags) > 8 or len(set(selected_tags)) != len(selected_tags)
            or any(type(i) is not str or len(i) != 16 for i in selected_tags)
            or tag_mode not in ('any', 'all') or selected_tags and value['kind'] != 'sources'):
        raise KnowledgeError('invalid_library_request')
    if document_type is not None and (type(document_type) is not str or document_type not in TYPES or value['kind'] != 'sources'):
        raise KnowledgeError('invalid_library_request')
    with database(root) as db:
        if db is None or get(db, 'policy') is None:
            raise KnowledgeError('knowledge_not_enabled')
        query = value['query'].strip().casefold()
        from .knowledge_tags import registry, assignment
        tagset = registry(db)['tags'] if value['kind'] == 'sources' else {}
        if any(identifier not in tagset for identifier in selected_tags):
            raise KnowledgeError('invalid_library_request')
        identity = digest(json.dumps({'project': str(Path(root).resolve()), 'kind': value['kind'], 'query': query,
                                      'document_type': document_type, 'tags': selected_tags, 'tag_mode': tag_mode,
                                      'section': section, 'area': area, 'classifier_version': VERSION,
                                      'epoch': get(db, 'library_epoch', 0), 'generation': get(db, 'generation', 0),
                                      'policy': get(db, 'policy'), 'dirty': get(db, 'needs_reconcile', True)}, sort_keys=True))
        if (value['snapshot'] is not None and value['snapshot'] != identity) or (value['offset'] and value['snapshot'] is None):
            raise KnowledgeError('library_snapshot_changed')
        db.create_function('casefold', 1, lambda text: (text or '').casefold(), deterministic=True)
        saved_types = reviews(db) if value['kind'] == 'sources' else {}
        db.create_function('document_type', 5, lambda identifier, kind, path, title, revision: effective(dict(id=identifier, kind=kind, path=path, title=title, revision=revision), saved_types.get(identifier))['key'], deterministic=True)
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
        parameters = [query]
        if selected_tags or section or area:
            eligible_ids = []
            from .knowledge_organization import effective as organization
            for row in db.execute('SELECT id,path,revision FROM sources WHERE deleted=0'):
                if selected_tags:
                    record = assignment(db, row, tagset)
                    found = set(record['ids']) if record['confirmed'] else set()
                    if not (bool(found.intersection(selected_tags)) if tag_mode == 'any' else set(selected_tags) <= found):
                        continue
                if section or area:
                    org = organization(db, row)
                    fields = org['fields'] if org['confirmed'] else {}
                    if section and (fields.get('section') or 'unassigned') != section:
                        continue
                    if area and area not in fields.get('areas', []):
                        continue
                eligible_ids.append(row['id'])
            where += ' AND id IN (' + ','.join('?' for _ in eligible_ids) + ')' if eligible_ids else ' AND 0'
            parameters.extend(eligible_ids)
        type_counts = dict(db.execute('SELECT document_type(id,kind,path,title,revision),count(*) FROM sources WHERE ' + where + ' GROUP BY 1', parameters)) if table == 'sources' else {}
        if document_type:
            where += ' AND document_type(id,kind,path,title,revision)=?'
            parameters.append(document_type)
        total = db.execute('SELECT count(*) FROM ' + table + ' WHERE ' + where, parameters).fetchone()[0]
        rows = [dict(row) for row in db.execute('SELECT ' + columns + ' FROM ' + table + ' WHERE ' + where
                                               + ' ORDER BY ' + ordering + ' LIMIT ? OFFSET ?', (*parameters, PAGE_SIZE, value['offset']))]
        if table == 'sources':
            from .knowledge_document_codes import _public as document_code
            for row in rows:
                row['physicalPath'] = physical_source_path(root, row['path'], row['kind'])
                from .knowledge_document_review import read_type
                row['document_type'] = read_type(db, row)
                code = document_code(db, row)
                row['document_code'] = {'display': code['assignment']['display'] if code['confirmed'] else '',
                                        'stale': code['stale'], 'pending': code['pending']}
        if table == 'wiki':
            for row in rows:
                dependencies = json.loads(row.pop('dependencies'))
                row.update(kind='draft' if row['id'].startswith('wiki:draft:') else ('reviewed' if row['id'].startswith('wiki:review:') else 'topic'),
                           source_count=len(dependencies), stale=bool(freshness(db, dependencies)))
        return {'kind': table, 'query': value['query'], 'offset': value['offset'], 'page_size': PAGE_SIZE,
                'snapshot': identity, 'total': total, 'rows': rows, 'document_type': document_type,
                'tags': selected_tags, 'tag_mode': tag_mode, 'section': section, 'area': area,
                'document_types': [dict(key=key, label=label, purpose=purpose, question=GUIDES[key][0], example=GUIDES[key][1], count=type_counts.get(key, 0)) for key, (label, purpose) in TYPES.items()] if table == 'sources' else [],
                'next_offset': value['offset'] + len(rows) if value['offset'] + len(rows) < total else None}
