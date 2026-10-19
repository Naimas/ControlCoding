"""Operational snapshots retain historical facts without mutable-state reads."""
import copy
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cc_development_operations as operations
import cc_development_jobs as jobs
import cc_evidence_inputs as inputs


def snapshot(value=12, receipt_id='development_job_' + 'a' * 32):
    configuration = {'environment': 'staging', 'deployedDigest': 'd' * 64, 'window': 'hour', 'metrics': [
        {'id': 'latency', 'baseline': 5, 'maxDelta': 3, 'minimumSamples': 2}]}
    observation = {'deployedDigest': 'd' * 64, 'window': 'hour', 'metrics': {
        'latency': {'value': value, 'samples': 4, 'citation': 'metrics/source#row'}}}
    plan = {'configuration': configuration, 'changeId': 'parent', 'candidateDigest': 'c' * 64}
    plan['approval_id'] = inputs.digest(plan)
    receipt = jobs._seal({'schemaVersion': 1, 'id': receipt_id, 'kind': 'monitor', 'state': 'allowed', 'provenance': 'observed_command',
        'createdAt': '2026-10-02T10:00:00+00:00', 'finishedAt': '2026-10-02T10:01:00+00:00',
        'plan': plan, 'approvalId': plan['approval_id'], 'observation': observation,
        'observationDigest': inputs.digest(observation), 'command': {'status': 'passed', 'returnCode': 0, 'error': None}})
    release_observation = {'candidateDigest': 'c' * 64, 'environment': 'staging', 'deployedDigest': 'd' * 64, 'status': 'deployed'}
    release = {'status': 'observed', 'environment': 'staging', 'artifact_digest': 'd' * 64,
               'observation': {'receiptId': 'release_receipt', 'observation': release_observation}}
    assessment = {'state': 'allowed', 'provenance': 'observed_command', 'receiptId': 'release_receipt', 'observation': release_observation}
    release_receipt = copy.deepcopy(receipt)
    release_receipt.update({'id': 'release_receipt', 'kind': 'deploy', 'observation': release_observation,
                            'observationDigest': inputs.digest(release_observation)})
    release_receipt['plan']['configuration'] = {'environment': 'staging', 'mode': 'live'}
    release_receipt['plan'].pop('approval_id')
    release_receipt['approvalId'] = inputs.digest(release_receipt['plan'])
    release_receipt['plan']['approval_id'] = release_receipt['approvalId']
    jobs._seal(release_receipt)
    group = {'releaseId': inputs.digest(release), 'releaseReceiptId': 'release_receipt', 'candidateDigest': 'c' * 64,
             'deployedDigest': 'd' * 64, 'environment': 'staging', 'window': 'hour'}
    result = {'capturedAt': receipt['finishedAt'], 'parentChangeId': 'parent', 'signalId': 'signal1', 'receipt': receipt,
              'receiptDigest': inputs.digest(receipt), 'configurationDigest': inputs.digest(configuration),
              'release': release, 'releaseAssessment': assessment, 'releaseReceipt': release_receipt,
              'group': group, 'facts': [operations._fact(receipt, 'latency')]}
    result['id'] = inputs.digest(result)
    return result


def incident(events):
    group = {**events[0]['group'], 'metric': 'latency'}
    events = [{'snapshot': e, 'fact': e['facts'][0]} for e in events]
    return {'id': inputs.digest({'parentChangeId': 'parent', 'group': group}), 'parentChangeId': 'parent',
            'group': group, 'occurrences': events, 'revision': inputs.digest({'group': group, 'occurrences': events})}


def test_snapshot_local_validation_and_forged_fact():
    event = snapshot()
    assert operations.validate_observation(event) == event
    forged = copy.deepcopy(event)
    forged['facts'][0]['value'] = 999
    forged['id'] = inputs.digest({k: v for k, v in forged.items() if k != 'id'})
    with pytest.raises(inputs.EvidenceError, match='incident_fact_changed'):
        operations.validate_observation(forged)


def test_nested_release_receipt_tampering_and_foreign_method_fail_closed():
    event = snapshot()
    event['releaseReceipt']['observation']['status'] = 'failed'
    event['id'] = inputs.digest({k: v for k, v in event.items() if k != 'id'})
    with pytest.raises(inputs.EvidenceError, match='job_receipt_integrity_changed'):
        operations.validate_observation(event)
    event = snapshot()
    change = {'id': 'parent', 'method': {'digest': 'different'}, 'incident_captures': [event],
              'incidents': operations.incidents_from_captures([event])}
    with pytest.raises(inputs.EvidenceError, match='incident_method_changed'):
        operations.validate_change(change)


def test_unknown_or_insufficient_monitor_cannot_be_incident():
    event = snapshot()
    event['receipt']['observation']['metrics']['latency']['samples'] = 0
    with pytest.raises(inputs.EvidenceError):
        operations._fact(event['receipt'], 'latency')
    with pytest.raises(inputs.EvidenceError, match='incident_occurrence_mismatch'):
        operations.validate_incident(incident([snapshot(7)]))


