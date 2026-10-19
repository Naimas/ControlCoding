"""Immutable checkpoint history and bounded cold idempotency lookup.

Checkpoint provenance is local and does not authenticate actors. Exact original
events are retained; reconstructed validation records never leave this module.
"""
import copy
import hashlib

import cc_development_store as store

HOT_LIMIT = 2048
ANCHOR_LIMIT = 2048
PAGE_SIZE = 64
LOOKUP_LIMIT = 66
SEGMENT_LIMIT = 256


def _require(value, code='invalid_retention'):
    if not value:
        raise store.StoreError(code)


def _reader(safe, reader=None):
    if getattr(reader, '_retention_cache', False): return reader
    original = reader or (lambda ref: store.get_object(safe, ref))
    cache = {}
    total = 0
    def cached(ref):
        nonlocal total
        store._ref(ref)
        if ref not in cache:
            value = original(ref)
            total += len(store._encoded(value))
            _require(len(cache) < store.MAX_OBJECTS and total <= store.MAX_BYTES, 'retention_validation_budget')
            cache[ref] = value
        return cache[ref]
    cached._retention_cache = True
    return cached


def index_lookup(root, key, reader):
    store._ref(key)
    if root is None:
        return None
    prefix = ''
    for _ in range(LOOKUP_LIMIT):
        node = reader(store._ref(root))
        _require(type(node) is dict and node.get('prefix') == prefix, 'retention_index_invalid')
        if node.get('kind') == 'retention_leaf':
            _require(set(node) == {'kind', 'prefix', 'entries'} and type(node['entries']) is dict
                     and len(node['entries']) <= PAGE_SIZE, 'retention_index_invalid')
            for item, ref in node['entries'].items():
                store._ref(item)
                store._ref(ref)
                _require(item.startswith(prefix), 'retention_index_invalid')
            return node['entries'].get(key)
        _require(node.get('kind') == 'retention_index' and set(node) == {'kind', 'prefix', 'children'}
                 and type(node['children']) is dict and len(prefix) < 64
                 and set(node['children']).issubset(set('0123456789abcdef')), 'retention_index_invalid')
        for ref in node['children'].values():
            store._ref(ref)
        char = key[len(prefix)]
        if char not in node['children']:
            return None
        root = node['children'][char]
        prefix += char
    raise store.StoreError('retention_lookup_incomplete')


def _insert(safe, root, entries, prefix=''):
    old = store.get_object(safe, root) if root else None
    if old:
        _require(old.get('prefix') == prefix, 'retention_index_invalid')
    if old is None or old.get('kind') == 'retention_leaf':
        combined = dict(old['entries']) if old else {}
        for key, value in entries.items():
            if key in combined and combined[key] != value:
                raise store.StoreError('retention_index_conflict')
            combined[key] = value
        if len(combined) <= PAGE_SIZE:
            return store.put_object(safe, {'kind': 'retention_leaf', 'prefix': prefix, 'entries': combined})
        entries = combined
        children = {}
    else:
        _require(old.get('kind') == 'retention_index', 'retention_index_invalid')
        children = dict(old['children'])
    _require(len(prefix) < 64, 'retention_index_invalid')
    groups = {}
    for key, value in entries.items():
        store._ref(key)
        groups.setdefault(key[len(prefix)], {})[key] = value
    for char, group in groups.items():
        children[char] = _insert(safe, children.get(char), group, prefix + char)
    return store.put_object(safe, {'kind': 'retention_index', 'prefix': prefix, 'children': children})


def lookup_operation(safe, manifest, operation_id, object_reader=None):
    retention = manifest.get('retention') if manifest else None
    if not retention:
        return None
    reader = _reader(safe, object_reader)
    ref = index_lookup(retention['replay_root'], operation_id, reader)
    if ref is None:
        return None
    receipt = _receipt_value(ref,reader)
    _receipt(receipt, operation_id)
    return receipt


def _receipt_value(ref,reader):
    value=reader(ref)
    if type(value) is dict and value.get('kind') in ('planning_operation','packed_operation'):
        _require(set(value)=={'kind','ref'},'retention_receipt_invalid')
        value=store.unpack_reference(value['ref'],reader)
    return value


