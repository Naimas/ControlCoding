"""Bounded planning projection over observed Work/Dev evidence.

The overlay stores only source IDs, revisions, numbers and dates. It never owns
task state, source text, or an additional lifecycle.
"""
from datetime import date, timedelta
import json
import math
from pathlib import Path

from .knowledge_sources import digest
from .knowledge_store import database, get, put, KnowledgeError

KEY = 'work_schedule_v1'
MAX_NODES = 512
MAX_EDGES = 2048
WORK_ROLES = frozenset(('objective', 'phase', 'activity'))
WORK_TYPES = frozenset(('task', 'phase', 'objective', 'activity', 'milestone',
                        'feature', 'criterion', 'work_item', 'deliverable'))
DONE_STATES = frozenset(('accepted', 'done', 'completed'))
CONTAINMENT_KINDS = frozenset(('canonical_work:contains_work', 'recorded_dev:contains',
                              'recorded_dev:part_of'))
DEPENDENCY_KINDS = frozenset(('recorded_dev:depends_on', 'recorded_dev:precedes'))
WARNING_MESSAGES = {
    'node_limit': 'The work source limit was reached. Narrow the observed scope before relying on this plan.',
    'edge_limit': 'The relation limit was reached. Reduce or review relations so every dependency is represented.',
    'stale_relation': 'An approved relation no longer matches its source revisions. Review and approve it against current evidence.',
    'missing_hierarchy_endpoint': 'A parent or child is absent from the current work sources. Restore or review that relation.',
    'multiple_parents': 'A work item has more than one parent. Review its containment relations.',
    'hierarchy_cycle': 'Containment forms a cycle. Review the parent-child relations before using this hierarchy.',
    'missing_dependency_endpoint': 'A predecessor or successor is absent from current work sources. Restore or review the dependency.',
    'dependency_cycle': 'Finish-to-start dependencies form a cycle. Review their direction before scheduling.',
    'reconcile_required': 'Source reconciliation is pending. Refresh the work plan before relying on readiness or timing.',
    'stale_estimate': 'A saved estimate belongs to an older source revision. Review and save it against the current record.',
    'missing_estimate': 'One or more leaf work items have no duration estimate. Add estimates to calculate dates and critical path.',
    'date_anchor_required': 'A date constraint needs a project start date. Set the anchor date to calculate timing.',
    'missing_blocker_endpoint': 'An explicit blocker or its target is absent. Restore or review the blocking relation.',
    'date_out_of_range': 'The estimated finish exceeds the supported calendar range. Shorten the plan or move its anchor date.',
    'deadline_missed': 'The estimated finish falls after the recorded deadline. Review the date, duration, or dependencies.',
    'owner_overlap': 'Work assigned to the same owner overlaps in estimated time. Review capacity or sequence.',
    'owner_parallel_conflict': 'Parallel-stage work shares an owner, and missing estimates prevent an overlap check. Review capacity.',
}


def require(test, code='invalid_work_schedule'):
    if not test:
        raise KnowledgeError(code)


def valid_date(value):
    if value is None:
        return None
    require(type(value) is str and len(value) == 10, 'invalid_schedule_date')
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise KnowledgeError('invalid_schedule_date') from exc
    require(parsed.isoformat() == value, 'invalid_schedule_date')
    return parsed


def valid_number(value, maximum, nullable=False):
    if nullable and value is None:
        return
    require(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= maximum,
            'invalid_schedule_number')


def validate_meta(value):
    require(type(value) is dict and set(value) == {'version', 'revision', 'start_date', 'tasks'},
            'invalid_schedule_metadata')
    require(type(value['version']) is int and value['version'] == 1 and
            type(value['revision']) is int and 0 <= value['revision'] <= 2**53,
            'invalid_schedule_metadata')
    valid_date(value['start_date'])
    tasks = value['tasks']
    require(type(tasks) is dict and len(tasks) <= MAX_NODES, 'invalid_schedule_metadata')
    for identifier, item in tasks.items():
        require(type(identifier) is str and 0 < len(identifier) <= 180 and
                type(item) is dict and set(item) == {'revision', 'duration_days', 'buffer_days',
                                                     'earliest_start', 'deadline'},
                'invalid_schedule_metadata')
        require(type(item['revision']) is str and 0 < len(item['revision']) <= 128,
                'invalid_schedule_metadata')
        valid_number(item['duration_days'], 3650, True)
        valid_number(item['buffer_days'], 365)
        valid_date(item['earliest_start'])
        valid_date(item['deadline'])
    require(len(json.dumps(value, ensure_ascii=False).encode('utf-8')) <= 65536,
            'invalid_schedule_metadata')
    return value


