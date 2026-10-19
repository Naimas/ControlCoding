"""Real source gateway IO; the separate engine tests its canonical receipt owner."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cc_development_gateway as gateway
import cc_development_execution as execution
import cc_development_lib as development
from cc_development_adapters import manual_handoff
from cc_development_lib.method import digest
from cc_evidence_inputs import EvidenceError
from test_cc_development_core import planned, act, artifact, decide

EXECUTION_ID = 'execution_' + 'a' * 32


def setup(root, monkeypatch, files=None):
    (root / 'source.py').write_bytes(b'before\n')
    planned(root)
    change = development.read(root)['changes'][0]
    if files:
        content = copy.deepcopy(change['artifacts']['plan'][-1]['content'])
        content['files'] = files
        content['ownership_scopes'] = [{'owner': 'Writer', 'files': files}]
        artifact(root, 'plan', content)
        decide(root, 'plan')
        change = development.read(root)['changes'][0]
    handoff = manual_handoff(root, change)
    receipt = {'schemaVersion': 1, 'id': EXECUTION_ID, 'state': 'allowed',
               'plan': {'handoff': handoff}, 'proposal': {'model': 'local-test-worker',
                   'usage': {'inputTokens': 1, 'outputTokens': 1},
                   'files': [{'path': p, 'content': 'after\n'} for p in (files or ['source.py'])]}}
    def read(project, receipt_id, current_change, *, current):
        assert Path(project) == root and receipt_id == EXECUTION_ID and current is False
        assert current_change['id'] == change['id']
        return copy.deepcopy(receipt)
    monkeypatch.setattr(execution, 'read_receipt', read)
    return change, receipt


def test_preview_no_writes_and_exact_diff_apply_candidate(tmp_path, monkeypatch):
    change, _ = setup(tmp_path, monkeypatch, ['source.py', 'new.py'])
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob('*'))
    preview = gateway.prepare(tmp_path, change, EXECUTION_ID)
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob('*')) == before
    assert '+after' in preview['files'][0]['diff']
    assert next(f for f in preview['files'] if f['path'] == 'new.py')['operation'] == 'add'
    result = gateway.apply(tmp_path, change, EXECUTION_ID, preview['approval_id'])
    assert result['state'] == 'applied' and not result['idempotent']
    assert (tmp_path / 'source.py').read_bytes() == (tmp_path / 'new.py').read_bytes() == b'after\n'
    assert not (tmp_path / '.controlcoding/development_apply.lock').exists()
    candidate = act(tmp_path, {'kind': 'candidate', 'change_id': 'C1', 'files': ['source.py', 'new.py'], 'actor': 'Writer'})['changes'][0]
    assessment = gateway.assess_applied(tmp_path, candidate, EXECUTION_ID, EXECUTION_ID)
    assert assessment['state'] == 'allowed', assessment
    assert assessment['sourceIdentity'] == candidate['candidate']['digest']
    assert gateway.apply(tmp_path, candidate, EXECUTION_ID, preview['approval_id'])['idempotent']
    (tmp_path / 'new.py').write_bytes(b'foreign\n')
    assert gateway.assess_applied(tmp_path, candidate, EXECUTION_ID)['state'] == 'blocked'
    with pytest.raises(EvidenceError, match='execution_applied_not_current'):
        gateway.apply(tmp_path, candidate, EXECUTION_ID, preview['approval_id'])


def test_stale_preview_writes_nothing(tmp_path, monkeypatch):
    change, _ = setup(tmp_path, monkeypatch)
    preview = gateway.prepare(tmp_path, change, EXECUTION_ID)
    (tmp_path / 'source.py').write_bytes(b'foreign\n')
    with pytest.raises(EvidenceError, match='execution_source_conflict'):
        gateway.apply(tmp_path, change, EXECUTION_ID, preview['approval_id'])
    assert (tmp_path / 'source.py').read_bytes() == b'foreign\n'
    assert not (tmp_path / '.controlcoding/development_apply_receipts').exists()


def test_exact_approval_and_changed_plan(tmp_path, monkeypatch):
    change, _ = setup(tmp_path, monkeypatch)
    preview = gateway.prepare(tmp_path, change, EXECUTION_ID)
    with pytest.raises(EvidenceError, match='execution_apply_approval_conflict'):
        gateway.apply(tmp_path, change, EXECUTION_ID, '0' * 64)
    content = copy.deepcopy(change['artifacts']['plan'][-1]['content'])
    content['verification_strategy'] = 'A changed strategy'
    artifact(tmp_path, 'plan', content)
    decide(tmp_path, 'plan')
    with pytest.raises(EvidenceError, match='execution_plan_stale'):
        gateway.apply(tmp_path, change, EXECUTION_ID, preview['approval_id'])
    assert (tmp_path / 'source.py').read_bytes() == b'before\n'


def test_multi_file_partial_retains_intents_and_reviewed_resume(tmp_path, monkeypatch):
    change, _ = setup(tmp_path, monkeypatch, ['source.py', 'z.py'])
    preview = gateway.prepare(tmp_path, change, EXECUTION_ID)
    original = gateway._write_bytes
    def fail_second(reader, row):
        if row['path'] == 'z.py':
            raise OSError('disk unavailable')
        return original(reader, row)
    monkeypatch.setattr(gateway, '_write_bytes', fail_second)
    with pytest.raises(OSError):
        gateway.apply(tmp_path, change, EXECUTION_ID, preview['approval_id'])
    assert (tmp_path / 'source.py').read_bytes() == b'after\n' and not (tmp_path / 'z.py').exists()
    retained = json.loads((tmp_path / '.controlcoding/development_apply_receipts' / (EXECUTION_ID + '.json')).read_text())
    assert retained['state'] == 'partial'
    assert any(h['kind'] == 'write_intent' and h['path'] == 'z.py' for h in retained['history'])
    assert gateway.assess_applied(tmp_path, change, EXECUTION_ID)['state'] == 'blocked'
    resume = gateway.prepare(tmp_path, change, EXECUTION_ID)
    assert resume['recovery'] and resume['approval_id'] != preview['approval_id']
    with pytest.raises(EvidenceError, match='execution_apply_approval_conflict'):
        gateway.apply(tmp_path, change, EXECUTION_ID, preview['approval_id'])
    monkeypatch.setattr(gateway, '_write_bytes', original)
    assert gateway.apply(tmp_path, change, EXECUTION_ID, resume['approval_id'])['state'] == 'applied'
    assert (tmp_path / 'z.py').read_bytes() == b'after\n'


def test_partial_foreign_conflict_never_overwritten(tmp_path, monkeypatch):
    change, _ = setup(tmp_path, monkeypatch, ['source.py', 'z.py'])
    preview = gateway.prepare(tmp_path, change, EXECUTION_ID)
    original = gateway._write_bytes
    def conflict_second(reader, row):
        if row['path'] == 'z.py':
            (tmp_path / 'z.py').write_bytes(b'foreign\n')
        return original(reader, row)
    monkeypatch.setattr(gateway, '_write_bytes', conflict_second)
    with pytest.raises(EvidenceError, match='execution_source_conflict'):
        gateway.apply(tmp_path, change, EXECUTION_ID, preview['approval_id'])
    assert (tmp_path / 'z.py').read_bytes() == b'foreign\n'
    with pytest.raises(EvidenceError, match='execution_source_conflict'):
        gateway.prepare(tmp_path, change, EXECUTION_ID)


@pytest.mark.parametrize('path', ['.git/config', '.controlcoding/a', 'cc/.controlcoding/a', '.codex/a',
    '.agents/a', '.claude/settings.json', 'scripts/hooks/a.py', 'AGENTS.md', 'CONTROLCODING.md',
    'cc_config.json', 'cc/controlcoding.verification.json', '../outside.py'])
def test_protected_scope(path):
    with pytest.raises(EvidenceError):
        gateway.validate_scope([path])


def test_proposal_outside_plan_and_duplicate_paths(tmp_path, monkeypatch):
    change, receipt = setup(tmp_path, monkeypatch)
    receipt['proposal']['files'].append({'path': 'unplanned.py', 'content': 'x'})
    with pytest.raises(EvidenceError, match='execution_proposal_outside_plan'):
        gateway.prepare(tmp_path, change, EXECUTION_ID)
    receipt['proposal']['files'][-1]['path'] = 'SOURCE.PY'
    with pytest.raises(EvidenceError, match='execution_scope_duplicate'):
        gateway.prepare(tmp_path, change, EXECUTION_ID)


def test_configured_deny_and_warn_snapshot(tmp_path, monkeypatch):
    change, _ = setup(tmp_path, monkeypatch)
    config = tmp_path / 'cc_config.json'
    config.write_text(json.dumps({'protected_zones': [{'path': 'source.py', 'level': 'warn'}]}))
    preview = gateway.prepare(tmp_path, change, EXECUTION_ID)
    assert preview['zones']['warnings'][0]['file'] == 'source.py'
    config.write_text(json.dumps({'protected_zones': [{'path': 'source.py', 'level': 'deny'}]}))
    with pytest.raises(EvidenceError, match='execution_configured_protected_path'):
        gateway.apply(tmp_path, change, EXECUTION_ID, preview['approval_id'])
    assert (tmp_path / 'source.py').read_bytes() == b'before\n'


def test_source_hardlink_blocked(tmp_path, monkeypatch):
    change, _ = setup(tmp_path, monkeypatch)
    os.link(tmp_path / 'source.py', tmp_path / 'linked.py')
    with pytest.raises(EvidenceError, match='execution_source_hardlink'):
        gateway.prepare(tmp_path, change, EXECUTION_ID)


def test_running_and_unknown_owner_cannot_recover(tmp_path, monkeypatch):
    change, _ = setup(tmp_path, monkeypatch)
    lock = tmp_path / '.controlcoding/development_apply.lock'
    lock.mkdir()
    from cc_development_jobs import _project_identity
    (lock / 'owner.json').write_text(json.dumps({'pid': os.getpid(), 'projectIdentity': _project_identity(tmp_path),
                                                'receiptId': EXECUTION_ID}))
    with pytest.raises(EvidenceError, match='execution_apply_lock_active'):
        gateway.prepare(tmp_path, change, EXECUTION_ID)
    monkeypatch.setattr(gateway, 'process_state', lambda pid: 'unknown')
    with pytest.raises(EvidenceError, match='execution_apply_lock_unknown'):
        gateway.prepare(tmp_path, change, EXECUTION_ID)
    assert lock.exists()


def test_unmodified_baseline_is_part_of_build_proof(tmp_path, monkeypatch):
    (tmp_path / 'other.py').write_bytes(b'unchanged\n')
    change, receipt = setup(tmp_path, monkeypatch, ['source.py', 'other.py'])
    receipt['proposal']['files'] = [{'path': 'source.py', 'content': 'after\n'}]
    preview = gateway.prepare(tmp_path, change, EXECUTION_ID)
    gateway.apply(tmp_path, change, EXECUTION_ID, preview['approval_id'])
    candidate = act(tmp_path, {'kind': 'candidate', 'change_id': 'C1', 'files': ['source.py', 'other.py'], 'actor': 'Writer'})['changes'][0]
    assert gateway.assess_applied(tmp_path, candidate, EXECUTION_ID)['state'] == 'allowed'
    (tmp_path / 'other.py').write_bytes(b'foreign\n')
    assert 'execution_baseline_source_stale' in gateway.assess_applied(tmp_path, candidate, EXECUTION_ID)['reasons']


def test_apply_receipt_tampering_and_wrong_apply_id(tmp_path, monkeypatch):
    change, _ = setup(tmp_path, monkeypatch)
    preview = gateway.prepare(tmp_path, change, EXECUTION_ID)
    gateway.apply(tmp_path, change, EXECUTION_ID, preview['approval_id'])
    assert gateway.assess_applied(tmp_path, change, EXECUTION_ID, 'execution_' + 'b' * 32)['state'] == 'blocked'
    path = tmp_path / '.controlcoding/development_apply_receipts' / (EXECUTION_ID + '.json')
    receipt = json.loads(path.read_text())
    receipt['files'][0]['after_sha256'] = '0' * 64
    path.write_text(json.dumps(receipt))
    assert 'execution_apply_receipt_changed' in gateway.assess_applied(tmp_path, change, EXECUTION_ID)['reasons']


def test_actual_abandoned_process_needs_reviewed_recovery(tmp_path, monkeypatch):
    change, receipt = setup(tmp_path, monkeypatch, ['source.py', 'z.py'])
    preview = gateway.prepare(tmp_path, change, EXECUTION_ID)
    snapshot = tmp_path / 'engine-test-receipt.json'
    snapshot.write_text(json.dumps(receipt))
    program = """
