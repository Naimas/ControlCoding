"""Disposable local Git and normalized hosted snapshots; no network calls."""
import copy
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cc_development_release as release
import cc_evidence_inputs as inputs
from test_cc_development_adapters import change_fixture, put


def git(root, *args):
    result = subprocess.run(['git', '-c', 'core.autocrlf=false', *args], cwd=root, check=True, capture_output=True)
    return result.stdout.decode().strip()


def fixture(root):
    git(root, 'init', '--quiet')
    git(root, 'config', 'user.name', 'Fixture')
    git(root, 'config', 'user.email', 'fixture@example.invalid')
    (root / 'app.py').write_bytes(b'old\n')
    git(root, 'add', 'app.py')
    git(root, 'commit', '--quiet', '-m', 'base')
    base = git(root, 'rev-parse', 'HEAD')
    change = change_fixture(root)
    git(root, 'add', 'app.py')
    git(root, 'commit', '--quiet', '-m', 'candidate')
    change.update(evidence=[], reviews=[], releases=[], observations=[], profile='standard')
    policy = {'schemaVersion': 1, 'repository': 'fixture/repo', 'baseRef': 'main',
              'requiredChecks': [{'kind': 'check_run', 'name': 'required', 'appId': 11},
                                 {'kind': 'commit_status', 'name': 'status', 'creator': 'ci-bot'}],
              'requiredReviewers': ['reviewer'], 'maxAgeSeconds': 60,
              'pullRequests': [{'changeId': change['id'], 'number': 3, 'base': base, 'headRepository': 'fixture/repo'}]}
    put(root, release.POLICY, policy)
    plan = release.prepare_observation(root, change)
    request = plan['request']
    observation = {k: request[k] for k in ('candidateDigest', 'repository', 'number', 'baseRef', 'base', 'head', 'headRepository')}
    observation.update(schemaVersion=1, provider='github', state='open', draft=False, merged=False,
                       mergeCommitSha=None, complete=True, checks=[
        {'kind': 'check_run', 'id': 1, 'name': 'required', 'appId': 11, 'head': request['head'], 'status': 'completed',
         'conclusion': 'success', 'startedAt': None, 'completedAt': None, 'url': 'https://github.com/fixture'},
        {'kind': 'commit_status', 'id': 2, 'name': 'status', 'creator': 'ci-bot', 'head': request['head'],
         'status': 'success', 'createdAt': None, 'url': 'https://github.com/fixture'}],
        reviews=[{'id': 3, 'login': 'reviewer', 'commit': request['head'], 'state': 'APPROVED', 'submittedAt': None}])
    timestamp = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=2)).isoformat()
    observation['checks'][0].update(startedAt=timestamp, completedAt=timestamp)
    observation['checks'][1]['createdAt'] = timestamp
    observation['reviews'][0]['submittedAt'] = timestamp
    return change, plan, observation


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def test_real_git_complete_scope_and_snapshot(tmp_path):
    change, plan, observation = fixture(tmp_path)
    assert plan['localGit']['changedFiles'] == ['app.py']
    assert plan['localGit']['tree'] == git(tmp_path, 'rev-parse', 'HEAD^{tree}')
    assert release.assess_observation(tmp_path, change, plan, observation, now())['state'] == 'allowed'


@pytest.mark.parametrize('mutation', ['head', 'base', 'repository', 'candidate', 'draft', 'merged', 'incomplete', 'pending',
                                     'failed', 'missing', 'app', 'creator', 'duplicate', 'oldhead', 'reviewer', 'dismissed',
                                     'oldreview', 'unknownfield'])
