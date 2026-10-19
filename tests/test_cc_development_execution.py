"""Qualified real local proposal workers; no model/provider requests."""
import hashlib
import json
from pathlib import Path
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cc_development_execution as execution
import cc_development_jobs as jobs
import cc_evidence_inputs as inputs
from test_cc_development_adapters import put
from test_cc_development_core import planned, artifact, decide, act
import cc_development_lib as development


def fixture(root, body="files=[{'path':'app.py','content':'improved\\n'}]", *, timeout=3, limit=8192, files=None):
    (root / 'app.py').write_text("print('application')\n", encoding='utf-8')
    planned(root, profile='routine')
    content = development.read(root)['changes'][0]['artifacts']['plan'][-1]['content']
    content['files'] = files or ['app.py']
    artifact(root, 'plan', content)
    decide(root, 'plan')
    change = act(root, {'kind': 'candidate', 'change_id': 'C1', 'files': ['app.py'], 'actor': 'implementation-agent'})['changes'][0]
    model = {'provider': 'fixture', 'id': 'local-worker', 'revision': '1'}
    worker = "import json,sys,time\nfrom pathlib import Path\nr=json.loads(Path(sys.argv[1]).read_text())\n" \
        + "if 'caseId' in r:\n Path('answer.txt').write_text('correct')\n files=None\nelse:\n " + body.replace('\n', '\n ') \
        + "\nout={'model':r['model'],'usage':{'costMicros':1,'inputTokens':2,'outputTokens':3}}\nif files is not None:out['files']=files\nprint(json.dumps(out))\n"
    (root / 'worker.py').write_text(worker)
    config = {'schemaVersion': 1, 'model': model, 'inputs': [], 'budgets': {
        'totalTimeoutSeconds': 3, 'caseTimeoutSeconds': 2, 'maxCostMicros': 10, 'maxInputTokens': 20, 'maxOutputTokens': 20}}
    suite = {'schemaVersion': 1, 'id': 'suite', 'version': '1', 'cases': [{'id': 'case1', 'prompt': 'answer', 'files': [],
              'checks': [{'kind': 'file_contains', 'path': 'answer.txt', 'text': 'correct'}]}]}
    put(root, 'config.json', config)
    put(root, 'suite.json', suite)
    pins = [{'path': p, 'sha256': hashlib.sha256((root / p).read_bytes()).hexdigest()} for p in ('worker.py', 'config.json', 'suite.json')]
    spec = {'id': 'qualified-worker', 'kind': 'agent_eval', 'argv': [sys.executable, '-B', 'worker.py', '{evalRequest}'],
            'timeoutSeconds': 4, 'outputLimitBytes': 8192, 'files': pins, 'configuration': {'evaluationVersion': 2,
            'implementationActor': 'implementation-agent', 'graderActor': 'independent-owner', 'suitePath': 'suite.json', 'configurationPath': 'config.json'}}
    put(root, jobs.MANIFEST, {'schemaVersion': 1, 'adapters': [spec]})
    value = {'kind': 'agent_eval', 'adapter_id': spec['id']}
    preview = jobs.prepare(root, value, change)
    result = jobs.execute(root, value, change, preview['approval_id'])
    assert result['state'] == 'allowed', result
    change = act(root, {'kind': 'evidence', 'change_id': 'C1', 'actor': 'independent-owner', 'records': [
        {'id': 'eval1', 'kind': 'agent_eval', 'receiptId': result['receiptId'], 'observation': result['observation']}]})['changes'][0]
    declaration = {'id': 'worker', 'kind': 'proposal_worker', 'argv': [sys.executable, '-B', 'worker.py', '{request}'],
        'files': pins, 'configurationPath': 'config.json', 'evaluationId': 'eval1', 'timeoutSeconds': timeout,
        'outputLimitBytes': limit, 'maxInputTokens': 20, 'maxOutputTokens': 20, 'attemptToken': 'attempt1'}
    put(root, execution.MANIFEST, {'schemaVersion': 1, 'adapters': [declaration]})
    return change


