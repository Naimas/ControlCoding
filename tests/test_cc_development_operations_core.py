"""Canonical operational feedback using real Git and native pinned local jobs."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cc
import cc_development_jobs as jobs
import cc_development_lib as development
from cc_development_lib.method import digest
from test_cc_development_core import act, artifact, decide, ready_for_release


def _git(root, *args):
    return subprocess.check_output(['git', '-c', 'core.autocrlf=false', '-c', 'core.hooksPath=', *args],
                                   cwd=root, stderr=subprocess.STDOUT).decode().strip()


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def _change(root, change_id='C1'):
    return next(c for c in development.read(root)['changes'] if c['id'] == change_id)


def _job(root, kind, adapter_id):
    change = _change(root)
    choice = {'kind': kind, 'adapter_id': adapter_id}
    preview = jobs.prepare(root, choice, change)
    receipt = jobs.execute(root, choice, change, preview['approval_id'])
    assert receipt['state'] == 'allowed', receipt
    return receipt


def monitor(root, value=12):
    (root / 'dist/metric.json').write_text(json.dumps({'value': value}), encoding='utf-8')
    return _job(root, 'monitor', 'monitor')


def capture(root, receipt, signal_id='M1'):
    return act(root, {'kind': 'incident_capture', 'change_id': 'C1', 'receipt_id': receipt['receiptId'],
                     'signal_id': signal_id, 'actor': 'Operator', 'reason': 'Review the canonical local observation'})


def incident_decision(root, incident_id, decision='deferred'):
    return act(root, {'kind': 'incident_decide', 'change_id': 'C1', 'incident_id': incident_id,
                     'decision': decision, 'owner': 'Operator', 'actor': 'Operator', 'reason': 'Reviewed retained occurrence'})


def followup_action(incident_id, child_id='C2'):
    return {'kind': 'incident_followup', 'change_id': 'C1', 'incident_id': incident_id, 'child_id': child_id,
            'title': 'Investigate observed latency', 'owner': 'Maintainer', 'risk': 'standard',
            'actor': 'Operator', 'reason': 'Review linked follow-up intent'}


def build_fixture(root):
    """Exportable offline fixture; no models, network, or deployed application."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    assert not list(root.iterdir()), 'Fixture destination must be empty'
    _git(root, 'init', '--quiet')
    _git(root, 'config', 'user.name', 'Offline fixture')
    _git(root, 'config', 'user.email', 'fixture@example.invalid')
    (root / '.gitignore').write_text('.controlcoding/\ndist/\n', encoding='utf-8')
    (root / 'source.py').write_text('value = 0\n', encoding='utf-8')
    _git(root, 'add', 'source.py', '.gitignore')
    _git(root, 'commit', '--quiet', '-m', 'Offline fixture base')
    base = _git(root, 'rev-parse', 'HEAD')
    (root / 'source.py').write_text('value = 1\n', encoding='utf-8')
    _git(root, 'add', 'source.py')
    _git(root, 'commit', '--quiet', '-m', 'Offline fixture candidate')
    (root / 'status.py').write_text("import json,sys,subprocess\nhead=subprocess.check_output(['git','rev-parse','HEAD']).decode().strip()\nprint(json.dumps({'candidateDigest':sys.argv[1],'deployedDigest':sys.argv[2],'environment':'local','status':'deployed','base':sys.argv[3],'head':head}))\n", encoding='utf-8')
    (root / 'monitor.py').write_text("import json,sys\nfrom pathlib import Path\nvalue=json.loads(Path('dist/metric.json').read_text())['value']\nprint(json.dumps({'candidateDigest':sys.argv[1],'deployedDigest':sys.argv[2],'window':'hour','metrics':{'latency':{'value':value,'samples':4,'citation':'offline-fixture:latency/hour'}}}))\n", encoding='utf-8')
    (root / 'diagnose.py').write_text("import json,sys\nfrom pathlib import Path\nr=json.loads(Path(sys.argv[1]).read_text())\nprint(json.dumps({'schemaVersion':1,**{k:r[k] for k in ('requestDigest','incidentId','incidentRevision')},'outcome':'abstained','findings':[]}))\n", encoding='utf-8')
    (root / 'dist').mkdir()
    (root / 'dist/metric.json').write_text('{"value":12}', encoding='utf-8')
    candidate = ready_for_release(root)
    def spec(name, kind, argv, configuration):
        return {'id': name, 'kind': kind, 'argv': [sys.executable, '-B', name + '.py', *argv],
                'timeoutSeconds': 10, 'outputLimitBytes': 8192,
                'files': [{'path': name + '.py', 'sha256': hashlib.sha256((root / (name + '.py')).read_bytes()).hexdigest()}],
                'configuration': configuration}
    adapters = [spec('status', 'release_status', ['{candidateDigest}', '{artifactDigest}', base],
                     {'environment': 'local', 'artifactDigest': candidate}),
                spec('monitor', 'monitor', ['{candidateDigest}', candidate],
                     {'environment': 'local', 'deployedDigest': candidate, 'window': 'hour',
                      'metrics': [{'id': 'latency', 'baseline': 5, 'maxDelta': 3, 'minimumSamples': 2}]})]
    _write_json(root / jobs.MANIFEST, {'schemaVersion': 1, 'adapters': adapters})
    # The final owner checks observe every fixed program and manifest byte.
    for run in (cc.cmd_verify_run, cc.cmd_invariants_run):
        with contextlib.redirect_stdout(io.StringIO()):
            assert run(root, json_output=True) == 0
    decide(root, 'review_release', 'ReleaseOwner')
    observed = _job(root, 'release_status', 'status')
    view = act(root, {'kind': 'release', 'change_id': 'C1', 'environment': 'local', 'artifact_digest': candidate,
                      'status': 'observed', 'adapter_kind': 'release_status', 'observation': observed,
                      'actor': 'ReleaseOwner', 'reason': 'Offline native Git-backed observation'})
    assert view['changes'][0]['outcomes']['released_current_candidate'], view['changes'][0]['release_assessment']
    return {'root': str(root), 'candidate': candidate, 'release_receipt': observed['receiptId'],
            'coverage': 'Real local Git/native jobs; no real application deployment, model or network call'}


