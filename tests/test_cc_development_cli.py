"""Exercise the actual CLI and isolated panel boundary, including stale writes."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import cc_panel_bridge as bridge
from cc_development_lib import read


def cli(root, *arguments):
    process = subprocess.run(
        [sys.executable, '-B', str(SCRIPTS / 'cc.py'), 'development', *arguments,
         '--project-root', str(root), '--json'],
        cwd=root, capture_output=True, timeout=30,
        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
    )
    assert not process.stderr, process.stderr.decode(errors='replace')
    return process.returncode, json.loads(process.stdout)


def call(root, operation, **extra):
    output = io.BytesIO()
    request = {'version': 1, 'id': 'development-test', 'operation': operation,
               'project_root': str(root), **extra}
    bridge.serve(io.BytesIO(json.dumps(request).encode()), output)
    return json.loads(output.getvalue())


def test_status_and_method_are_read_only_in_real_cli(tmp_path):
    source = tmp_path / 'main.py'
    source.write_text('PRIVATE_CANARY = 1\n')
    for command in ('status', 'method'):
        code, value = cli(tmp_path, command)
        assert code == 0 and value['enabled'] is False
        assert 'PRIVATE_CANARY' not in json.dumps(value)
    assert list(tmp_path.iterdir()) == [source]


def test_checkpoint_stale_and_history_cursor_errors_keep_specific_cli_codes(tmp_path):
    assert cli(tmp_path, 'init', '--profile', 'standard')[0] == 0
    assert cli(tmp_path, 'create', 'CH-01', '--title', 'First', '--owner', 'Owner')[0] == 0
    code, preview = cli(tmp_path, 'checkpoint-preview')
    assert code == 0
    assert cli(tmp_path, 'create', 'CH-02', '--title', 'Second', '--owner', 'Owner')[0] == 0
    revision = read(tmp_path)['revision']
    code, failed = cli(tmp_path, 'checkpoint', '--revision', preview['revision'],
                       '--approval-id', preview['approval_id'], '--actor', 'Owner')
    assert code == 1 and failed['error']['code'] == 'checkpoint_preview_stale'
    code, failed = cli(tmp_path, 'history', 'CH-01', '--cursor', 'invalid-json')
    assert code == 1 and failed['error']['code'] == 'history_cursor_invalid'
    assert read(tmp_path)['revision'] == revision


def test_cli_history_cursor_roundtrip_reads_next_original_event(tmp_path):
    assert cli(tmp_path, 'init', '--profile', 'standard')[0] == 0
    assert cli(tmp_path, 'create', 'CH-01', '--title', 'First', '--owner', 'Owner')[0] == 0
    # The original creation remains the sole event. A revision-bound cursor
    # at its end must return an empty page without changing project state.
    code, first = cli(tmp_path, 'history', 'CH-01', '--limit', '1')
    assert code == 0 and len(first['records']) == 1
    cursor = json.dumps({'revision': first['revision'], 'offset': 1})
    code, last = cli(tmp_path, 'history', 'CH-01', '--cursor', cursor, '--limit', '1')
    assert code == 0 and last['records'] == [] and last['cursor'] is None


def test_incident_preview_and_diagnosis_do_not_initialize_or_trust_claims(tmp_path):
    action = {'kind': 'incident_capture', 'change_id': 'CH-01',
              'receipt_id': 'development_job_' + 'a' * 32, 'signal_id': 'M1',
              'actor': 'Reviewer', 'reason': 'Review canonical receipt'}
    action_file = tmp_path / 'action.json'
    action_file.write_text(json.dumps(action))
    code, result = cli(tmp_path, 'preview', '--action-file', str(action_file))
    assert code == 1 and result['blockers'] == ['method_not_active']
    action['observation'] = {'state': 'allowed'}
    action_file.write_text(json.dumps(action))
    code, result = cli(tmp_path, 'preview', '--action-file', str(action_file))
    assert code == 1 and result['error']['code'] == 'invalid_action_fields'
    code, result = cli(tmp_path, 'job-preview', 'CH-01', '--kind', 'diagnosis', '--adapter-id', 'reader')
    assert code == 1 and result['error']['code'] == 'unknown_change'
    assert read(tmp_path)['revision'] == 'absent'
    assert list(tmp_path.iterdir()) == [action_file]


def test_explicit_cli_activation_and_duplicate_change(tmp_path):
    code, result = cli(tmp_path, 'init', '--profile', 'standard')
    assert code == 0 and result['view']['enabled']
    arguments = ('create', 'CH-01', '--title', 'Improve a flow', '--owner', 'maintainer')
    code, result = cli(tmp_path, *arguments)
    assert code == 0
    assert result['view']['changes'][0]['id'] == 'CH-01'
    before = read(tmp_path)['revision']
    code, result = cli(tmp_path, *arguments)
    assert code == 1 and result['error']['code'] == 'change_id_exists'
    assert read(tmp_path)['revision'] == before


def test_release_packet_commands_are_read_only_until_valid_approval(tmp_path):
    from test_cc_development_core import ready_for_release
    ready_for_release(tmp_path)
    before = read(tmp_path)['revision']
    code, result = cli(tmp_path, 'release-preview', 'C1', '--observation-id', 'development_job_' + 'a' * 32,
                       '--environment', 'local', '--artifact-digest', 'b' * 64)
    assert code == 1 and result['error']['code'] == 'release_packet_approval_missing'
    code, result = cli(tmp_path, 'release-status', 'C1', '--packet-id', 'release_packet_' + 'a' * 32)
    assert code == 1 and result['state'] == 'blocked'
    assert read(tmp_path)['revision'] == before
    assert not (tmp_path / '.controlcoding/development_release_packets').exists()


def test_preview_apply_and_idempotent_retry_through_real_cli(tmp_path):
    action = {'kind': 'activate', 'profile': 'routine'}
    action_file = tmp_path / 'activation.json'
    action_file.write_text(json.dumps(action))
    code, preview = cli(tmp_path, 'preview', '--action-file', str(action_file))
    assert code == 0 and preview['revision'] == 'absent'
    assert read(tmp_path)['enabled'] is False
    arguments = ('apply', '--action-file', str(action_file), '--revision', preview['revision'],
                 '--approval-id', preview['approval_id'])
    code, result = cli(tmp_path, *arguments)
    assert code == 0 and not result['receipt']['idempotent']
    first_revision = result['view']['revision']
    code, result = cli(tmp_path, *arguments)
    assert code == 0 and result['receipt']['idempotent']
    assert result['view']['revision'] == first_revision


@pytest.mark.parametrize('raw', [b'{PRIVATE_CANARY', b'{"kind":"activate","kind":"create"}',
                                b' ' * 65537, b'[]', b'\xff'],
                         ids=['malformed', 'duplicate-key', 'oversized', 'array', 'invalid-encoding'])
def test_cli_rejects_invalid_action_files_without_initialization(tmp_path, raw):
    action_file = tmp_path / 'request.json'
    action_file.write_bytes(raw)
    code, result = cli(tmp_path, 'preview', '--action-file', str(action_file))
    assert code == 1 and result['error']['code'] == 'invalid_action_file'
    assert 'PRIVATE_CANARY' not in json.dumps(result)
    assert read(tmp_path)['revision'] == 'absent'


def test_gate_exit_status_does_not_confuse_artifact_with_approval(tmp_path):
    assert cli(tmp_path, 'init')[0] == 0
    assert cli(tmp_path, 'create', 'CH-01', '--title', 'Repair', '--owner', 'maintainer')[0] == 0
    code, value = cli(tmp_path, 'check', 'CH-01', '--stage', 'plan')
    assert code == 1 and value['gate']['outcome'] != 'allowed'


def test_panel_reads_without_writes_and_uses_exact_preview(tmp_path):
    result = call(tmp_path, 'development_read_v1')
    assert result['status'] == 'ok' and result['result']['development']['revision'] == 'absent'
    assert not list(tmp_path.iterdir())
    action = {'kind': 'activate', 'profile': 'routine'}
    preview = call(tmp_path, 'development_preview_v1', action=action, revision='absent')
    assert preview['status'] == 'ok'
    approved = preview['result']['development_preview']
    assert not list(tmp_path.iterdir())
    result = call(tmp_path, 'development_commit_v1', action=action, revision='absent',
                  approval_id=approved['approval_id'])
    assert result['status'] == 'ok' and result['result']['development']['enabled']
    assert result['result']['development_receipt']['idempotent'] is False


def test_panel_rejects_stale_preview_and_unknown_payload_fields(tmp_path):
    action = {'kind': 'activate', 'profile': 'routine'}
    preview = call(tmp_path, 'development_preview_v1', action=action, revision='absent')['result']['development_preview']
    cli(tmp_path, 'init', '--profile', 'critical')
    result = call(tmp_path, 'development_commit_v1', action=action, revision='absent',
                  approval_id=preview['approval_id'])
    assert result['status'] == 'error' and result['error']['code'] == 'revision_conflict'
    result = call(tmp_path, 'development_read_v1', command='PRIVATE_CANARY')
    assert result['status'] == 'error' and 'PRIVATE_CANARY' not in json.dumps(result)


def test_real_isolated_panel_process_reads_development(tmp_path):
    request = {'version': 1, 'id': 'isolated', 'operation': 'development_read_v1',
               'project_root': str(tmp_path)}
    process = subprocess.run([sys.executable, '-I', '-B', str(SCRIPTS / 'cc_panel_bridge.py')],
                             input=json.dumps(request).encode(), capture_output=True,
                             cwd=tmp_path, timeout=20)
    assert process.returncode == 0 and not process.stderr
    result = json.loads(process.stdout)
    assert result['status'] == 'ok' and result['result']['development']['enabled'] is False
    assert not list(tmp_path.iterdir())


def test_default_verification_contract_includes_development_when_present(tmp_path):
    import cc
    tests = tmp_path / 'tests'
    tests.mkdir()
    (tests / 'test_cc_development_core.py').write_text('')
    policy = cc._default_verification_contract(tmp_path)
    suite = next(item for item in policy['suites'] if item['id'] == 'controlled-development-regression')
    assert suite['required'] and 'test_cc_development_core.py' in suite['command']


def test_backup_restores_same_project_history_and_rejects_other_project(tmp_path):
    from cc_development_archive import backup, prepare_restore, restore
    from cc_development_lib import DevelopmentError
    assert cli(tmp_path, 'init')[0] == 0
    assert cli(tmp_path, 'create', 'CH-01', '--title', 'Preserve history', '--owner', 'maintainer')[0] == 0
    archive = backup(tmp_path)
    before = read(tmp_path)
    registry = tmp_path / '.controlcoding' / 'development.json'
    registry.unlink()
    preview = prepare_restore(tmp_path, archive, 'maintainer', 'Restore a known backup')
    result = restore(tmp_path, archive, 'maintainer', 'Restore a known backup', preview['approval_id'])
    assert result['view']['changes'] == before['changes']
    assert result['view']['revision'] == before['revision']
    with pytest.raises(DevelopmentError, match='restore_requires_absent_state'):
        prepare_restore(tmp_path, archive, 'maintainer', 'Cannot overwrite')
    foreign = tmp_path / 'other-project'
    foreign.mkdir()
    with pytest.raises(DevelopmentError, match='archive_identity_invalid'):
        prepare_restore(foreign, archive, 'maintainer', 'Wrong project')
    assert not list(foreign.iterdir())


def test_archive_tampering_cannot_restore_approved_state(tmp_path):
    from cc_development_archive import backup, prepare_restore
    from cc_development_lib import DevelopmentError
    assert cli(tmp_path, 'init')[0] == 0
    archive = backup(tmp_path)
    (tmp_path / '.controlcoding' / 'development.json').unlink()
    archive['manifest']['method']['version'] = 'forged'
    with pytest.raises(DevelopmentError, match='archive_identity_invalid'):
        prepare_restore(tmp_path, archive, 'maintainer', 'Invalid backup')
    assert read(tmp_path)['revision'] == 'absent'


def test_cli_recovers_actual_terminated_writer_and_keeps_history(tmp_path):
    assert cli(tmp_path, 'init')[0] == 0
    worker = tmp_path / 'interrupted-writer.py'
    worker.write_text(
        'import os,sys\n'
        + 'sys.path.insert(0, ' + repr(str(SCRIPTS)) + ')\n'
        + 'from cc_evidence_inputs import SafeRoot\n'
        + 'from cc_development_lib import storage\n'
        + 'with SafeRoot(' + repr(str(tmp_path)) + ') as root:\n'
        + '    with storage.transaction(root, "interrupted-fixture"):\n'
        + '        os._exit(17)\n', encoding='utf-8')
    process = subprocess.run([sys.executable, '-B', str(worker)], capture_output=True, timeout=20)
    assert process.returncode == 17 and not process.stderr
    code, result = cli(tmp_path, 'status')
    assert code == 1 and result['error']['code'] == 'recovery_required'
    code, preview = cli(tmp_path, 'recovery-preview', '--actor', 'maintainer', '--reason', 'Observed terminated worker')
    assert code == 0 and not preview['blockers']
    action_file = tmp_path / 'recovery-action.json'
    action_file.write_text(json.dumps(preview['action']))
    code, result = cli(tmp_path, 'apply', '--action-file', str(action_file), '--revision', preview['revision'],
                       '--approval-id', preview['approval_id'])
    assert code == 0 and result['view']['enabled']
    assert result['receipt']['action']['kind'] == 'recover'
    assert not (tmp_path / '.controlcoding' / 'development.lock').exists()


def test_cli_eval_check_is_read_only_and_cannot_qualify_a_missing_evaluation(tmp_path):
    assert cli(tmp_path, 'init')[0] == 0
    assert cli(tmp_path, 'create', 'EVAL-1', '--title', 'Evaluate', '--owner', 'Owner')[0] == 0
    before = {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    code, report = cli(tmp_path, 'eval-check', 'EVAL-1', '--evaluation-id', 'eval-missing',
                       '--configuration-digest', 'a' * 64)
    assert code == 1 and report['state'] == 'blocked'
    assert report['autonomous_execution'] is False
    assert report['reasons'] == ['agent_configuration_not_evaluated']
    after = {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert after == before


def test_cli_runs_and_checks_actual_independently_graded_configuration(tmp_path):
    from test_cc_development_core import planned, act
    from test_cc_development_adapters import evaluation_v2_fixture
    planned(tmp_path)
    (tmp_path / 'source.py').write_text('value = 1\n')
    view = act(tmp_path, {'kind': 'candidate', 'change_id': 'C1', 'files': ['source.py'], 'actor': 'Writer'})
    evaluation_v2_fixture(tmp_path, view['changes'][0])
    code, preview = cli(tmp_path, 'job-preview', 'C1', '--kind', 'agent_eval', '--adapter-id', 'evaluation-2')
    assert code == 0
    code, result = cli(tmp_path, 'job-run', 'C1', '--kind', 'agent_eval', '--adapter-id', 'evaluation-2',
                       '--approval-id', preview['approval_id'])
    assert code == 0 and result['state'] == 'allowed'
    act(tmp_path, {'kind': 'evidence', 'change_id': 'C1', 'actor': 'Reviewer', 'records': [
        {'id': 'EVAL-01', 'kind': 'agent_eval', 'receiptId': result['receiptId'], 'observation': result['observation']}]})
    configuration = preview['evaluationPlan']['configDigest']
    code, checked = cli(tmp_path, 'eval-check', 'C1', '--evaluation-id', 'EVAL-01', '--configuration-digest', configuration)
    assert code == 0 and checked['evaluation']['qualification'] is True
    assert checked['evaluation']['evaluationPlan']['requiredCases'] == ['case-1', 'case-2']
    assert checked['autonomous_execution'] is False
    (tmp_path / 'skill.txt').write_text('Changed skill after qualification', encoding='utf-8')
    code, stale = cli(tmp_path, 'eval-check', 'C1', '--evaluation-id', 'EVAL-01', '--configuration-digest', configuration)
    assert code == 1 and stale['state'] == 'blocked'


def execution_project(root):
    """A real Core registry with canonically evaluated proposal worker."""
    import hashlib
    from test_cc_development_core import planned, act
    import cc_development_jobs as jobs
    import cc_development_execution as execution
    planned(root)
    (root / 'source.py').write_text('before\n')
    view = act(root, {'kind': 'candidate', 'change_id': 'C1', 'files': ['source.py'], 'actor': 'Writer'})
    worker = "import json,pathlib,sys\nr=json.loads(pathlib.Path(sys.argv[1]).read_text())\nout={'model':r['model'],'usage':{'costMicros':1,'inputTokens':2,'outputTokens':3}}\nif 'caseId' in r:pathlib.Path('answer.txt').write_text('correct')\nelse:out['files']=[{'path':'source.py','content':'after\\n'}]\nprint(json.dumps(out))\n"
    (root / 'worker.py').write_text(worker, encoding='utf-8')
    config = {'schemaVersion': 1, 'model': {'provider': 'local-fixture', 'id': 'proposal-worker', 'revision': '1'},
              'inputs': [], 'budgets': {'totalTimeoutSeconds': 5, 'caseTimeoutSeconds': 3,
              'maxCostMicros': 10, 'maxInputTokens': 20, 'maxOutputTokens': 20}}
    suite = {'schemaVersion': 1, 'id': 'representative', 'version': '1', 'cases': [
        {'id': 'case-1', 'prompt': 'Write the answer', 'files': [],
         'checks': [{'kind': 'file_contains', 'path': 'answer.txt', 'text': 'correct'}]}]}
    for name, value in [('config.json', config), ('suite.json', suite)]:
        (root / name).write_text(json.dumps(value), encoding='utf-8')
    pins = [{'path': p, 'sha256': hashlib.sha256((root / p).read_bytes()).hexdigest()}
            for p in ('worker.py', 'config.json', 'suite.json')]
    evaluation = {'id': 'evaluation', 'kind': 'agent_eval', 'argv': [sys.executable, '-B', 'worker.py', '{evalRequest}'],
                  'files': pins, 'timeoutSeconds': 5, 'outputLimitBytes': 8192,
                  'configuration': {'evaluationVersion': 2, 'implementationActor': 'Writer', 'graderActor': 'Reviewer',
                                    'suitePath': 'suite.json', 'configurationPath': 'config.json'}}
    (root / jobs.MANIFEST).write_text(json.dumps({'schemaVersion': 1, 'adapters': [evaluation]}), encoding='utf-8')
    choice = {'kind': 'agent_eval', 'adapter_id': 'evaluation'}
    preview = jobs.prepare(root, choice, view['changes'][0])
    result = jobs.execute(root, choice, view['changes'][0], preview['approval_id'])
    assert result['state'] == 'allowed'
    view = act(root, {'kind': 'evidence', 'change_id': 'C1', 'actor': 'Reviewer', 'records': [
        {'id': 'EVAL-1', 'kind': 'agent_eval', 'receiptId': result['receiptId'], 'observation': result['observation']}]})
    adapter = {'id': 'worker', 'kind': 'proposal_worker', 'argv': [sys.executable, '-B', 'worker.py', '{request}'],
               'files': pins, 'configurationPath': 'config.json', 'evaluationId': 'EVAL-1',
               'timeoutSeconds': 5, 'outputLimitBytes': 8192, 'maxInputTokens': 20, 'maxOutputTokens': 20,
               'attemptToken': 'attempt-1'}
    (root / execution.MANIFEST).write_text(json.dumps({'schemaVersion': 1, 'adapters': [adapter]}), encoding='utf-8')
    return view['changes'][0]


def test_cli_qualified_execution_review_apply_current_build_and_source_drift(tmp_path):
    from test_cc_development_core import act, artifact
    execution_project(tmp_path)
    before = {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    code, preview = cli(tmp_path, 'execution-preview', 'C1', '--adapter-id', 'worker')
    assert code == 0
    assert before == {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    code, run = cli(tmp_path, 'execution-run', 'C1', '--adapter-id', 'worker', '--approval-id', preview['approval_id'])
    assert code == 0 and run['state'] == 'allowed', run
    assert (tmp_path / 'source.py').read_text() == 'before\n'
    code, review = cli(tmp_path, 'proposal-preview', 'C1', '--execution-id', run['id'])
    assert code == 0, review
    assert '+after' in review['files'][0]['diff']
    code, applied = cli(tmp_path, 'proposal-apply', 'C1', '--execution-id', run['id'], '--approval-id', review['approval_id'])
    assert code == 0 and applied['state'] == 'applied', applied
    view = act(tmp_path, {'kind': 'candidate', 'change_id': 'C1', 'files': ['source.py'], 'actor': 'Writer'})
    view = act(tmp_path, {'kind': 'evidence', 'change_id': 'C1', 'actor': 'Reviewer', 'records': [
        {'id': 'EXEC-1', 'kind': 'execution', 'receiptId': run['id']}]})
    change = view['changes'][0]
    assert change['execution_reports'][0]['application']['state'] == 'allowed', change['execution_reports']
    view = artifact(tmp_path, 'build', {'approved_plan_revision': change['artifacts']['plan'][-1]['revision'],
             'source_identity': change['candidate']['digest'], 'deviations': [], 'changed_files': ['source.py'],
             'execution_provenance': {'mode': 'controlled_proposal', 'execution_id': run['id'], 'apply_id': run['id']}})
    assert view['changes'][0]['gates']['build']['outcome'] == 'allowed', view['changes'][0]['gates']['build']
    assert view['changes'][0]['outcomes']['verified'] is False
    code, status = cli(tmp_path, 'execution-status', 'C1', '--execution-id', run['id'])
    assert code == 0 and status['application']['state'] == 'allowed'
    # The current application proof includes the separately reviewed project
    # write policy, even though it is not a worker-configuration pin.
    policy = tmp_path / 'cc_config.json'
    assert not policy.exists()
    for level in ('deny', 'warn'):
        policy.write_text(json.dumps({'protected_zones': [{'path': 'source.py', 'level': level}]}))
        code, gate = cli(tmp_path, 'check', 'C1', '--stage', 'build')
        assert code == 1 and gate['gate']['outcome'] != 'allowed'
        code, status = cli(tmp_path, 'execution-status', 'C1', '--execution-id', run['id'])
        assert status['application']['state'] == 'blocked', status
        code, retry = cli(tmp_path, 'proposal-apply', 'C1', '--execution-id', run['id'], '--approval-id', review['approval_id'])
        assert code == 1, retry
        policy.unlink()
        code, gate = cli(tmp_path, 'check', 'C1', '--stage', 'build')
        assert code == 0 and gate['gate']['outcome'] == 'allowed'
    (tmp_path / 'source.py').write_text('foreign\n')
    code, gate = cli(tmp_path, 'check', 'C1', '--stage', 'build')
    assert code == 1 and gate['gate']['outcome'] != 'allowed'
