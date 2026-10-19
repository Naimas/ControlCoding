"""Independent canonical scope adoption and dependency negative controls."""
import copy
import contextlib
import io
import json
import hashlib
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))

import cc_development_lib as core
from test_cc_development_core import act
from test_cc_product_core import document, saved, detailed_action
from test_cc_product_evolution_transport import selection
import cc
import cc_product
from cc_product_documents import observe_files


def accept_document(root, doc):
    saved(root, doc)
    return act(root, {'kind': 'product_decide',
                     'product_revision': core.read(root)['product']['current_revision'],
                     'decision': 'accepted', 'actor': 'Owner', 'reason': 'Reviewed scope'})


def two_features():
    doc = document()
    feature = copy.deepcopy(doc['features'][0])
    feature.update(id='reports', title='Reports', problem='Members need reports',
                   outcome='Members read reports',
                   criteria=[{'id': 'report-success', 'text': 'Reports reflect stored accounts', 'required': True}])
    doc['features'].append(feature)
    doc['chapters'][0]['feature_ids'].append('reports')
    return doc


def selected(root, doc=None):
    accept_document(root, doc or document())
    act(root, {'kind': 'activate', 'profile': 'routine'})
    return act(root, selection(root))


def latest_selection(root):
    return core.read(root)['product']['evolution']['selections'][-1]


def scope_action(root, decision='keep'):
    row = latest_selection(root)
    return {'kind': 'product_scope', 'actor': 'Owner', 'increment_id': 'I1',
            'selection_revision': row['selection_revision'],
            'product_revision': core.read(root)['product']['accepted_revision'],
            'decision': decision, 'reason': 'Reviewed the exact changed requirements'}


def test_stale_approved_scope_preview_cannot_mix_competing_document_edits(tmp_path):
    selected(tmp_path)
    doc = document()
    doc['features'][0]['outcome'] = 'Members create accounts after validation'
    accept_document(tmp_path, doc)
    before = core.read(tmp_path)
    action = scope_action(tmp_path)
    preview = core.prepare(tmp_path, action, before['revision'])
    assert not preview['blockers']
    doc['features'][0]['outcome'] = 'Members create accounts after owner approval'
    accept_document(tmp_path, doc)
    current = core.read(tmp_path)
    with pytest.raises(core.DevelopmentError):
        core.commit(tmp_path, action, before['revision'], preview['approval_id'])
    assert core.read(tmp_path) == current


def test_independent_addition_preserves_original_selection_and_intent_decision(tmp_path):
    selected(tmp_path)
    act(tmp_path, {'kind': 'decide', 'change_id': 'C1', 'stage': 'intent',
                  'decision': 'accepted', 'actor': 'Owner', 'reason': 'Reviewed request'})
    before = core.read(tmp_path)
    original = copy.deepcopy(before['product']['evolution']['selections'])
    decision = copy.deepcopy(before['changes'][0]['decisions'])
    accept_document(tmp_path, two_features())
    current = core.read(tmp_path)
    assert current['product']['evolution']['selections'] == original
    assert current['changes'][0]['decisions'] == decision
    assert current['changes'][0]['gates']['intent']['accepted']
    assert not any(row['required'] for row in current['product']['evolution']['impacts'])


def test_keep_considers_one_revision_and_later_relevant_scope_needs_review(tmp_path):
    selected(tmp_path)
    doc = document()
    doc['features'][0]['outcome'] = 'Members create validated accounts'
    accept_document(tmp_path, doc)
    act(tmp_path, scope_action(tmp_path))
    after_keep = core.read(tmp_path)
    assert after_keep['product']['evolution']['scope_decisions']
    assert not any(row['required'] for row in after_keep['product']['evolution']['impacts'])
    doc['features'][0]['outcome'] = 'Members create validated accounts with MFA'
    accept_document(tmp_path, doc)
    assert any(row['required'] for row in core.read(tmp_path)['product']['evolution']['impacts'])


