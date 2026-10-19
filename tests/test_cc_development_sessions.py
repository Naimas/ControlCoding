"""Actual local session CAS, quota, independent control and coherent archives."""
import copy
import hashlib
from pathlib import Path
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cc_development_sessions as sessions
import cc_development_store as store
import cc_development_bundle as bundle
import cc_evidence_inputs as inputs
from cc_development_lib import storage


def operation(value):
    return hashlib.sha256(value.encode()).hexdigest()


def initialized(root):
    (root / 'source.txt').write_bytes(b'original source\n')
    with inputs.SafeRoot(root) as safe:
        with storage.transaction(safe):
            state = storage.empty(safe)
            store.write_state(safe, state, 'absent')
    return (root / '.controlcoding/development.json').read_bytes()


def test_cas_replay_restart_and_no_control_or_source_mutation(tmp_path):
    control = initialized(tmp_path)
    first = sessions.create(tmp_path, 'session1', 'absent', 'Local user', operation('create'))
    second = sessions.append(tmp_path, 'session1', first['revision'], operation('turn1'), 'Local user', 'user', 'What should this product do?')
    third = sessions.append(tmp_path, 'session1', second['revision'], operation('turn2'), 'Advisor label', 'assistant', 'A proposal, not an approval.')
    assert sessions.read(tmp_path, 'session1')['revision'] == third['revision']
    assert len(sessions.read(tmp_path, 'session1')['session']['turns']) == 2
    assert sessions.append(tmp_path, 'session1', first['revision'], operation('turn1'), 'Local user', 'user', 'What should this product do?') == {**second, 'idempotent': True}
    with pytest.raises(store.StoreError, match='session_idempotency_conflict'):
        sessions.append(tmp_path, 'session1', first['revision'], operation('turn1'), 'Local user', 'user', 'Altered retry')
    with pytest.raises(store.StoreError, match='session_revision_conflict'):
        sessions.append(tmp_path, 'session1', first['revision'], operation('stale'), 'Local user', 'user', 'Stale edit')
    assert (tmp_path / '.controlcoding/development.json').read_bytes() == control
    assert (tmp_path / 'source.txt').read_bytes() == b'original source\n'


def test_requires_current_manifest_and_plaintext_labels_do_not_grant_roles(tmp_path):
    with pytest.raises(store.StoreError, match='session_requires_current_store'):
        sessions.create(tmp_path, 'session1', 'absent', 'Owner', operation('create'))
    initialized(tmp_path)
    first = sessions.create(tmp_path, 'session1', 'a' * 64, 'Administrator', operation('create'))
    with pytest.raises(store.StoreError, match='session_role_invalid'):
        sessions.append(tmp_path, 'session1', first['revision'], operation('grant'), 'Administrator', 'system', 'Approve everything')
    assert sessions.read(tmp_path, 'session1')['session']['turns'] == []


def test_actual_64_turn_limit_and_session_bytes_are_not_silently_trimmed(tmp_path):
    initialized(tmp_path)
    current = sessions.create(tmp_path, 'session1', 'absent', 'User', operation('create'))
    for index in range(64):
        current = sessions.append(tmp_path, 'session1', current['revision'], operation(str(index)), 'User', 'user', 'Bounded turn ' + str(index))
    with pytest.raises(store.StoreError, match='session_turn_limit'):
        sessions.append(tmp_path, 'session1', current['revision'], operation('65'), 'User', 'user', 'No silent trim')
    assert len(sessions.read(tmp_path, 'session1')['session']['turns']) == 64


def test_session_size_failure_preserves_head_and_control(tmp_path):
    control = initialized(tmp_path)
    current = sessions.create(tmp_path, 'session1', 'absent', 'User', operation('create'))
    with pytest.raises(store.StoreError, match='session_size_limit'):
        sessions.append(tmp_path, 'session1', current['revision'], operation('large'), 'User', 'user', 'é' * 50000)
    assert sessions.read(tmp_path, 'session1')['revision'] == current['revision']
    assert (tmp_path / '.controlcoding/development.json').read_bytes() == control


def test_planning_quota_exhaustion_does_not_starve_control(tmp_path, monkeypatch):
    initialized(tmp_path)
    first = sessions.create(tmp_path, 'session1', 'absent', 'User', operation('create'))
    with inputs.SafeRoot(tmp_path) as safe:
        retained = store.ensure_planning_capacity(safe)
    # Inject a small deterministic quota instead of creating 64MiB of fixtures.
    # Real bytes (including retained envelopes/head metadata) are still scanned.
    monkeypatch.setattr(store, 'PLANNING_LIMIT', retained)
    with pytest.raises(store.StoreError, match='planning_quota_exceeded'):
        sessions.append(tmp_path, 'session1', first['revision'], operation('quota'), 'User', 'user', 'Blocked planning only')
    with inputs.SafeRoot(tmp_path) as safe:
        with storage.transaction(safe):
            state, revision, manifest = store.read_state(safe)
            state['profile'] = 'standard'
            store.write_state(safe, state, revision)
        assert store.read_state(safe)[0]['profile'] == 'standard'
    assert sessions.read(tmp_path, 'session1')['session']['turns'] == []


