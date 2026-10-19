"""Authentic checkpoint transitions and adversarial retained-source controls."""
from pathlib import Path
import copy
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cc_development_lib as dev
import cc_development_history as history
import cc_development_store as store
from cc_evidence_inputs import SafeRoot


def act(root, action):
    current = dev.read(root)
    prepared = dev.prepare(root, action, current['revision'])
    assert not prepared['blockers']
    return dev.commit(root, action, current['revision'], prepared['approval_id']), prepared


def fixture(root):
    act(root, {'kind': 'activate', 'profile': 'routine'})
    act(root, {'kind': 'create', 'change_id': 'C1', 'title': 'Feature', 'owner': 'Owner', 'risk': 'routine'})
    content = {'problem': 'First requirement', 'outcome': 'Useful result', 'affected_users_and_systems': ['User'],
               'constraints': [], 'non_goals': [], 'open_questions': [], 'success_measures': ['Observed behavior']}
    result, prepared = act(root, {'kind': 'artifact', 'change_id': 'C1', 'stage': 'intent', 'content': content, 'actor': 'Owner'})
    act(root, {'kind': 'decide', 'change_id': 'C1', 'stage': 'intent', 'decision': 'accepted', 'actor': 'Owner', 'reason': 'Reviewed scope'})
    return prepared, content


def checkpoint(root):
    prepared = history.checkpoint_prepare(root)
    result = history.checkpoint_commit(root, prepared['revision'], prepared['approval_id'], 'Owner')
    return prepared, result


def test_checkpoint_exact_history_and_archived_retry(tmp_path):
    old, content = fixture(tmp_path)
    original = dev.read(tmp_path)['changes'][0]['history']
    prepared, result = checkpoint(tmp_path)
    view = dev.read(tmp_path)
    change = view['changes'][0]
    assert change['gates']['intent']['accepted']
    assert change['history'] == [] and change['history_retention']['partial']
    assert history.page(tmp_path, 'C1')['records'] == original
    replay = dev.commit(tmp_path, old['action'], old['revision'], old['approval_id'])
    assert replay['receipt']['idempotent'] and replay['view']['revision'] == view['revision']
    repeat = history.checkpoint_commit(tmp_path, prepared['revision'], prepared['approval_id'], 'Owner')
    assert repeat['idempotent'] and repeat['revision'] == view['revision']
    with pytest.raises(store.StoreError, match='checkpoint_idempotency_conflict'):
        history.checkpoint_commit(tmp_path, prepared['revision'], prepared['approval_id'], 'Different actor')
    content['problem'] = 'Changed requirement'
    act(tmp_path, {'kind': 'artifact', 'change_id': 'C1', 'stage': 'intent', 'content': content, 'actor': 'Owner'})
    assert not dev.read(tmp_path)['changes'][0]['gates']['intent']['accepted']
    assert len(history.page(tmp_path, 'C1')['records']) == len(original) + 1


def test_stale_checkpoint_and_cursor(tmp_path):
    fixture(tmp_path)
    prepared = history.checkpoint_prepare(tmp_path)
    first = history.page(tmp_path, 'C1', limit=1)
    act(tmp_path, {'kind': 'decide', 'change_id': 'C1', 'stage': 'intent', 'decision': 'rejected', 'actor': 'Owner', 'reason': 'New issue'})
    with pytest.raises(store.StoreError, match='checkpoint_preview_stale'):
        history.checkpoint_commit(tmp_path, prepared['revision'], prepared['approval_id'], 'Owner')
    with pytest.raises(store.StoreError, match='history_revision_changed'):
        history.page(tmp_path, 'C1', cursor=first['cursor'])


def test_untouched_checkpoint_base_is_reused(tmp_path):
    fixture(tmp_path)
    checkpoint(tmp_path)
    with SafeRoot(tmp_path) as safe:
        initial = store.read_state(safe)[0]['changes'][0]['history_base']
    for _ in range(3):
        checkpoint(tmp_path)
    act(tmp_path, {'kind': 'create', 'change_id': 'C2', 'title': 'Other feature', 'owner': 'Owner', 'risk': 'routine'})
    checkpoint(tmp_path)
    with SafeRoot(tmp_path) as safe:
        state = store.read_state(safe)[0]
        assert state['changes'][0]['history_base'] == initial
        assert state['retention']['changes']['C1'] == initial['ref']
    assert len(history.page(tmp_path, 'C1')['records']) == 3


