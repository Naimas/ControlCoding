"""Local planning conversations, independent of Development control revision.

Actor labels are plaintext local provenance, not authenticated identities.
All publications use the Core publication barrier; no provider is invoked.
"""
from __future__ import annotations

import datetime
import json
import re
import stat

import cc_evidence_inputs as inputs
from cc_layout import managed_relative
import cc_development_store as store

SESSIONS = '.controlcoding/development/sessions'
MAX_TURNS = 64
MAX_BYTES = 256 * 1024
MAX_SESSIONS = 256
HASH = re.compile(r'[0-9a-f]{64}\Z')
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z')


def _require(value, code):
    if not value:
        raise store.StoreError(code)


def _bytes(value):
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False).encode() + b'\n'


def _id(value):
    _require(type(value) is str and ID.fullmatch(value), 'session_id_invalid')


def _revision(value):
    _require(type(value) is str and (value == 'absent' or HASH.fullmatch(value)), 'session_revision_invalid')


def _text(value, maximum, code):
    _require(type(value) is str and bool(value.strip()) and len(value) <= maximum and '\x00' not in value, code)


def _path(safe, session_id):
    _id(session_id)
    return managed_relative(safe.path, SESSIONS) + '/' + session_id + '.json'


def _head(safe, session_id):
    value = safe.read(_path(safe, session_id), inputs.Budget(total=8192, per_file=8192), missing=True)
    if value is None:
        return None
    _require(value[1].st_nlink == 1, 'session_head_unsafe')
    head = json.loads(value[0], object_pairs_hook=inputs._unique_object)
    if head.get('schemaVersion') == 2:
        import cc_planning
        return cc_planning._head(safe, session_id)
    _require(type(head) is dict and set(head) == {'schemaVersion', 'kind', 'id', 'object'}
             and type(head['schemaVersion']) is int and head['schemaVersion'] == 1
             and head['kind'] == 'planning_session_head' and head['id'] == session_id
             and type(head['object']) is str and HASH.fullmatch(head['object']), 'session_head_invalid')
    return head


def _history(safe, head, *, object_reader=None):
    from cc_development_lib.storage import identity
    result, seen, total = [], set(), len(_bytes(head))
    ref = head['object']
    while ref is not None:
        _require(ref not in seen and len(result) <= MAX_TURNS, 'session_history_invalid')
        seen.add(ref)
        def retained_payload(reference):
            envelope = store.get_raw_object(safe, reference)
            _require(envelope['domain'] == 'planning', 'session_object_domain_invalid')
            return store.validate_raw_object(reference, envelope)
        fetch = object_reader or retained_payload
        node = fetch(ref)
        _require(type(node) is dict and set(node) == {'schemaVersion', 'kind', 'id', 'projectIdentity',
            'baseRevision', 'createdAt', 'actor', 'previous', 'operation', 'turn', 'turnCount'}
            and type(node['schemaVersion']) is int and node['schemaVersion'] == 1
            and node['kind'] == 'planning_session_revision' and node['id'] == head['id']
            and node['projectIdentity'] == identity(safe), 'session_history_invalid')
        _revision(node['baseRevision'])
        _text(node['actor'], 160, 'session_actor_invalid')
        _require(type(node['createdAt']) is str and datetime.datetime.fromisoformat(node['createdAt']).tzinfo is not None,
                 'session_timestamp_invalid')
        _require(type(node['operation']) is dict and set(node['operation']) == {'id', 'requestDigest'}
                 and type(node['operation']['id']) is str and HASH.fullmatch(node['operation']['id'])
                 and type(node['operation']['requestDigest']) is str and HASH.fullmatch(node['operation']['requestDigest']),
                 'session_operation_invalid')
        _require(type(node['turnCount']) is int and 0 <= node['turnCount'] <= MAX_TURNS,
                 'session_turn_limit')
        total += store.object_storage_bytes(node, domain='planning')
        turn = None
        if node['turn'] is not None:
            _require(type(node['turn']) is str and HASH.fullmatch(node['turn']), 'session_turn_invalid')
            turn = fetch(node['turn'])
            _require(type(turn) is dict and set(turn) == {'schemaVersion', 'kind', 'sessionId', 'role', 'actor', 'text', 'createdAt'}
                     and type(turn['schemaVersion']) is int and turn['schemaVersion'] == 1
                     and turn['kind'] == 'planning_session_turn' and turn['sessionId'] == head['id']
                     and turn['role'] in ('user', 'assistant') and turn['actor'] == node['actor']
                     and turn['createdAt'] == node['createdAt'], 'session_turn_invalid')
            _text(turn['text'], MAX_BYTES, 'session_text_invalid')
            total += store.object_storage_bytes(turn, domain='planning')
        result.append({'revision': ref, 'node': node, 'turn': turn})
        _require(node['previous'] is None or type(node['previous']) is str and HASH.fullmatch(node['previous']),
                 'session_history_invalid')
        ref = node['previous']
    ordered = list(reversed(result))
    operations = set()
    for index, item in enumerate(ordered):
        node = item['node']
        _require(node['turnCount'] == index and (node['turn'] is None) == (index == 0)
                 and node['baseRevision'] == ordered[0]['node']['baseRevision']
                 and node['operation']['id'] not in operations, 'session_history_invalid')
        operations.add(node['operation']['id'])
        expected_request = {'sessionId': head['id'], 'expectedRevision': node['previous'] or 'absent',
                            'actor': node['actor'], 'baseRevision': node['baseRevision'] if index == 0 else None,
                            'role': item['turn']['role'] if item['turn'] else None,
                            'text': item['turn']['text'] if item['turn'] else None}
        _require(inputs.digest(expected_request) == node['operation']['requestDigest'], 'session_request_binding_invalid')
    _require(total <= MAX_BYTES, 'session_size_limit')
    return ordered, total


