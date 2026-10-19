"""Independent richer worker qualification: real bytes, no fabricated receipt."""
import hashlib
import json

import pytest

from test_cc_development_eval import fixture
import cc_development_eval as evaluation
from cc_evidence_inputs import EvidenceError


def qualified_detail_worker(root):
    from test_cc_product_planning_detail import planned
    from test_cc_development_core import act
    import cc_development_lib as core
    import cc_development_adapters as adapters
    import cc_development_jobs as jobs
    import cc_development_execution as execution
    import sys
    (root / 'accounts.py').write_text('original authorization policy\n', encoding='utf-8')
    planned(root)
    change = act(root, {'kind': 'candidate', 'change_id': 'C1', 'files': ['accounts.py'], 'actor': 'Author'})['changes'][0]
    packet = adapters.manual_handoff(root, change)
    worker = """import json,sys
from pathlib import Path
r=json.loads(Path(sys.argv[1]).read_text())
assert r['schemaVersion']==2
assert r['planningBinding']['contract']=='detailed_contract_v1'
value=r['planningDetail']['task_details'][0]['deliverable']
if 'caseId' in r:
 Path('answer.txt').write_text('correct')
 Path('detail.txt').write_text(value,encoding='utf-8')
 files=None
else:
 assert value in r['prompt']
 files=[{'path':'accounts.py','content':value+'\\n'}]
out={'model':r['model'],'usage':{'costMicros':1,'inputTokens':2,'outputTokens':3}}
if files is not None:out['files']=files
print(json.dumps(out))
"""
    (root / 'worker.py').write_text(worker, encoding='utf-8')
    model = {'provider': 'fixture', 'id': 'detailed-worker', 'revision': '1'}
    config = {'schemaVersion': 1, 'model': model, 'inputs': [], 'budgets': {
        'totalTimeoutSeconds': 5, 'caseTimeoutSeconds': 2, 'maxCostMicros': 10, 'maxInputTokens': 20, 'maxOutputTokens': 20}}
    suite = {'schemaVersion': 2, 'id': 'detail-support', 'version': '1', 'cases': [
        {'id': 'consume-detail', 'prompt': 'Read the structured task deliverable and persist it.', 'files': [],
         'checks': [{'kind': 'file_contains', 'path': 'answer.txt', 'text': 'correct'}],
         'planningDetail': packet['planningDetail'], 'planningBinding': packet['planningBinding'],
         'detail_behavior': {'task_id': 'design-policy', 'field': 'deliverable', 'path': 'detail.txt'}}]}
    for name, value in [('config.json', config), ('suite.json', suite)]:
        (root / name).write_text(json.dumps(value), encoding='utf-8')
    pins = [{'path': p, 'sha256': hashlib.sha256((root / p).read_bytes()).hexdigest()}
            for p in ('worker.py', 'config.json', 'suite.json')]
    spec = {'id': 'detail-eval', 'kind': 'agent_eval', 'argv': [sys.executable, '-B', 'worker.py', '{evalRequest}'],
        'timeoutSeconds': 5, 'outputLimitBytes': 8192, 'files': pins, 'configuration': {'evaluationVersion': 2,
        'implementationActor': 'Author', 'graderActor': 'Independent', 'suitePath': 'suite.json', 'configurationPath': 'config.json'}}
    (root / jobs.MANIFEST).write_text(json.dumps({'schemaVersion': 1, 'adapters': [spec]}), encoding='utf-8')
    value = {'kind': 'agent_eval', 'adapter_id': spec['id']}
    preview = jobs.prepare(root, value, change)
    observed = jobs.execute(root, value, change, preview['approval_id'])
    assert observed['state'] == 'allowed', observed
    change = act(root, {'kind': 'evidence', 'change_id': 'C1', 'actor': 'Independent', 'records': [
        {'id': 'detail-eval-record', 'kind': 'agent_eval', 'receiptId': observed['receiptId'], 'observation': observed['observation']}]})['changes'][0]
    adapter = {'id': 'detail-worker', 'kind': 'proposal_worker', 'argv': [sys.executable, '-B', 'worker.py', '{request}'],
        'files': pins, 'configurationPath': 'config.json', 'evaluationId': 'detail-eval-record', 'timeoutSeconds': 3,
        'outputLimitBytes': 8192, 'maxInputTokens': 20, 'maxOutputTokens': 20, 'attemptToken': 'detail-attempt'}
    (root / execution.MANIFEST).write_text(json.dumps({'schemaVersion': 1, 'adapters': [adapter]}), encoding='utf-8')
    return change, packet


def detail_fixture(root, *, consume=True):
    body = "Path('answer.txt').write_text('correct')\n"
    if consume:
        body += "assert r['schemaVersion']==2\nPath('detail.txt').write_text(r['planningDetail']['task_details'][0]['deliverable'],encoding='utf-8')"
    spec, _ = fixture(root, body)
    suite_path = root / 'suite.json'
    suite = json.loads(suite_path.read_text())
    suite['schemaVersion'] = 2
    suite['version'] = 'pw04-1'
    case = suite['cases'][0]
    case['planningDetail'] = {'schema_version': 1, 'contract': 'detailed_contract_v1',
        'task_details': [{'task_id': 'secure', 'deliverable': 'Reject unauthenticated access after restart. è'}]}
    case['planningBinding'] = {'contract': 'detailed_contract_v1',
        **{k: str(i) * 64 for i, k in enumerate(('profile_revision', 'selection_revision', 'artifact_revision',
            'detail_revision', 'source_manifest_digest'), 1)}, 'upstream_detail_revision': 'absent'}
    case['detail_behavior'] = {'task_id': 'secure', 'field': 'deliverable', 'path': 'detail.txt'}
    suite_path.write_text(json.dumps(suite), encoding='utf-8')
    for pin in spec['files']:
        pin['sha256'] = hashlib.sha256((root / pin['path']).read_bytes()).hexdigest()
    return spec, evaluation.prepare(root, spec, {'candidate': {'actor': 'implementer'}})