def _receipt(value, key):
    from cc_development_lib.schema import validate_action, revision
    fields = {'operation_id', 'approval_id', 'expected_revision', 'action', 'timestamp', 'provenance'}
    _require(type(value) is dict and set(value) == fields and value['operation_id'] == key
             and value['approval_id'] == key and value['provenance'] == 'local_revision'
             and type(value['timestamp']) is str, 'retention_receipt_invalid')
    revision(key); revision(value['expected_revision']); validate_action(value['action'])


def _index_entries(root, reader, prefix='', seen=None):
    if root is None: return {}
    seen = seen if seen is not None else set()
    _require(root not in seen and len(seen) < 262144, 'retention_index_invalid')
    seen.add(root)
    node = reader(root)
    _require(type(node) is dict, 'retention_index_invalid')
    _require(node.get('prefix') == prefix, 'retention_index_invalid')
    if node.get('kind') == 'retention_leaf':
        _require(set(node) == {'kind', 'prefix', 'entries'} and len(node['entries']) <= PAGE_SIZE, 'retention_index_invalid')
        for key, ref in node['entries'].items():
            store._ref(key); store._ref(ref)
            _require(key.startswith(prefix), 'retention_index_invalid')
        return dict(node['entries'])
    _require(node.get('kind') == 'retention_index' and set(node) == {'kind', 'prefix', 'children'}
             and len(prefix) < 64 and set(node['children']).issubset(set('0123456789abcdef')), 'retention_index_invalid')
    result = {}
    for char, ref in node['children'].items():
        values = _index_entries(ref, reader, prefix + char, seen)
        _require(not set(result).intersection(values), 'retention_index_invalid')
        result.update(values)
    return result


def _events(change, reader):
    refs = []
    base = change.get('history_base')
    if base:
        checkpoint = reader(base['ref'])
        root = checkpoint['history_root']
        segments = []
        seen = set()
        while root is not None:
            _require(root not in seen and len(segments) < SEGMENT_LIMIT, 'history_page_budget')
            seen.add(root)
            segment = reader(root)
            _require(type(segment) is dict and segment.get('kind') == 'history_sequence'
                     and type(segment.get('parts')) is list and len(segment['parts']) <= 32768 // PAGE_SIZE,
                     'history_sequence_invalid')
            segments.append(segment)
            root = segment['previous']
        for segment in reversed(segments):
            for ref in segment['parts']:
                page = reader(ref)
                _require(type(page) is dict and set(page) == {'kind', 'events'} and page['kind'] == 'history_page'
                         and type(page['events']) is list and len(page['events']) <= PAGE_SIZE, 'history_page_invalid')
                refs.extend(page['events'])
    return [reader(ref) for ref in refs] + copy.deepcopy(change['history'])


def full_history(safe, change, object_reader=None):
    events = _events(change, _reader(safe, object_reader))
    previous = 'absent'
    for event in events:
        _event(event, previous)
        previous = event['digest']
    return events


def _event(event, previous=None):
    from cc_development_lib.method import digest
    from cc_development_lib.schema import validate_action
    _require(type(event) is dict and set(event) == {'action', 'result', 'timestamp', 'previous', 'digest'}, 'invalid_history')
    _require(event['digest'] == digest({k: v for k, v in event.items() if k != 'digest'})
             and (previous is None or event['previous'] == previous), 'invalid_history')
    validate_action(event['action'])


def _selected(events):
    """Keep current stage/actor decisions and needed operational originals."""
    latest = {}
    keep = set()
    for index, event in enumerate(events):
        action = event['action']
        kind = action['kind']
        if kind == 'artifact':
            latest[('artifact', action['stage'])] = index
        elif kind == 'decide':
            latest[('decide', action['stage'], action['actor'])] = index
            latest[('decide_stage', action['stage'])] = index
            latest[('criterion_aggregate',)] = index
        elif kind in ('create', 'close'):
            latest[(kind,)] = index
        else:
            # Operational arrays have their own bounded domain contracts. Keep
            # every retained original needed for recurrence/release/ownership.
            keep.add(index)
    keep.update(latest.values())
    _require(len(keep) <= ANCHOR_LIMIT, 'checkpoint_active_scope_limit')
    return [events[index] for index in sorted(keep)]


