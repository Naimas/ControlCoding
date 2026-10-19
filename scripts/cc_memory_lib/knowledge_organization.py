"""Optional, source-bound document organization. Never changes source files."""
import json

from .knowledge_store import database, get, put, now, KnowledgeError
from .knowledge_sources import digest

PREFIX = 'document_organization:'
SECTIONS = {
    'direction': 'Project direction', 'areas': 'System areas',
    'area_plans': 'Area plans', 'requirements': 'Requirements and specifications',
    'decisions': 'Directives and decisions', 'execution': 'Phase execution plans',
    'delivery': 'Implementation and deliverables', 'operations': 'Release and operations',
    'testing': 'Tests and results', 'history': 'History and provenance',
}
FIELDS = {'section', 'areas', 'phase', 'status', 'currency', 'provenance', 'related'}
STATUSES = ('unknown', 'draft', 'proposed', 'approved')
CURRENCIES = ('unknown', 'current', 'partly_reused', 'superseded', 'historical')


def blank():
    return dict(section='', areas=[], phase='', status='unknown', currency='unknown', provenance='', related=[])


def check(fields):
    if type(fields) is not dict or set(fields) != FIELDS:
        raise KnowledgeError('invalid_document_organization')
    if (type(fields['section']) is not str or fields['section'] not in ('', *SECTIONS)
            or fields['status'] not in STATUSES or fields['currency'] not in CURRENCIES):
        raise KnowledgeError('invalid_document_organization')
    for key, limit in (('phase', 120), ('provenance', 600)):
        if type(fields[key]) is not str or len(fields[key]) > limit:
            raise KnowledgeError('invalid_document_organization')
    for key, limit in (('areas', 80), ('related', 180)):
        entries = fields[key]
        if (type(entries) is not list or len(entries) > 12
                or any(type(x) is not str or not x.strip() or len(x) > limit for x in entries)
                or len(set(entries)) != len(entries)):
            raise KnowledgeError('invalid_document_organization')
    return fields


def effective(db, source):
    saved = get(db, PREFIX + source['id'])
    if saved:
        if type(saved) is not dict or not {'fields', 'revision', 'path', 'reason', 'updated'} <= set(saved):
            raise KnowledgeError('invalid_document_organization_state')
        check(saved['fields'])
    links = {}
    for identifier in saved['fields']['related'] if saved else []:
        target = db.execute('SELECT revision FROM sources WHERE id=? AND deleted=0', (identifier,)).fetchone()
        links[identifier] = target['revision'] if target else None
    stale = bool(saved and (saved['revision'] != source['revision'] or saved['path'] != source['path']
                           or saved.get('related_versions', {}) != links))
    return dict(fields=saved['fields'] if saved else blank(), confirmed=bool(saved and not stale),
                stale=stale, reason=saved['reason'] if saved else '',
                token=digest(json.dumps([source['id'], source['revision'], source['path'], saved, links], sort_keys=True)))


def source(db, identifier):
    row = db.execute('SELECT id,path,title,kind,revision FROM sources WHERE id=? AND deleted=0', (identifier,)).fetchone()
    if row is None:
        raise KnowledgeError('source_unavailable')
    return dict(row)


def related_edges(db, identifier):
    """Explore confirmed links; a link is never independent proof of a claim."""
    edges = []
    for entry in db.execute('SELECT key FROM meta WHERE substr(key,1,?)=? AND (key=? OR instr(value,?)>0) ORDER BY key LIMIT 201',
                            (len(PREFIX), PREFIX, PREFIX + identifier, json.dumps(identifier))):
        owner = entry['key'][len(PREFIX):]
        try:
            row = source(db, owner)
        except KnowledgeError:
            continue
        record = effective(db, row)
        if not record['confirmed']:
            continue
        for target in record['fields']['related']:
            if identifier in (owner, target):
                edges.append(dict(source=owner, target=target, revision=row['revision'], kind='human_reviewed:document_related'))
                if len(edges) >= 201:
                    return edges
    return edges


def dispatch(root, action, value):
    with database(root) as db:
        if db is None or get(db, 'policy') is None:
            raise KnowledgeError('knowledge_not_enabled')
        if get(db, 'needs_reconcile', True):
            raise KnowledgeError('document_organization_refresh_required')
        if action == 'organization-view':
            if (type(value) is not dict or set(value) != {'query', 'section', 'area', 'offset'}
                    or any(type(value[k]) is not str or len(value[k]) > 200 for k in ('query', 'section', 'area'))
                    or value['section'] not in ('', 'unassigned', *SECTIONS)
                    or type(value['offset']) is not int or not 0 <= value['offset'] <= 100000):
                raise KnowledgeError('invalid_document_organization')
            rows, total, areas = [], 0, set()
            # Streaming rows; no source bodies or full catalog retained in memory.
            for raw in db.execute('SELECT id,path,title,kind,revision FROM sources WHERE deleted=0 ORDER BY path,id'):
                row = dict(raw)
                organization = effective(db, row)
                fields = organization['fields'] if organization['confirmed'] else blank()
                areas.update(fields['areas'])
                if (value['query'].casefold() not in (row['title'] + ' ' + row['path']).casefold()
                        or value['area'] and value['area'] not in fields['areas']
                        or value['section'] and (fields['section'] or 'unassigned') != value['section']):
                    continue
                if value['offset'] <= total < value['offset'] + 25:
                    rows.append({**row, 'organization': organization})
                total += 1
            return dict(rows=rows, total=total, offset=value['offset'], areas=sorted(areas),
                        sections=SECTIONS, statuses=STATUSES, currencies=CURRENCIES,
                        next_offset=value['offset'] + 25 if value['offset'] + 25 < total else None)
        if action not in ('organization-save', 'organization-prepare') or type(value) is not dict:
            raise KnowledgeError('invalid_document_organization')
        required = {'source', 'revision', 'expected'}
        if action == 'organization-save':
            required |= {'fields', 'reason'}
        if set(value) != required or any(type(value[k]) is not str for k in ('source', 'revision', 'expected')):
            raise KnowledgeError('invalid_document_organization')
        row = source(db, value['source'])
        current = effective(db, row)
        if value['revision'] != row['revision'] or value['expected'] != current['token']:
            raise KnowledgeError('document_organization_changed')
        if action == 'organization-prepare':
            body = db.execute('SELECT substr(body,1,24000),length(body) FROM sources WHERE id=?', (row['id'],)).fetchone()
            return dict(source=row, organization=current, text=body[0], truncated=body[1] > 24000,
                        sections=SECTIONS, statuses=STATUSES, currencies=CURRENCIES)
        if type(value['reason']) is not str or not 5 <= len(value['reason'].strip()) <= 600:
            raise KnowledgeError('invalid_document_organization')
        fields = value['fields']
        related_versions = {}
        if fields is not None:
            check(fields)
            for identifier in fields['related']:
                if identifier == row['id']:
                    raise KnowledgeError('invalid_document_organization')
                related_versions[identifier] = source(db, identifier)['revision']
        with db:
            if fields is None:
                db.execute('DELETE FROM meta WHERE key=?', (PREFIX + row['id'],))
            else:
                put(db, PREFIX + row['id'], dict(fields=fields, path=row['path'], revision=row['revision'],
                                               reason=value['reason'].strip(), updated=now(), related_versions=related_versions))
            put(db, 'library_epoch', get(db, 'library_epoch', 0) + 1)
        return dict(saved=True, source=row['id'], organization=effective(db, row))