def test_a12_actual_worker_consumes_richer_detail_with_independent_expected_bytes(tmp_path):
    spec, plan = detail_fixture(tmp_path)
    result = evaluation.execute(tmp_path, plan, spec['argv'], receipt_id='detail')
    assert result['status'] == 'passed', result
    assert evaluation.assess(plan, result, root=tmp_path)['state'] == 'allowed'
    request = json.loads((tmp_path / result['workspace'] / 'case0' / 'request.json').read_text())
    assert request['planningDetail']['task_details'][0]['deliverable'].endswith('è')
    assert 'checks' not in request and 'detail_behavior' not in request
    observed = tmp_path / result['workspace'] / 'case0' / 'detail.txt'
    assert observed.read_text(encoding='utf-8') == request['planningDetail']['task_details'][0]['deliverable']
    observed.write_text('Substituted old request output', encoding='utf-8')
    assert evaluation.assess(plan, result, root=tmp_path)['state'] == 'blocked'


def test_a12_old_worker_success_cannot_qualify_detailed_request(tmp_path):
    spec, plan = detail_fixture(tmp_path, consume=False)
    result = evaluation.execute(tmp_path, plan, spec['argv'], receipt_id='old-worker')
    assert result['status'] == 'failed'
    assert 'eval_independent_check_failed' in result['cases'][0]['reasons']
    assert evaluation.assess(plan, result, root=tmp_path)['state'] == 'blocked'


@pytest.mark.parametrize('field', ['planningBinding', 'detail_behavior'])
def test_a12_v2_qualification_rejects_omitted_contract_fields(tmp_path, field):
    spec, _ = detail_fixture(tmp_path)
    path = tmp_path / 'suite.json'
    suite = json.loads(path.read_text())
    del suite['cases'][0][field]
    path.write_text(json.dumps(suite), encoding='utf-8')
    for pin in spec['files']:
        pin['sha256'] = hashlib.sha256((tmp_path / pin['path']).read_bytes()).hexdigest()
    with pytest.raises(EvidenceError, match='eval_case_invalid'):
        evaluation.prepare(tmp_path, spec, {'candidate': {'actor': 'implementer'}})


def test_a11_a12_real_approved_worker_output_and_gateway_reject_detail_only_drift(tmp_path):
    import copy
    import cc_development_execution as execution
    import cc_development_gateway as gateway
    import cc_development_lib as core
    from test_cc_development_core import act
    from test_cc_product_planning_detail import plan_action, accept
    change, packet = qualified_detail_worker(tmp_path)
    before = (tmp_path / 'accounts.py').read_bytes()
    preview = execution.prepare(tmp_path, change, 'detail-worker')
    result = execution.execute(tmp_path, change, 'detail-worker', preview['approval_id'])
    assert result['state'] == 'allowed', result
    assert result['proposal']['files'][0]['content'] == packet['planningDetail']['task_details'][0]['deliverable'] + '\n'
    assert (tmp_path / 'accounts.py').read_bytes() == before
    reviewed = gateway.prepare(tmp_path, change, result['id'])
    assert reviewed['state'] == 'allowed'
    stale = copy.deepcopy(plan_action(tmp_path))
    stale['details']['task_details'][0]['approach'] += ' Explicitly preserve security boundary on recovery.'
    act(tmp_path, stale)
    accept(tmp_path, 'C1', 'plan')
    current = core.read(tmp_path)['changes'][0]
    assert current['artifacts']['plan'][-1]['digest'] == change['artifacts']['plan'][-1]['digest']
    with pytest.raises(Exception) as failure:
        gateway.prepare(tmp_path, current, result['id'])
    assert 'planning' in getattr(failure.value, 'code', str(failure.value)) or 'upstream' in getattr(failure.value, 'code', str(failure.value))
    assert (tmp_path / 'accounts.py').read_bytes() == before


def test_a11_a12_retained_evaluation_rejects_same_base_detail_change_after_reacceptance(tmp_path):
    import copy
    import cc_development_jobs as jobs
    import cc_development_lib as core
    from test_cc_development_core import act
    from test_cc_product_planning_detail import plan_action, accept
    change, _ = qualified_detail_worker(tmp_path)
    record = next(record for batch in change['evidence'] for record in batch['records']
                  if record['id'] == 'detail-eval-record')
    assessed = jobs.assess_job_observation(tmp_path, record, change, 'agent_eval')
    assert assessed['state'] == 'allowed' and assessed['qualification'], assessed
    changed = copy.deepcopy(plan_action(tmp_path))
    changed['details']['task_details'][0]['approach'] += ' Preserve reviewed security on recovery.'
    act(tmp_path, changed)
    accept(tmp_path, 'C1', 'plan')
    current = core.read(tmp_path)['changes'][0]
    assert current['candidate']['digest'] == change['candidate']['digest']
    assert current['artifacts']['plan'][-1]['digest'] == change['artifacts']['plan'][-1]['digest']
    stale = jobs.assess_job_observation(tmp_path, record, current, 'agent_eval')
    assert stale['state'] != 'allowed' and not stale.get('qualification', False)
    assert 'job_planning_detail_stale' in stale['reasons'], stale