def _synthetic(events):
    from cc_development_lib.method import digest
    previous = 'absent'
    result = []
    for original in events:
        event = copy.deepcopy(original)
        event['previous'] = previous
        event['digest'] = digest({k: v for k, v in event.items() if k != 'digest'})
        result.append(event)
        previous = event['digest']
    return result


def _projection(change, selected):
    """Derive bounded working fields from original anchored event results."""
    result = copy.deepcopy(change)
    result.pop('history_base', None)
    result['history'] = []
    result.update(artifacts={}, decisions=[], criterionDecisions=[], candidate=None, evidence=[], reviews=[],
                  releases=[], observations=[], closed=False, incidents=[], incident_captures=[], incident_decisions=[])
    from cc_development_operations import incidents_from_captures
    for event in selected:
        action, value = event['action'], event['result']
        kind = action['kind']
        if kind == 'artifact':
            result['artifacts'].setdefault(action['stage'], []).append(copy.deepcopy(value))
        elif kind == 'candidate':
            result['candidate'] = copy.deepcopy(value)
        elif kind == 'decide':
            result['decisions'].append(copy.deepcopy(value['decision']))
            result['criterionDecisions'] = copy.deepcopy(value['criterion_decisions'])
        elif kind in ('evidence', 'review', 'release', 'observation'):
            result[{'evidence': 'evidence', 'review': 'reviews', 'release': 'releases', 'observation': 'observations'}[kind]].append(copy.deepcopy(value))
        elif kind == 'incident_capture' and not value['replayed']:
            result['incident_captures'].append(copy.deepcopy(value['capture']))
            result['observations'].append(copy.deepcopy(value['observation']))
            result['incidents'] = incidents_from_captures(result['incident_captures'])
        elif kind in ('incident_decide', 'incident_followup'):
            result['incident_decisions'].append(copy.deepcopy(value['decision']))
        elif kind == 'close':
            result['closed'] = True
    return result


