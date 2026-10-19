"""Explicit same-project registry backup and absent-state restoration."""
import json
from pathlib import Path

from cc_evidence_inputs import SafeRoot, EvidenceError
from cc_development_lib import snapshot, translate, check_root
from cc_development_lib import storage
from cc_development_lib.method import IDENTITY, canonical, digest
from cc_development_lib.schema import DevelopmentError, revision, text

LIMIT = 192 * 1024 * 1024


def backup(root):
    """Return a validated archive; the caller chooses where to save it."""
    try:
        check_root(root)
        with SafeRoot(root) as safe:
            from cc_development_store import _read, _decode, MANIFEST, MANIFEST_LIMIT
            raw = _read(safe, MANIFEST, MANIFEST_LIMIT, missing=True)
            if raw and _decode(raw).get('schema_version') == 2:
                from cc_development_bundle import collect
                with storage.transaction(safe, 'backup'):
                    storage.load(safe, locked=True)
                    return collect(safe)
            state, state_revision = storage.load(safe)
            if state_revision == 'absent':
                raise DevelopmentError('development_state_absent')
            result = {'schemaVersion': 1, 'kind': 'controlled_development_backup',
                      'projectIdentity': state['project_identity'], 'method': dict(IDENTITY),
                      'stateDigest': digest(state), 'state': state}
            safe.validate()
            return result
    except (OSError, ValueError, TypeError, RecursionError, EvidenceError) as error:
        raise translate(error) from None


def read_archive(path):
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise DevelopmentError('archive_size_limit')
        return json.loads(raw, object_pairs_hook=storage.pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(DevelopmentError('invalid_archive')))
    except (OSError, UnicodeError, ValueError, RecursionError, EvidenceError) as error:
        raise translate(error) from None


def validate(safe, archive):
    if type(archive) is dict and archive.get('schemaVersion') == 2:
        from cc_development_bundle import validate as validate_bundle
        try:
            return validate_bundle(safe, archive)
        except ValueError:
            raise DevelopmentError('archive_identity_invalid') from None
    if (type(archive) is not dict or set(archive) != {'schemaVersion', 'kind', 'projectIdentity', 'method', 'stateDigest', 'state'}
            or type(archive['schemaVersion']) is not int or archive['schemaVersion'] != 1
            or archive['kind'] != 'controlled_development_backup' or archive['method'] != IDENTITY
            or type(archive['state']) is not dict
            or archive['projectIdentity'] != storage.identity(safe)
            or digest(archive['state']) != archive['stateDigest']
            or archive['state'].get('project_identity') != archive['projectIdentity']):
        raise DevelopmentError('archive_identity_invalid')
    if len(canonical(archive)) > LIMIT:
        raise DevelopmentError('archive_size_limit')
    storage.validate_state(safe, archive['state'])
    return archive['state']


def prepared(safe, archive, actor, reason, state_revision, *, locked=False, restore_lock=None):
    state = validate(safe, archive)
    evaluation_state = None
    if archive.get('schemaVersion') == 2:
        from cc_development_history import compact_validation_state
        from cc_development_store import validate_raw_object
        # validate() has already checked every supplied object and the exact
        # complete closure. Use only that authenticated graph before publication;
        # the original compact histories remain in the returned projection.
        reader = lambda ref: validate_raw_object(ref, archive['objects'][ref])
        evaluation_state = compact_validation_state(
            safe, state, object_reader=reader, compatibility=archive['manifest']['compatibility'])
    text(actor, 'actor', limit=160)
    text(reason, 'reason')
    if state_revision != 'absent':
        raise DevelopmentError('restore_requires_absent_state')
    action = {'kind': 'restore', 'archive_digest': digest(archive), 'actor': actor, 'reason': reason}
    if restore_lock is not None:
        action['restore_lock_fingerprint'] = restore_lock
    view = snapshot(safe, state, 'absent', locked=locked, evaluation_state=evaluation_state)
    approval = digest({'project_identity': storage.identity(safe), 'revision': 'absent', 'action': action,
                       'view': view})
    return {'schema_version': 1, 'revision': 'absent', 'approval_id': approval, 'action': action,
            'effects': ['Restore the validated registry to this same project; current gates are reevaluated'],
            'blockers': [], 'view': view}


def _restore_lock(safe, archive):
    """Only a matching pending restore can propose recovery of a dead writer."""
    from cc_development_bundle import _pending, restore_revision
    if _pending(safe) is None:
        return None, None
    validate(safe, archive)
    restore_revision(safe, archive)
    try:
        fingerprint, process, info = storage.recovery_lock(safe)
    except FileNotFoundError:
        return None, None
    if process != 'inactive':
        raise DevelopmentError('recovery_writer_' + process)
    return fingerprint, info


def prepare_restore(root, archive, actor, reason):
    try:
        check_root(root)
        with SafeRoot(root) as safe:
            if type(archive) is dict and archive.get('schemaVersion') == 2:
                from cc_development_bundle import restore_revision
                state_revision = restore_revision(safe, archive)
            else:
                _, state_revision = storage.load(safe)
            restore_lock, _ = _restore_lock(safe, archive) if archive.get('schemaVersion') == 2 else (None, None)
            result = prepared(safe, archive, actor, reason, state_revision, locked=True, restore_lock=restore_lock)
            safe.validate()
            return result
    except (OSError, ValueError, TypeError, RecursionError, EvidenceError) as error:
        raise translate(error) from None


def restore(root, archive, actor, reason, approval_id):
    revision(approval_id)
    try:
        check_root(root)
        with SafeRoot(root) as safe:
            bundled = type(archive) is dict and archive.get('schemaVersion') == 2
            restore_lock, lock_info = _restore_lock(safe, archive) if bundled else (None, None)
            if restore_lock is not None:
                from cc_development_bundle import restore_revision
                observed = prepared(safe, archive, actor, reason, restore_revision(safe, archive),
                                    locked=True, restore_lock=restore_lock)
                if observed['approval_id'] != approval_id:
                    raise DevelopmentError('approval_conflict')
                current_fingerprint, process, current_info = storage.recovery_lock(safe)
                if current_fingerprint != restore_lock or process != 'inactive':
                    raise DevelopmentError('recovery_lock_changed')
                storage.release_lock(safe, expected=current_info)
            with storage.transaction(safe, approval_id):
                if bundled:
                    from cc_development_bundle import restore_revision, publish
                    state_revision = restore_revision(safe, archive)
                else:
                    _, state_revision = storage.load(safe, locked=True)
                preview = prepared(safe, archive, actor, reason, state_revision, locked=True, restore_lock=restore_lock)
                if preview['approval_id'] != approval_id:
                    raise DevelopmentError('approval_conflict')
                state = validate(safe, archive)
                new_revision = publish(safe, archive) if bundled else storage.save(safe, state, 'absent')
            return {'schema_version': 1, 'view': snapshot(safe, state, new_revision),
                    'receipt': {'operation_id': approval_id, 'approval_id': approval_id,
                                'revision': new_revision, 'archive_digest': digest(archive),
                                'actor': actor, 'reason': reason, 'idempotent': False}}
    except (OSError, ValueError, TypeError, RecursionError, EvidenceError) as error:
        raise translate(error) from None