def read_safe_v1(safe, session_id):
    head = _head(safe, session_id)
    if head is None:
        return {'revision': 'absent', 'session': None}
    history, total = _history(safe, head)
    first = history[0]['node']
    return {'revision': head['object'], 'session': {'id': session_id, 'projectIdentity': first['projectIdentity'],
            'baseRevision': first['baseRevision'], 'createdAt': first['createdAt'],
            'turns': [item['turn'] for item in history if item['turn'] is not None], 'retainedBytes': total}}


def read_safe(safe, session_id):
    head = _head(safe, session_id)
    if head is not None and head['schemaVersion'] == 2:
        import cc_planning
        return cc_planning.read_safe(safe, session_id)
    return read_safe_v1(safe, session_id)


def _write(safe, session_id, expected_revision, operation_id, actor, *, base_revision=None, role=None, text=None):
    """Caller owns Core publication barrier. Head publication is the commit point."""
    _id(session_id)
    _revision(expected_revision)
    _require(type(operation_id) is str and HASH.fullmatch(operation_id), 'session_operation_invalid')
    _text(actor, 160, 'session_actor_invalid')
    creating = base_revision is not None
    if creating:
        _revision(base_revision)
        _require(expected_revision == 'absent' and role is None and text is None, 'session_create_invalid')
        _require(len(collect(safe)['heads']) < MAX_SESSIONS or _head(safe, session_id) is not None,
                 'session_count_limit')
    else:
        _require(role in ('user', 'assistant'), 'session_role_invalid')
        _text(text, MAX_BYTES, 'session_text_invalid')
    request = {'sessionId': session_id, 'expectedRevision': expected_revision, 'actor': actor,
               'baseRevision': base_revision, 'role': role, 'text': text}
    request_digest = inputs.digest(request)
    head = _head(safe, session_id)
    _require(head is None or head['schemaVersion'] == 1, 'planning_session_v2_required')
    history, retained = _history(safe, head) if head else ([], 0)
    for item in history:
        if item['node']['operation']['id'] == operation_id:
            _require(item['node']['operation']['requestDigest'] == request_digest, 'session_idempotency_conflict')
            return {'revision': item['revision'], 'operationId': operation_id, 'idempotent': True}
    actual = head['object'] if head else 'absent'
    _require(actual == expected_revision, 'session_revision_conflict')
    _require(bool(head) != creating, 'session_create_invalid')
    count = history[-1]['node']['turnCount'] if head else 0
    _require(creating or count < MAX_TURNS, 'session_turn_limit')
    from cc_development_lib.storage import identity
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    turn = None if creating else {'schemaVersion': 1, 'kind': 'planning_session_turn', 'sessionId': session_id,
                                'role': role, 'actor': actor, 'text': text, 'createdAt': now}
    node = {'schemaVersion': 1, 'kind': 'planning_session_revision', 'id': session_id,
            'projectIdentity': identity(safe), 'baseRevision': base_revision if creating else history[0]['node']['baseRevision'],
            'createdAt': now, 'actor': actor, 'previous': actual if head else None,
            'operation': {'id': operation_id, 'requestDigest': request_digest},
            'turn': inputs.digest(turn) if turn else None, 'turnCount': count + (0 if creating else 1)}
    # Hash spelling is backend-owned; placeholder has the same serialized size.
    prospective = {'schemaVersion': 1, 'kind': 'planning_session_head', 'id': session_id, 'object': '0' * 64}
    growth = store.object_storage_bytes(node, domain='planning') + (store.object_storage_bytes(turn, domain='planning') if turn else 0) + len(_bytes(prospective)) - (len(_bytes(head)) if head else 0)
    _require(retained + growth <= MAX_BYTES, 'session_size_limit')
    store.ensure_planning_capacity(safe, growth)
    if turn:
        node['turn'] = store.put_object(safe, turn, domain='planning')
    ref = store.put_object(safe, node, domain='planning')
    prospective['object'] = ref
    safe.write_atomic(_path(safe, session_id), prospective)
    store._durable(safe, SESSIONS + '/' + session_id + '.json')
    safe.validate()
    return {'revision': ref, 'operationId': operation_id, 'idempotent': False}