def test_stricter_shared_constraint_cannot_keep_original_scope(tmp_path):
    selected(tmp_path)
    doc = document()
    doc['constraints'].append('Require MFA for every account operation')
    accept_document(tmp_path, doc)
    before = core.read(tmp_path)
    action = scope_action(tmp_path)
    try:
        preview = core.prepare(tmp_path, action, before['revision'])
    except core.DevelopmentError:
        assert core.read(tmp_path) == before
        return
    assert preview['blockers']
    with pytest.raises(core.DevelopmentError):
        core.commit(tmp_path, action, before['revision'], preview['approval_id'])
    assert core.read(tmp_path) == before


def change(root, change_id):
    return next(row for row in core.read(root)['changes'] if row['id'] == change_id)


def decision(root, change_id, stage):
    return act(root, {'kind': 'decide', 'change_id': change_id, 'stage': stage,
                     'decision': 'accepted', 'actor': 'Owner', 'reason': 'Reviewed exact fixture behavior'})


def stage(root, change_id, name, content):
    return act(root, {'kind': 'artifact', 'change_id': change_id, 'stage': name,
                     'content': content, 'actor': 'Writer'})


def plan_change(root, change_id, filename):
    decision(root, change_id, 'intent')
    draft = cc_product.template(root, change_id, 'design')
    for criterion in draft['content']['criteria']:
        criterion.update(proof='automated', checks=[{'kind': 'verification', 'id': 'combined'}])
    for mapping in draft['mappings']:
        mapping['reason'] = 'Compared requirement and fixture assertion for exact equivalence'
    act(root, detailed_action(root, {'kind': 'product_specification', 'change_id': change_id,
               'content': draft['content'], 'mappings': draft['mappings'], 'actor': 'Writer'}))
    decision(root, change_id, 'design')
    draft = cc_product.template(root, change_id, 'plan')
    draft['content'].update(files=[filename], ownership_scopes=[{'owner': 'Writer', 'files': [filename]}],
        tasks=[{'id': 'implement', 'title': 'Implement the exact selected criterion', 'owner': 'Writer', 'depends_on': []}],
        verification_strategy='Check both contributions independently and their combined runtime result',
        rollback_assessment='Restore original fixture source bytes and preserve decisions')
    draft['content']['criterion_check_mapping'] = {key: ['combined'] for key in draft['content']['criterion_check_mapping']}
    act(root, detailed_action(root, {'kind': 'product_plan', 'change_id': change_id, 'content': draft['content'],
               'files': observe_files(root, [filename]), 'actor': 'Writer'}))
    decision(root, change_id, 'plan')


def build_change(root, change_id, filename):
    act(root, {'kind': 'candidate', 'change_id': change_id, 'files': [filename], 'actor': 'Writer'})
    current = change(root, change_id)
    candidate = current['candidate']['digest']
    stage(root, change_id, 'build', {'approved_plan_revision': current['artifacts']['plan'][-1]['revision'],
        'source_identity': candidate, 'execution_provenance': {'mode': 'manual', 'description': 'Observed disposable fixture'},
        'deviations': [], 'changed_files': [filename]})
    decision(root, change_id, 'build')
    return candidate


def receipts(root):
    with contextlib.redirect_stdout(io.StringIO()):
        assert cc.cmd_verify_run(root, json_output=True) == 0
        assert cc.cmd_invariants_run(root, json_output=True) == 0


def verify_change(root, change_id):
    current = change(root, change_id)
    stage(root, change_id, 'verify', {'candidate_identity': current['candidate']['digest'],
        'criterion_coverage': {row['id']: ['combined'] for row in current['artifacts']['design'][-1]['content']['criteria']},
        'required_contract_results': {'mode': 'owner_receipts'}, 'freshness': 'Read actual current receipts',
        'unresolved_failures': []})
    decision(root, change_id, 'verify')


