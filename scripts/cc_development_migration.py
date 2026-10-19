"""Reviewed exact predecessor upgrades; preserve approvals as historical facts."""
import copy
import hashlib

from cc_development_compatibility import PRIOR, RETAINED, RETENTION, PRODUCT, DISCUSSION, EVOLUTION, PLANNING, trusted_pair, trusted_chain, inspect_project, _json
from cc_evidence_inputs import SafeRoot, Budget
from cc_layout import managed_relative


def _owner():
    from cc_development_lib import storage
    from cc_development_lib.method import IDENTITY, digest
    return storage, IDENTITY, digest


def _raw(root):
    value = root.read(managed_relative(root.path, '.controlcoding/development.json'),
                      Budget(total=2 * 1024 * 1024, per_file=2 * 1024 * 1024), missing=True)
    if value is None:
        raise _owner()[0].DevelopmentError('historical_registry_absent')
    raw, info = value
    if info.st_nlink != 1:
        raise _owner()[0].DevelopmentError('unsafe_state_file')
    return raw, hashlib.sha256(raw).hexdigest()


def _binding(root, raw, observed, target):
    source = _json(raw)['method']
    policy = trusted_pair(PRIOR, target) if target == RETAINED else trusted_chain(source, target)
    return {'kind': 'cd10_storage_migration' if source == PRIOR else 'pw05_progress_migration' if source == PLANNING else 'pw04_planning_migration' if source == EVOLUTION else 'pw03_scope_migration' if source == DISCUSSION else 'pw02_discussion_migration' if source == PRODUCT else 'pw01_product_migration' if source == RETENTION else 'pw00_retention_storage_migration',
            'project_identity': _owner()[0].identity(root),
            'source_revision': hashlib.sha256(raw).hexdigest(), 'source_method': dict(source),
            'target_method': dict(target), 'compatibility': policy,
            'observed_receipts': observed}


def _decode_source(root, raw, object_reader=None):
    import cc_development_store as store
    source = _json(raw)
    if type(source) is not dict or source.get('method') not in (PRIOR, RETAINED,RETENTION,PRODUCT,DISCUSSION,EVOLUTION,PLANNING):
        raise _owner()[0].DevelopmentError('unsupported_migration_source')
    if source.get('schema_version') == 1 and source.get('method') == PRIOR:
        return source, None
    fields = {'schema_version', 'kind', 'project_identity', 'method', 'state_ref', 'archives', 'compatibility'}
    if (set(source) not in (fields,fields|{'retention'}) or source.get('schema_version') != 2
            or source.get('kind') != 'development_control' or source.get('method') not in (RETAINED,RETENTION,PRODUCT,DISCUSSION,EVOLUTION,PLANNING)
            or type(source['archives']) is not list or type(source['compatibility']) is not list):
        raise _owner()[0].DevelopmentError('unsupported_migration_source')
    reader = object_reader or (lambda ref: store.get_object(root, ref))
    state = store.unpack_reference(source['state_ref'], reader)
    if (type(state) is not dict or state.get('method') != source['method']
            or state.get('project_identity') != source['project_identity'] or state.get('retention') != source.get('retention')):
        raise _owner()[0].DevelopmentError('unsupported_migration_source')
    return state, source