def test_snapshot_failures_block(tmp_path, mutation):
    change, plan, observation = fixture(tmp_path)
    if mutation in {'head', 'base', 'repository'}:
        observation[mutation] = 'wrong'
    elif mutation == 'candidate': observation['candidateDigest'] = '0' * 64
    elif mutation == 'draft': observation['draft'] = True
    elif mutation == 'merged': observation['merged'] = True
    elif mutation == 'incomplete': observation['complete'] = False
    elif mutation == 'pending': observation['checks'][0]['status'] = 'in_progress'
    elif mutation == 'failed': observation['checks'][0]['conclusion'] = 'failure'
    elif mutation == 'missing': observation['checks'] = []
    elif mutation == 'app': observation['checks'][0]['appId'] = 12
    elif mutation == 'creator': observation['checks'][1]['creator'] = 'fake'
    elif mutation == 'duplicate': observation['checks'].append(copy.deepcopy(observation['checks'][0]))
    elif mutation == 'oldhead': observation['checks'][0]['head'] = plan['localGit']['base']
    elif mutation == 'reviewer': observation['reviews'][0]['login'] = 'fake'
    elif mutation == 'dismissed': observation['reviews'][0]['state'] = 'DISMISSED'
    elif mutation == 'oldreview': observation['reviews'][0]['commit'] = plan['localGit']['base']
    else: observation['manualPassed'] = True
    assert release.assess_observation(tmp_path, change, plan, observation, now())['state'] == 'blocked'


def test_latest_pending_supersedes_earlier_success(tmp_path):
    change, plan, observation = fixture(tmp_path)
    newer = copy.deepcopy(observation['checks'][0])
    newer.update(id=5, status='queued', conclusion=None)
    observation['checks'].append(newer)
    assert 'pr_required_check_unpassed' in release.assess_observation(tmp_path, change, plan, observation, now())['reasons']


def test_smaller_id_running_rerun_blocks_and_timestamp_wins_completed(tmp_path):
    change, plan, observation = fixture(tmp_path)
    newer = copy.deepcopy(observation['checks'][0])
    observation['checks'][0]['id'] = 50
    newer.update(id=4, status='in_progress', conclusion=None, completedAt=None)
    observation['checks'].append(newer)
    assert release.assess_observation(tmp_path, change, plan, observation, now())['state'] == 'blocked'
    newer.update(status='completed', conclusion='failure', completedAt=now())
    assert 'pr_required_check_unpassed' in release.assess_observation(tmp_path, change, plan, observation, now())['reasons']


def test_status_timestamp_and_ambiguous_timestamp(tmp_path):
    change, plan, observation = fixture(tmp_path)
    newer = copy.deepcopy(observation['checks'][1])
    newer.update(id=1, status='failure', createdAt=now())
    observation['checks'].append(newer)
    assert release.assess_observation(tmp_path, change, plan, observation, now())['state'] == 'blocked'
    newer.update(status='success', createdAt=observation['checks'][1]['createdAt'])
    assert 'pr_check_ambiguous' in release.assess_observation(tmp_path, change, plan, observation, now())['reasons']


def test_comment_does_not_erase_approval_but_later_dismissal_does(tmp_path):
    change, plan, observation = fixture(tmp_path)
    comment = copy.deepcopy(observation['reviews'][0])
    comment.update(id=5, state='COMMENTED', submittedAt=now())
    observation['reviews'].append(comment)
    assert release.assess_observation(tmp_path, change, plan, observation, now())['state'] == 'allowed'
    comment['state'] = 'DISMISSED'
    assert 'pr_required_review_unapproved' in release.assess_observation(tmp_path, change, plan, observation, now())['reasons']


def test_old_review_does_not_block_new_current_head_approval(tmp_path):
    change, plan, observation = fixture(tmp_path)
    older = copy.deepcopy(observation['reviews'][0])
    older.update(id=1, commit=plan['localGit']['base'],
                 submittedAt=(dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=10)).isoformat())
    observation['reviews'].append(older)
    assert release.assess_observation(tmp_path, change, plan, observation, now())['state'] == 'allowed'


