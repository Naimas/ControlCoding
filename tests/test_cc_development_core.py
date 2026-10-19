"""Adversarial local development service controls; fixtures live outside checkout."""
import hashlib
import json
import os
from pathlib import Path
import sys
import contextlib
import io
from concurrent.futures import ThreadPoolExecutor

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cc_development_lib as development
from cc_development_lib.method import digest
from cc_layout import MARKER_BYTES
from cc_evidence_inputs import SafeRoot
import cc_development_store as disk_store


def logical_state(root):
    with SafeRoot(root) as safe:
        return disk_store.read_state(safe)[0]


def corrupt_logical_state(root, value):
    # Deliberately bypass domain validation, while preserving valid disk hashes.
    # The Core reader must detect this domain/history attack independently.
    with SafeRoot(root) as safe:
        _, revision, _ = disk_store.read_state(safe)
        disk_store.write_state(safe, value, revision)


def act(root, action):
    before = development.read(root)
    preview = development.prepare(root, action, before['revision'])
    assert not preview['blockers'], preview['blockers']
    result = development.commit(root, action, before['revision'], preview['approval_id'])
    return result['view']


def created(root, profile='standard'):
    act(root, {'kind': 'activate', 'profile': profile})
    return act(root, {'kind': 'create', 'change_id': 'C1', 'title': 'Safe feature', 'owner': 'Owner', 'risk': profile})


def intent(problem='A problem'):
    return {'problem': problem, 'outcome': 'An outcome', 'affected_users_and_systems': ['User'], 'constraints': [],
            'non_goals': [], 'open_questions': [], 'success_measures': ['Behavior is measurable']}


def artifact(root, stage, content):
    return act(root, {'kind': 'artifact', 'change_id': 'C1', 'stage': stage, 'content': content, 'actor': 'Writer'})


def decide(root, stage, actor='Owner'):
    return act(root, {'kind': 'decide', 'change_id': 'C1', 'stage': stage, 'decision': 'accepted', 'actor': actor, 'reason': 'Reviewed exact revision'})


def planned(root, profile='standard'):
    created(root, profile)
    artifact(root, 'intent', intent())
    decide(root, 'intent')
    iv = development.read(root)['changes'][0]['artifacts']['intent'][-1]['revision']
    artifact(root, 'design', {'upstream_intent_revision': iv, 'requirements': [{'id': 'R1', 'text': 'Works'}],
             'criteria': [{'id': 'K1', 'requirement_id': 'R1', 'text': 'Check result', 'required': True, 'proof': 'automated',
                           'checks': [{'kind': 'verification', 'id': 'test'}]}],
             'decisions': [], 'risks': [], 'policy_concerns': [], 'source_references': []})
    decide(root, 'design')
    dv = development.read(root)['changes'][0]['artifacts']['design'][-1]['revision']
    artifact(root, 'plan', {'upstream_specification_revision': dv, 'files': ['source.py'], 'ownership_scopes': ['project'],
             'tasks': [{'id': 'T1', 'title': 'Implement', 'owner': 'Writer', 'depends_on': []}], 'dependencies': [],
             'criterion_check_mapping': {'K1': ['test']}, 'verification_strategy': 'Required checks', 'rollback_assessment': 'Revert candidate'})
    return decide(root, 'plan')


def test_read_and_prepare_do_not_initialize(tmp_path):
    assert development.read(tmp_path)['revision'] == 'absent'
    action = {'kind': 'activate', 'profile': 'routine'}
    assert development.prepare(tmp_path, action, 'absent')['view']['enabled']
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('contained', [False, True])
def test_layout_and_idempotent_commit(tmp_path, contained):
    if contained:
        (tmp_path / 'cc').mkdir()
        (tmp_path / 'cc/layout.json').write_bytes(MARKER_BYTES)
    action = {'kind': 'activate', 'profile': 'routine'}
    preview = development.prepare(tmp_path, action, 'absent')
    first = development.commit(tmp_path, action, 'absent', preview['approval_id'])
    retry = development.commit(tmp_path, action, 'absent', preview['approval_id'])
    assert first['view']['revision'] == retry['view']['revision']
    assert retry['receipt']['idempotent']
    expected = tmp_path / ('cc/.controlcoding/development.json' if contained else '.controlcoding/development.json')
    assert expected.is_file()
    assert not (expected.parent / 'development.lock').exists()