def combined_contracts(root):
    (root / 'accounts.py').write_text('value = 1\n', encoding='utf-8')
    (root / 'reports.py').write_text('value = 2\n', encoding='utf-8')
    checker = root / 'check_combined.py'
    checker.write_text("from pathlib import Path\nassert Path('accounts.py').read_text() == 'value = 1\\n'\n"
                       "assert Path('reports.py').read_text() == 'value = 2\\n'\n", encoding='utf-8')
    command = '"' + Path(sys.executable).as_posix() + '" -B check_combined.py'
    (root / 'controlcoding.verification.json').write_text(json.dumps({'schemaVersion': 1,
        'requiredKinds': ['targeted'], 'suites': [{'id': 'combined', 'kind': 'targeted', 'required': True,
                                                'command': command}]}), encoding='utf-8')
    (root / 'controlcoding.invariants.json').write_text(json.dumps({'schemaVersion': 1, 'invariants': [
        {'id': 'combined-invariant', 'kind': 'domain', 'domain': 'testing', 'severity': 'blocking',
         'status': 'active', 'property': 'Combined contributions retain required source behavior',
         'threshold': 'zero failures', 'command': command, 'required': True}]}), encoding='utf-8')


def shared_selection(root):
    action = selection(root)
    action['changes'].append({'change_id': 'C2', 'title': 'Accounts storage contribution', 'owner': 'Owner',
                              'risk': 'routine', 'feature_ids': ['signup']})
    action['criteria'][0].update(contributor_change_ids=['C1', 'C2'],
                                 integration_check={'kind': 'verification', 'id': 'combined'})
    return action


def two_change_selection(root):
    action = selection(root)
    action['features'].append('reports')
    action['changes'].append({'change_id': 'C2', 'title': 'Reports', 'owner': 'Owner',
                              'risk': 'routine', 'feature_ids': ['reports']})
    action['criteria'].append({'criterion_id': 'report-success', 'owner_change_id': 'C2',
                              'contributor_change_ids': ['C2'], 'integration_check': None})
    return action


def test_adoption_changes_only_impacted_intent_and_preserves_unaffected_decision_bytes(tmp_path):
    accept_document(tmp_path, two_features())
    act(tmp_path, {'kind': 'activate', 'profile': 'routine'})
    act(tmp_path, two_change_selection(tmp_path))
    decision(tmp_path, 'C1', 'intent')
    decision(tmp_path, 'C2', 'intent')
    original = copy.deepcopy(latest_selection(tmp_path))
    untouched = json.dumps(change(tmp_path, 'C2')['decisions'], sort_keys=True).encode()
    old_intent = change(tmp_path, 'C1')['artifacts']['intent'][-1]['revision']
    doc = two_features()
    doc['features'][0]['outcome'] = 'Members create accounts after owner validation'
    accept_document(tmp_path, doc)
    impacts = core.read(tmp_path)['product']['evolution']['impacts']
    assert any(row['change_ids'] == ['C1'] and row['required'] for row in impacts)
    act(tmp_path, scope_action(tmp_path, 'adopt'))
    assert latest_selection(tmp_path)['selection_revision'] != original['selection_revision']
    assert core.read(tmp_path)['product']['evolution']['selections'][0] == original
    assert change(tmp_path, 'C1')['artifacts']['intent'][-1]['revision'] != old_intent
    assert not change(tmp_path, 'C1')['gates']['intent']['accepted']
    assert json.dumps(change(tmp_path, 'C2')['decisions'], sort_keys=True).encode() == untouched
    assert change(tmp_path, 'C2')['gates']['intent']['accepted']