import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import cc_development_gateway as gateway
import cc_development_execution as engine
root = Path(sys.argv[2])
engine.read_receipt = lambda *args, **kwargs: json.loads((root/'engine-test-receipt.json').read_text())
original = gateway._write_bytes
def interrupted(reader, row):
    if row['path'] == 'z.py':
        os._exit(17)
    original(reader,row)
gateway._write_bytes = interrupted
gateway.apply(root, {'id':'C1'}, sys.argv[3], sys.argv[4])
"""
    process = subprocess.run([sys.executable, '-B', '-c', program,
        str(Path(__file__).resolve().parents[1] / 'scripts'), str(tmp_path), EXECUTION_ID, preview['approval_id']],
        capture_output=True, timeout=30)
    assert process.returncode == 17, process.stderr.decode(errors='replace')
    assert (tmp_path / '.controlcoding/development_apply.lock').is_dir()
    assert (tmp_path / '.controlcoding/development.lock').is_dir()
    assert (tmp_path / 'source.py').read_bytes() == b'after\n'
    with pytest.raises(development.DevelopmentError, match='recovery_required'):
        gateway.prepare(tmp_path, change, EXECUTION_ID)
    core_recovery = development.recovery_prepare(tmp_path, 'Owner', 'Recover observed terminated source promotion')
    assert not core_recovery['blockers']
    development.commit(tmp_path, core_recovery['action'], core_recovery['revision'], core_recovery['approval_id'])
    recovery = gateway.prepare(tmp_path, change, EXECUTION_ID)
    assert recovery['recovery'] and recovery['recoveryLock']
    with pytest.raises(EvidenceError, match='execution_apply_approval_conflict'):
        gateway.apply(tmp_path, change, EXECUTION_ID, preview['approval_id'])
    assert gateway.apply(tmp_path, change, EXECUTION_ID, recovery['approval_id'])['state'] == 'applied'
    assert not (tmp_path / '.controlcoding/development_apply.lock').exists()


def test_unmodified_baseline_drift_blocks_before_any_write(tmp_path, monkeypatch):
    (tmp_path / 'other.py').write_bytes(b'baseline\n')
    change, receipt = setup(tmp_path, monkeypatch, ['source.py', 'other.py'])
    receipt['proposal']['files'] = [{'path': 'source.py', 'content': 'after\n'}]
    preview = gateway.prepare(tmp_path, change, EXECUTION_ID)
    (tmp_path / 'other.py').write_bytes(b'foreign\n')
    with pytest.raises(EvidenceError, match='execution_baseline_source_stale'):
        gateway.apply(tmp_path, change, EXECUTION_ID, preview['approval_id'])
    assert (tmp_path / 'source.py').read_bytes() == b'before\n'


def test_core_plan_edit_contender_is_blocked_during_source_apply(tmp_path, monkeypatch):
    change, _ = setup(tmp_path, monkeypatch)
    preview = gateway.prepare(tmp_path, change, EXECUTION_ID)
    original = gateway._zones
    attempted = []
    def concurrent_core_edit(reader, paths):
        result = original(reader, paths)
        content = copy.deepcopy(change['artifacts']['plan'][-1]['content'])
        content['verification_strategy'] = 'Changed by a concurrent Core writer'
        with pytest.raises(development.DevelopmentError, match='recovery_required'):
            artifact(tmp_path, 'plan', content)
        attempted.append(True)
        return result
    monkeypatch.setattr(gateway, '_zones', concurrent_core_edit)
    assert gateway.apply(tmp_path, change, EXECUTION_ID, preview['approval_id'])['state'] == 'applied'
    assert attempted
    current = development.read(tmp_path)['changes'][0]
    assert current['artifacts']['plan'][-1]['digest'] == change['artifacts']['plan'][-1]['digest']
    assert not (tmp_path / '.controlcoding/development.lock').exists()


@pytest.mark.parametrize('level,reason', [('deny', 'execution_configured_protected_path'),
                                        ('warn', 'execution_apply_config_changed')])
def test_post_apply_policy_drift_blocks_proof_and_replay_exact_restore(tmp_path, monkeypatch, level, reason):
    change, _ = setup(tmp_path, monkeypatch)
    config = tmp_path / 'cc_config.json'
    original = json.dumps({'protected_zones': []}).encode()
    config.write_bytes(original)
    preview = gateway.prepare(tmp_path, change, EXECUTION_ID)
    gateway.apply(tmp_path, change, EXECUTION_ID, preview['approval_id'])
    candidate = act(tmp_path, {'kind': 'candidate', 'change_id': 'C1', 'files': ['source.py'], 'actor': 'Writer'})['changes'][0]
    assert gateway.assess_applied(tmp_path, candidate, EXECUTION_ID)['state'] == 'allowed'
    config.write_text(json.dumps({'protected_zones': [{'path': 'source.py', 'level': level}]}))
    assert reason in gateway.assess_applied(tmp_path, candidate, EXECUTION_ID)['reasons']
    with pytest.raises(EvidenceError, match=reason):
        gateway.apply(tmp_path, candidate, EXECUTION_ID, preview['approval_id'])
    assert (tmp_path / 'source.py').read_bytes() == b'after\n'
    config.write_bytes(original)
    assert gateway.assess_applied(tmp_path, candidate, EXECUTION_ID)['state'] == 'allowed'
    assert gateway.apply(tmp_path, candidate, EXECUTION_ID, preview['approval_id'])['idempotent']