def base_projection(safe, change, object_reader=None, *, expected_project=None, expected_method=None, compatibility=None):
    reader = _reader(safe, object_reader)
    descriptor = change['history_base']
    _require(type(descriptor) is dict and set(descriptor) == {'ref', 'head', 'count'}, 'history_checkpoint_invalid')
    node = reader(descriptor['ref'])
    fields = {'kind', 'identity', 'projection_ref', 'source_ref', 'source_position', 'source_manifest_ref', 'anchors', 'event_root', 'history_root', 'count', 'head'}
    _require(type(node) is dict and set(node) == fields and node['kind'] == 'history_checkpoint'
             and node['head'] == descriptor['head'] and node['count'] == descriptor['count']
             and type(node['anchors']) is list and len(node['anchors']) <= ANCHOR_LIMIT, 'history_checkpoint_invalid')
    sequence = reader(node['history_root'])
    _require(sequence['checkpoint'] == {k: node[k] for k in ('identity', 'projection_ref', 'source_ref', 'source_position', 'source_manifest_ref', 'anchors', 'event_root', 'count', 'head')},
             'history_checkpoint_source_mismatch')
    anchors = []
    positions = []
    for anchor in node['anchors']:
        _require(type(anchor) is dict and set(anchor) == {'digest', 'ref', 'position'}, 'history_anchor_invalid')
        _require(index_lookup(node['event_root'], anchor['digest'], reader) == anchor['ref'], 'history_anchor_missing')
        event = reader(anchor['ref'])
        _event(event)
        _require(event['digest'] == anchor['digest'] and event['action'].get('change_id') == change['id'], 'history_anchor_invalid')
        anchors.append(event)
        positions.append(anchor['position'])
    _require(positions == sorted(set(positions)) and positions and positions[0] == 0
             and positions[-1] < node['count'], 'history_anchor_order')
    projection = store.unpack_reference(node['projection_ref'], reader)
    _require(type(projection) is dict and projection.get('id') == change['id'], 'history_checkpoint_invalid')
    derived = _projection(projection, anchors)
    _require(derived == projection, 'history_checkpoint_projection_mismatch')
    # Independent precheckpoint working source: compare latest field values,
    # rather than trusting a rehashed checkpoint's duplicated selection claims.
    source_manifest = store._decode(store.read_archive_bytes(None, node['source_manifest_ref'], object_reader=reader))
    _require(source_manifest['state_ref'] == node['source_ref'], 'history_checkpoint_source_mismatch')
    source_project = store.path_value(node['source_ref'], ['project_identity'], reader)
    source_method = store.path_value(node['source_ref'], ['method'], reader)
    _require(source_manifest['project_identity'] == source_project
             and source_manifest['method'] == source_method
             and (expected_project is None or source_project == expected_project), 'history_checkpoint_source_identity')
    if expected_method is not None:
        from cc_development_compatibility import identity_checker
        _require(identity_checker(expected_method, compatibility)(source_method), 'history_checkpoint_source_method')
    source_path = ['changes', node['source_position']]
    try:
        source_base = store.path_value(node['source_ref'], source_path + ['history_base'], reader)
    except (KeyError, store.StoreError) as error:
        if isinstance(error, store.StoreError) and error.code != 'object_path_missing': raise
        source_base = None
    previous_count = source_base['count'] if source_base else 0
    source_tail_length = store.path_length(node['source_ref'], source_path + ['history'], reader)
    source_head = (store.path_value(node['source_ref'], source_path + ['history', -1], reader)['digest']
                   if source_tail_length else source_base['head'] if source_base else 'absent')
    _require(node['count'] == previous_count + source_tail_length and node['head'] == source_head, 'history_checkpoint_source_mismatch')
    previous_anchors = reader(source_base['ref'])['anchors'] if source_base else []
    previous_by_digest = {a['digest']: a for a in previous_anchors}
    for anchor, event in zip(node['anchors'], anchors):
        if anchor['position'] < previous_count:
            _require(previous_by_digest.get(anchor['digest']) == anchor, 'history_anchor_position_mismatch')
        else:
            expected_event = store.path_value(node['source_ref'], source_path + ['history', anchor['position'] - previous_count], reader)
            _require(expected_event == event, 'history_anchor_position_mismatch')
    source_artifacts = store.path_keys(node['source_ref'], source_path + ['artifacts'], reader)
    _require(set(source_artifacts) == set(projection['artifacts']), 'history_checkpoint_latest_mismatch')
    for stage in source_artifacts:
        expected = store.path_value(node['source_ref'], source_path + ['artifacts', stage, -1], reader)
        _require(projection['artifacts'].get(stage) == [expected], 'history_checkpoint_latest_mismatch')
    source_decisions = store.path_value(node['source_ref'], source_path + ['decisions'], reader)
    last = {}
    for index, decision in enumerate(source_decisions):
        last[('actor', decision['stage'], decision['actor'])] = index
        last[('stage', decision['stage'])] = index
        last[('aggregate',)] = index
    _require([source_decisions[index] for index in sorted(set(last.values()))] == projection['decisions'], 'history_checkpoint_latest_mismatch')
    for field in ('candidate', 'criterionDecisions', 'evidence', 'reviews', 'releases', 'observations', 'closed',
                  'incidents', 'incident_captures', 'incident_decisions'):
        _require(store.path_value(node['source_ref'], source_path + [field], reader) == projection[field], 'history_checkpoint_latest_mismatch')
    creation = anchors[0]['result']
    _require(anchors[0]['action']['kind'] == 'create'
             and all(projection.get(key) == value for key, value in creation.items()), 'history_checkpoint_identity_mismatch')
    _require(node['identity'] == {k: projection[k] for k in ('id', 'method', 'policy_revision', 'created_at', 'origin')},
             'history_checkpoint_identity_mismatch')
    return copy.deepcopy(projection), copy.deepcopy(anchors)


