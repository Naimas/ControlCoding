"""Diagnostic wire negatives and actual bounded local adapter execution."""
import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cc_development_diagnosis as diagnosis
import cc_development_jobs as jobs
import cc_evidence_inputs as inputs
from test_cc_development_adapters import change_fixture, put


def request():
    from test_cc_development_operations import snapshot, incident
    from cc_development_lib.method import IDENTITY
    event = snapshot()
    retained = incident([event])
    value = {'schemaVersion': 1, 'incidentId': retained['id'], 'incidentRevision': retained['revision'],
             'parentChangeId': 'parent', 'candidateDigest': retained['group']['candidateDigest'],
             'binding': {'method': IDENTITY, 'group': retained['group'],
                         'configurationDigest': event['configurationDigest'], 'receiptDigest': event['receiptDigest']},
             'events': [{'id': event['id'], 'receiptId': event['receipt']['id'], 'capturedAt': event['capturedAt'],
                         'group': retained['group'], **event['facts'][0]}], 'sourceFiles': [],
             'limitations': ['Historical observations do not establish current health.',
                             'Diagnosis findings are hypotheses requiring independent verification.']}
    value['requestDigest'] = inputs.digest(value)
    return value


def output(req):
    return {'schemaVersion': 1, **{k: req[k] for k in ('requestDigest', 'incidentId', 'incidentRevision')},
            'outcome': 'reported', 'findings': [{'id': 'finding-1', 'statement': 'The declared check may explain the failure.',
             'confidence': 0.5, 'hypothesis': True, 'citations': [req['events'][0]['id']], 'proposedChecks': ['Independently reproduce the check.']}]}


@pytest.mark.parametrize('mutation', ['digest', 'incident', 'revision', 'uncited', 'unknown', 'claim', 'boolean', 'nan', 'checks', 'extra', 'abstained'])
def test_reject_unsupported_or_unbound_result(mutation):
    req = request()
    result = output(req)
    if mutation == 'digest': result['requestDigest'] = 'b' * 64
    elif mutation == 'incident': result['incidentId'] = 'other'
    elif mutation == 'revision': result['incidentRevision'] = 'd' * 64
    elif mutation == 'uncited': result['findings'][0]['citations'] = []
    elif mutation == 'unknown': result['findings'][0]['citations'] = ['invented']
    elif mutation == 'claim': result['findings'][0]['hypothesis'] = False
    elif mutation == 'boolean': result['findings'][0]['confidence'] = True
    elif mutation == 'nan': result['findings'][0]['confidence'] = float('nan')
    elif mutation == 'checks': result['findings'][0]['proposedChecks'] = []
    elif mutation == 'extra': result['action'] = 'apply'
    else: result['outcome'] = 'abstained'
    with pytest.raises(inputs.EvidenceError): diagnosis.assess_result(req, result)


def test_abstention_and_report_remain_unverified_hypotheses():
    req = request()
    assert diagnosis.assess_result(req, output(req))['causalCorrectnessVerified'] is False
    result = {**output(req), 'outcome': 'abstained', 'findings': []}
    assert diagnosis.assess_result(req, result)['outcome'] == 'abstained'


def test_real_operations_request_and_digit_prefixed_hashes(tmp_path, monkeypatch):
    import cc_development_operations as operations
    from test_cc_development_operations import snapshot, incident
    from cc_development_lib.method import IDENTITY
    event = snapshot()
    receipt = event['receipt']
    receipt['plan']['method'] = IDENTITY
    receipt['plan']['approval_id'] = inputs.digest({k: v for k, v in receipt['plan'].items() if k != 'approval_id'})
    receipt['approvalId'] = receipt['plan']['approval_id']
    jobs._seal(receipt)
    event['receiptDigest'] = inputs.digest(receipt)
    release_receipt = event['releaseReceipt']
    release_receipt['plan']['method'] = IDENTITY
    release_receipt['plan']['approval_id'] = inputs.digest({k: v for k, v in release_receipt['plan'].items() if k != 'approval_id'})
    release_receipt['approvalId'] = release_receipt['plan']['approval_id']
    jobs._seal(release_receipt)
    event['id'] = inputs.digest({k: v for k, v in event.items() if k != 'id'})
    retained = incident([event])
    raw = b'print("offline")\n'
    (tmp_path / 'app.py').write_bytes(raw)
    change = {'id': 'parent', 'method': IDENTITY, 'incidents': [retained], 'incident_decisions': [],
              'incident_captures': [retained['occurrences'][0]['snapshot']],
              'candidate': {'digest': retained['group']['candidateDigest'], 'files': [
                  {'path': 'app.py', 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}]}}
    monkeypatch.setattr(operations, 'candidate_current', lambda *a: {'state': 'allowed'})
    req = operations.diagnosis_request(tmp_path, change, retained['id'])
    assert diagnosis.assess_request(req)['state'] == 'allowed'
    req['incidentId'] = '0' * 64
    req['events'][0]['id'] = '1' * 64
    req['requestDigest'] = inputs.digest({k: v for k, v in req.items() if k != 'requestDigest'})
    assert diagnosis.assess_result(req, output(req))['state'] == 'allowed'
    req['incidentId'] = 'INC-1'
    req['requestDigest'] = inputs.digest({k: v for k, v in req.items() if k != 'requestDigest'})
    with pytest.raises(inputs.EvidenceError, match='diagnosis_request_invalid'):
        diagnosis.assess_request(req)