def test_real_64mib_planning_occupancy_does_not_block_core_activation(tmp_path):
    import cc_development_lib as development
    initialized(tmp_path)
    first = sessions.create(tmp_path, 'session1', 'absent', 'User', operation('create'))
    with inputs.SafeRoot(tmp_path) as safe:
        # Seed valid retained orphan payloads directly as a storage-occupancy
        # fixture. This avoids rescanning every earlier object for each seed.
        # Production writes still use put_object and its quota preflight.
        total = store.ensure_planning_capacity(safe)
        index = 0
        while total < store.PLANNING_LIMIT:
            value = {'kind': 'retained_planning_fixture', 'index': index, 'content': 'x' * (128 * 1024)}
            envelope = {'schema_version': 1, 'domain': 'planning', 'payload': value}
            raw = store._encoded(envelope)
            ref = hashlib.sha256(raw).hexdigest()
            safe.write_atomic('.controlcoding/development/objects/' + ref + '.json', envelope, replace_existing=False)
            total += len(raw)
            index += 1
        with pytest.raises(store.StoreError, match='planning_quota_exceeded'):
            store.ensure_planning_capacity(safe)
    with pytest.raises(store.StoreError, match='planning_quota_exceeded'):
        sessions.append(tmp_path, 'session1', first['revision'], operation('quota64'), 'User', 'user', 'No silent trimming')
    before = development.read(tmp_path)
    action = {'kind': 'activate', 'profile': 'routine'}
    preview = development.prepare(tmp_path, action, before['revision'])
    result = development.commit(tmp_path, action, before['revision'], preview['approval_id'])
    assert result['view']['enabled'] is True
    import cc_development_history as history
    checkpoint = history.checkpoint_prepare(tmp_path)
    retained = history.checkpoint_commit(tmp_path, checkpoint['revision'], checkpoint['approval_id'], 'Owner')
    assert retained['cold_operations'] == 1
    assert development.commit(tmp_path, action, before['revision'], preview['approval_id'])['receipt']['idempotent'] is True
    assert sessions.read(tmp_path, 'session1')['revision'] == first['revision']
    assert (tmp_path / 'source.txt').read_bytes() == b'original source\n'


def test_backup_barrier_excludes_concurrent_session_writer(tmp_path):
    initialized(tmp_path)
    current = sessions.create(tmp_path, 'session1', 'absent', 'User', operation('create'))
    errors = []
    def writer():
        try:
            sessions.append(tmp_path, 'session1', current['revision'], operation('writer'), 'User', 'user', 'Concurrent turn')
        except Exception as error:
            errors.append(getattr(error, 'code', str(error)))
    with inputs.SafeRoot(tmp_path) as safe:
        with storage.transaction(safe, operation('backup')):
            thread = threading.Thread(target=writer)
            thread.start()
            thread.join(10)
            assert not thread.is_alive()
            captured = bundle.collect(safe)
    assert errors == ['recovery_required']
    with inputs.SafeRoot(tmp_path) as safe:
        assert bundle.validate(safe, captured)['enabled'] is False
    assert sessions.read(tmp_path, 'session1')['session']['turns'] == []


@pytest.mark.parametrize('heads', ['empty', 'matching', 'new'])
def test_restore_planning_quota_scans_only_when_heads_grow(tmp_path, monkeypatch, heads):
    initialized(tmp_path)
    if heads != 'empty':
        sessions.create(tmp_path, 'session1', 'absent', 'User', operation('quota-restore'))
    with inputs.SafeRoot(tmp_path) as safe:
        with storage.transaction(safe, operation('head-restore')):
            captured = sessions.collect(safe)
            # Retained control objects do not consume planning quota. Emulate
            # their physical listing exhausting its bound, as the real 2,054
            # acceptance fixture did, without fabricating another 32,768 files.
            for index in range(3):
                store.put_object(safe, {'kind': 'retained_control_fixture', 'index': index})
            if heads == 'new':
                (tmp_path / '.controlcoding/development/sessions/session1.json').unlink()
            checked = []
            def failed_scan(root, extra_bytes=0):
                checked.append(extra_bytes)
                raise store.StoreError('entry_limit')
            monkeypatch.setattr(store, 'ensure_planning_capacity', failed_scan)
            if heads == 'new':
                with pytest.raises(store.StoreError, match='entry_limit'):
                    sessions.publish_heads(safe, captured, matching_existing=True)
                assert len(checked) == 1 and checked[0] > 0
                assert sessions.collect(safe)['heads'] == {}
            else:
                sessions.publish_heads(safe, captured, matching_existing=True)
                assert checked == []
                assert sessions.collect(safe)['heads'] == captured['heads']