def test_upstream_adoption_succeeds_and_exact_old_revision_dependency_blocks_consumer(tmp_path):
    selected(tmp_path, two_features())
    old_selection = copy.deepcopy(latest_selection(tmp_path))
    action = selection(tmp_path)
    action.update(increment_id='I2', title='Reports', features=['reports'],
        changes=[{'change_id': 'C2', 'title': 'Reports', 'owner': 'Owner', 'risk': 'routine', 'feature_ids': ['reports']}],
        criteria=[{'criterion_id': 'report-success', 'owner_change_id': 'C2',
                   'contributor_change_ids': ['C2'], 'integration_check': None}],
        dependencies=[{'consumer_change_id': 'C2', 'target_change_id': 'C1',
            'target_feature_id': 'signup', 'target_feature_revision': old_selection['features'][0]['feature_revision'],
            'target_selection_revision': old_selection['selection_revision'],
            'required_result': 'specification_accepted', 'gate': 'design'}])
    act(tmp_path, action)
    consumer_decisions = copy.deepcopy(change(tmp_path, 'C2')['decisions'])
    doc = two_features()
    doc['features'][0]['outcome'] = 'Members create accounts after owner validation'
    accept_document(tmp_path, doc)
    adoption = {'kind': 'product_scope', 'actor': 'Owner', 'increment_id': 'I1',
                'selection_revision': old_selection['selection_revision'],
                'product_revision': core.read(tmp_path)['product']['accepted_revision'],
                'decision': 'adopt', 'reason': 'Reviewed changed accounts behavior'}
    act(tmp_path, adoption)
    view = core.read(tmp_path)
    assert any(row['id'] == 'I1' and row['selection_revision'] != old_selection['selection_revision']
               for row in view['product']['evolution']['selections'])
    assert 'product_dependency_target_stale' in change(tmp_path, 'C2')['gates']['design']['reasons']
    assert change(tmp_path, 'C2')['decisions'] == consumer_decisions


def test_actual_candidate_and_manual_execution_handoff_wait_for_verified_predecessor(tmp_path):
    import cc_development_adapters as adapters
    import cc_development_execution as execution
    combined_contracts(tmp_path)
    accept_document(tmp_path, two_features())
    act(tmp_path, {'kind': 'activate', 'profile': 'routine'})
    action = two_change_selection(tmp_path)
    revision = next(row['revision'] for row in core.read(tmp_path)['product']['evolution']['feature_revisions']
                    if row['feature_id'] == 'signup')
    action['dependencies'] = [{'consumer_change_id': 'C2', 'target_change_id': 'C1',
        'target_feature_id': 'signup', 'target_feature_revision': revision,
        'target_selection_revision': 'same_increment', 'required_result': 'verified', 'gate': 'build'}]
    act(tmp_path, action)
    plan_change(tmp_path, 'C1', 'accounts.py')
    plan_change(tmp_path, 'C2', 'reports.py')
    before = core.read(tmp_path)
    with pytest.raises(ValueError, match='product_dependency_verified_required'):
        adapters.manual_handoff(tmp_path, change(tmp_path, 'C2'))
    (tmp_path / 'config.json').write_text('{}', encoding='utf-8')
    (tmp_path / 'worker.py').write_text("raise AssertionError('Blocked dependency must prevent worker launch')\n", encoding='utf-8')
    pins = [{'path': name, 'sha256': hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()}
            for name in ('config.json', 'worker.py')]
    manifest = {'schemaVersion': 1, 'adapters': [{'id': 'worker', 'kind': 'proposal_worker',
        'argv': [sys.executable, '-B', 'worker.py', '{request}'], 'files': pins,
        'configurationPath': 'config.json', 'evaluationId': 'eval1', 'timeoutSeconds': 10,
        'outputLimitBytes': 8192, 'maxInputTokens': 20, 'maxOutputTokens': 20, 'attemptToken': 'attempt1'}]}
    (tmp_path / execution.MANIFEST).write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='product_dependency_verified_required'):
        execution.prepare(tmp_path, change(tmp_path, 'C2'), 'worker')
    assert not (tmp_path / execution.RECEIPTS).exists()
    candidate = {'kind': 'candidate', 'change_id': 'C2', 'files': ['reports.py'], 'actor': 'Writer'}
    preview = core.prepare(tmp_path, candidate, before['revision'])
    assert any('product_dependency_verified_required' in reason for reason in preview['blockers'])
    with pytest.raises(core.DevelopmentError):
        core.commit(tmp_path, candidate, before['revision'], preview['approval_id'])
    assert core.read(tmp_path) == before
    build_change(tmp_path, 'C1', 'accounts.py')
    receipts(tmp_path)
    verify_change(tmp_path, 'C1')
    assert adapters.manual_handoff(tmp_path, change(tmp_path, 'C2'))['packetDigest']
    build_change(tmp_path, 'C2', 'reports.py')
    assert change(tmp_path, 'C2')['gates']['build']['accepted']