def collect_references(manifest, payload_reader):
    """Typed predecessor closure; arbitrary hash-looking text is never followed."""
    import cc_development_store as store
    refs, active = set(), set()
    def visit(ref, mode, depth=0):
        if (type(ref) is not str or not store.HASH.fullmatch(ref)
                or depth > store.MAX_DEPTH or ref in active):
            raise store.StoreError('migration_reference_invalid')
        if ref in refs:
            return
        value = payload_reader(ref)
        refs.add(ref)
        active.add(ref)
        if len(refs) > store.MAX_OBJECTS:
            raise store.StoreError('migration_closure_limit')
        if mode == 'state':
            if type(value) is not dict or value.get('type') not in ('scalar', 'value', 'dict', 'list', 'text','planning'):
                raise store.StoreError('migration_state_invalid')
            kind = value['type']
            if kind=='planning':
                if set(value)!={'type','ref'}:raise store.StoreError('migration_state_invalid')
                visit(value['ref'],'state',depth+1)
            elif kind in ('dict', 'list', 'text'):
                if type(value.get('parts')) is not list:
                    raise store.StoreError('migration_state_invalid')
                for part in value['parts']:
                    visit(part, kind + '_part', depth + 1)
        elif mode in ('dict_part', 'list_part'):
            if type(value) is not list:
                raise store.StoreError('migration_state_invalid')
            for item in value:
                if mode == 'dict_part':
                    if type(item) is not list or len(item) != 2 or type(item[0]) is not str:
                        raise store.StoreError('migration_state_invalid')
                    item = item[1]
                visit(item, 'state', depth + 1)
        elif mode == 'archive':
            if type(value) is not dict or value.get('kind') != 'exact_bytes' or type(value.get('parts')) is not list:
                raise store.StoreError('migration_archive_invalid')
            for part in value['parts']:
                visit(part, 'archive_part', depth + 1)
            raw = store.read_archive_bytes(None, ref, object_reader=payload_reader)
            previous = _json(raw)
            if type(previous) is not dict or previous.get('method') not in (PRIOR, RETAINED,RETENTION,PRODUCT,DISCUSSION,EVOLUTION,PLANNING):
                raise store.StoreError('migration_archive_invalid')
            if previous.get('schema_version') == 2:
                if previous.get('kind') != 'development_control' or type(previous.get('archives')) is not list:
                    raise store.StoreError('migration_archive_invalid')
                visit(previous['state_ref'], 'state', depth + 1)
                if 'retention' in previous:
                    import cc_development_history as history
                    for child in history.collect_references(previous,payload_reader):visit(child,'historical',depth+1)
                for record in previous['archives']:
                    if type(record) is not dict or 'archive_ref' not in record:
                        raise store.StoreError('migration_archive_invalid')
                    visit(record['archive_ref'], 'archive', depth + 1)
            elif previous.get('schema_version') != 1:
                raise store.StoreError('migration_archive_invalid')
        elif mode in ('archive_part', 'text_part') and type(value) is not str:
            raise store.StoreError('migration_archive_invalid')
        active.remove(ref)
    if type(manifest.get('archives')) is not list or len(manifest['archives']) > 7:
        raise store.StoreError('migration_metadata_invalid')
    for record in manifest['archives']:
        if type(record) is not dict or 'archive_ref' not in record:
            raise store.StoreError('migration_metadata_invalid')
        visit(record['archive_ref'], 'archive')
    return refs


def _preview(root, *, locked=False):
    storage, target, digest = _owner()
    from cc_development_bundle import check_pending
    check_pending(root)
    raw, revision = _raw(root)
    state, source_manifest = _decode_source(root, raw)
    source_method = state['method']
    storage.validate_state(root, state, expected_method=source_method,
                           compatibility=source_manifest['compatibility'] if source_manifest else None)
    if source_manifest:
        validate_metadata(root, state, source_manifest, expected_method=source_method)
    inspection = inspect_project(root.path, target, locked=locked)
    if inspection['sourceSha256'] != revision:
        raise storage.DevelopmentError('revision_conflict')
    bound = _binding(root, raw, inspection['observedReceipts'], target)
    root.validate()
    return {'kind': bound['kind'], 'expected_revision': revision,
            'approval_id': digest(bound), 'source_method': dict(source_method),
            'target_method': dict(target), 'compatibility': bound['compatibility'],
            'observed_receipts': bound['observed_receipts'],
            'blockers': inspection['blockingEffects'], 'read_only': True,
            'changes': inspection['changes'], 'preserves_original_bytes': True,
            'decision_applicability': 'Intent, specification and plan decisions remain applicable. Later approvals require current-method review; receipts require current checks.'}


def preview(project):
    """Validate the exact original logical state before proposing any writes."""
    from cc_development_lib import check_root
    check_root(project)
    with SafeRoot(project) as root:
        return _preview(root)