def read(root, session_id):
    with inputs.SafeRoot(root) as safe:
        from cc_development_lib import storage
        storage.load(safe)
        _, _, manifest = store.read_state(safe)
        _require(manifest is not None and manifest.get('schema_version') == 2, 'session_requires_current_store')
        result = read_safe(safe, session_id)
        safe.validate()
        return result


def create(root, session_id, base_revision, actor, operation_id, *, expected_revision='absent'):
    with inputs.SafeRoot(root) as safe:
        from cc_development_lib import storage
        with storage.transaction(safe, operation_id):
            storage.load(safe, locked=True)
            _, _, manifest = store.read_state(safe)
            _require(manifest is not None and manifest.get('schema_version') == 2, 'session_requires_current_store')
            return _write(safe, session_id, expected_revision, operation_id, actor, base_revision=base_revision)


def append(root, session_id, expected_revision, operation_id, actor, role, text):
    with inputs.SafeRoot(root) as safe:
        from cc_development_lib import storage
        with storage.transaction(safe, operation_id):
            storage.load(safe, locked=True)
            _, _, manifest = store.read_state(safe)
            _require(manifest is not None and manifest.get('schema_version') == 2, 'session_requires_current_store')
            return _write(safe, session_id, expected_revision, operation_id, actor, role=role, text=text)


def collect(safe):
    """Collect validated session heads and reachable object IDs under owner barrier."""
    relative = managed_relative(safe.path, SESSIONS)
    try:
        entries = safe.listing(relative, inputs.Budget(entries=MAX_SESSIONS))
    except FileNotFoundError:
        return {'heads': {}, 'objects': []}
    heads, refs = {}, set()
    for name, info in entries:
        _require(name.endswith('.json') and stat.S_ISREG(info.st_mode), 'session_head_unsafe')
        session_id = name[:-5]
        head = _head(safe, session_id)
        if head['schemaVersion'] == 2:
            import cc_planning
            history, _ = cc_planning.history(safe, head)
            heads[session_id] = head
            refs.update(item['revision'] for item in history)
            continue
        history, _ = _history(safe, head)
        heads[session_id] = head
        for item in history:
            refs.add(item['revision'])
            if item['node']['turn']:
                refs.add(item['node']['turn'])
    safe.validate()
    return {'heads': heads, 'objects': sorted(refs)}


def validate_bundle(safe, bundle, object_reader):
    """Validate archive roots using a hash-validating detached object resolver."""
    _require(type(bundle) is dict and set(bundle) == {'heads', 'objects'} and type(bundle['heads']) is dict
             and len(bundle['heads']) <= MAX_SESSIONS and type(bundle['objects']) is list
             and all(type(ref) is str and HASH.fullmatch(ref) for ref in bundle['objects'])
             and len(set(bundle['objects'])) == len(bundle['objects']), 'session_archive_invalid')
    refs = set()
    for session_id, head in bundle['heads'].items():
        _id(session_id)
        if head.get('schemaVersion') == 2:
            import cc_planning
            _require(type(head) is dict and set(head) == {'schemaVersion','kind','id','object','reservedBytes'}
                     and head['kind'] == 'planning_session_head' and head['id'] == session_id
                     and type(head['reservedBytes']) is int and 0 <= head['reservedBytes'] <= cc_planning.FINISH_RESERVE,
                     'session_archive_invalid')
            history, _ = cc_planning.history(safe, head, object_reader=object_reader)
            refs.update(item['revision'] for item in history)
            continue
        _require(type(head) is dict and set(head) == {'schemaVersion', 'kind', 'id', 'object'}
                 and type(head['schemaVersion']) is int and head['schemaVersion'] == 1
                 and head['kind'] == 'planning_session_head' and head['id'] == session_id
                 and type(head['object']) is str and HASH.fullmatch(head['object']), 'session_archive_invalid')
        history, _ = _history(safe, head, object_reader=object_reader)
        for item in history:
            refs.add(item['revision'])
            if item['node']['turn']:
                refs.add(item['node']['turn'])
    _require(refs == set(bundle['objects']), 'session_archive_closure_invalid')
    return bundle


def publish_heads(safe, bundle, *, matching_existing=False):
    """Internal restore helper; caller validates bundle and owns publication barrier."""
    validate_bundle(safe, bundle, lambda ref: store.get_object(safe, ref))
    existing = collect(safe)['heads']
    _require(not existing or matching_existing and all(session_id in bundle['heads'] and bundle['heads'][session_id] == head
              for session_id, head in existing.items()), 'session_restore_requires_absent_heads')
    growth = sum(len(_bytes(head)) + head.get('reservedBytes', 0) for session_id, head in bundle['heads'].items() if session_id not in existing)
    if growth:
        store.ensure_planning_capacity(safe, growth)
    for session_id, head in bundle['heads'].items():
        if session_id not in existing:
            safe.write_atomic(_path(safe, session_id), head, replace_existing=False)
            store._durable(safe, SESSIONS + '/' + session_id + '.json')
    safe.validate()
