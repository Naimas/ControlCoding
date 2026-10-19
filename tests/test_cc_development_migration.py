"""Real retained-runtime fixtures and storage migration failure controls."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cc_development_migration as migration
import cc_development_store as store
from cc_development_compatibility import PRIOR
from cc_development_lib import storage
from cc_development_lib.method import IDENTITY
from cc_evidence_inputs import SafeRoot, EvidenceError


@pytest.fixture
def prior_project(tmp_path):
    runtime = os.environ.get('CC_TEST_PRIOR_RUNTIME')
    if not runtime:
        pytest.skip('Exact retained CD10 runtime supplied through CC_TEST_PRIOR_RUNTIME is required')
    return generate_prior_project(tmp_path, runtime, PRIOR)


def generate_prior_project(tmp_path, runtime, identity):
    script = '''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import cc_development_lib as d
from cc_development_lib.method import IDENTITY
assert IDENTITY == json.loads(sys.argv[3])
root=Path(sys.argv[2])
def act(a):
    before=d.read(root); p=d.prepare(root,a,before['revision'])
    assert not p['blockers'], p['blockers']
    return d.commit(root,a,before['revision'],p['approval_id'])
act({'kind':'activate','profile':'routine'})
act({'kind':'create','change_id':'C1','title':'Original feature','owner':'Owner','risk':'routine'})
act({'kind':'artifact','change_id':'C1','stage':'intent','actor':'Writer', 'content':{
    'problem':'A retained problem','outcome':'A measurable outcome',
    'affected_users_and_systems':['User'],'constraints':[],'non_goals':[],
    'open_questions':[],'success_measures':['Useful behavior']}})
act({'kind':'decide','change_id':'C1','stage':'intent','decision':'accepted','actor':'Owner','reason':'Reviewed exact original intent'})
iv=d.read(root)['changes'][0]['artifacts']['intent'][-1]['revision']
act({'kind':'artifact','change_id':'C1','stage':'design','actor':'Writer','content':{
    'upstream_intent_revision':iv,'requirements':[{'id':'R1','text':'Useful behavior'}],
    'criteria':[{'id':'K1','requirement_id':'R1','text':'Behavior check','required':True,
       'proof':'automated','checks':[{'kind':'verification','id':'test'}]}],
    'decisions':[],'risks':[],'policy_concerns':[],'source_references':[]}})
act({'kind':'decide','change_id':'C1','stage':'design','decision':'accepted','actor':'Owner','reason':'Reviewed specification'})
dv=d.read(root)['changes'][0]['artifacts']['design'][-1]['revision']
(root/'source.py').write_text('VALUE = 1\\n',encoding='utf-8')
act({'kind':'artifact','change_id':'C1','stage':'plan','actor':'Writer','content':{
    'upstream_specification_revision':dv,'files':['source.py'],'ownership_scopes':['project'],
    'tasks':[{'id':'T1','title':'Implement','owner':'Writer','depends_on':[]}],
    'dependencies':[],'criterion_check_mapping':{'K1':['test']},
    'verification_strategy':'Required check','rollback_assessment':'Revert candidate'}})
act({'kind':'decide','change_id':'C1','stage':'plan','decision':'accepted','actor':'Owner','reason':'Reviewed plan'})
view=act({'kind':'candidate','change_id':'C1','files':['source.py'],'actor':'Writer'})
change=view['view']['changes'][0]
act({'kind':'artifact','change_id':'C1','stage':'build','actor':'Writer','content':{
    'approved_plan_revision':change['artifacts']['plan'][-1]['revision'],
    'source_identity':change['candidate']['digest'],
    'execution_provenance':{'mode':'manual','description':'Real retained local source'},
    'deviations':[],'changed_files':['source.py']}})
act({'kind':'decide','change_id':'C1','stage':'build','decision':'accepted','actor':'Owner','reason':'Reviewed original build'})
'''
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
    subprocess.run([sys.executable, '-B', '-c', script, runtime, str(tmp_path), json.dumps(identity)],
                   check=True, env=env, capture_output=True, text=True)
    return tmp_path


def originals(root):
    raw = (root / '.controlcoding/development.json').read_bytes()
    return raw, json.loads(raw)


def test_real_prior_approved_intent_exact_archive_and_readonly_replay(prior_project):
    raw, original = originals(prior_project)
    before = {p.relative_to(prior_project): p.read_bytes() for p in prior_project.rglob('*') if p.is_file()}
    plan = migration.preview(prior_project)
    assert not plan['blockers']
    assert before == {p.relative_to(prior_project): p.read_bytes() for p in prior_project.rglob('*') if p.is_file()}
    result = migration.migrate(prior_project, plan['expected_revision'], plan['approval_id'], 'Owner')
    with SafeRoot(prior_project) as root:
        current, revision, manifest = store.read_state(root)
        assert store.read_archive_bytes(root, result['archive']['archive_ref']) == raw
        expected = copy.deepcopy(original)
        expected['method'] = IDENTITY
        assert current == expected
        assert current['changes'][0]['method'] == PRIOR
        assert current['changes'][0]['decisions'] == original['changes'][0]['decisions']
        storage.validate_state(root, current, compatibility=manifest['compatibility'])
        from cc_development_lib.evaluator import accepted
        assert accepted(current['changes'][0], 'intent')
        assert accepted(current['changes'][0], 'design') and accepted(current['changes'][0], 'plan')
        from cc_development_adapters import manual_handoff
        assert manual_handoff(prior_project, current['changes'][0])['planDigest'] == original['changes'][0]['artifacts']['plan'][-1]['digest']
    after = {p.relative_to(prior_project): p.read_bytes() for p in prior_project.rglob('*') if p.is_file()}
    replay = migration.migrate(prior_project, plan['expected_revision'], plan['approval_id'], 'Owner')
    assert replay['replayed'] and not replay['migrated'] and replay['revision'] == revision
    assert after == {p.relative_to(prior_project): p.read_bytes() for p in prior_project.rglob('*') if p.is_file()}


def test_new_mutation_preserves_original_history_and_approval(prior_project):
    import cc_development_lib as development
    from cc_development_lib.evaluator import accepted
    plan = migration.preview(prior_project)
    migration.migrate(prior_project, plan['expected_revision'], plan['approval_id'], 'Owner')
    with SafeRoot(prior_project) as root:
        original, _, _ = store.read_state(root)
    action = {'kind': 'create', 'change_id': 'C2', 'title': 'New feature', 'owner': 'Owner', 'risk': 'routine'}
    view = development.read(prior_project)
    prepared = development.prepare(prior_project, action, view['revision'])
    assert not prepared['blockers']
    development.commit(prior_project, action, view['revision'], prepared['approval_id'])
    with SafeRoot(prior_project) as root:
        updated, _, manifest = store.read_state(root)
        assert updated['changes'][0] == original['changes'][0]
        assert updated['changes'][1]['method'] == IDENTITY
        assert accepted(updated['changes'][0], 'intent')
        assert len(manifest['archives']) == 1


def test_execution_retained_change_must_match_validated_canonical_owner(prior_project):
    import cc_development_execution as execution
    plan = migration.preview(prior_project)
    migration.migrate(prior_project, plan['expected_revision'], plan['approval_id'], 'Owner')
    with SafeRoot(prior_project) as root:
        current, _, _ = store.read_state(root)
    change = current['changes'][0]
    execution._canonical_change(prior_project, change)
    forged = copy.deepcopy(change)
    forged['method'] = dict(IDENTITY)
    with pytest.raises(EvidenceError, match='execution_method_changed'):
        execution._canonical_change(prior_project, forged)
    forged = copy.deepcopy(change)
    forged['decisions'][-1]['reason'] = 'Forged accepted decision'
    with pytest.raises(EvidenceError, match='execution_method_changed'):
        execution._canonical_change(prior_project, forged)


def test_old_build_acceptance_is_history_and_fresh_current_decision_is_required(prior_project):
    import cc_development_lib as development
    from cc_development_lib.evaluator import accepted
    from test_cc_development_core import decide
    raw, original = originals(prior_project)
    assert original['changes'][0]['decisions'][-1]['stage'] == 'build'
    plan = migration.preview(prior_project)
    migration.migrate(prior_project, plan['expected_revision'], plan['approval_id'], 'Owner')
    with SafeRoot(prior_project) as root:
        migrated, _, _ = store.read_state(root)
    assert not accepted(migrated['changes'][0], 'build')
    decide(prior_project, 'build', 'CurrentOwner')
    with SafeRoot(prior_project) as root:
        current, _, manifest = store.read_state(root)
        assert accepted(current['changes'][0], 'build')
        assert current['changes'][0]['decisions'][-1]['method'] == IDENTITY
        assert current['changes'][0]['history'][:len(original['changes'][0]['history'])] == original['changes'][0]['history']
        assert store.read_archive_bytes(root, manifest['archives'][0]['archive_ref']) == raw


def test_metadata_cannot_drop_original_change_or_operation(prior_project):
    plan = migration.preview(prior_project)
    migration.migrate(prior_project, plan['expected_revision'], plan['approval_id'], 'Owner')
    with SafeRoot(prior_project) as root:
        current, _, manifest = store.read_state(root)
        lost_change = copy.deepcopy(current)
        lost_change['changes'] = []
        with pytest.raises(storage.DevelopmentError, match='migration_history_changed'):
            migration.validate_metadata(root, lost_change, manifest)
        lost_operation = copy.deepcopy(current)
        lost_operation['operations'] = lost_operation['operations'][1:]
        with pytest.raises(storage.DevelopmentError, match='migration_history_changed'):
            migration.validate_metadata(root, lost_operation, manifest)


def test_source_drift_and_wrong_approval_preserve_original(prior_project):
    plan = migration.preview(prior_project)
    raw, _ = originals(prior_project)
    with pytest.raises(storage.DevelopmentError, match='preview_mismatch'):
        migration.migrate(prior_project, plan['expected_revision'], 'a' * 64, 'Owner')
    assert originals(prior_project)[0] == raw
    path = prior_project / '.controlcoding/development.json'
    path.write_bytes(raw + b'\n')
    with pytest.raises(storage.DevelopmentError, match='revision_conflict'):
        migration.migrate(prior_project, plan['expected_revision'], plan['approval_id'], 'Owner')
    assert path.read_bytes() == raw + b'\n'


def test_active_receipt_and_receipt_drift_block_publication(prior_project):
    plan = migration.preview(prior_project)
    raw, _ = originals(prior_project)
    receipts = prior_project / '.controlcoding/development_job_receipts'
    receipts.mkdir()
    path = receipts / 'test.json'
    path.write_text(json.dumps({'state': 'running', 'kind': 'monitor'}), encoding='utf-8')
    with pytest.raises(storage.DevelopmentError, match='preview_mismatch'):
        migration.migrate(prior_project, plan['expected_revision'], plan['approval_id'], 'Owner')
    blocked = migration.preview(prior_project)
    assert blocked['blockers']
    with pytest.raises(storage.DevelopmentError, match='migration_active_effects'):
        migration.migrate(prior_project, blocked['expected_revision'], blocked['approval_id'], 'Owner')
    assert originals(prior_project)[0] == raw


def test_corrupt_history_cannot_be_archived_as_valid(prior_project):
    path = prior_project / '.controlcoding/development.json'
    value = json.loads(path.read_bytes())
    value['changes'][0]['history'][-1]['result']['decision']['reason'] = 'Forged approval'
    path.write_text(json.dumps(value), encoding='utf-8')
    raw = path.read_bytes()
    with pytest.raises(storage.DevelopmentError):
        migration.preview(prior_project)
    assert path.read_bytes() == raw
    assert not (prior_project / '.controlcoding/development/objects').exists()


def test_object_staging_failure_keeps_original_and_retry_works(prior_project, monkeypatch):
    plan = migration.preview(prior_project)
    raw, _ = originals(prior_project)
    real = store.write_state
    def fail(*args, **kwargs):
        raise OSError('Injected failure after exact archive staging')
    monkeypatch.setattr(store, 'write_state', fail)
    with pytest.raises(OSError):
        migration.migrate(prior_project, plan['expected_revision'], plan['approval_id'], 'Owner')
    assert originals(prior_project)[0] == raw
    monkeypatch.setattr(store, 'write_state', real)
    assert migration.migrate(prior_project, plan['expected_revision'], plan['approval_id'], 'Owner')['migrated']