def diagnose(root, incident_id):
    manifest_path = root / jobs.MANIFEST
    declaration = json.loads(manifest_path.read_text())
    declaration['adapters'].append({'id': 'diagnose', 'kind': 'diagnosis',
        'argv': [sys.executable, '-B', 'diagnose.py', '{diagnosisRequest}'], 'timeoutSeconds': 10, 'outputLimitBytes': 8192,
        'files': [{'path': 'diagnose.py', 'sha256': hashlib.sha256((root / 'diagnose.py').read_bytes()).hexdigest()}],
        'configuration': {'incidentId': incident_id, 'mode': 'deterministic'}})
    _write_json(manifest_path, declaration)
    for run in (cc.cmd_verify_run, cc.cmd_invariants_run):
        with contextlib.redirect_stdout(io.StringIO()):
            assert run(root, json_output=True) == 0
    return _job(root, 'diagnosis', 'diagnose')


def build_closed_fixture(root):
    """Close from healthy evidence, then capture a genuine breach on that parent."""
    root = Path(root)
    result = build_fixture(root)
    capture(root, monitor(root, 7))
    artifact(root, 'maintain', {'deployed_artifact_identity': result['candidate'], 'observation_scope': 'Offline local fixture',
             'observation_window': 'hour', 'signals': ['M1'], 'findings': [], 'dispositions': [], 'followup_owners': ['Operator']})
    decide(root, 'maintain', 'Operator')
    act(root, {'kind': 'close', 'change_id': 'C1', 'actor': 'Operator', 'reason': 'Reviewed healthy local observation'})
    receipt = monitor(root)
    view = capture(root, receipt)
    assert view['changes'][0]['closed']
    result.update(monitor_receipt=receipt['receiptId'], incident_id=view['changes'][0]['incidents'][0]['id'])
    return result