def test_duplicate_receipt_and_recurrence_reopens_decision():
    event = snapshot()
    first = incident([event])
    with pytest.raises(inputs.EvidenceError, match='incident_occurrence_duplicate'):
        operations.validate_incident(incident([event, event]))
    second = snapshot(receipt_id='development_job_' + 'b' * 32)
    repeated = incident([event, second])
    change = {'id': 'parent', 'incidents': [repeated], 'incident_captures': [event, second], 'incident_decisions': [{
        'incident_id': first['id'], 'incident_revision': first['revision'], 'decision': 'resolved', 'child_id': None,
        'owner': 'operator', 'actor': 'operator', 'reason': 'Reviewed observation', 'timestamp': '2026-10-02T12:00:00+00:00'}]}
    report = operations.reports(change)[0]
    assert report['recurrence_count'] == 2
    assert report['current_decision'] is False
    assert operations.incident_view(change, repeated['id'])['disposition'] == 'open'


def test_replay_retains_breach_after_new_healthy_observation(monkeypatch):
    event = snapshot()
    retained = incident([event])
    change = {'id': 'parent', 'incidents': [retained], 'incident_captures': [event, snapshot(7, 'development_job_' + 'b' * 32)]}
    monkeypatch.setattr(operations, 'read_json', lambda *args: pytest.fail('historical replay consulted current source'))
    result = operations.capture('unused', change, event['receipt']['id'], 'signal1', '2026-10-02T12:00:00+00:00')
    assert result['replay'] and result['incidents'][0]['revision'] == retained['revision']


def test_closed_source_intent_and_snapshot_bound():
    retained = incident([snapshot()])
    from cc_development_lib.schema import artifact
    artifact('intent', operations.proposed_intent(retained))
    oversized = snapshot()
    oversized['receipt']['padding'] = 'x' * operations.MAX_SNAPSHOT_BYTES
    with pytest.raises(inputs.EvidenceError, match='incident_snapshot_too_large'):
        operations.validate_observation(oversized)


def capture_fixture(monkeypatch, event=None):
    event = event or snapshot()
    receipt = event['receipt']
    change = {'id': 'parent', 'candidate': {'digest': 'c' * 64}, 'releases': [event['release']], 'incidents': []}
    monkeypatch.setattr(operations, 'read_json', lambda root, relative: (
        copy.deepcopy(event['releaseReceipt'] if relative.endswith('/release_receipt.json') else receipt), 'digest'))
    monkeypatch.setattr(jobs, 'assess_job_observation', lambda *args: {
        'state': 'allowed', 'configuration': receipt['plan']['configuration'], 'observation': receipt['observation']})
    monkeypatch.setattr(jobs, '_attempts', lambda *args: [receipt])
    monkeypatch.setattr(operations, 'assess_release', lambda *args: event['releaseAssessment'])
    return event, change


@pytest.mark.parametrize('environment', [None, 'another-environment'])
def test_canonical_capture_requires_explicit_matching_monitor_environment(monkeypatch, environment):
    event = snapshot()
    receipt = event['receipt']
    if environment is None:
        receipt['plan']['configuration'].pop('environment')
    else:
        receipt['plan']['configuration']['environment'] = environment
    receipt['plan'].pop('approval_id')
    receipt['approvalId'] = inputs.digest(receipt['plan'])
    receipt['plan']['approval_id'] = receipt['approvalId']
    jobs._seal(receipt)
    event['configurationDigest'] = inputs.digest(receipt['plan']['configuration'])
    event['receiptDigest'] = inputs.digest(receipt)
    event['id'] = inputs.digest({k: v for k, v in event.items() if k != 'id'})
    with pytest.raises(inputs.EvidenceError, match='incident_release_binding_invalid'):
        operations.validate_observation(event)
    event, change = capture_fixture(monkeypatch, event)
    with pytest.raises(inputs.EvidenceError, match='incident_environment_mismatch'):
        operations.capture(Path.cwd(), change, receipt['id'], 'signal', 'preview')


def test_capture_all_metrics_deterministic_preview_and_healthy(monkeypatch):
    event = snapshot()
    receipt = event['receipt']
    receipt['plan']['configuration']['metrics'].append({'id': 'errors', 'baseline': 0, 'maxDelta': 1, 'minimumSamples': 1})
    receipt['observation']['metrics']['errors'] = {'value': 4, 'samples': 2, 'citation': 'error/source'}
    approval = {k: v for k, v in receipt['plan'].items() if k != 'approval_id'}
    receipt['plan']['approval_id'] = receipt['approvalId'] = inputs.digest(approval)
    receipt['observationDigest'] = inputs.digest(receipt['observation'])
    jobs._seal(receipt)
    event, change = capture_fixture(monkeypatch, event)
    preview = operations.capture(Path.cwd(), change, receipt['id'], 'signal-user-label', 'preview')
    committed = operations.capture(Path.cwd(), change, receipt['id'], 'signal-user-label', '2026-10-02T12:00:00+00:00')
    assert preview == committed
    assert len(preview['incidents']) == 2
    assert {i['group']['metric'] for i in preview['incidents']} == {'latency', 'errors'}
    event, change = capture_fixture(monkeypatch, snapshot(7))
    assert operations.capture(Path.cwd(), change, event['receipt']['id'], 'healthy', 'preview')['incidents'] == []