def test_packet_historical_integrity_and_binding_identity(tmp_path):
    from cc_development_lib.storage import identity
    with inputs.SafeRoot(tmp_path) as reader:
        project = identity(reader)
    binding = {'projectIdentity': project, 'changeId': 'change', 'environment': 'stage'}
    packet_id = 'release_packet_' + release.digest(binding)[:32]
    approval = release.digest({'binding': binding,
        'effect': 'Retain the reviewed release packet. No deployment or publication occurs.'})
    packet = {'schemaVersion': 1, 'id': packet_id, 'binding': binding, 'approvalId': approval,
              'createdAt': now(), 'status': 'prepared'}
    packet['integrity'] = release.digest(packet)
    put(tmp_path, release.PACKETS + '/' + packet_id + '.json', packet)
    assert release.packet_inspect(tmp_path, packet_id)['status'] == 'prepared'
    # Recomputing an ordinary JSON integrity field cannot authorize a new target.
    packet['binding']['environment'] = 'production'
    packet['integrity'] = release.digest({k: v for k, v in packet.items() if k != 'integrity'})
    put(tmp_path, release.PACKETS + '/' + packet_id + '.json', packet)
    with pytest.raises(inputs.EvidenceError, match='release_packet_approval_changed'):
        release.packet_inspect(tmp_path, packet_id)


@pytest.mark.parametrize('field', ['schemaVersion', 'appId'])
def test_boolean_numeric_identity_is_not_hosted_proof(tmp_path, field):
    change, plan, observation = fixture(tmp_path)
    if field == 'schemaVersion': observation[field] = True
    else: observation['checks'][0][field] = True
    assert release.assess_observation(tmp_path, change, plan, observation, now())['state'] == 'blocked'


def test_packet_prepare_uses_owned_core_transaction_and_replays(tmp_path, monkeypatch):
    import cc_development_lib as core
    from test_cc_development_core import created
    view = created(tmp_path)
    change = view['changes'][0]
    binding = {'projectIdentity': view['project_identity'], 'changeId': change['id'], 'environment': 'stage'}
    packet_id = 'release_packet_' + release.digest(binding)[:32]
    approval = release.digest({'binding': binding,
        'effect': 'Retain the reviewed release packet. No deployment or publication occurs.'})
    # PR/gate grading is separately tested; this boundary isolates durable Core
    # transaction ownership, canonical change lookup and idempotent retention.
    monkeypatch.setattr(release, 'packet_preview', lambda root, change, *args:
                        {'binding': binding, 'approval_id': approval, 'packetId': packet_id})
    before = core.read(tmp_path)['revision']
    result = release.packet_prepare(tmp_path, change, 'receipt', 'stage', 'a' * 64, approval)
    assert result['status'] == 'prepared'
    assert core.read(tmp_path)['revision'] == before
    assert release.packet_prepare(tmp_path, change, 'receipt', 'stage', 'a' * 64, approval)['replayed']
    assert not (tmp_path / '.controlcoding/development.lock').exists()


@pytest.mark.parametrize('repository', ['../repo', 'fixture/..', '-owner/repo'])
def test_invalid_provider_repository_rejected_before_worker(tmp_path, repository):
    change, _, _ = fixture(tmp_path)
    declaration = json.loads((tmp_path / release.POLICY).read_text())
    declaration['repository'] = repository
    put(tmp_path, release.POLICY, declaration)
    with pytest.raises(inputs.EvidenceError, match='pr_provider_request_invalid'):
        release.prepare_observation(tmp_path, change)


def test_sha256_provider_request_is_explicitly_unsupported(tmp_path, monkeypatch):
    change, _, _ = fixture(tmp_path)
    monkeypatch.setattr(release, 'local_git', lambda *args: {
        'base': 'a' * 64, 'head': 'b' * 64, 'tree': 'c' * 64,
        'changedFiles': ['app.py'], 'candidateDigest': change['candidate']['digest']})
    with pytest.raises(inputs.EvidenceError, match='pr_provider_request_invalid'):
        release.prepare_observation(tmp_path, change)