def compact_validation_state(safe, state, object_reader=None, compatibility=None):
    """Internal bounded validation view; never exported as authentic history."""
    result = copy.deepcopy(state)
    retention = result.pop('retention', None)
    reader = _reader(safe, object_reader)
    if retention:
        validate_state(state, {'retention': retention, 'compatibility': compatibility}, reader, full=False)
        activation = retention['activation']
        if activation and not any(r['action']['kind'] == 'activate' for r in result['operations']):
            result['operations'].insert(0, activation)
    for change in result['changes']:
        if 'history_base' not in change:
            continue
        _, anchors = base_projection(safe, change, reader, expected_project=state['project_identity'],
                                     expected_method=state['method'], compatibility=compatibility)
        tail = change['history']
        previous = change['history_base']['head']
        for event in tail:
            _event(event, previous)
            previous = event['digest']
        change.pop('history_base')
        change['history'] = _synthetic(anchors + tail)
    return result


def preserved_history_prefix(safe, change, oldchange, object_reader=None):
    reader = _reader(safe, object_reader)
    if 'history_base' not in change:
        return change['history'][:len(oldchange['history'])] == oldchange['history']
    return full_history(safe, change, object_reader)[:len(oldchange['history'])] == oldchange['history']


def validate_lineage_anchor(safe, state, manifest, original_archive_ref, source_method, target_method,
                            object_reader=None, full=False):
    reader = _reader(safe, object_reader)
    retention = state.get('retention')
    _require(retention is not None and retention == manifest.get('retention'), 'retention_lineage_invalid')
    node = reader(retention['lineage_ref'])
    _require(node == {'kind': 'retention_lineage', 'project_identity': state['project_identity'],
                      'method': state['method'], 'archives': manifest['archives'], 'compatibility': manifest['compatibility']},
             'retention_lineage_invalid')
    records = [r for r in node['archives'] if r.get('archive_ref') == original_archive_ref]
    _require(len(records) == 1 and records[0]['source_method'] == source_method
             and records[0]['target_method'] == target_method, 'retention_lineage_invalid')
    archive = reader(original_archive_ref)
    _require(archive.get('kind') == 'exact_bytes' and archive.get('sha256') == records[0]['source_revision'], 'retention_lineage_invalid')
    return True


def validate_state(state, manifest, payload_reader, full=True, safe=None):
    payload_reader = _reader(safe, payload_reader)
    _require(state.get('retention') == manifest.get('retention'), 'retention_manifest_mismatch')
    retention = state.get('retention')
    cold_receipts = []
    if retention:
        _require(type(retention) is dict and set(retention) == {'replay_root', 'activation', 'count', 'lineage_ref', 'changes', 'checkpoints_root'}
                 and type(retention['count']) is int and retention['count'] >= 0 and type(retention['changes']) is dict,
                 'invalid_retention')
        _require(retention['changes'] == {c['id']: c['history_base']['ref'] for c in state['changes'] if 'history_base' in c}, 'retention_change_map_mismatch')
        if retention['activation']:
            ref = index_lookup(retention['replay_root'], retention['activation']['operation_id'], payload_reader)
            _require(ref is not None and payload_reader(ref) == retention['activation'], 'retention_activation_mismatch')
        if full:
            entries = _index_entries(retention['replay_root'], payload_reader)
            _require(len(entries) == retention['count'], 'retention_count_mismatch')
            hot_ids = {r['operation_id'] for r in state['operations']}
            for key, ref in entries.items():
                receipt = _receipt_value(ref,payload_reader)
                _receipt(receipt, key)
                _require(key not in hot_ids, 'duplicate_retention_operation')
                cold_receipts.append(receipt)
    for change in state['changes']:
        if 'history_base' in change:
            base_projection(None, change, payload_reader, expected_project=state['project_identity'],
                            expected_method=state['method'], compatibility=manifest.get('compatibility'))
            if full:
                events = full_history(None, change, payload_reader)
                _require(len(events) == change['history_base']['count'] + len(change['history']), 'history_count_mismatch')
                node = payload_reader(change['history_base']['ref'])
                original = events[:node['count']]
                _require(original and original[-1]['digest'] == node['head'], 'history_head_mismatch')
                expected = _selected(original)
                _require([payload_reader(a['ref']) for a in node['anchors']] == expected, 'history_checkpoint_latest_mismatch')
                _require([original[a['position']] for a in node['anchors']] == expected, 'history_anchor_position_mismatch')
    if full and safe is not None:
        from cc_development_lib import storage
        expanded = copy.deepcopy(state)
        expanded.pop('retention', None)
        expanded['operations'] = cold_receipts + expanded['operations']
        for index, change in enumerate(expanded['changes']):
            originals = full_history(safe, state['changes'][index], payload_reader)
            replacement = _projection(change, originals)
            replacement['history'] = originals
            expanded['changes'][index] = replacement
        storage.validate_state(safe, expanded, compatibility=manifest['compatibility'])


