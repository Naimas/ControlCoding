"""Real multi-window map reads, provenance and batch-boundary failures."""
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cc_project_map_sources as source
import cc_project_map_definition as definition
import cc_project_map_snapshots as batches
import cc_panel_bridge as bridge
import cc_setup_service as setup


def request(root):
    return dict(project_root=str(root), project_id='batch-test', observed_at='2026-09-30T12:00:00Z', design_paths=[])


def populate(root, count=40):
    for i in range(count):
        (root / f'file-{i:03}.py').write_bytes(b'value=1\n')


def tight(root, **changes):
    return replace(source.SourcePolicy(), input_count=len(root.parents) + 12, **changes)


def test_many_sources_keep_every_identity_without_raising_handle_bound(tmp_path, monkeypatch):
    populate(tmp_path, 480)
    active, peaks = set(), []
    original_open, original_close = setup._Snapshots._open, os.close
    def opened(self, path, directory):
        fd = original_open(self, path, directory)
        active.add(fd); peaks.append(len(active))
        return fd
    def closed(fd):
        active.discard(fd)
        return original_close(fd)
    monkeypatch.setattr(setup._Snapshots, '_open', opened)
    monkeypatch.setattr(os, 'close', closed)
    result = source.observe_project_map(request(tmp_path))
    assert result['projection']['summary']['units'] == 480
    assert result['projection']['summary']['verified_units'] == 0
    assert result['counts']['read_batches'] > 1
    assert result['counts']['observed_entries'] > 384
    # Missing marker entries consume reader slots but do not open descriptors.
    assert max(peaks) <= result['counts']['retained_entries'] <= 384
    assert not active
    assert result['counts']['content_bytes'] == 480 * len(b'value=1\n')
    expected = 'sha256:' + hashlib.sha256(b'value=1\n').hexdigest()
    assert all(s['identity'] == expected for s in result['projection']['bundle']['sources'])
    raw = json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode()
    assert 768 * 1024 < len(raw) < 4 * 1024 * 1024 and b'value=1' not in raw
    assert result == source.observe_project_map(request(tmp_path))


@pytest.mark.parametrize('mutation', ['content', 'replacement', 'deletion', 'membership'])
def test_changed_early_batch_is_rejected_before_publication(tmp_path, monkeypatch, mutation):
    populate(tmp_path)
    original = source._Inventory.recheck
    first = tmp_path / 'file-000.py'
    def changed(self):
        assert self.reader.windows > 1
        if mutation == 'content':
            before = first.stat(); first.write_bytes(b'value=2\n')
            os.utime(first, ns=(before.st_atime_ns, before.st_mtime_ns))
        elif mutation == 'replacement':
            first.unlink(); first.write_bytes(b'value=1\n')
        elif mutation == 'deletion':
            first.unlink()
        else:
            (tmp_path / 'late.py').write_bytes(b'value=1\n')
        return original(self)
    monkeypatch.setattr(source._Inventory, 'recheck', changed)
    with pytest.raises(source.MapSourceError, match='changed_input'):
        source.observe_project_map(request(tmp_path), policy=tight(tmp_path))
    (tmp_path / 'file-039.py').write_bytes(b'value=9\n')


def test_layout_switch_between_windows_fails_closed(tmp_path, monkeypatch):
    populate(tmp_path)
    close = batches.MapSnapshotBatches._close
    changed = []
    def switching(self, validate=True):
        close(self, validate)
        if not changed:
            changed.append(True)
            (tmp_path / 'cc').mkdir()
            (tmp_path / 'cc/layout.json').write_text('{"schema":1,"layout":"contained"}\n')
    monkeypatch.setattr(batches.MapSnapshotBatches, '_close', switching)
    with pytest.raises(source.MapSourceError, match='changed_input'):
        source.observe_project_map(request(tmp_path), policy=tight(tmp_path))


def test_released_file_replaced_with_hardlink_is_not_read(tmp_path, monkeypatch):
    populate(tmp_path)
    outside = tmp_path.parent / 'outside-batch.py'; outside.write_bytes(b'PRIVATE_OUTSIDE')
    alias = tmp_path.parent / 'outside-batch-alias.py'; os.link(outside, alias)
    first = tmp_path / 'file-000.py'; close = batches.MapSnapshotBatches._close
    changed = []
    def swapped(self, validate=True):
        close(self, validate)
        if not changed:
            changed.append(True)
            first.unlink(); alias.replace(first)
    monkeypatch.setattr(batches.MapSnapshotBatches, '_close', swapped)
    with pytest.raises(source.MapSourceError) as caught:
        source.observe_project_map(request(tmp_path), policy=tight(tmp_path))
    assert caught.value.code == 'unsupported_path', repr(caught.value.__context__)


def test_aggregate_content_budget_is_global_across_windows(tmp_path):
    populate(tmp_path)
    with pytest.raises(source.MapSourceError, match='scope_limit'):
        source.observe_project_map(request(tmp_path), policy=tight(tmp_path, target_bytes=200))
    assert not (tmp_path / '.controlcoding').exists()
    for p in tmp_path.glob('*.py'):
        p.write_bytes(b'pass\n')


def test_batch_rotation_does_not_reset_time_budget(tmp_path, monkeypatch):
    populate(tmp_path)
    initial = source.time.monotonic()
    clock = [initial]
    monkeypatch.setattr(source.time, 'monotonic', lambda: clock[0])
    close = batches.MapSnapshotBatches._close
    def elapsed(self, validate=True):
        close(self, validate)
        clock[0] = initial + 11
    monkeypatch.setattr(batches.MapSnapshotBatches, '_close', elapsed)
    with pytest.raises(source.MapSourceError, match='time_limit'):
        source.observe_project_map(request(tmp_path), policy=tight(tmp_path))


def test_batched_mapping_read_does_not_authorize_an_unpinned_write(tmp_path):
    populate(tmp_path, 400)
    req = request(tmp_path); preview = source.preview_project_map_scope(req)
    observed = definition.observe_reviewed(req, preview['preview_id'])
    assert observed['review']['write_supported'] is False
    assert observed['review']['write_reason'] == 'mapping_write_scope_limit'
    target = next(n['id'] for n in observed['projection']['bundle']['nodes'] if n['kind'] == 'file')
    change = dict(operation='rename', target=target, title='Reviewed name')
    args = (req, preview['preview_id'], observed['review']['source_snapshot'], 'absent', change)
    prepared, _, _ = definition.preview_change(*args)
    assert prepared['write_supported'] is False
    if os.name == 'nt':
        with pytest.raises(definition.DefinitionError, match='mapping_write_scope_limit'):
            definition.commit_change(*args, prepared['approval_id'])
        assert not (tmp_path / '.controlcoding').exists()


@pytest.mark.parametrize('operation,limit', [('read', bridge.MAX_RESPONSE), ('map_read_v1', bridge.MAX_MAP_RESPONSE)])
def test_bridge_output_limits_remain_operation_specific(monkeypatch, operation, limit):
    monkeypatch.setattr(bridge, 'dispatch', lambda *args: dict(operation=operation, text='x' * (limit + 1)))
    output = io.BytesIO(); bridge.serve(io.BytesIO(b'{}'), output)
    assert json.loads(output.getvalue())['error']['code'] == 'response_limit'
    monkeypatch.setattr(bridge, 'dispatch', lambda *args: dict(operation=operation, text='x' * (bridge.MAX_RESPONSE + 1)))
    output = io.BytesIO(); bridge.serve(io.BytesIO(b'{}'), output)
    assert ('error' in json.loads(output.getvalue())) == (operation == 'read')