def test_revision_conflict_preserves_history(tmp_path):
    created(tmp_path)
    before = development.read(tmp_path)
    action = {'kind': 'artifact', 'change_id': 'C1', 'stage': 'intent', 'content': intent(), 'actor': 'Writer'}
    preview = development.prepare(tmp_path, action, before['revision'])
    artifact(tmp_path, 'intent', intent('Changed problem'))
    current = development.read(tmp_path)
    with pytest.raises(development.DevelopmentError, match='revision_conflict'):
        development.commit(tmp_path, action, before['revision'], preview['approval_id'])
    assert development.read(tmp_path) == current


def test_project_bound_preview(tmp_path):
    other = tmp_path / 'other'
    other.mkdir()
    action = {'kind': 'activate', 'profile': 'routine'}
    preview = development.prepare(tmp_path, action, 'absent')
    with pytest.raises(development.DevelopmentError, match='approval_conflict'):
        development.commit(other, action, 'absent', preview['approval_id'])
    assert development.read(other)['revision'] == 'absent'


def test_unsafe_and_unknown_fields(tmp_path):
    for action in ({'kind': 'activate', 'profile': 'routine', 'extra': True},
                   {'kind': 'unknown'}, {'kind': 'create', 'change_id': '../bad', 'title': 'x', 'owner': 'y', 'risk': 'routine'},
                   {'kind': 'candidate', 'change_id': 'C1', 'files': ['../outside.py'], 'actor': 'Writer'}):
        with pytest.raises(development.DevelopmentError):
            development.prepare(tmp_path, action, 'absent')
    assert list(tmp_path.iterdir()) == []


def test_missing_stage_cannot_advance(tmp_path):
    created(tmp_path)
    before = development.read(tmp_path)
    preview = development.prepare(tmp_path, {'kind': 'decide', 'change_id': 'C1', 'stage': 'design', 'decision': 'accepted', 'actor': 'Owner', 'reason': 'Claim'}, before['revision'])
    assert preview['blockers'] == ['artifact_missing']


def test_upstream_change_preserves_and_invalidates_approvals(tmp_path):
    planned(tmp_path)
    before = development.read(tmp_path)['changes'][0]
    assert before['gates']['plan']['accepted']
    after = artifact(tmp_path, 'intent', intent('A different problem'))['changes'][0]
    assert after['decisions'] == before['decisions']
    assert not after['gates']['design']['accepted']
    assert not after['gates']['plan']['accepted']
    assert after['stage'] == 'intent'
    assert len(after['history']) == len(before['history']) + 1


def test_candidate_preview_binds_actual_source_bytes(tmp_path):
    planned(tmp_path)
    (tmp_path / 'source.py').write_text('value = 1\n')
    before = development.read(tmp_path)
    action = {'kind': 'candidate', 'change_id': 'C1', 'files': ['source.py'], 'actor': 'Writer'}
    preview = development.prepare(tmp_path, action, before['revision'])
    (tmp_path / 'source.py').write_text('value = 2\n')
    with pytest.raises(development.DevelopmentError, match='approval_conflict'):
        development.commit(tmp_path, action, before['revision'], preview['approval_id'])
    assert development.read(tmp_path)['changes'][0]['candidate'] is None


def test_candidate_stale_and_scope(tmp_path):
    planned(tmp_path)
    (tmp_path / 'source.py').write_text('value = 1\n')
    view = act(tmp_path, {'kind': 'candidate', 'change_id': 'C1', 'files': ['source.py'], 'actor': 'Writer'})
    assert view['changes'][0]['candidate']['current']
    (tmp_path / 'source.py').write_text('value = 2\n')
    assert not development.read(tmp_path)['changes'][0]['candidate']['current']
    current = development.read(tmp_path)
    preview = development.prepare(tmp_path, {'kind': 'candidate', 'change_id': 'C1', 'files': ['other.py'], 'actor': 'Writer'}, current['revision'])
    assert preview['blockers'] == ['candidate_outside_plan_scope']