def collect_references(manifest, payload_reader):
    payload_reader = _reader(None, payload_reader)
    refs = set()
    sources = set()
    def retention_roots(value):
        if value:
            visit(value['replay_root']); visit(value['lineage_ref']); visit(value.get('checkpoints_root'))
            for child in value['changes'].values(): visit(child)
    def source_roots(ref):
        if ref in sources: return
        sources.add(ref)
        try:
            retention_roots(store.path_value(ref, ['retention'], payload_reader))
        except (KeyError, store.StoreError) as error:
            if isinstance(error, store.StoreError) and error.code != 'object_path_missing': raise
        count = store.path_length(ref, ['changes'], payload_reader)
        _require(count <= 256, 'retention_source_limit')
        for index in range(count):
            try:
                descriptor = store.path_value(ref, ['changes', index, 'history_base'], payload_reader)
            except (KeyError, store.StoreError) as error:
                if isinstance(error, store.StoreError) and error.code != 'object_path_missing': raise
                continue
            visit(descriptor['ref'])
    def graph(ref):
        if ref in refs: return
        store._ref(ref)
        _require(len(refs) < 262144, 'retention_closure_limit')
        refs.add(ref)
        node = payload_reader(ref)
        _require(type(node) is dict and node.get('type') in ('value', 'scalar', 'dict', 'list', 'text','planning'), 'retention_graph_invalid')
        if node['type']=='planning': graph(node['ref'])
        if node['type'] in ('dict', 'list', 'text'):
            for part in node['parts']:
                store._ref(part); refs.add(part)
                values = payload_reader(part)
                if node['type'] == 'dict':
                    for pair in values: graph(pair[1])
                elif node['type'] == 'list':
                    for child in values: graph(child)
    def checkpoint_children(cp):
        graph(cp['projection_ref']); graph(cp['source_ref'])
        source_roots(cp['source_ref'])
        visit(cp['source_manifest_ref']); visit(cp['event_root'])
        for anchor in cp['anchors']: visit(anchor['ref'])
    def visit(ref):
        if ref is None or ref in refs:
            return
        store._ref(ref)
        _require(len(refs) < 262144, 'retention_closure_limit')
        refs.add(ref)
        value = payload_reader(ref)
        if type(value) is not dict: return
        kind = value.get('kind')
        if kind == 'retention_index':
            for child in value['children'].values(): visit(child)
        elif kind == 'retention_leaf':
            for child in value['entries'].values(): visit(child)
        elif kind in ('planning_operation','packed_operation'):
            graph(value['ref'])
        elif kind == 'history_sequence':
            visit(value['previous'])
            for child in value['parts']: visit(child)
            cp = value['checkpoint']
            checkpoint_children(cp)
        elif kind == 'history_page':
            for child in value['events']: visit(child)
        elif kind == 'history_checkpoint':
            checkpoint_children(value); visit(value['history_root'])
        elif kind == 'exact_bytes':
            for child in value['parts']: visit(child)
        elif kind == 'retention_lineage':
            for record in value['archives']: visit(record['archive_ref'])
    retention = manifest.get('retention')
    if retention:
        retention_roots(retention)
    return refs