def test_resealed_projection_cannot_restore_old_acceptance(tmp_path):
    fixture(tmp_path)
    act(tmp_path, {'kind': 'decide', 'change_id': 'C1', 'stage': 'intent', 'decision': 'rejected', 'actor': 'Owner', 'reason': 'New issue'})
    checkpoint(tmp_path)
    with SafeRoot(tmp_path) as safe:
        state, revision, manifest = store.read_state(safe)
        change = state['changes'][0]
        node = store.get_object(safe, change['history_base']['ref'])
        originals = history.full_history(safe, change)
        acceptance = next(event for event in originals if event['action'].get('decision') == 'accepted')
        # Replace latest rejection with the genuine older approval; reseal all
        # outer checkpoint objects while preserving original source/pages.
        old_anchor = next(a for a in node['anchors'] if store.get_object(safe, a['ref'])['action'].get('decision') == 'rejected')
        older_ref = history.index_lookup(node['event_root'], acceptance['digest'], lambda ref: store.get_object(safe, ref))
        replacement = {'digest': acceptance['digest'], 'ref': older_ref, 'position': originals.index(acceptance)}
        node['anchors'][node['anchors'].index(old_anchor)] = replacement
        selected = [store.get_object(safe, a['ref']) for a in node['anchors']]
        projection = history._projection(change, selected)
        projection.pop('history_base', None)
        node['projection_ref'] = store._pack(safe, projection)
        sequence = store.get_object(safe, node['history_root'])
        sequence['checkpoint'] = {k: v for k, v in node.items() if k not in ('kind', 'history_root')}
        node['history_root'] = store.put_object(safe, sequence)
        ref = store.put_object(safe, node)
        change['history_base']['ref'] = ref
        state['retention']['changes']['C1'] = ref
        change['decisions'] = projection['decisions']
        change['criterionDecisions'] = projection['criterionDecisions']
        store.write_state(safe, state, revision)
    with pytest.raises(dev.DevelopmentError):
        dev.read(tmp_path)


def test_checkpoint_failure_before_publication_preserves_original(tmp_path, monkeypatch):
    fixture(tmp_path)
    before = dev.read(tmp_path)
    prepared = history.checkpoint_prepare(tmp_path)
    original = SafeRoot.write_atomic
    def fail(safe, path, value, **kwargs):
        if path.endswith('/development.json'):
            raise OSError('injected publication interruption')
        return original(safe, path, value, **kwargs)
    monkeypatch.setattr(SafeRoot, 'write_atomic', fail)
    with pytest.raises(OSError, match='publication interruption'):
        history.checkpoint_commit(tmp_path, prepared['revision'], prepared['approval_id'], 'Owner')
    assert dev.read(tmp_path)['revision'] == before['revision']
    assert dev.read(tmp_path)['changes'][0]['history'] == before['changes'][0]['history']


def test_radix_lookup_bounded_and_corruption_fails_closed(tmp_path):
    import hashlib
    with SafeRoot(tmp_path) as safe:
        receipts = {}
        for index in range(3000):
            key = hashlib.sha256(str(index).encode()).hexdigest()
            receipts[key] = store.put_object(safe, {'operation_id': key, 'approval_id': key})
        root = history._insert(safe, None, receipts)
        reads = []
        def reader(ref):
            reads.append(ref)
            return store.get_object(safe, ref)
        key = next(iter(receipts))
        assert history.index_lookup(root, key, reader) == receipts[key]
        assert len(reads) <= history.LOOKUP_LIMIT
        assert history.index_lookup(root, 'f' * 64, reader) is None


