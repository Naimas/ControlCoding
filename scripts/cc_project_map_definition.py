"""Bounded, reviewed map choices. Never owns lifecycle, evidence or permissions.

See docs/project-map-definition.md for Windows commit and recovery boundaries.
"""
from cc_layout import managed_path, managed_relative, logical_relative, is_contained
from contextlib import ExitStack
import copy
import hashlib
import json
import os
import re

import cc_project_map_model as model
from cc_project_map_sources import observe_project_map, _relative, _excluded, SourcePolicy
from cc_setup_service import _Snapshots, _root, _ordinary, ReadPolicy, SetupServiceError

LIMIT = 256 * 1024
LOCATION = '.controlcoding/project-map/definition.json'
JOURNAL = '.controlcoding/project-map/transaction.json'
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')


class DefinitionError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, code='invalid_definition'):
    if not condition:
        raise DefinitionError(code)


def text(value, limit=160):
    return type(value) is str and 0 < len(value) <= limit and not any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF for c in value)


def ids(value, maximum=64):
    return type(value) is list and len(value) <= maximum and all(type(v) is str and ID.fullmatch(v) for v in value) and len(set(value)) == len(value)


def validate(value, root_identity):
    require(type(value) is dict and set(value) == {'schema_version', 'root_identity', 'entries'})
    require(type(value['schema_version']) is int and value['schema_version'] == 1)
    require(value['root_identity'] == root_identity, 'root_conflict')
    entries = value['entries']
    require(type(entries) is list and len(entries) <= 128)
    seen, bindings = set(), set()
    for row in entries:
        require(type(row) is dict and set(row) == {'id', 'kind', 'title', 'decision', 'bindings', 'sources', 'members', 'supersedes'})
        require(type(row['id']) is str and re.fullmatch(r'map:[0-9a-f]{40}', row['id']) and row['id'] not in seen)
        seen.add(row['id'])
        require(row['kind'] in model.STRUCTURE and text(row['title']))
        require(row['decision'] in ('confirmed', 'rejected'))
        require(ids(row['bindings'], 8) and ids(row['members']) and ids(row['supersedes']))
        require(not bindings.intersection(row['bindings']))
        bindings.update(row['bindings'])
        require(bool(row['bindings']) != bool(row['members']))
        require(not row['members'] or row['kind'] == 'component')
        require(type(row['sources']) is list and len(row['sources']) <= 8)
        for source in row['sources']:
            require(type(source) is dict and set(source) == {'path', 'identity'})
            _relative(source['path'])
            require(not _excluded(source['path']))
            require(source['identity'] is None or (type(source['identity']) is str and re.fullmatch(r'sha256:[0-9a-f]{64}', source['identity'])))
    for row in entries:
        require(set(row['supersedes']) <= seen and row['id'] not in row['supersedes'])
        require(all(member in seen or member.startswith('node:') for member in row['members']))
        require(row['id'] not in row['members'])
    # Reject cyclic group references or supersession, including foreign edits.
    for relation in ('members', 'supersedes'):
        adjacency = {r['id']: r[relation] for r in entries}
        def visit(node, active, done):
            require(node not in active)
            if node in done:
                return
            for child in adjacency.get(node, []):
                visit(child, active | {node}, done)
            done.add(node)
        done = set()
        for node in adjacency:
            visit(node, set(), done)
    require(len(encoded(value)) <= LIMIT, 'definition_limit')
    return value


def decode(data, root_identity):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result
    try:
        return validate(json.loads(data.decode('utf-8'), object_pairs_hook=pairs), root_identity)
    except (UnicodeError, ValueError, TypeError, KeyError, RecursionError) as error:
        if isinstance(error, DefinitionError):
            raise
        raise DefinitionError('invalid_definition') from None


def read_definition(root, root_identity):
    root = _root(str(root))
    try:
        with _Snapshots(root, {}, ReadPolicy(file_bytes=LIMIT * 3)) as reader:
            if reader.observe(managed_path(root, JOURNAL), {}) is not None:
                raise DefinitionError('recovery_required')
            saved = reader.observe(managed_path(root, LOCATION), {})
            reader.recheck()
            data = saved[-1] if saved is not None else None
            require(data is None or len(data) <= LIMIT, 'definition_limit')
            value = decode(data, root_identity) if data is not None else {'schema_version': 1, 'root_identity': root_identity, 'entries': []}
            return value, digest(data) if data is not None else 'absent'
    except SetupServiceError as error:
        raise DefinitionError(error.code) from None


