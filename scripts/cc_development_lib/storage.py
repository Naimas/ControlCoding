"""Single atomic registry, confined IO and fail-closed crash locking."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat

from cc_layout import managed_relative, LayoutError
from cc_evidence_inputs import SafeRoot, Budget, EvidenceError
from .method import IDENTITY, canonical, digest
from .schema import DevelopmentError

LOGICAL = '.controlcoding/development.json'
LOCK = '.controlcoding/development.lock'
LIMIT = 2 * 1024 * 1024


def identity(root):
    root.validate()
    return digest([os.path.normcase(str(root.path)), root.identity.st_dev, root.identity.st_ino])


def empty(root):
    return {'schema_version': 1, 'enabled': False, 'profile': 'routine', 'method': dict(IDENTITY),
            'project_identity': identity(root), 'changes': [], 'operations': []}


def pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise DevelopmentError('duplicate_json_key')
        value[key] = item
    return value


def load(root, *, locked=False):
    from cc_development_store import StoreError
    from cc_development_bundle import check_pending
    check_pending(root)
    from cc_product_progress_observation import current as progress_observation
    observer = progress_observation()
    if observer is not None and observer.state is not None and observer.project_path == root.path:
        observer.check()
        root.validate()
        return observer.state, observer.revision
    relative = managed_relative(root.path, LOGICAL)
    lock = managed_relative(root.path, LOCK)
    if not locked:
        try:
            with root.directory(lock):
                raise DevelopmentError('recovery_required')
        except FileNotFoundError:
            pass
    item = root.read(relative, Budget(per_file=LIMIT, total=LIMIT), missing=True)
    if item is None:
        return empty(root), 'absent'
    raw, info = item
    if info.st_nlink != 1:
        raise DevelopmentError('unsafe_state_file')
    try:
        state = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(DevelopmentError('invalid_state')))
        if state.get('schema_version') == 2:
            from cc_development_store import read_state
            state, revision, manifest = read_state(root)
            validate_state(root, state, compatibility=manifest['compatibility'])
            from cc_development_migration import validate_metadata
            validate_metadata(root, state, manifest)
            return state, revision
        validate_state(root, state)
    except (KeyError, TypeError, ValueError, RecursionError, UnicodeError) as error:
        if isinstance(error, (DevelopmentError, StoreError)):
            raise
        raise DevelopmentError('invalid_state') from None
    root.validate()
    return state, hashlib.sha256(raw).hexdigest()


def validate_state(root, state, *, expected_method=None, compatibility=None, object_reader=None):
    """Validate retained bytes and projections without reading or writing state."""
    try:
        if type(state) is dict and 'retention' in state:
            from cc_development_history import compact_validation_state
            state = compact_validation_state(root, state, object_reader=object_reader, compatibility=compatibility)
        base_fields = {'schema_version', 'enabled', 'profile', 'method', 'project_identity', 'changes', 'operations'}
        if type(state) is not dict or set(state) not in (base_fields,base_fields|{'product'}):
            raise DevelopmentError('invalid_state')
        if type(state['schema_version']) is not int or state['schema_version'] != 1 or type(state['enabled']) is not bool:
            raise DevelopmentError('unsupported_schema')
        expected_method = IDENTITY if expected_method is None else expected_method
        if state['method'] != expected_method:
            raise DevelopmentError('unsupported_method')
        from cc_development_compatibility import identity_checker
        allowed_identity = identity_checker(expected_method, compatibility)
        if not allowed_identity(expected_method):
            raise DevelopmentError('unsupported_compatibility_pair')
        if state['project_identity'] != identity(root):
            raise DevelopmentError('project_identity_changed')
        if state['profile'] not in ('routine', 'standard', 'critical') or type(state['changes']) is not list or type(state['operations']) is not list:
            raise DevelopmentError('invalid_state')
        if len(state['changes']) > 256 or len(state['operations']) > 32768:
            raise DevelopmentError('state_limit')
        operation_ids = set()
        activation = None
        from .schema import validate_action, revision
        for operation in state['operations']:
            if type(operation) is not dict or set(operation) != {'operation_id', 'approval_id', 'expected_revision', 'action', 'timestamp', 'provenance'}:
                raise DevelopmentError('invalid_operation')
            revision(operation['approval_id'])
            revision(operation['expected_revision'])
            if (operation['approval_id'] == 'absent' or operation['operation_id'] != operation['approval_id']
                    or operation['operation_id'] in operation_ids or operation['provenance'] != 'local_revision'):
                raise DevelopmentError('invalid_operation_identity')
            operation_ids.add(operation['operation_id'])
            validate_action(operation['action'])
            if operation['action']['kind'] == 'activate':
                if activation is not None:
                    raise DevelopmentError('duplicate_activation')
                activation = operation['action']
        if bool(activation) != state['enabled'] or (activation and activation['profile'] != state['profile']) or (not state['enabled'] and state['changes']):
            raise DevelopmentError('invalid_activation_projection')
        seen = set()
        creation_methods = {}
        for change in state['changes']:
            change_fields = {'id', 'title', 'owner', 'risk', 'profile', 'method', 'policy_revision', 'artifacts', 'decisions', 'criterionDecisions',
                             'candidate', 'evidence', 'reviews', 'releases', 'observations', 'closed', 'history', 'created_at',
                             'incidents', 'incident_captures', 'incident_decisions', 'origin'}
            if (type(change) is not dict or set(change) != change_fields or change['id'] in seen
                    or not allowed_identity(change['method'])):
                raise DevelopmentError('invalid_change')
            seen.add(change['id'])
            if not isinstance(change['artifacts'], dict) or not isinstance(change['decisions'], list):
                raise DevelopmentError('invalid_change')
            for stage, versions in change['artifacts'].items():
                from .schema import artifact
                from .method import STAGES
                if stage not in STAGES or type(versions) is not list:
                    raise DevelopmentError('invalid_artifact')
                for version in versions:
                    if type(version) is not dict or set(version) != {'revision', 'digest', 'content', 'upstream', 'actor', 'timestamp', 'owner', 'provenance'}:
                        raise DevelopmentError('invalid_artifact_fields')
                    artifact(stage, version['content'])
                    if version['digest'] != digest(version['content']) or version['revision'] != version['digest']:
                        raise DevelopmentError('invalid_artifact_identity')
            previous = 'absent'
            projected = {'artifacts': {}, 'decisions': [], 'criterionDecisions': [], 'candidate': None, 'evidence': [], 'reviews': [], 'releases': [], 'observations': [], 'closed': False,
                         'incidents': [], 'incident_captures': [], 'incident_decisions': []}
            for record in change['history']:
                if type(record) is not dict or set(record) != {'action', 'result', 'timestamp', 'previous', 'digest'}:
                    raise DevelopmentError('invalid_history')
                if record['previous'] != previous or record['digest'] != digest({k: v for k, v in record.items() if k != 'digest'}):
                    raise DevelopmentError('invalid_history')
                from .schema import validate_action
                validate_action(record['action'])
                kind = record['action']['kind']
                result = record['result']
                if record['action'].get('change_id') != change['id']:
                    raise DevelopmentError('invalid_history_owner')
                if kind == 'create':
                    if previous != 'absent' or any(change.get(key) != value for key, value in result.items()):
                        raise DevelopmentError('invalid_history_identity')
                    if any(result.get(key) != record['action'].get(key) for key in ('title', 'owner', 'risk')) or result.get('id') != record['action']['change_id']:
                        raise DevelopmentError('invalid_history_identity')
                    if not allowed_identity(result.get('method')):
                        raise DevelopmentError('invalid_history_identity')
                    creation_methods[change['id']] = result['method']
                elif kind == 'artifact':
                    if result['content'] != record['action']['content'] or result['actor'] != record['action']['actor']:
                        raise DevelopmentError('invalid_history_artifact')
                    projected['artifacts'].setdefault(record['action']['stage'], []).append(result)
                elif kind == 'candidate':
                    if sorted(row['path'] for row in result['files']) != sorted(record['action']['files']) or result['actor'] != record['action']['actor']:
                        raise DevelopmentError('invalid_history_candidate')
                    projected['candidate'] = result
                    if type(result) is not dict or set(result) != {'digest', 'files', 'upstream', 'actor', 'timestamp'}:
                        raise DevelopmentError('invalid_candidate_fields')
                    for row in result['files']:
                        if type(row) is not dict or set(row) != {'path', 'sha256', 'size'} or type(row['size']) is not int or row['size'] < 0:
                            raise DevelopmentError('invalid_candidate_inventory')
                        revision(row['sha256'])
                    if result['digest'] != digest({'files': result['files'], 'method': change['method']['digest'], 'upstream': result['upstream']}):
                        raise DevelopmentError('invalid_candidate_identity')
                elif kind == 'decide':
                    if any(result['decision'].get(key) != record['action'][key] for key in ('stage', 'decision', 'actor', 'reason')):
                        raise DevelopmentError('invalid_history_decision')
                    if ('method' in result['decision'] and not allowed_identity(result['decision']['method'])):
                        raise DevelopmentError('invalid_decision_method')
                    projected['decisions'].append(result['decision'])
                    projected['criterionDecisions'] = result['criterion_decisions']
                elif kind in ('evidence', 'review', 'release', 'observation'):
                    projected[{'evidence': 'evidence', 'review': 'reviews', 'release': 'releases', 'observation': 'observations'}[kind]].append(result)
                elif kind == 'incident_capture':
                    from cc_development_operations import validate_observation, incidents_from_captures
                    if type(result) is not dict or set(result) != {'capture', 'replayed', 'observation'} or type(result['replayed']) is not bool:
                        raise DevelopmentError('invalid_history_capture')
                    capture = validate_observation(result['capture'])
                    if (capture['receipt']['id'] != record['action']['receipt_id']
                            or capture['signalId'] != record['action']['signal_id'] or capture['parentChangeId'] != change['id']):
                        raise DevelopmentError('invalid_history_capture')
                    if capture['receipt']['plan'].get('projectIdentity') != state['project_identity']:
                        raise DevelopmentError('incident_project_identity_changed')
                    duplicate = any(item['receipt']['id'] == capture['receipt']['id'] for item in projected['incident_captures'])
                    if duplicate != result['replayed']:
                        raise DevelopmentError('invalid_history_capture_replay')
                    if duplicate:
                        if result['observation'] is not None or capture not in projected['incident_captures']:
                            raise DevelopmentError('invalid_history_capture_replay')
                    else:
                        if (not projected['releases'] or capture['release'] != projected['releases'][-1]
                                or not projected['candidate']
                                or capture['group']['candidateDigest'] != projected['candidate']['digest']):
                            raise DevelopmentError('incident_history_binding_changed')
                        expected_observation = {'signals': [{'id': capture['signalId'], 'kind': 'monitor',
                            'receiptId': capture['receipt']['id'], 'observation': capture['receipt']['observation']}],
                            'actor': record['action']['actor'], 'timestamp': record['timestamp'],
                            'candidate_identity': capture['group']['candidateDigest'],
                            'release_identity': capture['group']['releaseId'],
                            'release_artifact_identity': capture['group']['deployedDigest'], 'provenance': 'authoritative_adapter'}
                        if result['observation'] != expected_observation:
                            raise DevelopmentError('invalid_history_capture_observation')
                        projected['incident_captures'].append(capture)
                        projected['observations'].append(result['observation'])
                        projected['incidents'] = incidents_from_captures(projected['incident_captures'])
                elif kind in ('incident_decide', 'incident_followup'):
                    if type(result) is not dict or set(result) != {'decision', 'origin'}:
                        raise DevelopmentError('invalid_history_incident_decision')
                    incident = next((item for item in projected['incidents'] if item['id'] == record['action']['incident_id']), None)
                    if incident is None:
                        raise DevelopmentError('incident_history_source_missing')
                    expected_decision = {'incident_id': incident['id'], 'incident_revision': incident['revision'],
                        'decision': 'followup' if kind == 'incident_followup' else record['action']['decision'],
                        'owner': record['action']['owner'], 'actor': record['action']['actor'],
                        'reason': record['action']['reason'], 'timestamp': record['timestamp'], 'child_id': record['action'].get('child_id')}
                    expected_origin = ({'kind': 'incident_followup', 'change_id': change['id'],
                                        'incident_id': incident['id'], 'incident_revision': incident['revision']}
                                       if kind == 'incident_followup' else None)
                    if result['decision'] != expected_decision or result['origin'] != expected_origin:
                        raise DevelopmentError('invalid_history_incident_decision')
                    projected['incident_decisions'].append(result['decision'])
                elif kind == 'close':
                    projected['closed'] = True
                else:
                    raise DevelopmentError('invalid_history_action')
                previous = record['digest']
            if not change['history'] or any(change[key] != value for key, value in projected.items()):
                raise DevelopmentError('history_projection_mismatch')
            from cc_development_operations import validate_change
            validate_change(change)
            from cc_product import validate_change as validate_product_change
            validate_product_change(state.get('product'),change)
        if 'product' in state:
            from cc_product import validate_state as product_state
            product_state(state['product'], creation_methods=creation_methods)
        for change in state['changes']:
            origin = change['origin']
            if origin is None:
                continue
            if type(origin) is not dict or set(origin) != {'kind', 'change_id', 'incident_id', 'incident_revision'} or origin['kind'] != 'incident_followup':
                raise DevelopmentError('invalid_incident_origin')
            parent = next((item for item in state['changes'] if item['id'] == origin['change_id']), None)
            if parent is None or parent is change:
                raise DevelopmentError('incident_origin_parent_missing')
            linked = [item for item in parent['incident_decisions'] if item['decision'] == 'followup'
                      and item['incident_id'] == origin['incident_id'] and item['incident_revision'] == origin['incident_revision']
                      and item['child_id'] == change['id']]
            if len(linked) != 1:
                raise DevelopmentError('incident_origin_decision_missing')
        for increment in state.get('product',{}).get('increments',[]):
            child=next((change for change in state['changes'] if change['id']==increment['change_id']),None)
            if child is None:
                raise DevelopmentError('product_increment_child_missing')
        from cc_product_evolution import selections
        for selection in selections(state.get('product',{})):
            if any(not any(c['id']==assignment['change_id'] for c in state['changes']) for assignment in selection['changes']):
                raise DevelopmentError('product_increment_child_missing')
        for link in state.get('product',{}).get('evolution',{}).get('incident_links',[]):
            child=next((c for c in state['changes'] if c['id']==link['change_id']),None)
            if child is None or child.get('origin')!=link['origin']:
                raise DevelopmentError('product_incident_origin_changed')
        for parent in state['changes']:
            child_ids = set()
            incident_ids = set()
            for decision in parent['incident_decisions']:
                if decision['decision'] != 'followup':
                    continue
                if decision['child_id'] in child_ids or decision['incident_id'] in incident_ids:
                    raise DevelopmentError('duplicate_incident_child')
                child_ids.add(decision['child_id'])
                incident_ids.add(decision['incident_id'])
                child = next((item for item in state['changes'] if item['id'] == decision['child_id']), None)
                expected_origin = {'kind': 'incident_followup', 'change_id': parent['id'],
                                   'incident_id': decision['incident_id'], 'incident_revision': decision['incident_revision']}
                if child is None or child['origin'] != expected_origin:
                    raise DevelopmentError('incident_followup_child_missing')
    except (KeyError, TypeError, ValueError, RecursionError, UnicodeError) as error:
        if isinstance(error, DevelopmentError):
            raise
        raise DevelopmentError('invalid_state') from None
    root.validate()
    return state


@contextmanager
def transaction(root, operation_id='internal'):
    relative = managed_relative(root.path, LOCK)
    parent_name, name = relative.rsplit('/', 1)
    with root.directory(parent_name, create=True) as parent:
        created = False
        lock_info = None
        try:
            with root.directory(relative, create=True, exclusive=True) as lock_directory:
                created = True
                lock_info = os.stat(lock_directory) if os.name == 'nt' else os.fstat(lock_directory)
                root.write_atomic(relative + '/owner.json', {'schema_version': 1, 'pid': os.getpid(),
                                  'project_identity': identity(root), 'operation_id': operation_id}, replace_existing=False)
                yield
        except EvidenceError as error:
            if error.code == 'attempt_directory_exists':
                raise DevelopmentError('recovery_required') from None
            raise
        finally:
            if created:
                root.validate()
                release_lock(root, expected=lock_info)


def release_lock(root, *, expected):
    """Remove exactly our known owner file; never recursively delete additions."""
    relative = managed_relative(root.path, LOCK)
    signature = lambda info: (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))
    with root.directory(relative) as directory:
        current = os.stat(directory) if os.name == 'nt' else os.fstat(directory)
        if signature(current) != signature(expected):
            raise DevelopmentError('recovery_lock_changed')
        listing = root.listing(relative, Budget(entries=4))
        if {name for name, _ in listing} != {'owner.json'}:
            raise DevelopmentError('recovery_lock_changed')
        # Windows scandir's cached stat omits the real link count. Observe the
        # anchored leaf before asking the retained-handle deleter to validate it.
        owner_info = root._stat(directory, 'owner.json')
        if not stat.S_ISREG(owner_info.st_mode) or owner_info.st_nlink != 1:
            raise DevelopmentError('recovery_lock_owner_unsafe')
        if os.name == 'nt':
            root._win_remove_file(directory, 'owner.json', owner_info)
        else:
            observed = root._stat(directory, 'owner.json')
            if (observed.st_dev, observed.st_ino, observed.st_size, observed.st_mtime_ns) != (owner_info.st_dev, owner_info.st_ino, owner_info.st_size, owner_info.st_mtime_ns):
                raise DevelopmentError('recovery_lock_changed')
            os.unlink('owner.json', dir_fd=directory)
    parent_name, name = relative.rsplit('/', 1)
    with root.directory(parent_name) as parent:
        if signature(root._stat(parent, name)) != signature(expected):
            raise DevelopmentError('recovery_lock_changed')
        if os.name == 'nt':
            os.rmdir(parent / name)
        else:
            os.rmdir(name, dir_fd=parent)


def process_state(pid):
    """Return inactive only from an OS observation, never a user assertion."""
    if type(pid) is not int or pid < 1:
        return 'unknown'
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return 'inactive' if ctypes.get_last_error() == 87 else 'unknown'
        try:
            code = wintypes.DWORD()
            kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
            kernel.GetExitCodeProcess.restype = wintypes.BOOL
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                return 'unknown'
            return 'active' if code.value == 259 else 'inactive'
        finally:
            kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return 'active'
    except ProcessLookupError:
        return 'inactive'
    except OSError:
        return 'unknown'


def recovery_lock(root):
    relative = managed_relative(root.path, LOCK)
    with root.directory(relative) as directory:
        info = os.stat(directory) if os.name == 'nt' else os.fstat(directory)
        listing = root.listing(relative, Budget(entries=4))
        if {name for name, _ in listing} != {'owner.json'}:
            raise DevelopmentError('recovery_lock_owner_unknown')
        raw, owner_info = root.read(relative + '/owner.json', Budget(total=4096, per_file=4096))
        if owner_info.st_nlink != 1:
            raise DevelopmentError('recovery_lock_owner_unsafe')
        try:
            owner = json.loads(raw, object_pairs_hook=pairs)
            if (type(owner) is not dict or set(owner) != {'schema_version', 'pid', 'project_identity', 'operation_id'}
                    or type(owner['schema_version']) is not int or owner['schema_version'] != 1
                    or owner['project_identity'] != identity(root)):
                raise DevelopmentError('recovery_lock_owner_invalid')
        except (ValueError, TypeError, UnicodeError):
            raise DevelopmentError('recovery_lock_owner_invalid') from None
        fingerprint = digest([info.st_dev, info.st_ino, hashlib.sha256(raw).hexdigest()])
        return fingerprint, process_state(owner['pid']), info


def save(root, state, expected, *, capacity_class='operational'):
    current, revision = load(root, locked=True)
    if revision != expected:
        raise DevelopmentError('revision_conflict')
    from cc_development_store import write_state
    validate_state(root, state, compatibility=compatibility_for(root))
    return write_state(root, state, expected, capacity_class=capacity_class)


def compatibility_for(root):
    """Read only the installed manifest policy; callers validate it explicitly."""
    item = root.read(managed_relative(root.path, LOGICAL), Budget(per_file=LIMIT, total=LIMIT), missing=True)
    if item is None:
        return None
    if item[1].st_nlink != 1:
        raise DevelopmentError('unsafe_state_file')
    value = json.loads(item[0], object_pairs_hook=pairs)
    return value.get('compatibility') if value.get('schema_version') == 2 else None