def _compact(safe, state, manifest):
    result = copy.deepcopy(state)
    old = state.get('retention') or {}
    replay_root = old.get('replay_root')
    cold = result['operations']
    entries = {}
    for receipt in cold:
        if receipt['action']['kind'].startswith('product_'):
            # Use the same typed action path as the published hot receipt. Its
            # planning payload already exists; checkpoint adds control only.
            packed=store._pack(safe,receipt,path=('operations',0))
            entries[receipt['operation_id']]=store.put_object(safe,{'kind':'packed_operation','ref':packed})
        else:
            entries[receipt['operation_id']] = store.put_object(safe, receipt)
    replay_root = _insert(safe, replay_root, entries) if entries else replay_root
    activation = old.get('activation') or next((r for r in cold if r['action']['kind'] == 'activate'), None)
    changes = {}
    source_manifest_ref = store.archive_bytes(safe, store._read(safe, store.MANIFEST, store.MANIFEST_LIMIT))
    for source_position, change in enumerate(result['changes']):
        tail = change['history']
        previous_base = change.get('history_base')
        if previous_base and not tail:
            changes[change['id']] = previous_base['ref']
            continue
        old_node = store.get_object(safe, previous_base['ref']) if previous_base else None
        old_count = old_node['count'] if old_node else 0
        old_anchors = [store.get_object(safe, a['ref']) for a in old_node['anchors']] if old_node else []
        selected = _selected(old_anchors + tail)
        projection = _projection(change, selected)
        projection_ref = store._pack(safe, projection)
        source_ref = manifest['state_ref']
        event_entries = {}
        event_refs = []
        for event in tail:
            ref = store.put_object(safe, event)
            event_entries[event['digest']] = ref
            event_refs.append(ref)
        event_root = _insert(safe, old_node['event_root'] if old_node else None, event_entries) if event_entries else old_node['event_root']
        positions = {a['digest']: a['position'] for a in old_node['anchors']} if old_node else {}
        positions.update({e['digest']: old_count + i for i, e in enumerate(tail)})
        anchors = [{'digest': e['digest'], 'ref': index_lookup(event_root, e['digest'], lambda r: store.get_object(safe, r)),
                    'position': positions[e['digest']]} for e in selected]
        cp = {'identity': {k: projection[k] for k in ('id', 'method', 'policy_revision', 'created_at', 'origin')},
              'projection_ref': projection_ref, 'source_ref': source_ref, 'source_position': source_position,
              'source_manifest_ref': source_manifest_ref, 'anchors': anchors, 'event_root': event_root,
              'count': old_count + len(tail), 'head': tail[-1]['digest'] if tail else old_node['head']}
        parts = [store.put_object(safe, {'kind': 'history_page', 'events': event_refs[i:i+PAGE_SIZE]})
                 for i in range(0, len(event_refs), PAGE_SIZE)]
        history_root = store.put_object(safe, {'kind': 'history_sequence', 'previous': old_node['history_root'] if old_node else None,
                                               'parts': parts, 'start': old_count, 'size': len(tail), 'checkpoint': cp})
        node = {'kind': 'history_checkpoint', **cp, 'history_root': history_root}
        base_ref = store.put_object(safe, node)
        compact = copy.deepcopy(projection)
        compact['history'] = []
        compact['history_base'] = {'ref': base_ref, 'head': cp['head'], 'count': cp['count']}
        change.clear(); change.update(compact)
        changes[change['id']] = base_ref
    lineage = store.put_object(safe, {'kind': 'retention_lineage', 'project_identity': state['project_identity'],
                                    'method': state['method'], 'archives': manifest['archives'], 'compatibility': manifest['compatibility']})
    result['operations'] = []
    result['retention'] = {'replay_root': replay_root, 'activation': activation,
                           'count': old.get('count', 0) + len(cold), 'lineage_ref': lineage, 'changes': changes,
                           'checkpoints_root': old.get('checkpoints_root')}
    return result


def checkpoint_prepare(project):
    from cc_development_lib import storage, check_root
    from cc_development_lib.method import digest, IDENTITY
    from cc_evidence_inputs import SafeRoot
    check_root(project)
    with SafeRoot(project) as safe:
        state, revision = storage.load(safe)
        _, _, manifest = store.read_state(safe)
        _require(manifest is not None, 'checkpoint_requires_current_store')
        binding = {'kind': 'history_checkpoint', 'revision': revision, 'project_identity': state['project_identity'], 'method': IDENTITY}
        return {**binding, 'approval_id': digest(binding), 'read_only': True, 'operations': len(state['operations']),
                'history_records': sum(len(c['history']) for c in state['changes'])}


