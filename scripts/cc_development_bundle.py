"""Coherent control/session archive closure; callers own publication barrier."""
import hashlib
import json
import os
import stat

import cc_evidence_inputs as inputs
from cc_layout import managed_relative
import cc_development_store as store
import cc_development_sessions as sessions

LIMIT = 192 * 1024 * 1024
PENDING = '.controlcoding/development/restore_pending.json'


def _require(value, code):
    if not value:
        raise store.StoreError(code)


def _encoded(value):
    return store._encoded(value)


def _closure(manifest, session_bundle, fetch):
    refs, active = set(), set()
    domains={}
    def register_domains(root,domain='control',owner_paths=True,root_path=()):
        for ref,expected in store.packed_domains(root,fetch,domain=domain,owner_paths=owner_paths,root_path=root_path).items():
            _require(ref not in domains or domains[ref]==expected,'bundle_object_domain_invalid')
            domains[ref]=expected
    register_domains(manifest['state_ref'])
    total = 0
    def visit(ref, mode, depth=0):
        nonlocal total
        _require(type(ref) is str and store.HASH.fullmatch(ref) and depth <= store.MAX_DEPTH and ref not in active,
                 'bundle_reference_invalid')
        envelope = fetch(ref)
        value = store.validate_raw_object(ref, envelope)
        planning_mode = mode.startswith('planning_')
        expected_domain='planning' if mode == 'session' or planning_mode else 'control'
        if mode in ('history','predecessor') and ref in domains:expected_domain=domains[ref]
        _require(envelope['domain'] == expected_domain,
                 'bundle_object_domain_invalid')
        _require(ref not in domains or domains[ref]==expected_domain,'bundle_object_domain_invalid')
        domains[ref]=expected_domain
        if ref in refs:
            return
        refs.add(ref)
        active.add(ref)
        total += len(_encoded(envelope))
        _require(len(refs) <= store.MAX_OBJECTS and total <= LIMIT, 'bundle_closure_limit')
        decoded_mode = mode.removeprefix('planning_')
        prefix = 'planning_' if planning_mode else ''
        if decoded_mode == 'state':
            _require(type(value) is dict and value.get('type') in ('value', 'scalar', 'dict', 'list', 'text','planning'), 'bundle_state_invalid')
            kind = value['type']
            if kind=='planning':
                _require(not planning_mode and set(value)=={'type','ref'},'bundle_state_invalid')
                visit(value['ref'],'planning_state',depth+1)
            if kind in ('dict', 'list', 'text'):
                _require(type(value.get('parts')) is list, 'bundle_state_invalid')
                for part in value['parts']:
                    visit(part, prefix + kind + '_part', depth + 1)
        elif decoded_mode == 'dict_part':
            _require(type(value) is list, 'bundle_state_invalid')
            for pair in value:
                _require(type(pair) is list and len(pair) == 2 and type(pair[0]) is str, 'bundle_state_invalid')
                visit(pair[1], prefix+'state', depth + 1)
        elif decoded_mode == 'list_part':
            _require(type(value) is list, 'bundle_state_invalid')
            for child in value:
                visit(child, prefix+'state', depth + 1)
        elif mode == 'archive':
            _require(type(value) is dict and value.get('kind') == 'exact_bytes' and type(value.get('parts')) is list,
                     'bundle_archive_invalid')
            for part in value['parts']:
                visit(part, 'archive_part', depth + 1)
        elif mode in ('history','predecessor') and type(value) is dict and value.get('kind') in ('planning_operation','packed_operation'):
            _require(set(value)=={'kind','ref'},'bundle_state_invalid')
            visit(value['ref'],'planning_state' if value['kind']=='planning_operation' else 'state',depth+1)
        active.remove(ref)
    visit(manifest['state_ref'], 'state')
    for record in manifest['archives']:
        _require(type(record) is dict and 'archive_ref' in record, 'bundle_archive_invalid')
        visit(record['archive_ref'], 'archive')
    for ref in session_bundle['objects']:
        visit(ref, 'session')
    if 'retention' in manifest:
        import cc_development_history as history
        def payload(ref):
            return store.validate_raw_object(ref, fetch(ref))
        history_refs=history.collect_references(manifest,payload)
        # Establish domains through typed control roots before visiting detached
        # packed children. An arbitrary planning object cannot introduce itself.
        for ref in history_refs:
            value=payload(ref)
            if type(value) is dict and value.get('kind')=='history_checkpoint':register_domains(value['source_ref'])
            if type(value) is dict and value.get('kind')=='planning_operation':
                register_domains(value['ref'],'planning',False)
                visit(ref,'history')
            if type(value) is dict and value.get('kind')=='packed_operation':
                register_domains(value['ref'],root_path=('operations',0))
                visit(ref,'history')
            if type(value) is dict and value.get('type')=='planning':visit(ref,'state')
        for ref in history_refs:
            visit(ref, 'history')
    # Exact predecessor manifests may retain objects that current checkpoints
    # no longer reach. Their known typed roots remain part of the archive.
    from cc_development_migration import collect_references
    def predecessor_payload(ref):
        return store.validate_raw_object(ref, fetch(ref))
    predecessor_refs=collect_references(manifest, predecessor_payload)
    # A retained PW02 manifest has planning-domain roots. Register domains from
    # the exact typed predecessor roots before visiting its detached objects;
    # their order in the bounded reference set does not establish authority.
    for ref in predecessor_refs:
        value=predecessor_payload(ref)
        if type(value) is dict and value.get('kind')=='exact_bytes':
            previous=store._decode(store.read_archive_bytes(None,ref,object_reader=predecessor_payload))
            if previous.get('schema_version')==2:register_domains(previous['state_ref'])
        if type(value) is dict and value.get('kind')=='history_checkpoint':register_domains(value['source_ref'])
        if type(value) is dict and value.get('kind')=='planning_operation':register_domains(value['ref'],'planning',False)
        if type(value) is dict and value.get('kind')=='packed_operation':register_domains(value['ref'],root_path=('operations',0))
    for ref in predecessor_refs:
        visit(ref, 'predecessor')
    return refs


