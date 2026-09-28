"""Manual, durable consolidation proposals. No model calls or automatic approval."""
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from .knowledge_store import KnowledgeError, database, get, put, now
from . import knowledge_wiki_review as review
from .knowledge_wiki import freshness

ACTIONS = ('consolidation-view', 'consolidation-create', 'consolidation-propose',
           'consolidation-decide', 'consolidation-undo')
KINDS = ('summary', 'decision_summary', 'lesson', 'preference', 'open_question')
MAX_JOBS, MAX_PROPOSALS, MAX_SOURCES = 20, 20, 20
PRIVACY = 'consolidation_privacy_epoch'


def require(condition, code='invalid_consolidation_request'):
    if not condition:
        raise KnowledgeError(code)


def encoded(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode('utf-8')).hexdigest()


def identifier(value):
    return type(value) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', value)


def shape(value, keys):
    require(type(value) is dict and set(value) == set(keys.split()))


def binding(db, root):
    path = str(Path(root).resolve())
    return {'project': path.casefold() if os.name == 'nt' else path, 'generation': get(db, 'generation', 0),
            'head': get(db, 'head'), 'policy': digest(get(db, 'policy')),
            'privacy_epoch': get(db, PRIVACY, 0), 'review_epoch': get(db, review.EPOCH, 0)}


def stale(db, root, job):
    return (job['state'] in ('stale', 'purged') or get(db, 'needs_reconcile', True)
            or json.loads(job['binding']) != binding(db, root))


def current(db, root, job):
    require(not stale(db, root, job), 'consolidation_context_changed')


def job_row(db, value):
    require(identifier(value))
    row = db.execute('SELECT * FROM consolidation_jobs WHERE id=?', (value,)).fetchone()
    require(row is not None, 'consolidation_job_unavailable')
    return dict(row)


def page_revision(db, page_type):
    row = db.execute('SELECT revision FROM wiki WHERE id=?', ('wiki:review:' + page_type,)).fetchone()
    return row[0] if row else None


def bounded_job(db, job):
    size = sum(len(str(v).encode('utf-8')) for v in job.values() if v is not None)
    for row in db.execute('SELECT * FROM consolidation_proposals WHERE job=?', (job['id'],)):
        size += sum(len(str(v).encode('utf-8')) for v in row if v is not None)
    require(size <= 128 * 1024, 'consolidation_job_budget')


def view(db, root, selected=None):
    schema = db.execute('PRAGMA user_version').fetchone()[0] if db else 1
    result = {'enabled': schema in (2, 3), 'schema': schema, 'jobs': [], 'job': None, 'pages': [],
              'sources': [], 'coverage': {'selected': 0, 'total': 0, 'deferred': 0},
              'notice': 'Manual source-bound proposals. No AI analysis or automatic consolidation is running.'}
    if schema not in (2, 3):
        return result
    if schema == 3:
        from .knowledge_consolidation_selection import progress
        result['progress'] = progress(db)
        result['notice'] = 'Source-bound consolidation. Analysis produces pending proposals; publication requires explicit review.'
    for ptype, (title, _) in review.PAGE_TYPES.items():
        result['pages'].append({'type': ptype, 'id': 'wiki:review:' + ptype,
                                'title': title, 'revision': page_revision(db, ptype)})
    for row in db.execute('SELECT * FROM consolidation_jobs ORDER BY created DESC,id DESC LIMIT 20'):
        manifest = json.loads(row['manifest'])
        count = db.execute('SELECT count(*) FROM consolidation_proposals WHERE job=?', (row['id'],)).fetchone()[0]
        result['jobs'].append({'id': row['id'], 'title': manifest['title'], 'state': row['state'],
                               'created': row['created'], 'count': count})
    if selected is not None:
        job = job_row(db, selected)
        manifest = json.loads(job['manifest'])
        is_stale = stale(db, root, job)
        proposals = []
        for row in db.execute('SELECT * FROM consolidation_proposals WHERE job=? ORDER BY ordinal', (selected,)):
            ptype, key = row['target'].split('/', 1)
            deps = json.loads(row['dependencies'])
            before = manifest.get('before', {}).get(row['id'])
            metadata = db.execute('SELECT * FROM consolidation_proposal_metadata WHERE proposal=?',
                                  (row['id'],)).fetchone() if schema == 3 else None
            extra = ({'epistemic_status': metadata['epistemic_status'], 'scope': metadata['scope'],
                      'conflicting': json.loads(metadata['conflicting']),
                      'prerequisite_ids': json.loads(metadata['prerequisites'])} if metadata else {})
            proposals.append({**dict(row), **extra, 'page_type': ptype, 'key': key, 'dependencies': deps,
                              'before': before, 'eligible': row['status'] == 'pending' and not is_stale
                              and row['base_revision'] == page_revision(db, ptype) and not freshness(db, deps)})
        result['job'] = {'id': selected, 'title': manifest['title'], 'state': job['state'],
                         'stale': is_stale, 'proposals': proposals,
                         'receipt': json.loads(job['receipt']) if job['receipt'] else None}
        if schema == 3:
            result['job']['attempts'] = [dict(r) for r in db.execute('SELECT request_id,ordinal,mode,state,owner,lease_until,started,finished,outcome,error,usage FROM consolidation_attempts WHERE job=? ORDER BY ordinal', (selected,))]
            packet = db.execute('SELECT packet FROM consolidation_attempts WHERE job=? ORDER BY ordinal DESC LIMIT 1', (selected,)).fetchone()
            result['job']['packet'] = json.loads(packet[0]) if packet and packet[0] != '{}' else None
        result['sources'] = manifest.get('sources', [])
        result['coverage'] = {'selected': manifest['count'], 'total': manifest['total'],
                              'deferred': manifest['deferred']}
    require(len(encoded(result).encode('utf-8')) <= 256 * 1024, 'consolidation_view_budget')
    return result


