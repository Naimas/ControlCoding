"""Lossless storage and failure boundaries, without invoking project execution."""
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cc_development_store as store
from cc_evidence_inputs import SafeRoot
from cc_layout import managed_relative, MARKER_BYTES


def state():
    return {'schema_version': 1, 'project_identity': 'project', 'method': {'version': 'test'},
            'changes': [], 'operations': [], 'enabled': False, 'profile': 'routine'}


def test_restart_lossless_and_metadata(tmp_path):
    value = state()
    value['operations'] = [{'id': i, 'text': 'shared'} for i in range(2100)]
    with SafeRoot(tmp_path) as safe:
        revision = store.write_state(safe, value, 'absent', {'archives': [], 'compatibility': [{'old': 'test'}]})
    with SafeRoot(tmp_path) as safe:
        restored, actual, manifest = store.read_state(safe)
        assert restored == value and actual == revision
        assert len((tmp_path / managed_relative(tmp_path, store.MANIFEST)).read_bytes()) < 1024
        value['enabled'] = True
        store.write_state(safe, value, actual)
        assert store.read_state(safe)[2]['compatibility'] == manifest['compatibility']


def test_stale_cas_preserves_head(tmp_path):
    with SafeRoot(tmp_path) as safe:
        revision = store.write_state(safe, state(), 'absent')
        with pytest.raises(store.StoreError, match='revision_conflict'):
            store.write_state(safe, state(), 'absent')
        assert store.read_state(safe)[1] == revision


def test_manifest_identity_tamper_is_not_logical_method_adoption(tmp_path):
    with SafeRoot(tmp_path) as safe:
        store.write_state(safe, state(), 'absent')
        manifest = store.read_state(safe)[2]
        manifest['method']['version'] = 'tampered'
        safe.write_atomic(managed_relative(tmp_path, store.MANIFEST), manifest)
        with pytest.raises(store.StoreError, match='manifest_identity_mismatch'):
            store.read_state(safe)


@pytest.mark.parametrize('corrupt', [False, True])
def test_missing_or_corrupt_object_fails(tmp_path, corrupt):
    with SafeRoot(tmp_path) as safe:
        store.write_state(safe, state(), 'absent')
        ref = store.read_state(safe)[2]['state_ref']
    path = tmp_path / managed_relative(tmp_path, store.OBJECTS + ref + '.json')
    if corrupt:
        path.write_bytes(b'{}\n')
    else:
        path.unlink()
    with SafeRoot(tmp_path) as safe:
        with pytest.raises(store.StoreError, match='object_digest_mismatch|object_missing'):
            store.read_state(safe)


def test_archive_exact_bytes_and_large_unicode(tmp_path):
    raw = b'  {"old":true}\r\n' * 10000
    value = state()
    value['text'] = '\U0001f680' * 60000
    with SafeRoot(tmp_path) as safe:
        ref = store.archive_bytes(safe, raw)
        assert store.read_archive_bytes(safe, ref) == raw
        store.write_state(safe, value, 'absent')
        assert store.read_state(safe)[0] == value


def test_state_over_old_ceiling_and_small_state_single_object(tmp_path):
    value = state()
    value['text'] = 'large content ' * 180000
    with SafeRoot(tmp_path) as safe:
        store.write_state(safe, value, 'absent')
        assert store.read_state(safe)[0] == value
    other = tmp_path / 'small'
    other.mkdir()
    with SafeRoot(other) as safe:
        store.write_state(safe, state(), 'absent')
    assert len(list((other / managed_relative(other, store.OBJECTS.rstrip('/'))).iterdir())) == 1


def test_planning_object_quota_retains_orphans_and_control_eligibility(tmp_path, monkeypatch):
    with SafeRoot(tmp_path) as safe:
        ref = store.put_object(safe, {'reply': 'retained orphan'}, domain='planning')
        usage = store.ensure_planning_capacity(safe)
        assert usage == store.object_storage_bytes({'reply': 'retained orphan'}, 'planning')
        monkeypatch.setattr(store, 'PLANNING_LIMIT', usage)
        with pytest.raises(store.StoreError, match='planning_quota_exceeded'):
            store.put_object(safe, {'reply': 'new'}, domain='planning')
        store.write_state(safe, state(), 'absent')
        assert store.get_object(safe, ref) == {'reply': 'retained orphan'}


def test_contained_layout(tmp_path):
    (tmp_path / 'cc').mkdir()
    (tmp_path / 'cc/layout.json').write_bytes(MARKER_BYTES)
    with SafeRoot(tmp_path) as safe:
        store.write_state(safe, state(), 'absent')
        assert store.read_state(safe)[0] == state()
    assert (tmp_path / 'cc/.controlcoding/development.json').is_file()
    assert not (tmp_path / '.controlcoding').exists()


def test_hardlinked_object_rejected_preserves_foreign_bytes(tmp_path):
    with SafeRoot(tmp_path) as safe:
        ref = store.put_object(safe, {'safe': True})
    path = tmp_path / managed_relative(tmp_path, store.OBJECTS + ref + '.json')
    other = tmp_path / 'foreign.json'
    os.link(path, other)
    original = other.read_bytes()
    with SafeRoot(tmp_path) as safe:
        with pytest.raises(store.StoreError, match='unsafe_state_file'):
            store.get_object(safe, ref)
    assert other.read_bytes() == original


def test_failure_before_manifest_preserves_previous_commit(tmp_path, monkeypatch):
    with SafeRoot(tmp_path) as safe:
        revision = store.write_state(safe, state(), 'absent')
        original = safe.write_atomic
        def fail(path, value, **kwargs):
            if path == managed_relative(tmp_path, store.MANIFEST):
                raise OSError('injected disk full')
            return original(path, value, **kwargs)
        monkeypatch.setattr(safe, 'write_atomic', fail)
        changed = state()
        changed['enabled'] = True
        with pytest.raises(OSError, match='disk full'):
            store.write_state(safe, changed, revision)
        assert store.read_state(safe)[:2] == (state(), revision)


def test_planning_manifest_reserve_does_not_block_operational(tmp_path):
    metadata = {'compatibility': [{'note': 'x' * (store.MANIFEST_LIMIT - store.PLANNING_RESERVE)}]}
    with SafeRoot(tmp_path) as safe:
        with pytest.raises(store.StoreError, match='manifest_size_limit'):
            store.write_state(safe, state(), 'absent', metadata, capacity_class='planning')
        assert store.read_state(safe) == (None, 'absent', None)
        store.write_state(safe, state(), 'absent', metadata, capacity_class='operational')
        assert store.read_state(safe)[0] == state()


@pytest.mark.parametrize('ref', ['../foreign', 'f' * 63, 'F' * 64])
def test_reference_paths_rejected(tmp_path, ref):
    with SafeRoot(tmp_path) as safe:
        with pytest.raises(store.StoreError, match='invalid_object_reference'):
            store.get_object(safe, ref)