def test_automation_requires_selected_current_qualification_and_still_blocks_missing_host(tmp_path, monkeypatch):
    from cc_development_lib import policy
    planned(tmp_path)
    (tmp_path / 'source.py').write_text('value = 1\n')
    view = act(tmp_path, {'kind': 'candidate', 'change_id': 'C1', 'files': ['source.py'], 'actor': 'Writer'})
    candidate = view['changes'][0]['candidate']['digest']
    artifact(tmp_path, 'build', {'approved_plan_revision': view['changes'][0]['artifacts']['plan'][-1]['revision'],
             'source_identity': candidate, 'execution_provenance': {'mode': 'automated'}, 'deviations': [],
             'changed_files': ['source.py']})
    gate = development.read(tmp_path)['changes'][0]['gates']['build']
    assert 'agent_evaluation_selection_missing' in gate['reasons']
    assert 'coding_host_adapter_unavailable' in gate['reasons']
    # This boundary test supplies owner assessments; real worker grading and
    # canonical receipt validation are separately exercised by adapter tests.
    assessed = {'id': 'evaluation-1', 'state': 'allowed', 'qualification': False,
                'current': True, 'configurationDigest': 'a' * 64, 'reasons': []}
    monkeypatch.setattr(policy, 'job_assessments', lambda *_: {'agent_evaluations': [dict(assessed)], 'monitoring': []})
    provenance = {'mode': 'automated', 'evaluation_id': 'evaluation-1', 'configuration_digest': 'a' * 64}
    change = view['changes'][0]
    assert policy.automation_qualification(tmp_path, change, provenance)['state'] == 'blocked'
    assessed['qualification'] = True
    assert policy.automation_qualification(tmp_path, change, {**provenance, 'configuration_digest': 'b' * 64})['reasons'] == ['agent_evaluation_configuration_mismatch']
    assessed['current'] = False
    assert policy.automation_qualification(tmp_path, change, provenance)['state'] == 'blocked'
    assessed['current'] = True
    assert policy.automation_qualification(tmp_path, change, provenance)['state'] == 'allowed'
    artifact(tmp_path, 'build', {'approved_plan_revision': change['artifacts']['plan'][-1]['revision'],
             'source_identity': candidate, 'execution_provenance': provenance, 'deviations': [],
             'changed_files': ['source.py']})
    gate = development.read(tmp_path)['changes'][0]['gates']['build']
    assert gate['reasons'] == ['coding_host_adapter_unavailable']


def test_prior_method_registry_is_preserved_and_never_silently_reinterpreted(tmp_path):
    created(tmp_path)
    registry = tmp_path / '.controlcoding/development.json'
    state = logical_state(tmp_path)
    state['method']['version'] = '0.1.0'
    corrupt_logical_state(tmp_path, state)
    retained = registry.read_bytes()
    with pytest.raises(development.DevelopmentError, match='unsupported_method'):
        development.read(tmp_path)
    assert registry.read_bytes() == retained


def test_crash_lock_is_retained(tmp_path):
    created(tmp_path)
    lock = tmp_path / '.controlcoding/development.lock'
    lock.mkdir()
    with pytest.raises(development.DevelopmentError, match='recovery_required'):
        development.read(tmp_path)
    assert lock.is_dir()


def test_duplicate_json_and_malformed_registry(tmp_path):
    directory = tmp_path / '.controlcoding'
    directory.mkdir()
    state = directory / 'development.json'
    state.write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(development.DevelopmentError, match='duplicate_json_key'):
        development.read(tmp_path)
    state.write_text('{"schema_version":999}')
    with pytest.raises(development.DevelopmentError):
        development.read(tmp_path)


def test_concurrent_previews_cannot_overwrite(tmp_path):
    act(tmp_path, {'kind': 'activate', 'profile': 'routine'})
    before = development.read(tmp_path)
    actions = [{'kind': 'create', 'change_id': f'C{i}', 'title': 'Feature', 'owner': 'Owner', 'risk': 'routine'} for i in (1, 2)]
    previews = [development.prepare(tmp_path, a, before['revision']) for a in actions]
    def run(index):
        try:
            development.commit(tmp_path, actions[index], before['revision'], previews[index]['approval_id'])
            return True
        except development.DevelopmentError:
            return False
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(run, [0, 1]))
    assert outcomes.count(True) == 1
    assert len(development.read(tmp_path)['changes']) == 1


def test_history_corruption_is_rejected(tmp_path):
    created(tmp_path)
    path = tmp_path / '.controlcoding/development.json'
    registry = logical_state(tmp_path)
    registry['changes'][0]['history'][0]['action']['title'] = 'Tampered'
    corrupt_logical_state(tmp_path, registry)
    with pytest.raises(development.DevelopmentError, match='invalid_history'):
        development.read(tmp_path)