def project(observation, definition, revision):
    """Overlay reviewed identity only; keep all evidence dimensions unchanged."""
    result = copy.deepcopy(observation)
    bundle = result['projection']['bundle']
    source_snapshot = bundle['snapshot_id']
    original = {n['id']: n for n in bundle['nodes']}
    remap = {}
    additions = []
    for row in definition['entries']:
        available = [b for b in row['bindings'] if b in original]
        for binding in available:
            remap[binding] = row['id'] if binding == available[-1] else binding
        node = copy.deepcopy(original[available[-1]]) if available else {
            'id': row['id'], 'kind': row['kind'], 'title': row['title'],
            'presence': 'intended' if row['members'] else 'missing', 'origin': 'user',
            'mapping': row['decision'], 'sources': [], 'lifecycle': {'owner': 'adapter', 'state': 'unknown'},
            'plan': 'unknown', 'criteria': [], 'reason': 'Reviewed map choice; implementation and verification remain unassessed'}
        node.update(id=row['id'], title=row['title'], mapping='conflicted' if len(available) > 1 else row['decision'])
        if not available:
            for i, source in enumerate(row['sources']):
                sid = row['id'] + ':source:' + str(i)
                bundle['sources'].append({'id': sid, 'owner': 'user', 'locator': {'path': source['path'], 'fragment': None},
                    'identity': None, 'adapter': 'cc-map-definition-v1', 'resolution': 'unresolved',
                    'reason': 'Historical mapping reference; current source was not observed', 'synthetic': False})
                node['sources'].append(sid)
        additions.append(node)
    bundle['nodes'] = [n for n in bundle['nodes'] if remap.get(n['id'], n['id']) == n['id']] + additions
    for edge in bundle['edges']:
        for key in ('source', 'target'):
            edge[key] = remap.get(edge[key], edge[key])
    for key, field in (('coverage', 'scope'), ('selection', 'nodes')):
        bundle[key][field] = list(dict.fromkeys(remap.get(n, n) for n in bundle[key][field]))
    known = {n['id'] for n in bundle['nodes']}
    for row in definition['entries']:
        for relation, members in (('references', row['members']), ('supersedes', row['supersedes'])):
            for member in members:
                target = remap.get(member, member)
                if target not in known:
                    # Retain a visible missing member; never silently drop a reviewed reference.
                    bundle['nodes'].append({'id': target, 'kind': 'component', 'title': 'Missing reviewed member',
                        'presence': 'missing', 'origin': 'user', 'mapping': 'confirmed', 'sources': [],
                        'lifecycle': {'owner': 'adapter', 'state': 'unknown'}, 'plan': 'unknown', 'criteria': [],
                        'reason': 'This reviewed group member is outside the current observation'})
                    known.add(target)
                bundle['edges'].append({'id': 'mapping:' + digest((row['id'] + relation + target).encode())[:40],
                    'source': row['id'], 'target': target, 'relation': relation, 'origin': 'user', 'mapping': 'confirmed', 'sources': []})
    for observation_row in result['observations']:
        observation_row['node'] = remap.get(observation_row['node'], observation_row['node'])
        for symbol in observation_row['details'].get('symbols', []):
            symbol['node'] = remap.get(symbol['node'], symbol['node'])
    bundle['snapshot_id'] = 'review:' + digest((source_snapshot + revision).encode())
    result['projection'] = model.build_map_projection(bundle)
    result['review'] = {'revision': revision, 'source_snapshot': source_snapshot, 'entries': definition['entries'], 'write_supported': os.name == 'nt'}
    return model._bounded_copy(result, model.MapPolicy(), model.MapPolicy().max_output_bytes)


def observe_reviewed(request, expected_preview):
    observation = observe_project_map(request, expected_preview=expected_preview)
    definition, revision = read_definition(request['project_root'], observation['root_identity'])
    return project(observation, definition, revision)


