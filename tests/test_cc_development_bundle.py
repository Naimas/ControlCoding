"""Native interrupted restore retains barrier/journal and reviewed retry."""
import json
import copy
import errno
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cc_development_archive as archive
import cc_development_sessions as sessions
import cc_development_bundle as bundle
import cc_development_lib as development
from cc_development_lib.schema import DevelopmentError
from test_cc_development_sessions import initialized, operation
from test_cc_development_core import created, artifact, intent, decide
import cc_development_history as history
import cc_evidence_inputs as inputs
import cc_development_store as store
from test_cc_development_cli import call


def checkpointed(root):
    initialized(root)
    created(root, 'routine')
    artifact(root, 'intent', intent())
    decide(root, 'intent')
    original = history.page(root, 'C1')['records']
    preview = history.checkpoint_prepare(root)
    history.checkpoint_commit(root, preview['revision'], preview['approval_id'], 'Owner')
    return original


def test_panel_checkpoint_read_preserves_current_gates_and_marks_partial_history(tmp_path):
    initialized(tmp_path)
    created(tmp_path, 'routine')
    artifact(tmp_path, 'intent', intent())
    decide(tmp_path, 'intent')
    before = call(tmp_path, 'development_read_v1')['result']['development']['changes'][0]
    preview = history.checkpoint_prepare(tmp_path)
    history.checkpoint_commit(tmp_path, preview['revision'], preview['approval_id'], 'Owner')
    manifest = (tmp_path / '.controlcoding/development.json').read_bytes()
    response = call(tmp_path, 'development_read_v1')
    assert response['status'] == 'ok'
    after = response['result']['development']['changes'][0]
    assert after['gates'] == before['gates']
    assert (after['stage'], after['status']) == (before['stage'], before['status'])
    assert after['history_retention']['partial'] is True
    assert after['history_retention']['retained_records'] == len(before['history'])
    assert after['history'] == []
    assert (tmp_path / '.controlcoding/development.json').read_bytes() == manifest


def test_multiple_checkpoints_restore_original_source_and_cold_branches(tmp_path):
    original = checkpointed(tmp_path)
    for index in range(70):
        artifact(tmp_path, 'intent', intent('Second generation ' + str(index)))
    preview = history.checkpoint_prepare(tmp_path)
    history.checkpoint_commit(tmp_path, preview['revision'], preview['approval_id'], 'Owner')
    expected = []
    cursor = None
    while True:
        page = history.page(tmp_path, 'C1', cursor=cursor)
        expected.extend(page['records'])
        cursor = page['cursor']
        if cursor is None:
            break
    assert expected[:len(original)] == original
    captured = archive.backup(tmp_path)
    (tmp_path / '.controlcoding/development.json').unlink()
    restore = archive.prepare_restore(tmp_path, captured, 'Owner', 'Restore multiple checkpoints')
    archive.restore(tmp_path, captured, 'Owner', 'Restore multiple checkpoints', restore['approval_id'])
    restored = []
    cursor = None
    while True:
        page = history.page(tmp_path, 'C1', cursor=cursor)
        restored.extend(page['records'])
        cursor = page['cursor']
        if cursor is None:
            break
    assert restored == expected


def test_disk_full_checkpoint_preserves_committed_control_and_session(tmp_path, monkeypatch):
    checkpointed(tmp_path)
    artifact(tmp_path, 'intent', intent('Pending checkpoint after disk exhaustion'))
    current = sessions.create(tmp_path, 'discussion', 'absent', 'Owner', operation('disk-session'))
    before = development.read(tmp_path)
    manifest = (tmp_path / '.controlcoding/development.json').read_bytes()
    preview = history.checkpoint_prepare(tmp_path)
    original_write = inputs.SafeRoot.write_atomic
    staged = []
    def disk_full(self, path, value, **kwargs):
        if '/objects/' in path:
            staged.append(path)
            if len(staged) == 2:
                raise OSError(errno.ENOSPC, 'Injected physical disk exhaustion')
        return original_write(self, path, value, **kwargs)
    monkeypatch.setattr(inputs.SafeRoot, 'write_atomic', disk_full)
    with pytest.raises(OSError) as observed:
        history.checkpoint_commit(tmp_path, preview['revision'], preview['approval_id'], 'Owner')
    assert observed.value.errno == errno.ENOSPC
    assert len(staged) == 2
    monkeypatch.setattr(inputs.SafeRoot, 'write_atomic', original_write)
    assert development.read(tmp_path)['revision'] == before['revision']
    assert (tmp_path / '.controlcoding/development.json').read_bytes() == manifest
    assert sessions.read(tmp_path, 'discussion')['revision'] == current['revision']
    history.checkpoint_commit(tmp_path, preview['revision'], preview['approval_id'], 'Owner')
    assert development.read(tmp_path)['changes'][0]['gates'] == before['changes'][0]['gates']
    assert sessions.read(tmp_path, 'discussion')['revision'] == current['revision']