def test_shared_state_file_is_rejected(tmp_path):
    created(tmp_path)
    path = tmp_path / '.controlcoding/development.json'
    try:
        os.link(path, tmp_path / 'shared.json')
    except OSError:
        pytest.skip('Hard links unavailable')
    with pytest.raises(development.DevelopmentError, match='unsafe_state_file'):
        development.read(tmp_path)


def ready_for_verification(root, profile='standard'):
    import cc
    (root / 'source.py').write_text('value = 1\n')
    checker = f'"{Path(sys.executable).as_posix()}" -B -c "assert open(\'source.py\').read() == \'value = 1\\n\'"'
    (root / 'controlcoding.verification.json').write_text(json.dumps({'schemaVersion': 1, 'requiredKinds': ['targeted'],
        'suites': [{'id': 'test', 'kind': 'targeted', 'required': True, 'command': checker}]}))
    (root / 'controlcoding.invariants.json').write_text(json.dumps({'schemaVersion': 1, 'invariants': [
        {'id': 'source-valid', 'kind': 'domain', 'domain': 'testing', 'severity': 'blocking', 'status': 'active',
         'property': 'Source preserves expected value', 'threshold': 'zero failures', 'command': checker, 'required': True}]}))
    planned(root, profile)
    view = act(root, {'kind': 'candidate', 'change_id': 'C1', 'files': ['source.py'], 'actor': 'Writer'})
    change = view['changes'][0]
    candidate = change['candidate']['digest']
    artifact(root, 'build', {'approved_plan_revision': change['artifacts']['plan'][-1]['revision'], 'source_identity': candidate,
                           'execution_provenance': {'mode': 'manual', 'description': 'Fixture source prepared locally'},
                           'deviations': [], 'changed_files': ['source.py']})
    decide(root, 'build')
    for run in (cc.cmd_verify_run, cc.cmd_invariants_run):
        with contextlib.redirect_stdout(io.StringIO()):
            assert run(root, json_output=True) == 0
    artifact(root, 'verify', {'candidate_identity': candidate, 'criterion_coverage': {'K1': ['test']},
        'required_contract_results': {'mode': 'owner_receipts'}, 'freshness': 'Recomputed by Core', 'unresolved_failures': []})
    return candidate


def ready_for_release(root, profile='standard'):
    candidate = ready_for_verification(root, profile)
    decide(root, 'verify', 'Verifier')
    act(root, {'kind': 'review', 'change_id': 'C1', 'findings': [], 'actor': 'Reviewer', 'reason': 'Reviewed behavior and scope'})
    artifact(root, 'review_release', {'candidate_identity': candidate, 'review_findings': [], 'finding_resolutions': [],
        'required_approvals': [], 'environment': 'local', 'release_artifact_identity': candidate,
        'rollback_readiness': 'Retain previous version', 'observation_provenance': 'self_reported'})
    return candidate


def test_full_manual_cycle_real_owner_receipts_and_retained_history(tmp_path):
    candidate = ready_for_release(tmp_path)
    view = decide(tmp_path, 'review_release', 'ReleaseOwner')
    assert view['changes'][0]['outcomes']['verified']
    assert view['changes'][0]['outcomes']['approved']
    release = {'kind': 'release', 'change_id': 'C1', 'environment': 'local', 'artifact_digest': candidate,
               'status': 'self_reported', 'actor': 'ReleaseOwner', 'reason': 'Manual fixture record'}
    view = act(tmp_path, release)
    assert not view['changes'][0]['outcomes']['released']
    act(tmp_path, {'kind': 'observation', 'change_id': 'C1', 'signals': [{'id': 'S1', 'description': 'Manual outcome observed'}], 'actor': 'Owner'})
    artifact(tmp_path, 'maintain', {'deployed_artifact_identity': candidate, 'observation_scope': 'Local fixture',
        'observation_window': 'One fixture run', 'signals': ['S1'], 'findings': [], 'dispositions': [], 'followup_owners': ['Owner']})
    decide(tmp_path, 'maintain', 'Owner')
    closed = act(tmp_path, {'kind': 'close', 'change_id': 'C1', 'actor': 'Owner', 'reason': 'Local outcome reviewed'})
    assert closed['changes'][0]['stage'] == 'closed'
    assert closed['changes'][0]['outcomes']['release_status'] == 'self_reported'
    assert len(closed['changes'][0]['history']) >= 20
    reopened = development.read(tmp_path)
    assert reopened == closed
    source = tmp_path / 'source.py'
    source.write_text('value = 2\n')
    drifted = development.read(tmp_path)['changes'][0]
    assert drifted['stage'] == 'closed'
    assert not drifted['outcomes']['verified']
    assert not drifted['outcomes']['candidate_current']