def change_definition(observation, definition, change):
    require(type(change) is dict and type(change.get('operation')) is str, 'invalid_change')
    operation = change['operation']
    fields = {'accept': {'target'}, 'reject': {'target'}, 'rename': {'target', 'title'},
              'alias': {'target', 'replacement'}, 'group': {'title', 'members'},
              'split': {'target', 'groups'}, 'merge': {'targets', 'title'}}
    require(operation in fields and set(change) == fields[operation] | {'operation'}, 'invalid_change')
    result = copy.deepcopy(definition)
    entries = result['entries']
    nodes = {n['id']: n for n in observation['projection']['bundle']['nodes']}
    sources = {s['id']: s for s in observation['projection']['bundle']['sources']}
    def find(identifier):
        return next((r for r in entries if r['id'] == identifier or identifier in r['bindings']), None)
    def ensure(identifier):
        require(type(identifier) is str, 'invalid_change')
        row = find(identifier)
        if row:
            return row
        require(identifier in nodes, 'unknown_element')
        node = nodes[identifier]
        row = {'id': 'map:' + digest((observation['root_identity'] + identifier).encode())[:40],
               'kind': node['kind'], 'title': node['title'], 'decision': 'confirmed', 'bindings': [identifier],
               'sources': [{'path': sources[s]['locator']['path'], 'identity': sources[s]['identity']} for s in node['sources']],
               'members': [], 'supersedes': []}
        entries.append(row)
        return row
    def group(title, members, supersedes):
        require(text(title) and ids(members) and members, 'invalid_change')
        require(all(m in nodes or find(m) for m in members), 'unknown_element')
        # Store reviewed member identities so later explicit aliases remain connected.
        stable = [ensure(m)['id'] for m in members]
        require(len(set(stable)) == len(stable), 'invalid_change')
        gid = 'map:' + digest(encoded([definition, change, title, stable]))[:40]
        entries.append({'id': gid, 'kind': 'component', 'title': title, 'decision': 'confirmed',
                        'bindings': [], 'sources': [], 'members': stable, 'supersedes': supersedes})
    if operation in ('accept', 'reject', 'rename', 'alias', 'split'):
        row = ensure(change['target'])
        if operation in ('accept', 'reject'):
            row['decision'] = 'confirmed' if operation == 'accept' else 'rejected'
        elif operation == 'rename':
            require(text(change['title']), 'invalid_change')
            row['title'] = change['title']
        elif operation == 'alias':
            replacement = change['replacement']
            require(type(replacement) is str and replacement in nodes and bool(row['bindings']), 'invalid_change')
            require(nodes[replacement]['kind'] == row['kind'] and find(replacement) is None, 'alias_conflict')
            # Old binding must be absent; simultaneous old/new sources need explicit separate choices.
            require(not any(b in nodes for b in row['bindings']), 'alias_conflict')
            row['bindings'].append(replacement)
            row['sources'] = [{'path': sources[s]['locator']['path'], 'identity': sources[s]['identity']} for s in nodes[replacement]['sources']]
        else:
            groups = change['groups']
            require(row['members'] and type(groups) is list and 2 <= len(groups) <= 8, 'invalid_change')
            flat = []
            for part in groups:
                require(type(part) is dict and set(part) == {'title', 'members'} and ids(part['members']), 'invalid_change')
                flat.extend(part['members'])
            require(len(flat) == len(set(flat)) and set(flat) == set(row['members']), 'invalid_partition')
            for part in groups:
                group(part['title'], part['members'], [row['id']])
    elif operation == 'group':
        group(change['title'], change['members'], [])
    else:
        require(ids(change['targets'], 8) and len(change['targets']) >= 2, 'invalid_change')
        rows = [find(t) for t in change['targets']]
        require(all(r and r['members'] for r in rows), 'invalid_change')
        group(change['title'], list(dict.fromkeys(m for r in rows for m in r['members'])), [r['id'] for r in rows])
    validate(result, observation['root_identity'])
    require(result != definition, 'no_change')
    return result