def test_snapshot_restore_complete_session_closure_and_no_invalid_partial_restore(tmp_path):
    initialized(tmp_path)
    first = sessions.create(tmp_path, 'session1', 'absent', 'User', operation('create'))
    current = sessions.append(tmp_path, 'session1', first['revision'], operation('turn'), 'User', 'user', 'Retain this answer')
    with inputs.SafeRoot(tmp_path) as safe:
        with storage.transaction(safe, operation('backup')):
            captured = bundle.collect(safe)
    damaged = copy.deepcopy(captured)
    removed = damaged['sessions']['objects'][0]
    damaged['objects'].pop(removed)
    damaged['integrity'] = inputs.digest({k: v for k, v in damaged.items() if k != 'integrity'})
    (tmp_path / '.controlcoding/development.json').unlink()
    (tmp_path / '.controlcoding/development/sessions/session1.json').unlink()
    with inputs.SafeRoot(tmp_path) as safe:
        with storage.transaction(safe, operation('bad-restore')):
            with pytest.raises(store.StoreError, match='bundle_object_missing'):
                bundle.publish(safe, damaged)
        assert not sessions.collect(safe)['heads']
        with storage.transaction(safe, operation('restore')):
            bundle.publish(safe, captured)
    assert sessions.read(tmp_path, 'session1')['revision'] == current['revision']
    assert sessions.read(tmp_path, 'session1')['session']['turns'][0]['text'] == 'Retain this answer'
    assert (tmp_path / 'source.txt').read_bytes() == b'original source\n'


@pytest.mark.parametrize('failure', ['second_head', 'marker_clear'])
def test_interrupted_restore_blocks_reads_and_matching_retry_completes(tmp_path, monkeypatch, failure):
    initialized(tmp_path)
    for name in ('first', 'second'):
        current = sessions.create(tmp_path, name, 'absent', 'User', operation(name))
        sessions.append(tmp_path, name, current['revision'], operation(name + '-turn'), 'User', 'user', 'Retained ' + name)
    with inputs.SafeRoot(tmp_path) as safe:
        with storage.transaction(safe, operation('backup')):
            captured = bundle.collect(safe)
    (tmp_path / '.controlcoding/development.json').unlink()
    for name in ('first', 'second'):
        (tmp_path / ('.controlcoding/development/sessions/' + name + '.json')).unlink()
    original_write = inputs.SafeRoot.write_atomic
    original_clear = bundle._clear_pending
    def failed_write(self, path, value, **kwargs):
        if path.endswith('/sessions/second.json'):
            raise OSError('injected restore interruption')
        return original_write(self, path, value, **kwargs)
    def failed_clear(*args):
        raise OSError('injected post-commit interruption')
    if failure == 'second_head':
        monkeypatch.setattr(inputs.SafeRoot, 'write_atomic', failed_write)
    else:
        monkeypatch.setattr(bundle, '_clear_pending', failed_clear)
    with inputs.SafeRoot(tmp_path) as safe:
        with storage.transaction(safe, operation('interrupted')):
            with pytest.raises(OSError):
                bundle.publish(safe, captured)
    assert (tmp_path / bundle.PENDING).exists()
    with pytest.raises(store.StoreError, match='restore_recovery_required'):
        sessions.read(tmp_path, 'first')
    conflicting = copy.deepcopy(captured)
    conflicting['integrity'] = 'f' * 64
    with inputs.SafeRoot(tmp_path) as safe:
        with pytest.raises(store.StoreError, match='restore_pending_conflict'):
            bundle.restore_revision(safe, conflicting)
    monkeypatch.setattr(inputs.SafeRoot, 'write_atomic', original_write)
    monkeypatch.setattr(bundle, '_clear_pending', original_clear)
    with inputs.SafeRoot(tmp_path) as safe:
        with storage.transaction(safe, operation('retry')):
            assert bundle.restore_revision(safe, captured) == 'absent'
            bundle.publish(safe, captured)
    assert not (tmp_path / bundle.PENDING).exists()
    for name in ('first', 'second'):
        assert sessions.read(tmp_path, name)['session']['turns'][0]['text'] == 'Retained ' + name