def metadata(db):
    raw = get(db, KEY)
    return validate_meta(raw) if raw is not None else dict(version=1, revision=0,
                                                           start_date=None, tasks={})


def warn(warnings, code, ids=()):
    values = sorted(set(ids))[:512]
    existing = next((w for w in warnings if w['code'] == code), None)
    if existing:
        existing['nodes'] = sorted(set(existing['nodes']) | set(values))[:512]
    else:
        warnings.append({'code': code, 'message': WARNING_MESSAGES[code], 'nodes': values})


def _record(record, dirty):
    item = {key: record[key] for key in ('id', 'title', 'path', 'revision', 'kind')}
    try:
        payload = json.loads(record['body'].rsplit('\n```json\n', 1)[1].rsplit('\n```', 1)[0])
        node = payload.get('node', payload)
        lifecycle = node.get('lifecycle', 'unknown')
        state = lifecycle.get('state', 'unknown') if type(lifecycle) is dict else lifecycle
        owner = node.get('owner') or 'Not recorded'
        item.update(state=state if type(state) is str else 'unknown',
                    owner=owner if type(owner) is str else 'Not recorded')
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        item.update(state='unknown', owner='Not recorded')
    item['source_stale'] = bool(dirty)
    return item


def _is_work(record):
    if record['kind'] in ('dev-feature', 'dev-criterion'):
        return True
    try:
        payload = json.loads(record['body'].rsplit('\n```json\n', 1)[1].rsplit('\n```', 1)[0])
        node = payload.get('node', payload)
        return type(node) is dict and node.get('type', node.get('kind')) in WORK_TYPES
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return False


def _topology(ids, pairs):
    incoming = {key: set() for key in ids}
    outgoing = {key: set() for key in ids}
    for source, target in pairs:
        if source in incoming and target in incoming:
            outgoing[source].add(target)
            incoming[target].add(source)
    waiting = {key: len(value) for key, value in incoming.items()}
    waves = []
    ready = sorted(key for key in ids if waiting[key] == 0)
    while ready:
        waves.append(ready)
        following = []
        for key in ready:
            for successor in sorted(outgoing[key]):
                waiting[successor] -= 1
                if waiting[successor] == 0:
                    following.append(successor)
        ready = sorted(following)
    return waves, incoming, outgoing