@pytest.mark.parametrize('mutation', ['extra', 'event_extra', 'bad_event', 'source_budget', 'limitations'])
def test_request_is_exact_and_bounded(mutation):
    req = request()
    if mutation == 'extra': req['action'] = 'repair'
    elif mutation == 'event_extra': req['events'][0]['action'] = 'repair'
    elif mutation == 'bad_event': req['events'][0]['id'] = 'event-1'
    elif mutation == 'limitations': req['limitations'] = []
    else:
        raw = b'x' * 8193
        req['sourceFiles'] = [{'path': 'source.py', 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                               'content': raw.decode(), 'reason': None}]
    req['requestDigest'] = inputs.digest({k: v for k, v in req.items() if k != 'requestDigest'})
    with pytest.raises(inputs.EvidenceError): diagnosis.assess_request(req)


def fixture(root, monkeypatch):
    import cc_development_operations as operations
    change = change_fixture(root)
    change['closed'] = True
    req = request()
    req['candidateDigest'] = change['candidate']['digest']
    req['binding']['group']['candidateDigest'] = req['candidateDigest']
    req['requestDigest'] = inputs.digest({k: v for k, v in req.items() if k != 'requestDigest'})
    monkeypatch.setattr(jobs, '_canonical_change', lambda *a, **kw: copy.deepcopy(change))
    monkeypatch.setattr(jobs, '_project_identity', lambda root: {'id': 'local-project'})
    monkeypatch.setattr(operations, 'diagnosis_request', lambda *args: copy.deepcopy(req))
    script = "import json,sys\nfrom pathlib import Path\nr=json.loads(Path(sys.argv[1]).read_text())\nprint(json.dumps({'schemaVersion':1,**{k:r[k] for k in ('requestDigest','incidentId','incidentRevision')},'outcome':'abstained','findings':[]}))\n"
    (root / 'diagnose.py').write_text(script)
    spec = {'id': 'diagnose', 'kind': 'diagnosis', 'argv': [sys.executable, '-B', 'diagnose.py', '{diagnosisRequest}'],
            'timeoutSeconds': 5, 'outputLimitBytes': 8192, 'files': [{'path': 'diagnose.py', 'sha256': hashlib.sha256((root / 'diagnose.py').read_bytes()).hexdigest()}],
            'configuration': {'incidentId': req['incidentId'], 'mode': 'deterministic'}}
    put(root, jobs.MANIFEST, {'schemaVersion': 1, 'adapters': [spec]})
    return change, {'kind': 'diagnosis', 'adapter_id': 'diagnose'}, spec, req


def test_actual_closed_source_diagnosis_replay_and_incident_drift(tmp_path, monkeypatch):
    change, value, spec, req = fixture(tmp_path, monkeypatch)
    plan = jobs.prepare(tmp_path, value, change)
    assert not (tmp_path / jobs.RECEIPTS).exists()
    result = jobs.execute(tmp_path, value, change, plan['approval_id'])
    assert result['state'] == 'allowed', result
    assert jobs.assess_job_observation(tmp_path, result, change, 'diagnosis')['state'] == 'allowed'
    assert jobs.execute(tmp_path, value, change, plan['approval_id'])['replayed']
    req['incidentRevision'] = 'd' * 64
    req['requestDigest'] = inputs.digest({k: v for k, v in req.items() if k != 'requestDigest'})
    assert jobs.assess_job_observation(tmp_path, result, change, 'diagnosis')['state'] != 'allowed'
    with pytest.raises(inputs.EvidenceError, match='preview_mismatch'):
        jobs.execute(tmp_path, value, change, plan['approval_id'])


def test_new_failed_diagnosis_never_revives_prior_success(tmp_path, monkeypatch):
    change, value, spec, req = fixture(tmp_path, monkeypatch)
    first = jobs.prepare(tmp_path, value, change)
    result = jobs.execute(tmp_path, value, change, first['approval_id'])
    (tmp_path / 'diagnose.py').write_text('raise SystemExit(1)\n')
    spec['files'][0]['sha256'] = hashlib.sha256((tmp_path / 'diagnose.py').read_bytes()).hexdigest()
    put(tmp_path, jobs.MANIFEST, {'schemaVersion': 1, 'adapters': [spec]})
    second = jobs.prepare(tmp_path, value, change)
    assert jobs.execute(tmp_path, value, change, second['approval_id'])['state'] == 'blocked'
    reports = diagnosis.reports(tmp_path, change)
    assert len(reports) == 2 and reports[-1]['current'] and reports[-1]['state'] != 'allowed'
    assert reports[0]['current'] is False
    assert jobs.assess_job_observation(tmp_path, result, change, 'diagnosis')['state'] != 'allowed'