@pytest.mark.parametrize('kind', ['retention_leaf', 'history_page', 'history_checkpoint'])
def test_detached_checkpoint_archive_requires_entire_typed_closure(tmp_path, kind):
    original = checkpointed(tmp_path)
    current = sessions.create(tmp_path, 'discussion', 'absent', 'Owner', operation('discussion'))
    captured = archive.backup(tmp_path)
    removed = next(ref for ref, value in captured['objects'].items()
                   if isinstance(value['payload'], dict) and value['payload'].get('kind') == kind)
    damaged = copy.deepcopy(captured)
    damaged['objects'].pop(removed)
    damaged['integrity'] = inputs.digest({key: value for key, value in damaged.items() if key != 'integrity'})
    # Original objects remain on disk: validation must use the detached bundle,
    # never silently repair its missing closure from the source project.
    with inputs.SafeRoot(tmp_path) as safe:
        with pytest.raises((store.StoreError, development.DevelopmentError)):
            bundle.validate(safe, damaged)
    (tmp_path / '.controlcoding/development.json').unlink()
    (tmp_path / '.controlcoding/development/sessions/discussion.json').unlink()
    preview = archive.prepare_restore(tmp_path, captured, 'Owner', 'Restore retained checkpoint')
    archive.restore(tmp_path, captured, 'Owner', 'Restore retained checkpoint', preview['approval_id'])
    assert history.page(tmp_path, 'C1')['records'] == original
    assert sessions.read(tmp_path, 'discussion')['revision'] == current['revision']
    assert (tmp_path / 'source.txt').read_bytes() == b'original source\n'


def test_native_dead_restore_requires_fresh_review_and_recovers_session(tmp_path):
    original_history = checkpointed(tmp_path)
    created = sessions.create(tmp_path, 'discussion', 'absent', 'Owner', operation('create'))
    expected = sessions.append(tmp_path, 'discussion', created['revision'], operation('turn'), 'Owner', 'user', 'Preserve the reviewed discussion.')
    captured = archive.backup(tmp_path)
    archive_path = tmp_path / 'backup.json'
    archive_path.write_text(json.dumps(captured), encoding='utf-8')
    (tmp_path / '.controlcoding/development.json').unlink()
    (tmp_path / '.controlcoding/development/sessions/discussion.json').unlink()
    preview = archive.prepare_restore(tmp_path, captured, 'Owner', 'Reviewed absent-state restoration')
    child = tmp_path / 'interrupt_restore.py'
    child.write_text("""import json,os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import cc_evidence_inputs as inputs
import cc_development_archive as archive
original=inputs.SafeRoot.write_atomic
def interrupted(self,path,value,**kwargs):
    result=original(self,path,value,**kwargs)
    if path.endswith('/sessions/discussion.json'):
        os._exit(17)
    return result
inputs.SafeRoot.write_atomic=interrupted
archive.restore(Path(sys.argv[2]),json.loads(Path(sys.argv[3]).read_text()),'Owner','Reviewed absent-state restoration',sys.argv[4])
""", encoding='utf-8')
    scripts = str(Path(__file__).resolve().parents[1] / 'scripts')
    observed = subprocess.run([sys.executable, '-B', str(child), scripts, str(tmp_path), str(archive_path), preview['approval_id']],
                              capture_output=True, timeout=30)
    assert observed.returncode == 17, observed.stderr.decode(errors='replace')
    assert (tmp_path / bundle.PENDING).exists()
    assert (tmp_path / '.controlcoding/development.lock/owner.json').exists()
    with pytest.raises(DevelopmentError, match='restore_recovery_required'):
        development.read(tmp_path)
    with pytest.raises(DevelopmentError, match='approval_conflict'):
        archive.restore(tmp_path, captured, 'Owner', 'Reviewed absent-state restoration', preview['approval_id'])
    reviewed = archive.prepare_restore(tmp_path, captured, 'Owner', 'Reviewed absent-state restoration')
    assert reviewed['approval_id'] != preview['approval_id']
    archive.restore(tmp_path, captured, 'Owner', 'Reviewed absent-state restoration', reviewed['approval_id'])
    assert not (tmp_path / bundle.PENDING).exists()
    assert not (tmp_path / '.controlcoding/development.lock').exists()
    assert sessions.read(tmp_path, 'discussion')['revision'] == expected['revision']
    assert sessions.read(tmp_path, 'discussion')['session']['turns'][0]['text'] == 'Preserve the reviewed discussion.'
    assert (tmp_path / 'source.txt').read_bytes() == b'original source\n'
    assert history.page(tmp_path, 'C1')['records'] == original_history