def test_canonical_breach_replay_recurrence_and_healthy_history(tmp_path):
    build_fixture(tmp_path)
    receipt = monitor(tmp_path)
    first = capture(tmp_path, receipt)['changes'][0]
    incident = first['operational_incidents'][0]
    assert incident['recurrence_count'] == 1 and not incident['current_decision']
    original_revision = incident['revision']
    replay = capture(tmp_path, receipt)['changes'][0]
    assert len(replay['incident_captures']) == 1
    assert replay['operational_incidents'][0]['revision'] == original_revision
    incident_decision(tmp_path, incident['id'])
    repeated = capture(tmp_path, monitor(tmp_path))['changes'][0]['operational_incidents'][0]
    assert repeated['recurrence_count'] == 2 and repeated['revision'] != original_revision
    assert not repeated['current_decision']
    incident_decision(tmp_path, repeated['id'], 'resolved')
    recovered = capture(tmp_path, monitor(tmp_path, 7))['changes'][0]
    assert len(recovered['incident_captures']) == 3
    assert recovered['operational_incidents'][0]['recurrence_count'] == 2
    assert recovered['operational_incidents'][0]['current_decision']
    assert recovered['operational_incidents'][0]['currentHealth'] == 'unknown'
    assert development.read(tmp_path)['changes'][0]['incidents'] == recovered['incidents']
    candidate = recovered['candidate']['digest']
    artifact(tmp_path, 'maintain', {'deployed_artifact_identity': candidate, 'observation_scope': 'Offline local fixture',
             'observation_window': 'hour', 'signals': ['M1'], 'findings': [], 'dispositions': [], 'followup_owners': ['Operator']})
    decide(tmp_path, 'maintain', 'Operator')
    assert _change(tmp_path)['gates']['maintain']['outcome'] == 'allowed'
    (tmp_path / 'dist/metric.json').write_text('{"value":"unavailable"}', encoding='utf-8')
    change = _change(tmp_path)
    choice = {'kind': 'monitor', 'adapter_id': 'monitor'}
    job = jobs.prepare(tmp_path, choice, change)
    failed = jobs.execute(tmp_path, choice, change, job['approval_id'])
    assert failed['state'] == 'blocked'
    after = development.read(tmp_path)
    assert after['changes'][0]['gates']['maintain']['outcome'] == 'unknown'
    assert any(row['current'] and row['state'] == 'unknown' for row in after['changes'][0]['monitoring'])
    closure = development.prepare(tmp_path, {'kind': 'close', 'change_id': 'C1',
                    'actor': 'Operator', 'reason': 'Earlier healthy data cannot cover a failed attempt'}, after['revision'])
    assert closure['blockers'] and not development.read(tmp_path)['changes'][0]['closed']