def collect(safe):
    """Caller holds Core barrier for the complete bounded closure observation."""
    state, revision, manifest = store.read_state(safe)
    _require(manifest is not None, 'development_state_absent')
    session_bundle = sessions.collect(safe)
    objects = {}
    def fetch(ref):
        if ref not in objects:
            objects[ref] = store.get_raw_object(safe, ref)
        return objects[ref]
    _closure(manifest, session_bundle, fetch)
    result = {'schemaVersion': 2, 'kind': 'controlled_development_bundle',
              'projectIdentity': manifest['project_identity'], 'manifest': manifest,
              'manifestDigest': inputs.digest(manifest), 'objects': objects, 'sessions': session_bundle}
    result['integrity'] = inputs.digest(result)
    validate(safe, result)
    safe.validate()
    return result


def validate(safe, bundle):
    """Hash and decode the complete detached closure before any restore write."""
    from cc_development_lib import storage
    _require(type(bundle) is dict and set(bundle) == {'schemaVersion', 'kind', 'projectIdentity',
            'manifest', 'manifestDigest', 'objects', 'sessions', 'integrity'}
            and type(bundle['schemaVersion']) is int and bundle['schemaVersion'] == 2
            and bundle['kind'] == 'controlled_development_bundle'
            and bundle['projectIdentity'] == storage.identity(safe)
            and bundle['integrity'] == inputs.digest({k: v for k, v in bundle.items() if k != 'integrity'}), 'bundle_invalid')
    _require(len(_encoded(bundle)) <= LIMIT and type(bundle['objects']) is dict
             and len(bundle['objects']) <= store.MAX_OBJECTS, 'bundle_size_limit')
    manifest = bundle['manifest']
    manifest_keys = {'schema_version', 'kind', 'project_identity', 'method', 'state_ref', 'archives', 'compatibility'}
    _require(type(manifest) is dict and set(manifest) in (manifest_keys, manifest_keys | {'retention'})
            and type(manifest['schema_version']) is int
            and manifest['schema_version'] == 2 and manifest['kind'] == 'development_control'
            and manifest['project_identity'] == bundle['projectIdentity']
            and type(manifest['archives']) is list and type(manifest['compatibility']) is list
            and inputs.digest(manifest) == bundle['manifestDigest'], 'bundle_manifest_invalid')
    def envelope(ref):
        _require(ref in bundle['objects'], 'bundle_object_missing')
        return bundle['objects'][ref]
    def payload(ref):
        return store.validate_raw_object(ref, envelope(ref))
    # Validate every supplied object, even when an adversarial archive adds extras.
    for ref in bundle['objects']:
        payload(ref)
    sessions.validate_bundle(safe, bundle['sessions'], payload)
    _require(all(envelope(ref)['domain'] == 'planning' for ref in bundle['sessions']['objects']),
             'session_object_domain_invalid')
    _require(_closure(manifest, bundle['sessions'], envelope) == set(bundle['objects']), 'bundle_closure_invalid')
    state = store.unpack_reference(manifest['state_ref'], payload)
    _require(type(state) is dict and state.get('project_identity') == manifest['project_identity']
             and state.get('method') == manifest['method'], 'bundle_state_invalid')
    storage.validate_state(safe, state, compatibility=manifest['compatibility'], object_reader=payload)
    if 'retention' in manifest:
        import cc_development_history as history
        history.validate_state(state, manifest, payload, full=True, safe=safe)
    from cc_development_migration import validate_metadata
    validate_metadata(safe, state, manifest, object_reader=payload, full=True)
    for record in manifest['archives']:
        raw = store.read_archive_bytes(safe, record['archive_ref'], object_reader=payload)
        if 'source_revision' in record:
            _require(hashlib.sha256(raw).hexdigest() == record['source_revision'], 'bundle_archive_digest_invalid')
    planning = sum(len(_encoded(value)) for value in bundle['objects'].values() if value['domain'] == 'planning')
    planning += sum(len(_encoded(head)) + head.get('reservedBytes', 0) for head in bundle['sessions']['heads'].values())
    _require(planning <= store.PLANNING_LIMIT, 'planning_quota_exceeded')
    safe.validate()
    return state