def create(db, root, value):
    shape(value, 'title')
    require(type(value['title']) is str and 0 < len(value['title'].strip()) <= 120)
    require(not get(db, 'needs_reconcile', True), 'reconcile_required')
    require(db.execute('SELECT count(*) FROM consolidation_jobs').fetchone()[0] < MAX_JOBS,
            'consolidation_job_limit')
    # Reconciliation already compiles enabled pages when sources change. A
    # continuation on the same generation must not reload every source body.
    if not get(db, review.ENABLED, False) or any(page_revision(db, page) is None for page in review.PAGE_TYPES):
        put(db, review.ENABLED, True)
        review.compile_pages(db, [dict(r) for r in db.execute('SELECT * FROM sources WHERE deleted=0 ORDER BY path')], now())
    sources = []
    total = db.execute('SELECT count(*) FROM sources WHERE deleted=0').fetchone()[0]
    schema = db.execute('PRAGMA user_version').fetchone()[0]
    if schema == 3:
        from .knowledge_consolidation_selection import choose
        sources, total = choose(db)
        require(bool(sources), 'consolidation_no_new_sources')
    else:
        for row in db.execute('SELECT * FROM sources WHERE deleted=0 ORDER BY path LIMIT 20'):
            lines = row['body'].splitlines()
            line = next((i for i, text in enumerate(lines) if text.strip() and not text.startswith('#')), None)
            if line is None:
                line = next((i for i, text in enumerate(lines) if text.strip()), None)
            if line is None:
                continue
            sources.append({'source': row['id'], 'title': row['title'][:256], 'path': row['path'],
                            'revision': row['revision'], 'line': line + 1, 'end_line': line + 1,
                            'excerpt': lines[line][:1000]})
    key, stamp = uuid4().hex, now()
    manifest = {'title': value['title'].strip(), 'sources': sources, 'count': len(sources),
                'total': total, 'deferred': total - len(sources), 'before': {}}
    require(len(encoded(manifest)) <= 48000, 'consolidation_source_budget')
    db.execute('INSERT INTO consolidation_jobs VALUES(?,?,?,?,?,?,?,?)',
               (key, stamp, stamp, 'ready', encoded(binding(db, root)), encoded(manifest), None, None))
    for source in sources:
        db.execute('INSERT INTO consolidation_inputs VALUES(?,?,?)', (key, source['source'], source['revision']))
    put(db, 'library_epoch', get(db, 'library_epoch', 0) + 1)
    return key


def propose(db, root, value):
    shape(value, 'job page_type key title body kind citations reason')
    job = job_row(db, value['job'])
    current(db, root, job)
    require(type(value['page_type']) is str and value['page_type'] in review.PAGE_TYPES)
    require(type(value['key']) is str and re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', value['key']))
    for key, limit in [('title', 120), ('body', 3000), ('reason', 1000)]:
        require(type(value[key]) is str and 0 < len(value[key].strip()) <= limit)
    require(type(value['kind']) is str and value['kind'] in KINDS)
    deps = review._validated_dependencies(db, value['citations'])
    refs = set(re.findall(r'\[(S\d+)\]', value['body']))
    require(refs and refs <= {d['citation'] for d in deps}, 'invalid_consolidation_citation')
    allowed = {r['source']: r['revision'] for r in db.execute('SELECT * FROM consolidation_inputs WHERE job=?', (job['id'],))}
    require(all(allowed.get(d['source']) == d['revision'] for d in deps), 'consolidation_source_outside_job')
    count = db.execute('SELECT count(*) FROM consolidation_proposals WHERE job=?', (job['id'],)).fetchone()[0]
    require(count < MAX_PROPOSALS, 'consolidation_proposal_limit')
    target = value['page_type'] + '/' + value['key']
    manifest = json.loads(job['manifest'])
    key = uuid4().hex
    manifest['before'][key] = get(db, review.STATE, {}).get(value['page_type'], {}).get(value['key'])
    db.execute('INSERT INTO consolidation_proposals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
               (key, job['id'], count, value['kind'], target, page_revision(db, value['page_type']),
                value['title'].strip(), value['body'].strip(), encoded(deps), value['reason'].strip(), 'pending', now(), None))
    db.execute('UPDATE consolidation_jobs SET manifest=?,updated=? WHERE id=?', (encoded(manifest), now(), job['id']))
    return job['id']


def dispatch(root, action, value=None):
    require(action in ACTIONS)
    if action == 'consolidation-view':
        if value is not None:
            shape(value, 'job')
        with database(root) as db:
            return view(db, root, value['job'] if value is not None else None)
    from .knowledge_service import reconcile
    with database(root) as db:
        require(db is not None and db.execute('PRAGMA user_version').fetchone()[0] in (2, 3),
                'consolidation_migration_required')
    # Reject/review remain useful for stale jobs; writes that could publish must
    # recapture original source state, including changes not yet observed by UI.
    if action != 'consolidation-decide' or not isinstance(value, dict) or value.get('decision') != 'reject':
        reconcile(root)
    with database(root) as db:
        require(db is not None and db.execute('PRAGMA user_version').fetchone()[0] in (2, 3),
                'consolidation_migration_required')
        with db:
            if action == 'consolidation-create':
                selected = create(db, root, value)
            elif action == 'consolidation-propose':
                selected = propose(db, root, value)
            else:
                from .knowledge_consolidation_review import decide, undo
                selected = (decide if action == 'consolidation-decide' else undo)(db, root, value)
            bounded_job(db, job_row(db, selected))
            # Every committed review must remain exportable and restorable.
            from .knowledge_consolidation_store import validate_storage
            validate_storage(db)
            return view(db, root, selected)
