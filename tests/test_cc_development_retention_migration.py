"""Genuine frozen PW00 predecessor upgrade and retained checkpoint lineage."""
import copy
import json
import os
import subprocess
import sys

import pytest

from test_cc_development_migration import generate_prior_project, prior_project
import cc_development_migration as migration
import cc_development_store as store
import cc_development_history as history
from cc_development_compatibility import PRIOR, RETAINED, RETENTION, PRODUCT, DISCUSSION, EVOLUTION, PLANNING
from cc_development_lib.method import IDENTITY
from cc_development_lib import storage
from cc_development_lib.evaluator import accepted
from cc_evidence_inputs import SafeRoot


@pytest.fixture
def retained_project(tmp_path):
    runtime = os.environ.get('CC_TEST_PW00_RUNTIME')
    if not runtime:
        pytest.skip('Exact frozen PW00 runtime supplied through CC_TEST_PW00_RUNTIME is required')
    return generate_prior_project(tmp_path, runtime, RETAINED)


def test_reviewed_pw00_upgrade_preserves_objects_decisions_and_exact_manifest(retained_project):
    raw = (retained_project / '.controlcoding/development.json').read_bytes()
    before = {p.relative_to(retained_project): p.read_bytes() for p in retained_project.rglob('*') if p.is_file()}
    with SafeRoot(retained_project) as safe:
        original, _, old_manifest = store.read_state(safe)
    plan = migration.preview(retained_project)
    assert plan['source_method'] == RETAINED and plan['target_method'] == IDENTITY
    assert 'Later approvals require current-method review' in plan['decision_applicability']
    assert before == {p.relative_to(retained_project): p.read_bytes() for p in retained_project.rglob('*') if p.is_file()}
    result = migration.migrate(retained_project, plan['expected_revision'], plan['approval_id'], 'Owner')
    with SafeRoot(retained_project) as safe:
        upgraded, _, manifest = store.read_state(safe)
        expected = copy.deepcopy(original)
        expected['method'] = IDENTITY
        assert upgraded == expected
        assert store.read_archive_bytes(safe, result['archive']['archive_ref']) == raw
        assert manifest['archives'][:-1] == old_manifest['archives']
        assert accepted(upgraded['changes'][0], 'plan')
        assert not accepted(upgraded['changes'][0], 'build')
    for path, data in before.items():
        if path.as_posix() != '.controlcoding/development.json':
            assert (retained_project / path).read_bytes() == data


def test_repeated_checkpoint_keeps_migration_lineage_and_old_migration_replay(retained_project):
    plan = migration.preview(retained_project)
    first = migration.migrate(retained_project, plan['expected_revision'], plan['approval_id'], 'Owner')
    for _ in range(2):
        preview = history.checkpoint_prepare(retained_project)
        history.checkpoint_commit(retained_project, preview['revision'], preview['approval_id'], 'Owner')
    replay = migration.migrate(retained_project, plan['expected_revision'], plan['approval_id'], 'Owner')
    assert replay['replayed'] and replay['archive'] == first['archive']
    with SafeRoot(retained_project) as safe:
        current, _, manifest = store.read_state(safe)
        migration.validate_metadata(safe, current, manifest)
        migration.validate_metadata(safe, current, manifest, full=True)


def test_current_build_review_after_retained_checkpoint_preserves_original_decisions(retained_project):
    from test_cc_development_core import decide
    with SafeRoot(retained_project) as safe:
        original, _, _ = store.read_state(safe)
    plan = migration.preview(retained_project)
    migration.migrate(retained_project, plan['expected_revision'], plan['approval_id'], 'Owner')
    checkpoint = history.checkpoint_prepare(retained_project)
    history.checkpoint_commit(retained_project, checkpoint['revision'], checkpoint['approval_id'], 'Owner')
    decide(retained_project, 'build', 'CurrentOwner')
    with SafeRoot(retained_project) as safe:
        current, _, manifest = store.read_state(safe)
        change = current['changes'][0]
        assert accepted(change, 'build')
        assert change['decisions'][-1]['method'] == IDENTITY
        events = history.full_history(safe, change)
        assert events[:len(original['changes'][0]['history'])] == original['changes'][0]['history']
        migration.validate_metadata(safe, current, manifest, full=True)