def test_packet_gate_does_not_assess_downstream_monitoring(tmp_path, monkeypatch):
    import cc_development_lib.evaluator as evaluator
    change = {'id': 'change', 'method': {'digest': 'method'}, 'candidate': {'digest': 'candidate', 'actor': 'implementer'},
              'decisions': [], 'reviews': [{'candidate_identity': 'candidate', 'actor': 'reviewer', 'findings': []}],
              'observations': [{'signals': [{'kind': 'monitor', 'receiptId': 'downstream-monitor'}]}]}
    artifact = {'revision': 'review-1', 'digest': 'artifact', 'content': {'environment': 'stage',
                'release_artifact_identity': 'a' * 64,
                'observation_provenance': {'mode': 'github_pr', 'receiptId': 'pr-receipt'}}}
    monkeypatch.setattr(evaluator, 'latest', lambda *args: artifact)
    monkeypatch.setattr(evaluator, 'accepted', lambda *args: True)
    monkeypatch.setattr(evaluator, 'assess_receipts', lambda *args: {'state': 'allowed'})
    def grade(reader, current, *, through_stage=None):
        # An unbounded evaluation would enter monitoring and re-enter packet
        # qualification through the monitor's authoritative release binding.
        assert current['observations'][0]['signals'][0]['kind'] == 'monitor'
        assert through_stage == 'review_release'
        return {'review_release': {'outcome': 'allowed'}}
    monkeypatch.setattr(evaluator, 'evaluate', grade)
    monkeypatch.setattr(release, '_canonical_observation', lambda *args: {
        'plan': {'projectIdentity': 'project', 'policyDigest': 'policy'},
        'assessment': {'localGit': {'head': 'head'}}, 'receiptDigest': 'receipt-digest'})
    result = release._packet_binding(tmp_path, change, 'pr-receipt', 'stage', 'a' * 64)
    assert result['artifactDigest'] == 'a' * 64


@pytest.mark.parametrize('offset', [-61, 60])
def test_expired_or_future_snapshot_blocks(tmp_path, offset):
    change, plan, observation = fixture(tmp_path)
    timestamp = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=offset)).isoformat()
    assert 'pr_observation_expired' in release.assess_observation(tmp_path, change, plan, observation, timestamp)['reasons']


def test_absent_policy_legacy_and_missing_selection(tmp_path):
    change = change_fixture(tmp_path)
    assert release.assessment(tmp_path, change) == {'state': 'allowed', 'reasons': [], 'required': False, 'policyDigest': None}
    _, _, _ = fixture(tmp_path)
    declaration = json.loads((tmp_path / release.POLICY).read_text())
    declaration['pullRequests'] = []
    put(tmp_path, release.POLICY, declaration)
    assert release.assessment(tmp_path, change)['state'] == 'blocked'


@pytest.mark.parametrize('mutation', ['uncommitted', 'extra', 'deleted', 'policy'])
def test_local_git_and_policy_drift_blocks(tmp_path, mutation):
    change, plan, observation = fixture(tmp_path)
    if mutation == 'uncommitted': (tmp_path / 'app.py').write_text('uncommitted')
    elif mutation == 'extra':
        (tmp_path / 'extra.py').write_text('extra')
        git(tmp_path, 'add', 'extra.py'); git(tmp_path, 'commit', '--quiet', '-m', 'extra')
    elif mutation == 'deleted':
        git(tmp_path, 'rm', '--quiet', 'app.py'); git(tmp_path, 'commit', '--quiet', '-m', 'delete')
    else:
        declaration = json.loads((tmp_path / release.POLICY).read_text())
        declaration['maxAgeSeconds'] = 50
        put(tmp_path, release.POLICY, declaration)
    assert release.assess_observation(tmp_path, change, plan, observation, now())['state'] == 'blocked'


def test_manual_selection_cannot_qualify(tmp_path):
    change, _, _ = fixture(tmp_path)
    change['artifacts']['review_release'] = [{'content': {'observation_provenance': 'I checked the PR'}}]
    assert 'pr_observation_selection_missing' in release.assessment(tmp_path, change)['reasons']


def test_packet_reports_absent_do_not_initialize(tmp_path):
    assert release.packet_reports(tmp_path, {'id': 'change'}) == []
    assert list(tmp_path.iterdir()) == []
