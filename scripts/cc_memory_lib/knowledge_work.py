"""Revision-bound human work relations over observed sources; never canonical promotion."""
import json
from pathlib import Path
from .knowledge_store import database, get, put, KnowledgeError, now
from .knowledge_sources import digest

KEY = 'work_relations_v1'
KINDS = ('part_of', 'depends_on', 'blocks', 'decides', 'evidences', 'conflicts')
ROLES = ('objective', 'phase', 'activity', 'blocker', 'decision', 'evidence', 'document')


def require(value, code='invalid_work_relation'):
    if not value:
        raise KnowledgeError(code)


def snapshot(db):
    return digest(json.dumps([get(db, 'generation', 0), get(db, 'needs_reconcile', True),
                             get(db, KEY, [])], sort_keys=True))


def read_db(db, root):
    require(db is not None and get(db, 'policy') is not None, 'knowledge_not_enabled')
    rows = []
    for relation in get(db, KEY, []):
        row = dict(relation)
        endpoints = []
        for side in ('source', 'target'):
            record = db.execute('SELECT id,path,title,revision,kind,deleted FROM sources WHERE id=?', (row[side],)).fetchone()
            endpoints.append(dict(record) if record else None)
        stale = get(db, 'needs_reconcile', True) or any(
            not node or node['deleted'] or node['revision'] != row[side+'_revision']
            for node, side in zip(endpoints, ('source', 'target')))
        rows.append({**row, 'stale': bool(stale), 'endpoints': endpoints,
                     'effective': row['status'] == 'approved' and not stale})
    canonical = []
    for record in db.execute("SELECT id,path,title,kind,revision,body FROM sources WHERE deleted=0 AND (kind LIKE 'dev-record:%' OR kind IN ('dev-feature','dev-criterion')) ORDER BY path LIMIT 256"):
        item = {key: record[key] for key in ('id', 'path', 'title', 'kind', 'revision')}
        try:
            payload = json.loads(record['body'].rsplit('\n```json\n', 1)[1].rsplit('\n```', 1)[0])
            node = payload.get('node', payload)
            lifecycle = node.get('lifecycle', 'unknown')
            item.update(state=lifecycle.get('state', 'unknown') if type(lifecycle) is dict else lifecycle,
                        owner=node.get('owner') or 'Not recorded',
                        record_type=node.get('kind', node.get('type', 'unknown')),
                        issues=payload.get('registry_issues', []))
        except (ValueError, KeyError, IndexError, TypeError):
            item.update(state='unknown', owner='Not recorded', record_type='unknown', issues=['unreadable_projection'])
        item['stale'] = get(db, 'needs_reconcile', True)
        canonical.append(item)
    recorded = [dict(row) for row in db.execute("SELECT source,target,kind,revision FROM edges WHERE kind LIKE 'canonical_work:%' OR kind LIKE 'recorded_dev:%' ORDER BY source,target LIMIT 512")]
    edge_total = db.execute("SELECT count(*) FROM edges WHERE kind LIKE 'canonical_work:%' OR kind LIKE 'recorded_dev:%'").fetchone()[0]
    return {'snapshot': digest(str(Path(root).resolve()) + snapshot(db)), 'relations': rows, 'canonical': canonical, 'recorded_edges': recorded, 'recorded_edge_total': edge_total, 'kinds': KINDS, 'roles': ROLES,
            'notice': 'These links record your review of source revisions. They do not change feature states or mark work complete.'}


def dispatch(root, action, value):
    with database(root) as db:
        state = read_db(db, root)
        if action == 'work-view':
            return state
        require(type(value) is dict and value.get('snapshot') == state['snapshot'], 'work_snapshot_changed')
        require(not get(db, 'needs_reconcile', True), 'reconcile_required')
        rows = get(db, KEY, [])
        if action == 'work-propose':
            require(set(value) == {'snapshot', 'source', 'target', 'source_role', 'target_role', 'kind', 'reason'})
            require(value['kind'] in KINDS and value['source_role'] in ROLES and value['target_role'] in ROLES)
            require(type(value['reason']) is str and 5 <= len(value['reason'].strip()) <= 2000)
            require(type(value['source']) is str and type(value['target']) is str and value['source'] != value['target'])
            require(len(rows) < 128, 'work_relation_budget')
            endpoints = [db.execute('SELECT revision FROM sources WHERE id=? AND deleted=0', (value[side],)).fetchone() for side in ('source', 'target')]
            require(all(endpoints), 'source_unavailable')
            identifier = digest(json.dumps([value['source'], value['target'], value['kind']]))
            require(not any(r['id'] == identifier for r in rows), 'work_relation_exists')
            rows.append({k: value[k] for k in ('source', 'target', 'source_role', 'target_role', 'kind', 'reason')} |
                        {'id': identifier, 'source_revision': endpoints[0][0], 'target_revision': endpoints[1][0],
                         'status': 'proposed', 'review_reason': '', 'updated': now(), 'history': []})
        else:
            require(action == 'work-review' and set(value) == {'snapshot', 'id', 'status', 'reason'})
            require(value['status'] in ('approved', 'rejected', 'resolved', 'proposed'))
            require(type(value['reason']) is str and 5 <= len(value['reason'].strip()) <= 2000)
            row = next((r for r in rows if r['id'] == value['id']), None)
            require(row is not None, 'unknown_work_relation')
            require(value['status'] != 'resolved' or row['kind'] == 'conflicts' and row['status'] == 'approved')
            current = next(r for r in state['relations'] if r['id'] == row['id'])
            require(not current['stale'] or value['status'] in ('proposed', 'rejected'), 'work_evidence_changed')
            if value['status'] == 'proposed':
                require(all(n and not n['deleted'] for n in current['endpoints']), 'source_unavailable')
            require(len(row['history']) < 64, 'work_review_budget')
            if value['status'] == 'approved' and row['kind'] in ('part_of', 'depends_on'):
                adjacency = {}
                for other in state['relations']:
                    if other['effective'] and other['kind'] in ('part_of', 'depends_on') and other['id'] != row['id']:
                        adjacency.setdefault(other['source'], []).append(other['target'])
                todo, visited = [row['target']], set()
                while todo:
                    node = todo.pop()
                    require(node != row['source'], 'work_dependency_cycle')
                    if node not in visited:
                        visited.add(node)
                        todo.extend(adjacency.get(node, []))
            row['history'].append({'status': row['status'], 'reason': row['review_reason'], 'updated': row['updated'],
                                   'source_revision': row['source_revision'], 'target_revision': row['target_revision']})
            if value['status'] == 'proposed':
                row.update(source_revision=current['endpoints'][0]['revision'], target_revision=current['endpoints'][1]['revision'])
            row.update(status=value['status'], review_reason=value['reason'].strip(), updated=now())
        require(len(json.dumps(rows).encode('utf-8')) <= 131072, 'work_relation_budget')
        with db:
            put(db, KEY, rows)
        return read_db(db, root)