def _pending(safe):
    item = safe.read(managed_relative(safe.path, PENDING), inputs.Budget(total=8192, per_file=8192), missing=True)
    if item is None:
        return None
    _require(item[1].st_nlink == 1, 'restore_pending_invalid')
    marker = store._decode(item[0])
    _require(type(marker) is dict and set(marker) == {'schemaVersion', 'kind', 'bundleIntegrity', 'projectIdentity'}
             and marker['schemaVersion'] == 1 and marker['kind'] == 'development_restore_pending'
             and type(marker['bundleIntegrity']) is str and store.HASH.fullmatch(marker['bundleIntegrity']),
             'restore_pending_invalid')
    return marker


def check_pending(safe):
    if _pending(safe) is not None:
        raise store.StoreError('restore_recovery_required')


def restore_revision(safe, bundle):
    """Logical absent revision for a reviewed matching interrupted restore."""
    marker = _pending(safe)
    _, revision, manifest = store.read_state(safe)
    if marker is not None:
        _require(marker['bundleIntegrity'] == bundle['integrity'] and marker['projectIdentity'] == bundle['projectIdentity'],
                 'restore_pending_conflict')
        _require(manifest is None or manifest == bundle['manifest'], 'restore_pending_conflict')
        existing = sessions.collect(safe)['heads']
        _require(all(key in bundle['sessions']['heads'] and bundle['sessions']['heads'][key] == head
                     for key, head in existing.items()), 'restore_pending_conflict')
        return 'absent'
    _require(revision == 'absent' and not sessions.collect(safe)['heads'], 'restore_requires_absent_state')
    return revision


def _clear_pending(safe, expected):
    _require(_pending(safe) == expected, 'restore_pending_conflict')
    relative = managed_relative(safe.path, PENDING)
    parent, name = relative.rsplit('/', 1)
    with safe.directory(parent) as directory:
        info = safe._stat(directory, name)
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'restore_pending_invalid')
        if os.name == 'nt':
            safe._win_remove_file(directory, name, info)
        else:
            os.unlink(name, dir_fd=directory)
    store._durable(safe, PENDING)


def publish(safe, bundle):
    """Stage validated immutable payloads, then heads, then the control commit point.

    Caller owns the publication barrier and restore recovery journal. A failure
    before manifest publication leaves staged material for explicit recovery.
    """
    validate(safe, bundle)
    restore_revision(safe, bundle)
    marker = {'schemaVersion': 1, 'kind': 'development_restore_pending', 'bundleIntegrity': bundle['integrity'],
              'projectIdentity': bundle['projectIdentity']}
    if _pending(safe) is None:
        safe.write_atomic(managed_relative(safe.path, PENDING), marker, replace_existing=False)
        store._durable(safe, PENDING)
    for ref, envelope in bundle['objects'].items():
        actual = store.put_object(safe, envelope['payload'], domain=envelope['domain'])
        _require(actual == ref, 'bundle_object_digest_invalid')
    sessions.publish_heads(safe, bundle['sessions'], matching_existing=True)
    _, revision, manifest = store.read_state(safe)
    if manifest is None:
        safe.write_atomic(managed_relative(safe.path, store.MANIFEST), bundle['manifest'], replace_existing=False)
    else:
        _require(manifest == bundle['manifest'], 'restore_pending_conflict')
    store._durable(safe, store.MANIFEST)
    _clear_pending(safe, marker)
    safe.validate()
    return hashlib.sha256(_encoded(bundle['manifest'])).hexdigest()