def test_receipt_failure_and_fake_pass_cannot_approve(tmp_path):
    ready_for_verification(tmp_path)
    (tmp_path / 'source.py').write_text('value = 2\n')
    act(tmp_path, {'kind': 'evidence', 'change_id': 'C1', 'records': [{'id': 'E1', 'status': 'passed', 'kind': 'claim'}], 'actor': 'Writer'})
    view = development.read(tmp_path)
    action = {'kind': 'decide', 'change_id': 'C1', 'stage': 'verify', 'decision': 'accepted', 'actor': 'Verifier', 'reason': 'Claim'}
    preview = development.prepare(tmp_path, action, view['revision'])
    assert preview['blockers'] and 'candidate_stale' in preview['blockers'][0]


def test_new_unresolved_review_blocks_current_maintenance(tmp_path):
    candidate = ready_for_release(tmp_path)
    decide(tmp_path, 'review_release', 'ReleaseOwner')
    act(tmp_path, {'kind': 'release', 'change_id': 'C1', 'environment': 'local', 'artifact_digest': candidate,
        'status': 'prepared', 'actor': 'ReleaseOwner', 'reason': 'Prepared fixture'})
    act(tmp_path, {'kind': 'review', 'change_id': 'C1', 'findings': [{'id': 'F1', 'severity': 'critical', 'text': 'Regression found', 'disposition': 'open'}],
        'actor': 'Reviewer', 'reason': 'Late finding'})
    view = development.read(tmp_path)['changes'][0]
    assert view['gates']['review_release']['accepted']
    assert view['gates']['review_release']['outcome'] == 'blocked'
    assert 'upstream_review_release_not_current' in view['gates']['maintain']['reasons']
    assert not view['outcomes']['approved']


def test_recovery_requires_inactive_bound_writer(tmp_path, monkeypatch):
    from cc_development_lib import storage
    from cc_evidence_inputs import SafeRoot
    created(tmp_path)
    owner = tmp_path / '.controlcoding/development.lock'
    owner.mkdir()
    with SafeRoot(tmp_path) as root:
        (owner / 'owner.json').write_text(json.dumps({'schema_version': 1, 'pid': os.getpid(), 'project_identity': storage.identity(root), 'operation_id': 'abandoned'}))
    preview = development.recovery_prepare(tmp_path, 'Owner', 'Inspect interrupted writer')
    assert preview['blockers'] == ['recovery_writer_active']
    with pytest.raises(development.DevelopmentError, match='recovery_writer_active'):
        development.commit(tmp_path, preview['action'], preview['revision'], preview['approval_id'])
    assert owner.is_dir()
    monkeypatch.setattr(storage, 'process_state', lambda _: 'inactive')
    preview = development.recovery_prepare(tmp_path, 'Owner', 'Recover observed dead writer')
    result = development.commit(tmp_path, preview['action'], preview['revision'], preview['approval_id'])
    assert not owner.exists()
    assert result['receipt']['action']['kind'] == 'recover'
    assert development.read(tmp_path)['changes'][0]['id'] == 'C1'
    retry = development.commit(tmp_path, preview['action'], preview['revision'], preview['approval_id'])
    assert retry['receipt']['idempotent']


def test_projection_tamper_and_old_candidate_retention(tmp_path):
    planned(tmp_path)
    (tmp_path / 'source.py').write_text('value = 1\n')
    one = act(tmp_path, {'kind': 'candidate', 'change_id': 'C1', 'files': ['source.py'], 'actor': 'Writer'})['changes'][0]['candidate']['digest']
    (tmp_path / 'source.py').write_text('value = 2\n')
    two = act(tmp_path, {'kind': 'candidate', 'change_id': 'C1', 'files': ['source.py'], 'actor': 'Writer'})['changes'][0]
    results = [row['result']['digest'] for row in two['history'] if row['action']['kind'] == 'candidate']
    assert one in results and two['candidate']['digest'] in results
    path = tmp_path / '.controlcoding/development.json'
    state = logical_state(tmp_path)
    state['changes'][0]['decisions'][0]['actor'] = 'ForgedOwner'
    corrupt_logical_state(tmp_path, state)
    with pytest.raises(development.DevelopmentError, match='history_projection_mismatch'):
        development.read(tmp_path)