def test_actual_qualified_worker_proposal_replay_no_source_apply(tmp_path):
    change = fixture(tmp_path)
    before = (tmp_path / 'app.py').read_bytes()
    preview = execution.prepare(tmp_path, change, 'worker')
    assert not (tmp_path / execution.RECEIPTS).exists()
    result = execution.execute(tmp_path, change, 'worker', preview['approval_id'])
    assert result['state'] == 'allowed', result['reasons']
    assert (tmp_path / 'app.py').read_bytes() == before
    assert execution.execute(tmp_path, change, 'worker', preview['approval_id'])['replayed']
    validated = execution.validate_proposal(tmp_path, change, result['id'])
    assert validated['files'][0]['before_sha256'] == hashlib.sha256(before).hexdigest()
    assert validated['proposal']['files'][0]['content'] == 'improved\n'
    (tmp_path / 'app.py').write_text('after apply')
    with pytest.raises(inputs.EvidenceError):
        execution.read_receipt(tmp_path, result['id'], change)
    assert execution.read_receipt(tmp_path, result['id'], change, current=False)['state'] == 'allowed'


@pytest.mark.parametrize('body,reason', [
    ("files=[{'path':'outside.py','content':'x'}]", 'execution_output_out_of_scope'),
    ("files=[{'path':'.git/config','content':'x'}]", 'execution_protected_scope'),
    ("time.sleep(4);files=[]", 'execution_timeout'),
    ("print('x'*9000);files=[]", 'execution_output_limit'),
    ("raise SystemExit(7)", 'execution_command_failed'),
    ("r['model']['id']='spoof';files=[]", 'execution_output_invalid'),
])
def test_failures_are_retained_and_never_applied(tmp_path, body, reason):
    change = fixture(tmp_path, body, timeout=1)
    plan = execution.prepare(tmp_path, change, 'worker')
    result = execution.execute(tmp_path, change, 'worker', plan['approval_id'])
    assert result['state'] == 'blocked'
    assert reason in result['reasons'], result['reasons']
    assert (tmp_path / 'app.py').read_text() == "print('application')\n"


def test_unqualified_argv_variant_rejected(tmp_path):
    change = fixture(tmp_path)
    declaration = json.loads((tmp_path / execution.MANIFEST).read_text())
    declaration['adapters'][0]['argv'].insert(1, '-u')
    put(tmp_path, execution.MANIFEST, declaration)
    with pytest.raises(inputs.EvidenceError, match='execution_argv_unqualified'):
        execution.prepare(tmp_path, change, 'worker')


def test_missing_qualification_and_old_approval_block(tmp_path):
    change = fixture(tmp_path)
    plan = execution.prepare(tmp_path, change, 'worker')
    (tmp_path / 'app.py').write_text('changed candidate\n', encoding='utf-8')
    change = act(tmp_path, {'kind': 'candidate', 'change_id': 'C1', 'files': ['app.py'], 'actor': 'implementation-agent'})['changes'][0]
    with pytest.raises(inputs.EvidenceError, match='execution_configuration_unqualified'):
        execution.execute(tmp_path, change, 'worker', plan['approval_id'])


def test_source_changed_during_worker_blocks(tmp_path):
    body = "Path(" + repr(str(tmp_path / 'app.py')) + ").write_text('external drift');files=[{'path':'app.py','content':'good'}]"
    change = fixture(tmp_path, body)
    plan = execution.prepare(tmp_path, change, 'worker')
    result = execution.execute(tmp_path, change, 'worker', plan['approval_id'])
    assert result['state'] == 'blocked'
    assert 'execution_inputs_changed' in result['reasons']


def test_cancel_owned_child_and_no_implicit_retry(tmp_path):
    change = fixture(tmp_path, "time.sleep(10);files=[]", timeout=15)
    plan = execution.prepare(tmp_path, change, 'worker')
    result = {}
    def run():
        result.update(execution.execute(tmp_path, change, 'worker', plan['approval_id']))
    thread = threading.Thread(target=run)
    thread.start()
    receipt_id = 'execution_' + inputs.digest(['execution-v1', plan['approval_id']])[:32]
    deadline = time.monotonic() + 4
    while not (tmp_path / execution._relative(tmp_path, receipt_id)).exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    cancellation = execution.cancel_prepare(tmp_path, receipt_id)
    assert execution.cancel(tmp_path, receipt_id, cancellation['approval_id'])['state'] == 'cancellation_requested'
    thread.join(5)
    assert not thread.is_alive()
    assert 'execution_cancelled' in result['reasons'], result
    assert execution.execute(tmp_path, change, 'worker', plan['approval_id'])['replayed']