def test_atomic_closed_parent_followup_is_draft_and_survives_restart(tmp_path):
    fixture = build_fixture(tmp_path)
    first = capture(tmp_path, monitor(tmp_path))['changes'][0]
    incident_id = first['incidents'][0]['id']
    incident_decision(tmp_path, incident_id)
    capture(tmp_path, monitor(tmp_path, 7))
    artifact(tmp_path, 'maintain', {'deployed_artifact_identity': fixture['candidate'], 'observation_scope': 'Offline local fixture',
             'observation_window': 'hour', 'signals': ['M1'], 'findings': [], 'dispositions': [], 'followup_owners': ['Operator']})
    decide(tmp_path, 'maintain', 'Operator')
    act(tmp_path, {'kind': 'close', 'change_id': 'C1', 'actor': 'Operator', 'reason': 'Healthy observation and retained disposition reviewed'})
    diagnosed = diagnose(tmp_path, incident_id)
    assert diagnosed['observation']['outcome'] == 'abstained'
    assert jobs.assess_job_observation(tmp_path, diagnosed, _change(tmp_path), 'diagnosis')['state'] == 'allowed'
    incident_decision(tmp_path, incident_id, 'resolved')
    before = development.read(tmp_path)
    action = followup_action(incident_id)
    action['risk'] = 'routine'
    preview = development.prepare(tmp_path, action, before['revision'])
    assert not preview['blockers'], preview['blockers']
    assert (tmp_path / '.controlcoding/development.json').is_file()
    assert len(development.read(tmp_path)['changes']) == 1
    committed = development.commit(tmp_path, action, before['revision'], preview['approval_id'])['view']
    parent, child = committed['changes']
    assert parent['closed'] and parent['incident_decisions'][-1]['child_id'] == 'C2'
    assert child['origin']['incident_id'] == incident_id
    assert child['origin']['change_id'] == 'C1'
    assert child['stage'] == 'intent' and set(child['artifacts']) == {'intent'}
    assert child['decisions'] == [] and child['candidate'] is None and not child['closed']
    assert child['risk'] == parent['profile'] == 'standard'
    assert not child['gates']['intent']['accepted']
    assert development.read(tmp_path) == committed
    retry = development.commit(tmp_path, action, before['revision'], preview['approval_id'])
    assert retry['receipt']['idempotent'] and len(retry['view']['changes']) == 2
    duplicate = development.prepare(tmp_path, followup_action(incident_id, 'C3'), committed['revision'])
    assert duplicate['blockers'] == ['incident_followup_exists']
    from cc_development_archive import backup, prepare_restore, restore
    archive = backup(tmp_path)
    # Only this disposable fixture loses its state, to exercise absent-state restore.
    (tmp_path / '.controlcoding/development.json').unlink()
    restoration = prepare_restore(tmp_path, archive, 'Owner', 'Recover the same fixture')
    restored = restore(tmp_path, archive, 'Owner', 'Recover the same fixture', restoration['approval_id'])
    assert restored['view'] == committed


def test_stale_followup_approval_is_rejected_atomically(tmp_path):
    build_fixture(tmp_path)
    first = capture(tmp_path, monitor(tmp_path))['changes'][0]
    action = followup_action(first['incidents'][0]['id'])
    before = development.read(tmp_path)
    preview = development.prepare(tmp_path, action, before['revision'])
    incident_decision(tmp_path, action['incident_id'])
    current = development.read(tmp_path)
    with pytest.raises(development.DevelopmentError, match='revision_conflict'):
        development.commit(tmp_path, action, before['revision'], preview['approval_id'])
    assert development.read(tmp_path) == current and len(current['changes']) == 1


@pytest.mark.parametrize('tamper', ['projection', 'linked_history'])
def test_tampered_projection_or_linked_history_is_rejected(tmp_path, tamper):
    build_fixture(tmp_path)
    first = capture(tmp_path, monitor(tmp_path))['changes'][0]
    act(tmp_path, followup_action(first['incidents'][0]['id']))
    registry_path = tmp_path / '.controlcoding/development.json'
    from cc_evidence_inputs import SafeRoot
    import cc_development_store as disk_store
    with SafeRoot(tmp_path) as safe:
        state, revision, _ = disk_store.read_state(safe)
    if tamper == 'projection':
        state['changes'][0]['incidents'] = []
    else:
        child = state['changes'][1]
        child['origin']['incident_id'] = 'f' * 64
        child['history'][0]['result']['origin'] = child['origin']
        previous = 'absent'
        for event in child['history']:
            event['previous'] = previous
            event['digest'] = digest({k: v for k, v in event.items() if k != 'digest'})
            previous = event['digest']
    # Retain valid immutable-object hashes while attacking domain projections.
    with SafeRoot(tmp_path) as safe:
        disk_store.write_state(safe, state, revision)
    with pytest.raises(development.DevelopmentError):
        development.read(tmp_path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--capture-breach', action='store_true')
    parser.add_argument('--closed-before-breach', action='store_true')
    args = parser.parse_args()
    result = build_closed_fixture(Path(args.fixture)) if args.closed_before_breach else build_fixture(Path(args.fixture))
    if args.capture_breach and not args.closed_before_breach:
        receipt = monitor(Path(args.fixture))
        view = capture(Path(args.fixture), receipt)
        result.update(monitor_receipt=receipt['receiptId'], incident_id=view['changes'][0]['incidents'][0]['id'])
    print(json.dumps(result, indent=2))