def test_observed_pr_policy_invalidates_manual_release_qualification(tmp_path):
    ready_for_release(tmp_path)
    decide(tmp_path, 'review_release', 'ReleaseOwner')
    assert development.read(tmp_path)['changes'][0]['outcomes']['approved']
    policy = {'schemaVersion': 1, 'repository': 'fixture/repo', 'baseRef': 'main',
              'requiredChecks': [{'kind': 'check_run', 'name': 'test', 'appId': 123}],
              'requiredReviewers': ['reviewer'], 'maxAgeSeconds': 60,
              'pullRequests': [{'changeId': 'C1', 'number': 1, 'base': 'a' * 40, 'headRepository': 'fixture/repo'}]}
    path = tmp_path / '.controlcoding/development-release.json'
    path.write_text(json.dumps(policy))
    current = development.read(tmp_path)['changes'][0]
    assert not current['outcomes']['approved']
    assert current['gates']['review_release']['pull_request']['required']
    assert 'pr_observation_selection_missing' in current['gates']['review_release']['reasons']
    path.unlink()
    assert development.read(tmp_path)['changes'][0]['outcomes']['approved']


def test_critical_role_decisions_policy_digest_and_rehearsal(tmp_path, monkeypatch):
    import cc_development_jobs
    candidate = ready_for_release(tmp_path, 'critical')
    known = 'a' * 64
    observation = {'candidateDigest': candidate, 'environment': 'local', 'deployedDigest': candidate,
                   'status': 'rolled_back', 'mode': 'rehearsal', 'restoredDigest': known}
    policy = {'schema_version': 1, 'critical': {'approvers': {'technical': 'Technical', 'policy': 'Policy', 'environment': 'Environment'},
              'security_checks': [{'kind': 'verification', 'id': 'test'}],
              'recovery_receipt': {'receiptId': 'canonical-fixture', 'observation': observation}}}
    policy_path = tmp_path / '.controlcoding/development-policy.json'
    policy_path.write_text(json.dumps(policy))
    real_assess = cc_development_jobs.assess_job_observation
    def assess(root, record, change, kind):
        if kind == 'rollback' and record == policy['critical']['recovery_receipt']:
            return {'state': 'allowed', 'reasons': [], 'observation': observation,
                    'configuration': {'mode': 'rehearsal', 'environment': 'local', 'knownArtifactDigest': known}}
        return real_assess(root, record, change, kind)
    monkeypatch.setattr(cc_development_jobs, 'assess_job_observation', assess)
    for actor in ('Technical', 'Policy'):
        view = decide(tmp_path, 'review_release', actor)
        assert view['changes'][0]['stage'] == 'review_release'
        assert view['changes'][0]['gates']['review_release']['outcome'] == 'blocked'
    complete = decide(tmp_path, 'review_release', 'Environment')
    assert complete['changes'][0]['gates']['review_release']['outcome'] == 'allowed'
    assert complete['changes'][0]['outcomes']['approved']
    policy['critical']['approvers']['technical'] = 'OtherTechnical'
    policy_path.write_text(json.dumps(policy))
    changed = development.read(tmp_path)['changes'][0]
    assert not changed['outcomes']['approved']
    assert 'critical_approval_missing:technical' in changed['gates']['review_release']['reasons']


def test_critical_distinct_approvers_and_unobserved_rehearsal_block(tmp_path):
    candidate = ready_for_release(tmp_path, 'critical')
    policy = {'schema_version': 1, 'critical': {'approvers': {'technical': 'Writer', 'policy': 'Writer', 'environment': 'Writer'},
              'security_checks': [{'kind': 'verification', 'id': 'test'}],
              'recovery_receipt': {'receiptId': 'forged', 'observation': {'candidateDigest': candidate, 'status': 'rolled_back'}}}}
    path = tmp_path / '.controlcoding/development-policy.json'
    path.write_text(json.dumps(policy))
    view = development.read(tmp_path)['changes'][0]
    assert 'critical_role_separation_required' in view['gates']['review_release']['reasons']
    policy['critical']['approvers'] = {'technical': 'Technical', 'policy': 'Policy', 'environment': 'Environment'}
    path.write_text(json.dumps(policy))
    view = development.read(tmp_path)['changes'][0]
    assert any(reason.startswith('critical_recovery:') for reason in view['gates']['review_release']['reasons'])