def preview_change(request, expected_preview, snapshot, revision, change):
    root = _root(request['project_root'])
    observation = observe_project_map(request, expected_preview=expected_preview)
    require(observation['projection']['bundle']['snapshot_id'] == snapshot, 'stale_snapshot')
    definition, current = read_definition(request['project_root'], observation['root_identity'])
    require(current == revision, 'definition_conflict')
    proposed = change_definition(observation, definition, change)
    # Validate complete overlay/output budgets before any persistence.
    project(observation, proposed, digest(encoded(proposed)))
    before = {r['id']: r for r in definition['entries']}
    changes = [{'before': before.get(r['id']), 'after': r} for r in proposed['entries'] if before.get(r['id']) != r]
    binding = {'root_identity': observation['root_identity'], 'snapshot': snapshot, 'revision': revision, 'change': change, 'definition': proposed}
    return {'approval_id': digest(encoded(binding)), 'revision': revision, 'snapshot': snapshot,
            'path': managed_relative(root, LOCATION), 'changes': changes, 'write_supported': os.name == 'nt'}, proposed, observation


def _exclusive(path, create=False):
    """Native Windows file identity and mandatory sharing exclusion."""
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    call = kernel.CreateFileW
    call.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    call.restype = wintypes.HANDLE
    handle = call(str(path), 0xC0010000, 0, None, 1 if create else 3, 0x00200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle(handle)
        raise


def _delete_owned(fd):
    import ctypes
    from ctypes import wintypes
    import msvcrt
    class Disposition(ctypes.Structure):
        _fields_ = [('delete', wintypes.BOOL)]
    call = ctypes.WinDLL('kernel32', use_last_error=True).SetFileInformationByHandle
    call.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    call.restype = wintypes.BOOL
    info = Disposition(True)
    if not call(msvcrt.get_osfhandle(fd), 4, ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())


def _write(fd, data):
    os.lseek(fd, 0, os.SEEK_SET)
    view = memoryview(data)
    while view:
        count = os.write(fd, view)
        if not count:
            raise OSError('write failed')
        view = view[count:]
    os.ftruncate(fd, len(data))
    os.fsync(fd)


def commit_change(request, expected_preview, snapshot, revision, change, approval_id):
    require(os.name == 'nt', 'write_unsupported')
    preview, proposed, observation = preview_change(request, expected_preview, snapshot, revision, change)
    require(preview['approval_id'] == approval_id, 'preview_mismatch')
    root = _root(request['project_root'])
    journal_created = False
    try:
        with ExitStack() as stack:
            reader = stack.enter_context(_Snapshots(root, {}, SourcePolicy()))
            root_snapshot = reader.observe(root, {}, directory=True)
            require(digest((os.path.normcase(str(root)) + '\0' + repr(root_snapshot)).encode('utf-8')) == observation['root_identity'], 'root_conflict')
            # Pin observed content identities before saving mapping choices.
            for source in observation['projection']['bundle']['sources']:
                if source['identity']:
                    saved = reader.observe(root / source['locator']['path'], {})
                    require(saved is not None and 'sha256:' + digest(saved[-1]) == source['identity'], 'stale_snapshot')
            for directory in (managed_path(root, '.controlcoding'), managed_path(root, '.controlcoding/project-map')):
                try:
                    directory.mkdir()
                except FileExistsError:
                    pass
                reader.observe(directory, {}, directory=True)
            target = managed_path(root, LOCATION)
            fd = None
            before = None
            if revision != 'absent':
                fd = _exclusive(target)
                stack.callback(os.close, fd)
                _ordinary(os.fstat(fd), False, 'definition')
                require(os.fstat(fd).st_size <= LIMIT, 'definition_limit')
                before = os.read(fd, LIMIT + 1)
                require(digest(before) == revision, 'definition_conflict')
            else:
                require(not target.exists(), 'definition_conflict')
            reader.recheck()
            # CREATE_NEW also arbitrates other MAP writers, without reclaiming stale locks.
            journal = _exclusive(managed_path(root, JOURNAL), create=True)
            stack.callback(os.close, journal)
            journal_created = True
            data = encoded(proposed)
            _write(journal, encoded({'schema_version': 1, 'state': 'prepared', 'before': before.decode('utf-8') if before is not None else None,
                                    'after_sha256': digest(data), 'approval_id': approval_id}))
            if fd is None:
                fd = _exclusive(target, create=True)
                stack.callback(os.close, fd)
            _ordinary(os.fstat(fd), False, 'definition')
            _write(fd, data)
            _delete_owned(journal)
            return {'revision': digest(data), 'path': managed_relative(root, LOCATION), 'saved': True}
    except (OSError, SetupServiceError) as error:
        raise DefinitionError('recovery_required' if journal_created else 'definition_busy') from None