def test_resealed_anchor_position_cannot_change_original_order(tmp_path):
    fixture(tmp_path)
    act(tmp_path, {'kind': 'decide', 'change_id': 'C1', 'stage': 'intent', 'decision': 'rejected', 'actor': 'Owner', 'reason': 'Later issue'})
    checkpoint(tmp_path)
    with SafeRoot(tmp_path) as safe:
        state, revision, _ = store.read_state(safe)
        change = state['changes'][0]
        node = store.get_object(safe, change['history_base']['ref'])
        node['anchors'][1]['position'] = 2  # intent belongs at source position 1
        sequence = store.get_object(safe, node['history_root'])
        sequence['checkpoint'] = {k: v for k, v in node.items() if k not in ('kind', 'history_root')}
        node['history_root'] = store.put_object(safe, sequence)
        ref = store.put_object(safe, node)
        change['history_base']['ref'] = ref
        state['retention']['changes']['C1'] = ref
        store.write_state(safe, state, revision)
    with pytest.raises(dev.DevelopmentError): dev.read(tmp_path)


def test_cold_malformed_receipt_is_not_absence(tmp_path):
    fixture(tmp_path)
    checkpoint(tmp_path)
    with SafeRoot(tmp_path) as safe:
        state, _, manifest = store.read_state(safe)
        key = state['retention']['activation']['operation_id']
        ref = history.index_lookup(state['retention']['replay_root'], key, lambda r: store.get_object(safe, r))
        def reader(item):
            value = store.get_object(safe, item)
            if item == ref: value.pop('action')
            return value
        with pytest.raises(store.StoreError, match='retention_receipt_invalid'):
            history.lookup_operation(safe, manifest, key, reader)


def test_checkpoint_source_manifest_foreign_project_rejected(tmp_path):
    fixture(tmp_path)
    checkpoint(tmp_path)
    with SafeRoot(tmp_path) as safe:
        state, revision, _ = store.read_state(safe)
        change = state['changes'][0]
        node = store.get_object(safe, change['history_base']['ref'])
        source_manifest = store._decode(store.read_archive_bytes(safe, node['source_manifest_ref']))
        source_manifest['project_identity'] = 'f' * 64
        node['source_manifest_ref'] = store.archive_bytes(safe, store._encoded(source_manifest))
        sequence = store.get_object(safe, node['history_root'])
        sequence['checkpoint'] = {k: v for k, v in node.items() if k not in ('kind', 'history_root')}
        node['history_root'] = store.put_object(safe, sequence)
        ref = store.put_object(safe, node)
        change['history_base']['ref'] = ref
        state['retention']['changes']['C1'] = ref
        store.write_state(safe, state, revision)
    with pytest.raises(dev.DevelopmentError): dev.read(tmp_path)


def test_actual_checkpoint_writer_crash_recovery_and_replay(tmp_path):
    import subprocess
    fixture(tmp_path)
    prepared = history.checkpoint_prepare(tmp_path)
    program = (
        'import os,sys\n'
        'sys.path.insert(0,sys.argv[1])\n'
        'import cc_development_history as h,cc_development_store as s\n'
        'original=s._durable\n'
        'def crash(root,path):\n'
        ' if path==s.MANIFEST: os._exit(47)\n'
        ' return original(root,path)\n'
        's._durable=crash\n'
        'h.checkpoint_commit(sys.argv[2],sys.argv[3],sys.argv[4],"Owner")\n'
    )
    child = subprocess.run([sys.executable, '-B', '-c', program, str(Path(__file__).resolve().parents[1] / 'scripts'),
                            str(tmp_path), prepared['revision'], prepared['approval_id']], capture_output=True, timeout=60)
    assert child.returncode == 47, child.stderr.decode(errors='replace')
    with pytest.raises(dev.DevelopmentError, match='recovery_required'): dev.read(tmp_path)
    recovery = dev.recovery_prepare(tmp_path, 'Owner', 'Observed checkpoint writer exit')
    assert not recovery['blockers']
    dev.commit(tmp_path, recovery['action'], recovery['revision'], recovery['approval_id'])
    replay = history.checkpoint_commit(tmp_path, prepared['revision'], prepared['approval_id'], 'Owner')
    assert replay['idempotent'] and history.page(tmp_path, 'C1')['total'] == 3