@pytest.mark.parametrize('drift', ['source', 'contribution_rejected'])
def test_shared_criterion_needs_final_current_combined_receipt_after_constituents_verified(tmp_path, drift):
    combined_contracts(tmp_path)
    accept_document(tmp_path, document())
    act(tmp_path, {'kind': 'activate', 'profile': 'routine'})
    act(tmp_path, shared_selection(tmp_path))
    for change_id, filename in [('C1', 'accounts.py'), ('C2', 'reports.py')]:
        plan_change(tmp_path, change_id, filename)
        build_change(tmp_path, change_id, filename)
    receipts(tmp_path)
    verify_change(tmp_path, 'C1')
    verify_change(tmp_path, 'C2')
    for change_id in ('C1', 'C2'):
        assert change(tmp_path, change_id)['outcomes']['verified']
        assert any('integration' in reason for reason in change(tmp_path, change_id)['gates']['review_release']['reasons'])
    action = {'kind': 'product_integration', 'actor': 'Verifier', 'increment_id': 'I1',
              'selection_revision': latest_selection(tmp_path)['selection_revision'], 'criterion_id': 'signup-success',
              'candidates': [{'change_id': change_id, 'candidate_identity': change(tmp_path, change_id)['candidate']['digest']}
                             for change_id in ('C1', 'C2')]}
    act(tmp_path, action)
    for change_id in ('C1', 'C2'):
        assert not any('integration' in reason for reason in change(tmp_path, change_id)['gates']['review_release']['reasons'])
    if drift == 'source':
        (tmp_path / 'reports.py').write_text('value = 3\n', encoding='utf-8')
    else:
        act(tmp_path, {'kind': 'decide', 'change_id': 'C2', 'stage': 'verify', 'decision': 'rejected',
                      'actor': 'Verifier', 'reason': 'Contribution no longer qualifies after review'})
    assert any('integration' in reason for reason in change(tmp_path, 'C1')['gates']['review_release']['reasons'])


def test_old_receipt_cannot_cover_recaptured_generated_contribution(tmp_path):
    combined_contracts(tmp_path)
    (tmp_path / 'dist').mkdir()
    (tmp_path / 'reports.py').rename(tmp_path / 'dist/reports.py')
    checker = tmp_path / 'check_combined.py'
    checker.write_text(checker.read_text(encoding='utf-8').replace("Path('reports.py')", "Path('dist/reports.py')"), encoding='utf-8')
    accept_document(tmp_path, document())
    act(tmp_path, {'kind': 'activate', 'profile': 'routine'})
    act(tmp_path, shared_selection(tmp_path))
    plan_change(tmp_path, 'C1', 'accounts.py')
    build_change(tmp_path, 'C1', 'accounts.py')
    receipts(tmp_path)
    verify_change(tmp_path, 'C1')
    # Ordinary inventory excludes generated dist/. Recapture changed bytes under
    # the real current candidate owner; the old receipt itself still reports current.
    (tmp_path / 'dist/reports.py').write_text('value = 3\n', encoding='utf-8')
    plan_change(tmp_path, 'C2', 'dist/reports.py')
    build_change(tmp_path, 'C2', 'dist/reports.py')
    verify_change(tmp_path, 'C2')
    before = core.read(tmp_path)
    action = {'kind': 'product_integration', 'actor': 'Verifier', 'increment_id': 'I1',
              'selection_revision': latest_selection(tmp_path)['selection_revision'], 'criterion_id': 'signup-success',
              'candidates': [{'change_id': change_id, 'candidate_identity': change(tmp_path, change_id)['candidate']['digest']}
                             for change_id in ('C1', 'C2')]}
    preview = core.prepare(tmp_path, action, before['revision'])
    assert 'product_integration_candidate_not_covered' in preview['blockers']
    with pytest.raises(core.DevelopmentError):
        core.commit(tmp_path, action, before['revision'], preview['approval_id'])
    assert core.read(tmp_path) == before