def test_agent_requires_current_exact_configuration_before_command(tmp_path, monkeypatch):
    from cc_development_lib import policy
    change, value, spec, req = fixture(tmp_path, monkeypatch)
    put(tmp_path, 'configuration.json', {'model': 'offline-fixture'})
    raw = (tmp_path / 'configuration.json').read_bytes()
    spec['configuration'] = {'incidentId': req['incidentId'], 'mode': 'agent', 'evaluationId': 'eval-1', 'configurationPath': 'configuration.json'}
    spec['files'].append({'path': 'configuration.json', 'sha256': hashlib.sha256(raw).hexdigest()})
    put(tmp_path, jobs.MANIFEST, {'schemaVersion': 1, 'adapters': [spec]})
    monkeypatch.setattr(policy, 'automation_qualification', lambda *a: {'state': 'blocked', 'reasons': []})
    with pytest.raises(inputs.EvidenceError, match='diagnosis_configuration_unqualified'):
        jobs.prepare(tmp_path, value, change)
    assert not (tmp_path / jobs.RECEIPTS).exists()


def test_running_attempt_is_retained_and_cannot_replay(tmp_path, monkeypatch):
    change, value, spec, req = fixture(tmp_path, monkeypatch)
    plan = jobs.prepare(tmp_path, value, change)
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(jobs, 'run_command', interrupt)
    with pytest.raises(KeyboardInterrupt):
        jobs.execute(tmp_path, value, change, plan['approval_id'])
    report = diagnosis.reports(tmp_path, change)[0]
    assert report['current'] and report['attemptState'] == 'running' and report['state'] != 'allowed'
    with pytest.raises(inputs.EvidenceError, match='job_attempt_recovery_required'):
        jobs.execute(tmp_path, value, change, plan['approval_id'])


def test_success_of_another_incident_does_not_supersede_first(tmp_path, monkeypatch):
    change, value, spec, req = fixture(tmp_path, monkeypatch)
    first = jobs.execute(tmp_path, value, change, jobs.prepare(tmp_path, value, change)['approval_id'])
    # Both adapters were reviewed in one unchanged manifest, making receipt
    # currentness about the incident rather than global manifest drift.
    spec2 = copy.deepcopy(spec)
    spec2['id'] = 'second'
    spec2['configuration']['incidentId'] = '2' * 64
    put(tmp_path, jobs.MANIFEST, {'schemaVersion': 1, 'adapters': [spec, spec2]})
    first = jobs.execute(tmp_path, value, change, jobs.prepare(tmp_path, value, change)['approval_id'])
    import cc_development_operations as operations
    def select(root, change, incident):
        current = copy.deepcopy(req)
        current['incidentId'] = incident
        current['requestDigest'] = inputs.digest({k: v for k, v in current.items() if k != 'requestDigest'})
        return current
    monkeypatch.setattr(operations, 'diagnosis_request', select)
    second_value = {'kind': 'diagnosis', 'adapter_id': 'second'}
    second = jobs.execute(tmp_path, second_value, change, jobs.prepare(tmp_path, second_value, change)['approval_id'])
    assert jobs.assess_job_observation(tmp_path, first, change, 'diagnosis')['state'] == 'allowed'
    assert jobs.assess_job_observation(tmp_path, second, change, 'diagnosis')['state'] == 'allowed'


def test_public_prepare_rejects_lock_and_owned_report_propagates_lock(tmp_path, monkeypatch):
    canonical = jobs._canonical_change
    change, value, spec, req = fixture(tmp_path, monkeypatch)
    result = jobs.execute(tmp_path, value, change, jobs.prepare(tmp_path, value, change)['approval_id'])
    (tmp_path / '.controlcoding/development.lock').mkdir()
    monkeypatch.setattr(jobs, '_canonical_change', canonical)
    from cc_development_lib.schema import DevelopmentError
    with pytest.raises(DevelopmentError, match='recovery_required'):
        jobs.prepare(tmp_path, value, change)
    # Report-only trusted callers pass ownership of their existing transaction.
    # The Core integration regression exercises that caller with real state.
    seen = []
    def owned(root, change_id, *, locked=False, evaluate_gates=True):
        seen.append(locked)
        if not locked:
            raise inputs.EvidenceError('recovery_required')
        return copy.deepcopy(change)
    monkeypatch.setattr(jobs, '_canonical_change', owned)
    assert diagnosis.reports(tmp_path, change, locked=True)[0]['state'] == 'allowed'
    assert seen == [True]
    assert diagnosis.reports(tmp_path, change)[0]['state'] != 'allowed'