def checkpoint_commit(project, expected_revision, approval_id, actor):
    from cc_development_lib import storage, check_root
    from cc_development_lib.method import digest, IDENTITY
    from cc_evidence_inputs import SafeRoot
    check_root(project)
    _require(type(actor) is str and actor.strip() and len(actor) <= 160, 'invalid_actor')
    with SafeRoot(project) as safe:
        with storage.transaction(safe, approval_id):
            state, revision = storage.load(safe, locked=True)
            _, _, manifest = store.read_state(safe)
            retained = state.get('retention') or {}
            previous_ref = index_lookup(retained.get('checkpoints_root'), approval_id, lambda r: store.get_object(safe, r))
            if previous_ref:
                receipt = store.get_object(safe, previous_ref)
                _require(receipt['expected_revision'] == expected_revision and receipt['actor'] == actor, 'checkpoint_idempotency_conflict')
                return {**receipt, 'revision': revision, 'idempotent': True}
            binding = {'kind': 'history_checkpoint', 'revision': expected_revision,
                       'project_identity': state['project_identity'], 'method': IDENTITY}
            _require(revision == expected_revision and digest(binding) == approval_id, 'checkpoint_preview_stale')
            compact = _compact(safe, state, manifest)
            receipt = {'kind': 'checkpoint_receipt', 'approval_id': approval_id, 'expected_revision': expected_revision,
                       'actor': actor, 'cold_operations': compact['retention']['count'], 'hot_operations': 0}
            receipt_ref = store.put_object(safe, receipt)
            compact['retention']['checkpoints_root'] = _insert(safe, retained.get('checkpoints_root'), {approval_id: receipt_ref})
            storage.validate_state(safe, compact, compatibility=manifest['compatibility'])
            new_revision = store.write_state(safe, compact, revision, {'retention': compact['retention']})
            return {**receipt, 'revision': new_revision, 'idempotent': False}


def page(project, change_id, cursor=None, limit=64, expected_revision=None):
    from cc_development_lib import storage
    from cc_evidence_inputs import SafeRoot
    with SafeRoot(project) as safe:
        state, revision = storage.load(safe)
        return page_state(safe,state,revision,change_id,cursor,limit,expected_revision)


def page_state(safe, state, revision, change_id, cursor=None, limit=64, expected_revision=None):
    """Existing history pagination against an authenticated retained state."""
    _require(type(limit) is int and 1 <= limit <= PAGE_SIZE, 'history_page_limit')
    if expected_revision is not None: _require(revision == expected_revision, 'history_revision_changed')
    change = next((c for c in state['changes'] if c['id'] == change_id), None)
    _require(change is not None, 'change_missing')
    offset = 0 if cursor is None else cursor['offset']
    _require(cursor is None or cursor['revision'] == revision, 'history_revision_changed')
    base = change.get('history_base')
    cold_count = base['count'] if base else 0
    total = cold_count + len(change['history'])
    _require(type(offset) is int and 0 <= offset <= total, 'history_cursor_invalid')
    selected = []
    segments = []
    if base and offset < cold_count:
        root = store.get_object(safe, base['ref'])['history_root']
        seen = set()
        while root is not None:
            _require(root not in seen and len(seen) < SEGMENT_LIMIT, 'history_page_budget')
            seen.add(root)
            segment = store.get_object(safe, root)
            start, size = segment['start'], segment['size']
            if start < offset + limit and start + size > offset:
                segments.append(segment)
            if start <= offset: break
            root = segment['previous']
        for segment in reversed(segments):
            first = max(0, offset - segment['start'])
            last = min(segment['size'], offset + limit - segment['start'])
            for index in range(first // PAGE_SIZE, (last + PAGE_SIZE - 1) // PAGE_SIZE):
                values = store.get_object(safe, segment['parts'][index])['events']
                for local in range(max(first, index * PAGE_SIZE), min(last, index * PAGE_SIZE + len(values))):
                    selected.append(store.get_object(safe, values[local % PAGE_SIZE]))
    if offset + limit > cold_count:
        selected.extend(copy.deepcopy(change['history'][max(0, offset-cold_count):max(0, offset+limit-cold_count)]))
    for event in selected: _event(event)
    return {'revision': revision, 'records': selected, 'total': total,
            'cursor': {'revision': revision, 'offset': offset + len(selected)} if offset + len(selected) < total else None}