def test_newer_failed_monitor_and_stale_release_block_capture(monkeypatch):
    event, change = capture_fixture(monkeypatch)
    newer = copy.deepcopy(event['receipt'])
    newer['id'] = 'development_job_' + 'b' * 32
    newer['state'] = 'running'
    monkeypatch.setattr(jobs, '_attempts', lambda *args: [event['receipt'], newer])
    with pytest.raises(inputs.EvidenceError, match='incident_monitor_superseded'):
        operations.capture(Path.cwd(), change, event['receipt']['id'], 'signal', 'preview')
    monkeypatch.setattr(operations, 'assess_release', lambda *args: {'state': 'unknown'})
    with pytest.raises(inputs.EvidenceError, match='incident_release_stale'):
        operations.capture(Path.cwd(), change, event['receipt']['id'], 'signal', 'preview')


def test_replay_different_signal_cannot_create_second_occurrence():
    event = snapshot()
    change = {'id': 'parent', 'incidents': [incident([event])], 'incident_captures': [event]}
    with pytest.raises(inputs.EvidenceError, match='incident_receipt_already_captured'):
        operations.capture('unused', change, event['receipt']['id'], 'renamed', 'preview')


@pytest.mark.parametrize('status,artifact', [('prepared', 'd' * 64), ('self_reported', 'd' * 64), ('observed', 'e' * 64)])
def test_capture_never_falls_back_to_older_observed_release(monkeypatch, status, artifact):
    event, change = capture_fixture(monkeypatch)
    change['releases'].append({**event['release'], 'status': status, 'artifact_digest': artifact})
    with pytest.raises(inputs.EvidenceError, match='incident_latest_release_unobserved'):
        operations.capture(Path.cwd(), change, event['receipt']['id'], 'signal', 'preview')


def test_latest_other_environment_cannot_reuse_old_assessment(monkeypatch):
    event, change = capture_fixture(monkeypatch)
    change['releases'].append({**event['release'], 'environment': 'production'})
    with pytest.raises(inputs.EvidenceError, match='incident_environment_mismatch'):
        operations.capture(Path.cwd(), change, event['receipt']['id'], 'signal', 'preview')


def test_projection_cannot_drop_breach_or_invent_occurrence():
    event = snapshot()
    change = {'id': 'parent', 'incident_captures': [event], 'incidents': []}
    with pytest.raises(inputs.EvidenceError, match='incident_projection_changed'):
        operations.validate_change(change)
    change['incidents'] = operations.incidents_from_captures([event])
    assert operations.validate_change(change)
    change['incidents'][0]['occurrences'].append(copy.deepcopy(change['incidents'][0]['occurrences'][0]))
    with pytest.raises(inputs.EvidenceError):
        operations.validate_change(change)


def test_preview_decision_timestamp_never_validates_persisted_state():
    event = snapshot()
    retained = incident([event])
    decision = {'incident_id': retained['id'], 'incident_revision': retained['revision'], 'decision': 'deferred',
                'owner': 'owner', 'actor': 'actor', 'reason': 'Need another observation', 'timestamp': 'preview', 'child_id': None}
    change = {'id': 'parent', 'incident_captures': [event], 'incidents': [retained], 'incident_decisions': [decision]}
    assert operations.reports(change, allow_preview=True)[0]['current_decision']
    with pytest.raises(ValueError):
        operations.validate_change(change)


def test_diagnosis_request_bounded_exact_sources_and_historical_facts(monkeypatch):
    event = snapshot()
    retained = incident([event])
    raw = b'print("source")\n'
    source = {'path': 'app.py', 'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
    change = {'id': 'parent', 'candidate': {'digest': 'c' * 64, 'files': [source]},
              'incident_captures': [event], 'incidents': [retained]}
    class Reader:
        def __init__(self, root):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, path, budget):
            return raw, None
        def validate(self):
            pass
    monkeypatch.setattr(inputs, 'SafeRoot', Reader)
    monkeypatch.setattr(operations, 'candidate_current', lambda *args: {'state': 'allowed'})
    request = operations.diagnosis_request('unused', change, retained['id'])
    assert request['sourceFiles'][0]['content'] == raw.decode()
    assert request['events'][0]['id'] == event['id']
    assert request['requestDigest'] == inputs.digest({k: v for k, v in request.items() if k != 'requestDigest'})
    source['sha256'] = 'f' * 64
    with pytest.raises(inputs.EvidenceError, match='diagnosis_source_changed'):
        operations.diagnosis_request('unused', change, retained['id'])
    monkeypatch.setattr(operations, 'candidate_current', lambda *args: {'state': 'blocked'})
    with pytest.raises(inputs.EvidenceError, match='diagnosis_candidate_stale'):
        operations.diagnosis_request('unused', change, retained['id'])