def test_wrong_retained_digest_and_stale_upgrade_cannot_mutate(retained_project):
    path = retained_project / '.controlcoding/development.json'
    original = path.read_bytes()
    plan = migration.preview(retained_project)
    wrong = json.loads(original)
    wrong['method']['digest'] = 'f' * 64
    path.write_text(json.dumps(wrong), encoding='utf-8')
    corrupted = path.read_bytes()
    with pytest.raises((storage.DevelopmentError, ValueError)):
        migration.migrate(retained_project, plan['expected_revision'], plan['approval_id'], 'Owner')
    assert path.read_bytes() == corrupted


def test_valid_retained_owner_history_above_cd10_capacity_reaches_migration(retained_project):
    """Use the exact predecessor's apply/validator, not invented event records."""
    runtime = os.environ['CC_TEST_PW00_RUNTIME']
    script = '''
import sys,json
sys.path.insert(0,sys.argv[1])
from cc_development_lib import apply,storage
from cc_development_lib.method import IDENTITY,digest
from cc_evidence_inputs import SafeRoot
assert IDENTITY == json.loads(sys.argv[3])
with SafeRoot(sys.argv[2]) as safe:
    state,revision=storage.load(safe)
    content=state['changes'][0]['artifacts']['intent'][-1]['content']
    while len(state['changes'][0]['history']) < 2049:
        index=len(state['operations'])
        action={'kind':'artifact','change_id':'C1','stage':'intent','actor':'HistoricalWriter','content':content}
        stamp='2026-10-02T00:00:00Z'
        # The predecessor handler reads the current version and previous head.
        # Keep already generated immutable prefixes outside its JSON copy, then
        # restore them before the predecessor's full authoritative validation.
        events=state['changes'][0]['history']
        versions=state['changes'][0]['artifacts']['intent']
        operations=state['operations']
        state['changes'][0]['history']=events[-1:]
        state['changes'][0]['artifacts']['intent']=versions[-1:]
        state['operations']=[]
        state=apply(safe,state,action,timestamp=stamp)
        events.append(state['changes'][0]['history'][-1])
        versions.append(state['changes'][0]['artifacts']['intent'][-1])
        state['changes'][0]['history']=events
        state['changes'][0]['artifacts']['intent']=versions
        state['operations']=operations
        approval=digest(['legacy-fixture-operation',index,action])
        state['operations'].append({'operation_id':approval,'approval_id':approval,
            'expected_revision':digest(['legacy-fixture-revision',index]),'action':action,
            'timestamp':stamp,'provenance':'local_revision'})
    storage.validate_state(safe,state)
    storage.save(safe,state,revision)
'''
    subprocess.run([sys.executable, '-B', '-c', script, runtime, str(retained_project), json.dumps(RETAINED)],
                   check=True, capture_output=True, text=True,
                   env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    raw = (retained_project / '.controlcoding/development.json').read_bytes()
    with SafeRoot(retained_project) as safe:
        original, _, _ = store.read_state(safe)
    oldest = copy.deepcopy(original['operations'][0])
    assert len(original['operations']) > 2048 and len(original['changes'][0]['history']) > 2048
    plan = migration.preview(retained_project)
    assert not plan['blockers']
    result = migration.migrate(retained_project, plan['expected_revision'], plan['approval_id'], 'Owner')
    with SafeRoot(retained_project) as safe:
        current, _, manifest = store.read_state(safe)
        assert current['changes'] == original['changes'] and current['operations'] == original['operations']
        assert store.read_archive_bytes(safe, result['archive']['archive_ref']) == raw
        migration.validate_metadata(safe, current, manifest, full=True)
    checkpoint = history.checkpoint_prepare(retained_project)
    history.checkpoint_commit(retained_project, checkpoint['revision'], checkpoint['approval_id'], 'Owner')
    import cc_development_lib as development
    view = development.read(retained_project)
    assert view['changes'][0]['history_retention']['partial']
    assert view['changes'][0]['history_retention']['retained_records'] == 2049
    replay = development.commit(retained_project, oldest['action'], oldest['expected_revision'], oldest['approval_id'])
    assert replay['receipt']['idempotent'] and replay['view']['revision'] == view['revision']
    first = history.page(retained_project, 'C1', limit=64)
    last = history.page(retained_project, 'C1', cursor={'revision': first['revision'], 'offset': 2048}, limit=64)
    assert first['total'] == last['total'] == 2049
    assert first['records'][0] == original['changes'][0]['history'][0]
    assert last['records'] == [original['changes'][0]['history'][-1]]
    with SafeRoot(retained_project) as safe:
        current, _, manifest = store.read_state(safe)
        assert history.full_history(safe, current['changes'][0]) == original['changes'][0]['history']
        migration.validate_metadata(safe, current, manifest, full=True)


def test_compact_metadata_rejects_malformed_original_revision(retained_project):
    plan = migration.preview(retained_project)
    migration.migrate(retained_project, plan['expected_revision'], plan['approval_id'], 'Owner')
    checkpoint = history.checkpoint_prepare(retained_project)
    history.checkpoint_commit(retained_project, checkpoint['revision'], checkpoint['approval_id'], 'Owner')
    with SafeRoot(retained_project) as safe:
        current, _, manifest = store.read_state(safe)
        malformed = copy.deepcopy(manifest)
        malformed['archives'][0]['source_revision'] = 'not-a-revision'
        with pytest.raises(storage.DevelopmentError, match='migration_metadata_invalid'):
            migration.validate_metadata(safe, current, malformed)


def test_genuine_cd10_pw00_successor_chain_retains_both_originals(prior_project):
    runtime = os.environ.get('CC_TEST_PW00_RUNTIME')
    if not runtime:
        pytest.skip('Exact frozen PW00 runtime supplied through CC_TEST_PW00_RUNTIME is required')
    path = prior_project / '.controlcoding/development.json'
    cd10 = path.read_bytes()
    script = '''
import sys,json
sys.path.insert(0,sys.argv[1])
from cc_development_lib.method import IDENTITY
from cc_development_migration import preview,migrate
assert IDENTITY == json.loads(sys.argv[3])
p=preview(sys.argv[2]);assert not p['blockers']
migrate(sys.argv[2],p['expected_revision'],p['approval_id'],'OriginalOwner')
'''
    subprocess.run([sys.executable, '-B', '-c', script, runtime, str(prior_project), json.dumps(RETAINED)],
                   check=True, capture_output=True, text=True,
                   env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    pw00 = path.read_bytes()
    with SafeRoot(prior_project) as safe:
        original, _, previous = store.read_state(safe)
    p = migration.preview(prior_project)
    migration.migrate(prior_project, p['expected_revision'], p['approval_id'], 'CurrentOwner')
    with SafeRoot(prior_project) as safe:
        current, _, manifest = store.read_state(safe)
        assert [(row['source'], row['target'], row['policy']) for row in manifest['compatibility']] == [
            (PRIOR, RETAINED, 'cd10_storage_only_v1'),
            (RETAINED, RETENTION, 'pw00_retention_storage_only_v1'),
            (RETENTION, PRODUCT, 'pw01_optional_product_unlinked_gates_unchanged_v1'),
            (PRODUCT, DISCUSSION, 'pw02_optional_discussion_pw01_gates_unchanged_v1'),
            (DISCUSSION, EVOLUTION, 'pw03_revision_scope_dependencies_unaffected_decisions_v1'),
            (EVOLUTION, PLANNING, 'pw04_explicit_detail_upgrade_unaffected_decisions_v1'),
            (PLANNING, IDENTITY, 'pw05_observed_progress_unaffected_planning_decisions_v1'),
        ]
        assert len(manifest['archives']) == 2
        assert manifest['archives'][0] == previous['archives'][0]
        assert store.read_archive_bytes(safe, manifest['archives'][0]['archive_ref']) == cd10
        assert store.read_archive_bytes(safe, manifest['archives'][1]['archive_ref']) == pw00
        assert current['changes'] == original['changes']
        migration.validate_metadata(safe, current, manifest, full=True)
        refs = migration.collect_references(manifest, lambda ref: store.get_object(safe, ref))
        assert previous['state_ref'] in refs
    checkpoint = history.checkpoint_prepare(prior_project)
    history.checkpoint_commit(prior_project, checkpoint['revision'], checkpoint['approval_id'], 'CurrentOwner')
    with SafeRoot(prior_project) as safe:
        current, _, manifest = store.read_state(safe)
        migration.validate_metadata(safe, current, manifest)
        migration.validate_metadata(safe, current, manifest, full=True)