def read_db(db, root):
    require(db is not None and get(db, 'policy') is not None, 'knowledge_not_enabled')
    from .knowledge_work import KEY as RELATIONS_KEY
    dirty = bool(get(db, 'needs_reconcile', True))
    meta = metadata(db)
    warnings = []
    observed = [dict(row) for row in db.execute(
        "SELECT id,title,path,revision,kind,body FROM sources WHERE deleted=0 "
        "AND (kind LIKE 'dev-record:%' OR kind IN ('dev-feature','dev-criterion')) "
        "ORDER BY path LIMIT ?", (MAX_NODES + 1,))]
    records = [row for row in observed if _is_work(row)]
    if len(observed) > MAX_NODES or len(records) > MAX_NODES:
        warn(warnings, 'node_limit')
        records = records[:MAX_NODES]
    nodes = {r['id']: _record(r, dirty) for r in records}
    raw_relations = get(db, RELATIONS_KEY, [])
    require(type(raw_relations) is list and len(raw_relations) <= 128, 'invalid_work_relations')
    source_rows = {r['id']: r for r in records}
    for identifier in {r.get(side) for r in raw_relations if type(r) is dict
                       for side in ('source', 'target') if type(r.get(side)) is str}:
        if identifier not in source_rows:
            row = db.execute('SELECT id,title,path,revision,kind,body FROM sources '
                             'WHERE deleted=0 AND id=?', (identifier,)).fetchone()
            if row:
                source_rows[identifier] = dict(row)
    relation_fingerprint = []
    active = []
    for relation in raw_relations:
        require(type(relation) is dict and all(key in relation for key in
                ('source', 'target', 'kind', 'status', 'source_revision', 'target_revision')),
                'invalid_work_relations')
        relation_fingerprint.append([relation[key] for key in
                                     ('source', 'target', 'kind', 'status', 'source_revision', 'target_revision')])
        if relation['status'] != 'approved':
            continue
        source, target = source_rows.get(relation['source']), source_rows.get(relation['target'])
        if (dirty or not source or not target or source['revision'] != relation['source_revision']
                or target['revision'] != relation['target_revision']):
            warn(warnings, 'stale_relation', (relation['source'], relation['target']))
            continue
        active.append(relation)
        for side in ('source', 'target'):
            identifier = relation[side]
            if identifier not in nodes and relation.get(side + '_role') in WORK_ROLES:
                if len(nodes) >= MAX_NODES:
                    warn(warnings, 'node_limit', (identifier,))
                else:
                    nodes[identifier] = _record(source_rows[identifier], dirty)
    edge_rows = [dict(e) for e in db.execute(
        "SELECT source,target,kind,revision FROM edges WHERE kind LIKE 'canonical_work:%' "
        "OR kind LIKE 'recorded_dev:%' ORDER BY source,target,kind LIMIT ?", (MAX_EDGES + 1,))]
    if len(edge_rows) > MAX_EDGES:
        warn(warnings, 'edge_limit')
        edge_rows = edge_rows[:MAX_EDGES]
    fingerprint = [str(Path(root).resolve()), get(db, 'generation', 0), dirty,
                   [(r['id'], r['revision']) for r in records], relation_fingerprint,
                   [(e['source'], e['target'], e['kind'], e['revision']) for e in edge_rows], meta]
    snapshot = digest(json.dumps(fingerprint, sort_keys=True, ensure_ascii=False))
    hierarchy, raw_dependencies, blockers = [], [], []
    for edge in edge_rows:
        source, target, kind = edge['source'], edge['target'], edge['kind']
        if source not in nodes and target not in nodes:
            continue
        if kind in CONTAINMENT_KINDS:
            parent, child = (target, source) if kind.endswith(':part_of') else (source, target)
            hierarchy.append((parent, child, kind, 'canonical'))
        elif kind in DEPENDENCY_KINDS:
            predecessor, successor = (target, source) if kind.endswith(':depends_on') else (source, target)
            raw_dependencies.append((predecessor, successor, kind, 'canonical'))
    for relation in active:
        source, target, kind = relation['source'], relation['target'], relation['kind']
        if kind == 'part_of':
            hierarchy.append((target, source, kind, 'reviewed'))
        elif kind == 'depends_on':
            raw_dependencies.append((target, source, kind, 'reviewed'))
        elif kind == 'blocks':
            blockers.append((source, target))
    parents = {key: set() for key in nodes}
    children = {key: set() for key in nodes}
    clean_hierarchy = []
    for parent, child, kind, origin in hierarchy:
        if parent not in nodes or child not in nodes:
            warn(warnings, 'missing_hierarchy_endpoint', (parent, child))
            continue
        parents[child].add(parent)
        children[parent].add(child)
        clean_hierarchy.append({'parent': parent, 'child': child, 'kind': kind, 'origin': origin})
    for key, owners in parents.items():
        if len(owners) > 1:
            warn(warnings, 'multiple_parents', (key, *owners))
    hierarchy_waves, _, _ = _topology(nodes, ((e['parent'], e['child']) for e in clean_hierarchy))
    if sum(map(len, hierarchy_waves)) != len(nodes):
        warn(warnings, 'hierarchy_cycle')
    depth = {key: 0 for key in nodes}
    for wave in hierarchy_waves:
        for key in wave:
            for child in children[key]:
                depth[child] = max(depth[child], depth[key] + 1)
    def leaves(identifier):
        todo, found, seen = [identifier], set(), set()
        while todo:
            key = todo.pop()
            if key in seen:
                continue
            seen.add(key)
            if not children[key]:
                found.add(key)
            else:
                todo.extend(children[key])
        return found
    leaf_ids = {key for key in nodes if not children[key]}
    expanded = set()
    dependencies = []
    expanded_limit = False
    for predecessor, successor, kind, origin in raw_dependencies:
        if predecessor not in nodes or successor not in nodes:
            warn(warnings, 'missing_dependency_endpoint', (predecessor, successor))
            continue
        for left in leaves(predecessor):
            for right in leaves(successor):
                if (left, right) not in expanded:
                    if len(expanded) >= MAX_EDGES:
                        expanded_limit = True
                        continue
                    expanded.add((left, right))
                    dependencies.append({'source': left, 'target': right, 'kind': kind,
                                         'origin': origin})
    if expanded_limit:
        warn(warnings, 'edge_limit')
    waves, incoming, outgoing = _topology(leaf_ids, expanded)
    if sum(map(len, waves)) != len(leaf_ids):
        warn(warnings, 'dependency_cycle')
    stage = {key: index for index, wave in enumerate(waves) for key in wave}
    for key in reversed([item for wave in hierarchy_waves for item in wave]):
        if children[key]:
            stage[key] = min((stage[c] for c in children[key] if c in stage), default=0)
    if dirty:
        warn(warnings, 'reconcile_required')
    estimate = {}
    for key, node in nodes.items():
        stored = meta['tasks'].get(key)
        stale = bool(stored and stored['revision'] != node['revision'])
        if stale:
            warn(warnings, 'stale_estimate', (key,))
        estimate[key] = {'duration_days': stored['duration_days'] if stored else None,
                         'buffer_days': stored['buffer_days'] if stored else 0,
                         'earliest_start': stored['earliest_start'] if stored else None,
                         'deadline': stored['deadline'] if stored else None,
                         'revision': stored['revision'] if stored else None,
                         'stale': stale}
    if any(estimate[key]['duration_days'] is None for key in leaf_ids):
        warn(warnings, 'missing_estimate', (key for key in leaf_ids
                                            if estimate[key]['duration_days'] is None))
    if any((estimate[key]['earliest_start'] or estimate[key]['deadline'])
           and not meta['start_date'] for key in leaf_ids):
        warn(warnings, 'date_anchor_required')
    actual = {key: set() for key in leaf_ids}
    for key in nodes:
        if str(nodes[key]['state']).lower() in ('blocked', 'on_hold', 'on-hold'):
            for child in leaves(key):
                if child in actual:
                    actual[child].add(key)
    for source, target in blockers:
        if target not in nodes or source not in source_rows:
            warn(warnings, 'missing_blocker_endpoint', (source, target))
            continue
        for key in leaves(target):
            if key in actual:
                actual[key].add(source)
    for wave in waves:
        for key in wave:
            for successor in outgoing[key]:
                actual[successor].update(actual[key])
    offset, finish, float_days, critical = {}, {}, {}, {}
    structural = {'node_limit', 'edge_limit', 'missing_hierarchy_endpoint', 'multiple_parents',
                  'hierarchy_cycle', 'missing_dependency_endpoint', 'dependency_cycle',
                  'reconcile_required', 'stale_relation', 'missing_blocker_endpoint'}
    timing = {'stale_estimate', 'missing_estimate', 'date_anchor_required'}
    complete = not any(w['code'] in structural for w in warnings)
    critical_available = complete and bool(leaf_ids) and not any(
        w['code'] in timing for w in warnings)
    duration_days = None
    anchor = valid_date(meta['start_date'])
    if critical_available:
        for wave in waves:
            for key in wave:
                est = estimate[key]
                start = max((finish[parent] for parent in incoming[key]), default=0)
                if est['earliest_start']:
                    start = max(start, (valid_date(est['earliest_start']) - anchor).days)
                offset[key] = start
                finish[key] = start + est['duration_days'] + est['buffer_days']
        duration_days = max(finish.values(), default=0)
        if anchor and (anchor.toordinal() + math.ceil(duration_days) > date.max.toordinal()):
            warn(warnings, 'date_out_of_range')
            complete = False
            critical_available = False
        late = {}
        for wave in reversed(waves):
            for key in wave:
                latest_finish = min((late[successor] for successor in outgoing[key]),
                                    default=duration_days)
                late[key] = latest_finish - estimate[key]['duration_days'] - estimate[key]['buffer_days']
                float_days[key] = max(0, latest_finish - finish[key])
                critical[key] = math.isclose(float_days[key], 0, abs_tol=1e-9)
        for key in leaf_ids:
            deadline = estimate[key]['deadline']
            if deadline and anchor and critical_available and anchor + timedelta(days=math.ceil(finish[key])) > valid_date(deadline):
                warn(warnings, 'deadline_missed', (key,))
        owners = {}
        for key in leaf_ids:
            owner = nodes[key]['owner']
            if owner != 'Not recorded':
                owners.setdefault(owner, []).append(key)
        for members in owners.values():
            for i, left in enumerate(members):
                for right in members[i+1:]:
                    if offset[left] < finish[right] and offset[right] < finish[left]:
                        warn(warnings, 'owner_overlap', (left, right))
    else:
        owners = {}
        for key in leaf_ids:
            owner = nodes[key]['owner']
            if owner != 'Not recorded':
                owners.setdefault((stage.get(key), owner), []).append(key)
        for members in owners.values():
            if len(members) > 1:
                warn(warnings, 'owner_parallel_conflict', members)
    if not critical_available:
        offset.clear()
        finish.clear()
        float_days.clear()
        critical.clear()
        duration_days = None
    else:
        for key in nodes:
            if key not in leaf_ids:
                descendants = leaves(key)
                offset[key] = min(offset[child] for child in descendants)
                finish[key] = max(finish[child] for child in descendants)
                float_days[key] = min(float_days[child] for child in descendants)
                critical[key] = any(critical[child] for child in descendants)
    result_nodes = []
    for key, node in sorted(nodes.items(), key=lambda pair: (depth[pair[0]], pair[1]['path'])):
        descendants = leaves(key)
        blocked = sorted(set().union(*(actual.get(child, set()) for child in descendants)))
        waiting = sorted({parent for child in descendants for parent in incoming.get(child, ())
                          if str(nodes[parent]['state']).lower() not in DONE_STATES})
        state = str(node['state']).lower()
        readiness = ('unknown' if not complete else 'complete' if state in DONE_STATES else
                     'blocked' if blocked else 'waiting' if waiting else 'ready')
        is_leaf = key in leaf_ids
        start = offset.get(key)
        end = finish.get(key)
        predecessors = sorted({predecessor for child in descendants
                               for predecessor in incoming.get(child, ())
                               if predecessor not in descendants})
        successors = sorted({successor for child in descendants
                             for successor in outgoing.get(child, ())
                             if successor not in descendants})
        result_nodes.append({**node, 'parents': sorted(parents[key]), 'children': sorted(children[key]),
                             'leaf': is_leaf, 'depth': depth[key], 'stage': stage.get(key),
                             'readiness': readiness, 'blocked_by': blocked, 'waiting_on': waiting,
                             'predecessors': predecessors, 'successors': successors,
                             'estimate': estimate[key],
                             'earliest_start': start, 'earliest_finish': end,
                             'start_date': (anchor + timedelta(days=math.floor(start))).isoformat()
                             if anchor and start is not None and critical_available else None,
                             'finish_date': (anchor + timedelta(days=math.ceil(end))).isoformat()
                             if anchor and end is not None and critical_available else None,
                             'float_days': float_days.get(key), 'critical': critical.get(key, False)})
    return {'snapshot': snapshot, 'start_date': meta['start_date'], 'complete': complete,
            'critical_available': critical_available, 'duration_days': duration_days,
            'nodes': result_nodes, 'dependencies': dependencies, 'hierarchy': clean_hierarchy,
            'waves': [{'stage': index, 'ids': wave} for index, wave in enumerate(waves)],
            'warnings': warnings, 'notice': 'Planning estimates use calendar days and do not change canonical work state.'}