def test_method_bytes_are_pinned_independently_of_cwd(tmp_path, monkeypatch):
    import shutil
    from cc_development_lib import method
    source = Path(method.__file__).absolute().parent.parent
    clone = tmp_path / 'trusted-owner'
    clone.mkdir()
    (clone / 'cc_development_lib').mkdir()
    for name in ('method.py', 'schema.py', 'evaluator.py', 'storage.py', 'policy.py', '__init__.py'):
        shutil.copy2(source / 'cc_development_lib' / name, clone / 'cc_development_lib' / name)
    for name in ('cc_development_adapters.py', 'cc_development_jobs.py', 'cc_development_eval.py', 'cc_layout.py', 'cc_evidence.py',
                 'cc_development_execution.py', 'cc_development_gateway.py', 'cc_development_codex.py',
                 'cc_development_release.py', 'cc_development_github.py',
                 'cc_development_operations.py', 'cc_development_diagnosis.py',
                 'cc_development.py', 'cc_development_archive.py',
                 'cc_development_store.py', 'cc_development_compatibility.py',
                 'cc_development_history.py',
                 'cc_development_migration.py', 'cc_development_sessions.py', 'cc_development_bundle.py',
                 'cc_evidence_inputs.py', 'cc_evidence_process.py', 'cc.py',
                 'cc_product.py', 'cc_product_evolution.py', 'cc_product_documents.py', 'cc_product_adoption.py', 'cc_product_planning.py', 'cc_product_cli.py',
                 'cc_product_progress.py', 'cc_product_progress_contract.py', 'cc_product_progress_observation.py',
                 'cc_planning.py', 'cc_planning_cli.py'):
        shutil.copy2(source / name, clone / name)
    monkeypatch.setattr(method, '__file__', str(clone / 'cc_development_lib/method.py'))
    assert method.implementation_identity() == method.IMPLEMENTATION_DIGEST
    monkeypatch.chdir(tmp_path)
    assert method.implementation_identity() == method.IMPLEMENTATION_DIGEST
    evaluator = clone / 'cc_development_lib/evaluator.py'
    evaluator.write_bytes(evaluator.read_bytes() + b'\n# Changed evaluator bytes, same method version.\n')
    changed = method.implementation_identity()
    assert changed != method.IMPLEMENTATION_DIGEST
    assert method.digest([method.METHOD, changed]) != method.IDENTITY['digest']
    with pytest.raises(development.DevelopmentError, match='method_implementation_changed'):
        development.read(tmp_path)


def test_uncovered_requirement_and_conflicting_scopes_are_rejected(tmp_path):
    view = planned(tmp_path)
    change = view['changes'][0]
    design = json.loads(json.dumps(change['artifacts']['design'][-1]['content']))
    design['requirements'].append({'id': 'R2', 'text': 'Needs separate criterion'})
    action = {'kind': 'artifact', 'change_id': 'C1', 'stage': 'design', 'content': design, 'actor': 'Writer'}
    with pytest.raises(development.DevelopmentError, match='requirement_criteria_missing'):
        development.prepare(tmp_path, action, view['revision'])
    plan = json.loads(json.dumps(change['artifacts']['plan'][-1]['content']))
    plan['ownership_scopes'] = [{'owner': 'Alice', 'files': ['source.py']}, {'owner': 'Bob', 'files': ['source.py']}]
    action.update(stage='plan', content=plan)
    with pytest.raises(development.DevelopmentError, match='conflicting_file_owners'):
        development.prepare(tmp_path, action, view['revision'])
    plan['ownership_scopes'] = ['Writer']
    plan['criterion_check_mapping'] = {'K1': ['unrelated']}
    artifact(tmp_path, 'plan', plan)
    current = development.read(tmp_path)['changes'][0]
    assert 'criterion_mapping_mismatch:K1' in current['gates']['plan']['reasons']


def test_malformed_action_types_raise_public_error(tmp_path):
    for action in ({'kind': []}, {'kind': 'candidate', 'change_id': 'C1', 'files': [{}], 'actor': 'Writer'},
                   {'kind': 'activate', 'profile': {}}):
        with pytest.raises(development.DevelopmentError):
            development.prepare(tmp_path, action, 'absent')


def maintain_ready(root):
    candidate = ready_for_release(root)
    decide(root, 'review_release', 'ReleaseOwner')
    act(root, {'kind': 'release', 'change_id': 'C1', 'environment': 'local', 'artifact_digest': candidate,
              'status': 'self_reported', 'actor': 'ReleaseOwner', 'reason': 'Manual fixture record'})
    return candidate, {'deployed_artifact_identity': candidate, 'observation_scope': 'Local fixture',
        'observation_window': 'One fixture run', 'signals': ['M1'], 'findings': [], 'dispositions': [], 'followup_owners': ['Owner']}