def validate_metadata(root, state, manifest, *, object_reader=None, expected_method=None, full=False):
    """Verify retained lineage without blessing historical delivery evidence."""
    import cc_development_store as store
    storage, installed, digest = _owner()
    target = installed if expected_method is None else expected_method
    if type(manifest) is dict and manifest.get('compatibility') == [] and manifest.get('archives') == []:
        return
    if type(manifest) is not dict:
        raise storage.DevelopmentError('migration_metadata_invalid')
    archives = manifest.get('archives')
    fields = {'kind', 'archive_ref', 'source_revision', 'approval_id', 'actor',
              'source_method', 'target_method', 'observed_receipts'}
    if type(archives) is not list or not 1 <= len(archives) <= 7:
        raise storage.DevelopmentError('migration_metadata_invalid')
    for record in archives:
        if (type(record) is not dict or set(record) != fields
                or record['kind'] != ('cd10_migration' if record['source_method'] == PRIOR else 'pw05_progress_migration' if record['source_method']==PLANNING else 'pw04_planning_migration' if record['source_method']==EVOLUTION else 'pw03_scope_migration' if record['source_method']==DISCUSSION else 'pw02_discussion_migration' if record['source_method']==PRODUCT else 'pw01_product_migration' if record['source_method']==RETENTION else 'pw00_retention_migration')
                or record['source_method'] not in (PRIOR, RETAINED,RETENTION,PRODUCT,DISCUSSION,EVOLUTION,PLANNING)
                or record['target_method'] not in (RETAINED,RETENTION,PRODUCT,DISCUSSION,EVOLUTION,PLANNING, target)
                or any(type(record[key]) is not str or not store.HASH.fullmatch(record[key])
                       for key in ('archive_ref', 'source_revision', 'approval_id'))
                or type(record['actor']) is not str or not record['actor'].strip() or len(record['actor']) > 120
                or type(record['observed_receipts']) is not list or len(record['observed_receipts']) > 2048):
            raise storage.DevelopmentError('migration_metadata_invalid')
    if any(a['target_method']!=b['source_method'] for a,b in zip(archives,archives[1:])):
        raise storage.DevelopmentError('migration_metadata_invalid')
    record = archives[-1]
    expected_policy = trusted_chain(archives[0]['source_method'], target)
    if record['target_method'] != target or manifest.get('compatibility') != expected_policy:
        raise storage.DevelopmentError('migration_metadata_invalid')
    if state.get('retention') and record['source_method'] not in (RETENTION,PRODUCT,DISCUSSION,EVOLUTION,PLANNING):
        from cc_development_history import validate_lineage_anchor
        validate_lineage_anchor(root, state, manifest, record['archive_ref'],
                                record['source_method'], target, object_reader=object_reader, full=full)
        if not full:
            return
    raw = store.read_archive_bytes(root, record['archive_ref'], object_reader=object_reader)
    original, previous_manifest = _decode_source(root, raw, object_reader)
    storage.validate_state(root, original, expected_method=record['source_method'],
                           compatibility=previous_manifest['compatibility'] if previous_manifest else None,
                           object_reader=object_reader)
    if previous_manifest:
        if archives[:-1] != previous_manifest['archives']:
            raise storage.DevelopmentError('migration_history_changed')
        validate_metadata(root, original, previous_manifest, object_reader=object_reader,
                          expected_method=record['source_method'], full=full)
    if (hashlib.sha256(raw).hexdigest() != record['source_revision']
            or digest(_binding(root, raw, record['observed_receipts'], target)) != record['approval_id']):
        raise storage.DevelopmentError('migration_metadata_invalid')
    if state.get('retention'):
        # Explicit full audits may materialize predecessor history. Ordinary
        # loads use the bounded checkpoint lineage anchors above instead.
        from cc_development_history import full_history, lookup_operation
        if (state['project_identity'] != original['project_identity'] or state['method'] != target
                or (original['enabled'] and (not state['enabled'] or state['profile'] != original['profile']))):
            raise storage.DevelopmentError('migration_history_changed')
        hot = {receipt['operation_id']: receipt for receipt in state['operations']}
        for receipt in original['operations']:
            retained = hot.get(receipt['operation_id'])
            if retained is None:
                retained = lookup_operation(root, manifest, receipt['operation_id'], object_reader=object_reader)
            if retained != receipt:
                raise storage.DevelopmentError('migration_history_changed')
        current = {change['id']: change for change in state['changes']}
        for old in original['changes']:
            change = current.get(old['id'])
            if (change is None or change['method'] != old['method']
                    or any(change.get(key) != old.get(key) for key in ('id', 'title', 'owner', 'risk', 'profile',
                                                                      'policy_revision', 'created_at', 'origin'))):
                raise storage.DevelopmentError('migration_history_changed')
            events = full_history(root, change, object_reader=object_reader)
            old_events=full_history(root,old,object_reader=object_reader) if old.get('history_base') else old['history']
            if events[:len(old_events)] != old_events:
                raise storage.DevelopmentError('migration_history_changed')
        return
    if (state['project_identity'] != original['project_identity'] or state['method'] != target
            or (original['enabled'] and (not state['enabled'] or state['profile'] != original['profile']))
            or state['operations'][:len(original['operations'])] != original['operations']):
        raise storage.DevelopmentError('migration_history_changed')
    current = {change['id']: change for change in state['changes']}
    for old in original['changes']:
        change = current.get(old['id'])
        if (change is None or change['method'] != old['method']
                or change['history'][:len(old['history'])] != old['history']
                or any(change.get(key) != old.get(key) for key in ('id', 'title', 'owner', 'risk', 'profile',
                                                                  'policy_revision', 'created_at', 'origin'))):
            raise storage.DevelopmentError('migration_history_changed')