def dispatch(root, action, value):
    with database(root) as db:
        state = read_db(db, root)
        if action == 'work-schedule-view':
            require(value is None)
            return state
        require(action == 'work-schedule-save' and type(value) is dict and
                set(value) == {'snapshot', 'start_date', 'task', 'reason'})
        require(type(value['snapshot']) is str and value['snapshot'] == state['snapshot'],
                'work_schedule_snapshot_changed')
        require(type(value['reason']) is str and 5 <= len(value['reason'].strip()) <= 2000)
        require(not any(w['code'] == 'reconcile_required' for w in state['warnings']),
                'reconcile_required')
        valid_date(value['start_date'])
        meta = metadata(db)
        task = value['task']
        if task is not None:
            require(type(task) is dict and set(task) == {'id', 'revision', 'duration_days',
                                                         'buffer_days', 'earliest_start', 'deadline'})
            require(type(task['id']) is str and type(task['revision']) is str)
            current = next((n for n in state['nodes'] if n['id'] == task['id']), None)
            require(current is not None and current['revision'] == task['revision'] and
                    current['leaf'], 'schedule_task_changed')
            valid_number(task['duration_days'], 3650, True)
            valid_number(task['buffer_days'], 365)
            valid_date(task['earliest_start'])
            valid_date(task['deadline'])
            meta['tasks'][task['id']] = {key: task[key] for key in
                                        ('revision', 'duration_days', 'buffer_days',
                                         'earliest_start', 'deadline')}
        meta['start_date'] = value['start_date']
        meta['revision'] += 1
        validate_meta(meta)
        with db:
            put(db, KEY, meta)
        return read_db(db, root)