def test_monitor_unknown_breach_pending_incident_and_recovery_gate(tmp_path, monkeypatch):
    import cc_development_adapters
    candidate, content = maintain_ready(tmp_path)
    incident_id = 'b' * 64
    def assess(root, change, record, prior_incident_ids=()):
        status = record['observation']['fixture_status']
        result = {'state': status, 'reasons': ['monitor_disconnected'] if status == 'unknown' else ['monitor_threshold_breached'] if status == 'blocked' else [],
                  'proposals': [], 'provenance': 'observed_command'}
        if status == 'blocked' and incident_id not in prior_incident_ids:
            result['proposals'] = [{'id': incident_id, 'kind': 'incident_proposal', 'deployedDigest': candidate}]
        return result
    monkeypatch.setattr(cc_development_adapters, 'assess_monitor', assess)
    def observe(status):
        act(tmp_path, {'kind': 'observation', 'change_id': 'C1',
            'signals': [{'id': 'M1', 'kind': 'monitor', 'receiptId': 'owner-fixture', 'observation': {'fixture_status': status}}], 'actor': 'Owner'})
    observe('unknown')
    view = artifact(tmp_path, 'maintain', content)['changes'][0]
    assert view['gates']['maintain']['outcome'] == 'unknown'
    assert 'monitor:monitor_disconnected' in view['gates']['maintain']['reasons']
    observe('blocked')
    view = development.read(tmp_path)['changes'][0]
    assert view['gates']['maintain']['outcome'] == 'blocked'
    assert 'monitor:monitor_threshold_breached' in view['gates']['maintain']['reasons']
    assert 'monitor_incident_disposition_missing' in view['gates']['maintain']['reasons']
    observe('allowed')
    view = development.read(tmp_path)['changes'][0]
    assert 'monitor:monitor_threshold_breached' not in view['gates']['maintain']['reasons']
    assert 'monitor_incident_disposition_missing' in view['gates']['maintain']['reasons']
    content['dispositions'] = [{'incident_id': incident_id, 'decision': 'assigned', 'owner': 'Owner', 'reason': 'Follow-up assigned'}]
    view = artifact(tmp_path, 'maintain', content)['changes'][0]
    assert view['gates']['maintain']['outcome'] == 'allowed'
    decide(tmp_path, 'maintain', 'Owner')
    closed = act(tmp_path, {'kind': 'close', 'change_id': 'C1', 'actor': 'Owner', 'reason': 'Current monitoring and incident disposition reviewed'})
    assert closed['changes'][0]['stage'] == 'closed'


def test_observation_is_bound_to_current_candidate_and_release(tmp_path):
    from cc_development_lib.evaluator import evaluate
    from cc_evidence_inputs import SafeRoot
    candidate, content = maintain_ready(tmp_path)
    act(tmp_path, {'kind': 'observation', 'change_id': 'C1', 'signals': [{'id': 'S1', 'description': 'Manual observation'}], 'actor': 'Owner'})
    view = artifact(tmp_path, 'maintain', content)
    assert view['changes'][0]['gates']['maintain']['outcome'] == 'allowed'
    assert view['changes'][0]['observations'][-1]['candidate_identity'] == candidate
    assert view['changes'][0]['observations'][-1]['provenance'] == 'self_reported'
    change = json.loads(json.dumps(view['changes'][0]))
    change['observations'][-1]['candidate_identity'] = 'f' * 64
    with SafeRoot(tmp_path) as safe:
        gate = evaluate(safe, change)['maintain']
    assert 'operational_observation_missing' in gate['reasons']
    act(tmp_path, {'kind': 'release', 'change_id': 'C1', 'environment': 'local', 'artifact_digest': candidate,
              'status': 'self_reported', 'actor': 'ReleaseOwner', 'reason': 'A later release requires its own observation'})
    view = development.read(tmp_path)['changes'][0]
    assert 'operational_observation_missing' in view['gates']['maintain']['reasons']
    act(tmp_path, {'kind': 'observation', 'change_id': 'C1', 'signals': [{'id': 'S2', 'description': 'New release observation'}], 'actor': 'Owner'})
    assert development.read(tmp_path)['changes'][0]['gates']['maintain']['outcome'] == 'allowed'