def _replay(root, state, manifest, expected, approval, actor):
    import cc_development_store as store
    storage, target, digest = _owner()
    records = manifest['archives']
    fields = {'kind', 'archive_ref', 'source_revision', 'approval_id', 'actor',
              'source_method', 'target_method', 'observed_receipts'}
    matches = [r for r in records if type(r) is dict and r.get('source_revision') == expected
               and r.get('approval_id') == approval]
    if len(matches) != 1:
        raise storage.DevelopmentError('revision_conflict')
    record = matches[0]
    if (set(record) != fields or record['kind'] not in ('cd10_migration', 'pw00_retention_migration','pw01_product_migration','pw02_discussion_migration','pw03_scope_migration','pw04_planning_migration','pw05_progress_migration')
            or record['actor'] != actor or record['source_method'] not in (PRIOR, RETAINED,RETENTION,PRODUCT,DISCUSSION,EVOLUTION,PLANNING)
            or record['target_method'] != target):
        raise storage.DevelopmentError('migration_replay_invalid')
    original = store.read_archive_bytes(root, record['archive_ref'])
    old, previous_manifest = _decode_source(root, original)
    storage.validate_state(root, old, expected_method=record['source_method'],
                           compatibility=previous_manifest['compatibility'] if previous_manifest else None)
    if (hashlib.sha256(original).hexdigest() != expected
            or digest(_binding(root, original, record['observed_receipts'], target)) != approval):
        raise storage.DevelopmentError('migration_replay_invalid')
    storage.validate_state(root, state, compatibility=manifest['compatibility'])
    validate_metadata(root, state, manifest)
    return record


def migrate(project, expected_revision, approval_id, actor):
    """CAS preview under the canonical owner lock; archive exact original bytes."""
    import cc_development_store as store
    from cc_development_lib import check_root
    check_root(project)
    storage, target, digest = _owner()
    if type(actor) is not str or not actor.strip() or len(actor) > 120:
        raise storage.DevelopmentError('invalid_actor')
    if any(type(v) is not str or len(v) != 64 or any(c not in '0123456789abcdef' for c in v)
           for v in (expected_revision, approval_id)):
        raise storage.DevelopmentError('invalid_migration_preview')
    with SafeRoot(project) as root:
        with storage.transaction(root, operation_id=approval_id):
            raw, revision = _raw(root)
            decoded = _json(raw)
            if type(decoded) is not dict:
                raise storage.DevelopmentError('invalid_state')
            if decoded.get('schema_version') == 2 and decoded.get('method') == target:
                state, revision, manifest = store.read_state(root)
                record = _replay(root, state, manifest, expected_revision, approval_id, actor)
                return {'migrated': False, 'replayed': True, 'revision': revision,
                        'archive': record, 'compatibility': manifest['compatibility']}
            reviewed = _preview(root, locked=True)
            if reviewed['expected_revision'] != expected_revision:
                raise storage.DevelopmentError('revision_conflict')
            if reviewed['approval_id'] != approval_id:
                raise storage.DevelopmentError('preview_mismatch')
            if reviewed['blockers']:
                raise storage.DevelopmentError('migration_active_effects')
            old, previous_manifest = _decode_source(root, raw)
            source_method = old['method']
            updated = copy.deepcopy(old)
            updated['method'] = dict(target)
            policies = ((previous_manifest['compatibility'] if previous_manifest else []) + trusted_chain(source_method,target)
                        if source_method in (RETAINED,RETENTION,PRODUCT,DISCUSSION,EVOLUTION,PLANNING) else trusted_chain(PRIOR,target))
            storage.validate_state(root, updated, compatibility=policies)
            archive_ref = store.archive_bytes(root, raw)
            record = {'kind': 'cd10_migration' if source_method == PRIOR else 'pw05_progress_migration' if source_method==PLANNING else 'pw04_planning_migration' if source_method==EVOLUTION else 'pw03_scope_migration' if source_method==DISCUSSION else 'pw02_discussion_migration' if source_method==PRODUCT else 'pw01_product_migration' if source_method==RETENTION else 'pw00_retention_migration', 'archive_ref': archive_ref,
                      'source_revision': expected_revision, 'approval_id': approval_id,
                      'actor': actor, 'source_method': dict(source_method), 'target_method': dict(target),
                      'observed_receipts': reviewed['observed_receipts']}
            # Reobserve effects immediately before publishing the authoritative
            # manifest. Immutable objects may remain unreferenced after failure.
            final = _preview(root, locked=True)
            if final['approval_id'] != approval_id or final['blockers']:
                raise storage.DevelopmentError('preview_mismatch')
            check_root(project)
            archives = (previous_manifest['archives'] if previous_manifest else []) + [record]
            if updated.get('retention'):
                updated['retention']['lineage_ref']=store.put_object(root,{'kind':'retention_lineage','project_identity':updated['project_identity'],'method':dict(target),'archives':archives,'compatibility':policies})
            revision = store.write_state(root, updated, expected_revision,
                                         {'archives': archives, 'compatibility': policies})
            return {'migrated': True, 'replayed': False, 'revision': revision,
                    'archive': record, 'compatibility': policies}
