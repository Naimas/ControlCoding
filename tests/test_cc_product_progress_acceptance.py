"""Independent real-owner progress journeys and read-only consumer boundaries."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import os
import venv

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import cc
import cc_product
import cc_product_documents as documents
import cc_product_progress as progress
import cc_development_lib as core
import cc_development_history as history
import cc_development_jobs as jobs
from cc_layout import MARKER_BYTES
from test_cc_development_core import act
from test_cc_product_core import document, saved, detailed_action
from test_cc_product_evolution_acceptance import (
    accept_document, two_features, shared_selection, latest_selection, change,
    decision, stage, build_change, receipts, verify_change, combined_contracts,
)
from test_cc_knowledge import project  # noqa: F401 - intentionally initialized legacy-memory fixture


from cc_product_progress_fixture import (inventory, fixture_seed, fixture_verify, fixture_release, fixture_drift)


def feature(root, identifier='signup'):
    return progress.read(root, view='feature', entity_id=identifier)


def test_a01_a13_empty_and_corrupt_optional_owners_are_never_initialized_or_called(tmp_path, monkeypatch):
    import cc_memory_lib.knowledge_service as knowledge
    import cc_memory_lib.knowledge_work_schedule as schedule
    monkeypatch.setattr(knowledge, 'status', lambda *a, **k: pytest.fail('Canonical progress called optional status'))
    monkeypatch.setattr(knowledge, 'reconcile', lambda *a, **k: pytest.fail('Canonical progress reconciled memory'))
    monkeypatch.setattr(schedule, 'dispatch', lambda *a, **k: pytest.fail('Canonical progress called schedule'))
    empty = progress.read(tmp_path)
    assert empty['status'] == 'empty' and empty['enabled'] is False
    assert empty['summary']['next_actions'] and list(tmp_path.iterdir()) == []
    saved(tmp_path)
    (tmp_path / '.controlwork').mkdir()
    (tmp_path / '.controlwork/knowledge.db').write_bytes(b'corrupt optional database')
    before = inventory(tmp_path)
    result = progress.read(tmp_path)
    assert result['status'] == 'available' and result['rows'][0]['summary']['outcome'] == 'planned'
    assert inventory(tmp_path) == before


def test_a13_actual_configured_corrupt_knowledge_database_does_not_block_or_repair_progress(tmp_path, monkeypatch):
    from cc_memory_lib import knowledge_service as knowledge
    from cc_memory_lib import knowledge_work_schedule as schedule
    from cc_memory_lib.knowledge_store import database_path
    saved(tmp_path)
    knowledge.configure(tmp_path, dict(knowledge.DEFAULT, automatic=False, worker=False, embedding=''))
    database = database_path(tmp_path)
    assert database.is_file()
    database.write_bytes(b'Actual configured database is corrupt and must not be repaired by progress.')
    monkeypatch.setattr(knowledge, 'status', lambda *a, **k: pytest.fail('Progress consulted corrupt optional memory'))
    monkeypatch.setattr(knowledge, 'reconcile', lambda *a, **k: pytest.fail('Progress attempted optional repair'))
    monkeypatch.setattr(schedule, 'dispatch', lambda *a, **k: pytest.fail('Progress consulted optional schedule'))
    before = inventory(tmp_path)
    result = feature(tmp_path)
    assert result['status'] == 'available' and result['summary']['outcome'] == 'planned'
    assert inventory(tmp_path) == before


def test_a04_current_receipts_and_raw_input_changes_revoke_proof_without_control_revision_change(tmp_path):
    fixture_seed(tmp_path, shared=False)
    contract_path = tmp_path / 'controlcoding.verification.json'
    contract = json.loads(contract_path.read_text())
    contract['suites'].append(dict(contract['suites'][0], id='second-required'))
    contract_path.write_text(json.dumps(contract))
    fixture_verify(tmp_path, change_ids=('C1',))
    first = feature(tmp_path)
    assert first['summary']['outcome'] == 'verified'
    control = core.read(tmp_path)['revision']
    for name in ('accounts.py', 'check_combined.py', 'controlcoding.verification.json'):
        path = tmp_path / name
        raw = path.read_bytes()
        path.write_bytes(raw + b'\n')
        changed = feature(tmp_path)
        assert changed['summary']['outcome'] not in ('verified', 'released'), name
        assert core.read(tmp_path)['revision'] == control
        path.write_bytes(raw)
    folder = tmp_path / '.controlcoding/verification_receipts'
    receipt = max(folder.glob('verify_*.json'), key=lambda p: p.stat().st_mtime_ns)
    original = receipt.read_bytes()
    receipt.write_bytes(b'{malformed')
    unavailable = feature(tmp_path)
    assert unavailable['summary']['outcome'] not in ('verified', 'released')
    receipt.write_bytes(original)
    added = folder / 'verify_new-malformed.json'
    added.write_bytes(b'{new malformed receipt')
    assert feature(tmp_path)['summary']['outcome'] not in ('verified', 'released')
    added.unlink()
    checker = tmp_path / 'check_combined.py'
    contract_path = tmp_path / 'controlcoding.verification.json'
    checker_raw, contract_raw = checker.read_bytes(), contract_path.read_bytes()
    for kind in ('failed', 'incomplete', 'subset'):
        if kind == 'failed': checker.write_text("raise AssertionError('Actual newer fixture failure')\n")
        if kind == 'incomplete':
            checker.write_text('import time\ntime.sleep(2)\n')
            contract = json.loads(contract_raw)
            contract['suites'][0]['timeoutSeconds'] = 1
            contract_path.write_text(json.dumps(contract))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cc.cmd_verify_run(tmp_path, suite_ids=['combined'] if kind == 'subset' else None, json_output=True)
        attempt = json.loads(output.getvalue())
        assert code == (0 if kind == 'subset' else 1)
        assert attempt['status'] == ('passed_subset' if kind == 'subset' else kind)
        checker.write_bytes(checker_raw); contract_path.write_bytes(contract_raw)
        assert feature(tmp_path)['summary']['outcome'] not in ('verified', 'released'), kind
        (tmp_path / attempt['receiptPath']).unlink()
    assert core.read(tmp_path)['revision'] == control


def test_a03_two_verified_contributors_still_need_exact_current_combined_proof(tmp_path):
    fixture_seed(tmp_path, include_consumer=False)
    def implemented_ladder(root):
        built = feature(root)
        assert built['summary']['outcome'] == 'implemented'
        assert built['summary']['coverage']['required']['implemented'] == 1
        assert built['summary']['coverage']['required']['verified'] == 0
        source = root / 'accounts.py'
        raw = source.read_bytes(); source.write_bytes(raw + b'# candidate drift\n')
        assert feature(root)['summary']['outcome'] not in ('implemented', 'verified', 'released')
        source.write_bytes(raw)
    fixture_verify(tmp_path, integration=False, after_build=implemented_ladder)
    before = feature(tmp_path)
    assert before['summary']['coverage']['required']['total'] == 1
    assert before['summary']['coverage']['required']['verified'] == 0
    condition = before['rows'][0]['data']
    assert len(condition['contributions']) == 2
    assert all(c['verified'] for c in condition['contributions'])
    assert condition['integration']['required'] is True and condition['integration']['state'] == 'blocked'
    assert before['summary']['outcome'] != 'verified'
    selection = next(row for row in core.read(tmp_path)['product']['evolution']['selections'] if row['id'] == 'I1')
    act(tmp_path, {'kind': 'product_integration', 'actor': 'Verifier', 'increment_id': 'I1',
        'selection_revision': selection['selection_revision'], 'criterion_id': 'signup-success',
        'candidates': [{'change_id': cid, 'candidate_identity': change(tmp_path, cid)['candidate']['digest']} for cid in ('C1', 'C2')]})
    current = feature(tmp_path)
    assert current['summary']['outcome'] == 'verified'
    assert current['summary']['coverage']['required']['verified'] == 1
    assert current['rows'][0]['data']['integration']['state'] == 'allowed'
    act(tmp_path, {'kind': 'decide', 'change_id': 'C2', 'stage': 'verify', 'decision': 'rejected',
                  'actor': 'Verifier', 'reason': 'Withdraw one exact required contribution.'})
    stale = feature(tmp_path)
    assert stale['summary']['coverage']['required']['verified'] == 0
    assert stale['rows'][0]['data']['integration']['state'] == 'blocked'
    assert stale['summary']['outcome'] not in ('verified', 'released')


def test_a04_actual_executable_drift_invalidates_owner_proof_with_source_bytes_equal(tmp_path):
    import cc_evidence_inputs as inputs
    fixture_seed(tmp_path, shared=False)
    environment = tmp_path / '.venv'
    venv.EnvBuilder(with_pip=False, symlinks=False).create(environment)
    executable = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    assert executable.is_file() and not executable.is_symlink()
    contract_path = tmp_path / 'controlcoding.verification.json'
    contract = json.loads(contract_path.read_text())
    contract['suites'][0]['command'] = '"' + executable.as_posix() + '" -B check_combined.py'
    contract_path.write_text(json.dumps(contract))
    fixture_verify(tmp_path, change_ids=('C1',))
    assert feature(tmp_path)['summary']['outcome'] == 'verified'
    prepared = cc._evidence_prepare(tmp_path, 'verification')
    before = inputs.capture_inputs(tmp_path, prepared['policy'])
    current = cc._evidence_history(tmp_path, 'verification', prepared)
    assert current['assessment']['currentRequiredPass'] is True
    control = core.read(tmp_path)['revision']
    digest_before = hashlib.sha256(executable.read_bytes()).hexdigest()
    with executable.open('ab') as output: output.write(b'PW05-INERT-TRAILING-BYTES')
    version = subprocess.run([str(executable), '--version'], capture_output=True, text=True, timeout=30)
    assert version.returncode == 0, version.stdout + version.stderr
    assert hashlib.sha256(executable.read_bytes()).hexdigest() != digest_before
    after = inputs.capture_inputs(tmp_path, prepared['policy'])
    assert before['contentDigest'] == after['contentDigest']
    changed = cc._evidence_history(tmp_path, 'verification', prepared)
    assert changed['assessment']['currentRequiredPass'] is False
    assert any('context' in reason or 'executable' in reason for reason in changed['assessment']['reasons'])
    assert feature(tmp_path)['summary']['outcome'] not in ('verified', 'released')
    assert core.read(tmp_path)['revision'] == control


def test_a05_a16_real_observed_release_survives_as_original_history_after_raw_drift(tmp_path):
    fixture_seed(tmp_path, shared=False); fixture_verify(tmp_path, change_ids=('C1',), retain_evidence=True)
    candidate = change(tmp_path, 'C1')['candidate']['digest']
    act(tmp_path, {'kind': 'review', 'change_id': 'C1', 'findings': [], 'actor': 'Reviewer', 'reason': 'Review fixture.'})
    stage(tmp_path, 'C1', 'review_release', {'candidate_identity': candidate, 'review_findings': [], 'finding_resolutions': [],
        'required_approvals': [], 'environment': 'local', 'release_artifact_identity': candidate,
        'rollback_readiness': 'Restore source.', 'observation_provenance': 'self_reported'})
    decision(tmp_path, 'C1', 'review_release')
    for status in ('prepared', 'self_reported'):
        # These owner records are retained observations; neither proves release.
        act(tmp_path, {'kind': 'release', 'change_id': 'C1', 'environment': 'local', 'artifact_digest': candidate,
            'status': status, 'actor': 'ReleaseOwner', 'reason': 'Retain unqualified local preparation.'})
        assert feature(tmp_path)['summary']['outcome'] != 'released'
    retained = fixture_release(tmp_path, change_ids=('C1',))
    released = feature(tmp_path)
    assert released['summary']['outcome'] == 'released'
    selection = next(row for row in core.read(tmp_path)['product']['evolution']['selections'] if row['id'] == 'I1')
    selected_feature = selection['features'][0]
    act(tmp_path, {'kind': 'product_select', 'product_revision': core.read(tmp_path)['product']['accepted_revision'],
        'increment_id': 'I2', 'title': 'Exact released dependency consumer', 'features': ['reports'],
        'changes': [{'change_id': 'C3', 'title': 'Reports consumer', 'owner': 'Writer', 'risk': 'routine', 'feature_ids': ['reports']}],
        'criteria': [{'criterion_id': 'report-success', 'owner_change_id': 'C3', 'contributor_change_ids': ['C3'], 'integration_check': None}],
        'dependencies': [{'consumer_change_id': 'C3', 'target_change_id': 'C1', 'target_feature_id': 'signup',
            'target_feature_revision': selected_feature['feature_revision'], 'target_selection_revision': selection['selection_revision'],
            'required_result': 'released', 'gate': 'build'}], 'actor': 'Owner'})
    def edge():
        return progress.read(tmp_path, view='increment', entity_id='I2', collection='dependencies')['rows'][0]['data']
    qualified = edge()
    assert qualified['state'] == 'allowed' and qualified['precision'] == 'edge'
    assert qualified['required_result'] == 'released' and qualified['gate'] == 'build'
    assert qualified['target_feature_revision'] == selected_feature['feature_revision']
    assert qualified['target_selection_revision'] == selection['selection_revision']
    before = progress.read(tmp_path, view='change', entity_id='C1', collection='releases')
    assert any(row['data']['status'] == 'observed' for row in before['rows'])
    from cc_product_progress_fixture import assert_transport_rows
    observed_rows = assert_transport_rows(tmp_path, [
        ('increment', 'I2', 'dependencies', 'dependency'),
        ('change', 'C1', 'evidence', 'evidence'),
        ('change', 'C1', 'incidents', 'incident'),
        ('change', 'C1', 'releases', 'release'),
        ('feature', 'signup', 'history_refs', 'history_ref'),
        ('history', 'C1', 'events', 'event'),
    ], private_isolated=False)
    assert all(row['private_isolated'] is False for row in observed_rows)
    assert {row['kind'] for row in observed_rows} == {'dependency', 'evidence', 'incident', 'release', 'history_ref', 'event'}
    from cc_product_progress_fixture import assert_isolated_proof_context
    isolated_context = assert_isolated_proof_context(tmp_path, release_progress=before)
    assert isolated_context['production_isolated_parity'] is True
    print(json.dumps({'production_isolated_context': isolated_context}, ensure_ascii=False))
    original_source = (tmp_path / 'accounts.py').read_bytes()
    control = core.read(tmp_path)['revision']; fixture_drift(tmp_path)
    assert feature(tmp_path)['summary']['outcome'] != 'released'
    assert edge()['state'] == 'blocked'
    after = progress.read(tmp_path, view='change', entity_id='C1', collection='releases')
    assert any(row['data']['candidate_identity'] == candidate and row['data']['status'] == 'observed'
               and row['data']['environment'] == 'local' for row in after['rows'])
    assert core.read(tmp_path)['revision'] == control
    (tmp_path / 'accounts.py').write_bytes(original_source)
    assert edge()['state'] == 'allowed'
    malformed = tmp_path / '.controlcoding/verification_receipts/verify_release-proof-invalid.json'
    malformed.write_bytes(b'{new unqualified release proof')
    assert edge()['state'] == 'blocked'
    malformed.unlink()
    assert edge()['state'] == 'allowed'
    changed_document = copy.deepcopy(core.read(tmp_path)['product']['document'])
    changed_document['features'][0]['criteria'][0]['text'] += ' Require explicit owner confirmation.'
    accept_document(tmp_path, changed_document)
    act(tmp_path, {'kind': 'product_scope', 'actor': 'Owner', 'increment_id': 'I1',
        'selection_revision': selection['selection_revision'],
        'product_revision': core.read(tmp_path)['product']['accepted_revision'], 'decision': 'adopt',
        'reason': 'Adopt the changed exact required condition; old release remains historical.'})
    superseded_edge = edge()
    assert superseded_edge['state'] == 'blocked'
    assert superseded_edge['target_selection_revision'] == selection['selection_revision']
    assert superseded_edge['reasons']
    historical = progress.read(tmp_path, view='change', entity_id='C1', collection='releases')
    observed = [row for row in historical['rows'] if row['data']['status'] == 'observed']
    assert observed
    assert all(row['scope']['selection_revision'] == selection['selection_revision'] for row in observed)
    assert all(row['scope']['feature_revision'] == selected_feature['feature_revision'] for row in observed)
    incidents = progress.read(tmp_path, view='change', entity_id='C1', collection='incidents')
    incident = next(row for row in incidents['rows'] if row['data']['incident_id'] == retained['incident_id'])
    assert incident['scope']['selection_revision'] == selection['selection_revision']
    assert incident['scope']['feature_revision'] == selected_feature['feature_revision']
    assert incident['target']['incident_id'] == retained['incident_id']
    assert incident['target']['selection_revision'] == selection['selection_revision']
    events = progress.read(tmp_path, view='history', entity_id='C1')
    records = list(events['rows'])
    while events['pagination']['next_cursor']:
        events = progress.read(tmp_path, view='history', entity_id='C1', cursor=events['pagination']['next_cursor'],
                               observation_id=events['observation']['observation_id'])
        records.extend(events['rows'])
    assert any(row['data']['event'].get('action', {}).get('kind') == 'release' for row in records)
    assert retained['incident_id']


def test_a07_legacy_completion_and_same_textual_ids_do_not_promote_saved_scope(tmp_path):
    saved(tmp_path)
    before = feature(tmp_path)
    (tmp_path / '.controlcoding/features').mkdir(parents=True)
    (tmp_path / '.controlcoding/features/features.json').write_text(json.dumps({'features': [{'id': 'signup', 'status': 'complete', 'passing': True}]}))
    (tmp_path / '.controlwork').mkdir()
    (tmp_path / '.controlwork/wiki.md').write_text('# signup\nAccepted and completed.\n')
    (tmp_path / '.controlwork/schedule.json').write_text('{"id":"signup","state":"done","deadline":"2000-01-01"}')
    observed = feature(tmp_path)
    assert observed['summary']['outcome'] == before['summary']['outcome'] == 'planned'
    assert observed['summary']['coverage']['required']['verified'] == 0
    assert not core.read(tmp_path)['changes']


def test_a14_real_legacy_estimate_save_cannot_change_same_id_canonical_task_order(project):
    from test_cc_product_planning_detail import planned
    from test_cc_knowledge_work_schedule import seed, view, save
    (project / 'accounts.py').write_text('value = 1\n')
    planned(project)
    canonical = progress.read(project, view='change', entity_id='C1', collection='tasks')
    seed(project, ['design-policy'], states={'design-policy': 'done'})
    old = view(project)
    changed = save(project, old, 'design-policy', 2, anchor='2000-01-01', deadline='2000-01-03')
    assert changed['snapshot'] != old['snapshot']
    current = progress.read(project, view='change', entity_id='C1', collection='tasks')
    assert current['rows'] == canonical['rows'] and current['summary'] == canonical['summary']
    assert all(row['data']['completion'] == 'not_tracked' for row in current['rows'])


@pytest.mark.parametrize('contained', [False, True])
@pytest.mark.parametrize('ownership', ['managed', 'project_managed'])
def test_a17_progress_preserves_all_layout_and_document_ownership_bytes(tmp_path, contained, ownership):
    if contained:
        (tmp_path / 'cc').mkdir()
        (tmp_path / 'cc/layout.json').write_bytes(MARKER_BYTES)
    saved(tmp_path)
    product = core.read(tmp_path)['product']
    path = 'docs/owner-product.md' if ownership == 'project_managed' else ''
    export = documents.export_preview(tmp_path, product['document'], product['current_revision'], ownership=ownership, path=path)
    documents.export_commit(tmp_path, product['document'], product['current_revision'],
                            **{key: export[key] for key in ('ownership', 'path', 'expected_hash', 'preview_id')})
    token = core.prepare(tmp_path, {'kind': 'activate', 'profile': 'routine'}, core.read(tmp_path)['revision'])
    before = inventory(tmp_path)
    assert progress.read(tmp_path)['status'] == 'available'
    assert feature(tmp_path)['scope']['feature_id'] == 'signup'
    assert inventory(tmp_path) == before
    # A read cannot consume or replace the outstanding normal approval.
    result = core.commit(tmp_path, {'kind': 'activate', 'profile': 'routine'}, token['revision'], token['approval_id'])
    assert result['view']['enabled']


def test_a17_unsafe_shared_registry_fails_without_repair_or_external_sentinel_change(tmp_path):
    saved(tmp_path)
    registry = tmp_path / '.controlcoding/development.json'
    sentinel = tmp_path.parent / (tmp_path.name + '-external-sentinel.json')
    raw = registry.read_bytes()
    sentinel.write_bytes(raw)
    registry.unlink()
    try:
        os.link(sentinel, registry)
    except OSError:
        registry.write_bytes(raw)
        pytest.skip('Hard links unavailable on this host')
    before = inventory(tmp_path)
    with pytest.raises(ValueError): progress.read(tmp_path)
    assert inventory(tmp_path) == before
    assert sentinel.read_bytes() == raw