def test_receipt_tamper_rejected(tmp_path):
    change = fixture(tmp_path)
    plan = execution.prepare(tmp_path, change, 'worker')
    result = execution.execute(tmp_path, change, 'worker', plan['approval_id'])
    path = tmp_path / execution._relative(tmp_path, result['id'])
    value = json.loads(path.read_text())
    value['proposal']['files'][0]['content'] = 'tamper'
    path.write_text(json.dumps(value))
    with pytest.raises(inputs.EvidenceError, match='execution_receipt_changed'):
        execution.read_receipt(tmp_path, result['id'], change)


def test_new_planned_file_absent_baseline(tmp_path):
    change = fixture(tmp_path, "files=[{'path':'new.py','content':'new source'}]", files=['app.py', 'new.py'])
    plan = execution.prepare(tmp_path, change, 'worker')
    result = execution.execute(tmp_path, change, 'worker', plan['approval_id'])
    assert result['state'] == 'allowed', result['reasons']
    assert not (tmp_path / 'new.py').exists()
    row = execution.validate_proposal(tmp_path, change, result['id'])['files'][0]
    assert row['before_sha256'] is None and row['before_size'] == 0


def test_usage_budget_and_negative_usage_fail_closed(tmp_path):
    change = fixture(tmp_path)
    declaration = json.loads((tmp_path / execution.MANIFEST).read_text())
    declaration['adapters'][0]['maxOutputTokens'] = 1
    put(tmp_path, execution.MANIFEST, declaration)
    plan = execution.prepare(tmp_path, change, 'worker')
    result = execution.execute(tmp_path, change, 'worker', plan['approval_id'])
    assert result['state'] == 'blocked'
    assert 'execution_token_budget_exceeded' in result['reasons']


def test_postapply_accessor_rejects_qualification_result_tamper(tmp_path):
    change = fixture(tmp_path)
    plan = execution.prepare(tmp_path, change, 'worker')
    result = execution.execute(tmp_path, change, 'worker', plan['approval_id'])
    qualification = json.loads((tmp_path / plan['qualificationReceiptPath']).read_text())
    workspace = qualification['observation']['workspace']
    (tmp_path / workspace / 'case1' / 'answer.txt').write_text('corrupted')
    with pytest.raises(inputs.EvidenceError, match='execution_qualification_invalidated'):
        execution.read_receipt(tmp_path, result['id'], change, current=False)


@pytest.mark.parametrize('path', ['templates/hooks/a.py', 'cc/.controlcoding/a.json', 'controlcoding.verification.json', 'AGENTS.md'])
def test_protected_plan_context_never_copied(tmp_path, path):
    change = fixture(tmp_path)
    content = change['artifacts']['plan'][-1]['content']
    content['files'].append(path)
    if path.startswith('cc/.controlcoding/'):
        with pytest.raises(development.DevelopmentError, match='managed_source_path'):
            artifact(tmp_path, 'plan', content)
        assert not (tmp_path / execution.RECEIPTS).exists()
        return
    artifact(tmp_path, 'plan', content)
    decide(tmp_path, 'plan')
    change = development.read(tmp_path)['changes'][0]
    reason = 'execution_protected_path' if path == 'controlcoding.verification.json' else 'execution_protected_scope'
    with pytest.raises(inputs.EvidenceError, match=reason):
        execution.prepare(tmp_path, change, 'worker')
    assert not (tmp_path / execution.RECEIPTS).exists()


def test_historical_inspection_survives_stale_manifest_without_qualification(tmp_path):
    change = fixture(tmp_path)
    plan = execution.prepare(tmp_path, change, 'worker')
    result = execution.execute(tmp_path, change, 'worker', plan['approval_id'])
    assert result['state'] == 'allowed'
    declaration = json.loads((tmp_path / execution.MANIFEST).read_text())
    declaration['adapters'][0]['attemptToken'] = 'new-reviewed-attempt'
    put(tmp_path, execution.MANIFEST, declaration)
    with pytest.raises(inputs.EvidenceError, match='execution_manifest_changed'):
        execution.read_receipt(tmp_path, result['id'], change, current=False)
    historical = execution.inspect_receipt(tmp_path, result['id'], change)
    assert historical['state'] == 'allowed' and historical['historical'] is True
    assert historical['currentAssessment']['qualification'] is False
    assert 'execution_manifest_changed' in historical['currentAssessment']['reasons']
    assert execution.inspect_receipt(tmp_path, result['id'], change['id'])['currentAssessment']['state'] == 'not_assessed'
