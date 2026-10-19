"""Independent stable-ID, current companion and transport acceptance cases."""
import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import cc_development_lib as core
import cc_product as product
import cc_product_planning as detail
import cc_product_documents as documents
import cc_development_adapters as adapters
from cc_evidence_inputs import SafeRoot
from test_cc_development_core import act
from test_cc_product_core import document
from test_cc_product_evolution import select_action


def provenance(criterion='signup-success'):
    return {'kind': 'product', 'source_refs': [], 'product_criterion_ids': [criterion],
            'rationale': 'Preserve accepted mandatory authorization condition.'}


def selected(root):
    doc = document()
    doc['features'][0]['criteria'][0]['text'] = 'Authorization remains mandatory after restart.'
    action = select_action(root, doc)
    # Two contributors use the same condition, while final integration remains
    # owned by the existing shared proof contract rather than local tasks.
    action['changes'][1]['feature_ids'] = ['signup']
    action['criteria'][0].update(contributor_change_ids=['C1', 'C2'],
        integration_check={'kind': 'verification', 'id': 'security'})
    act(root, action)
    for cid in ('C1', 'C2'):
        act(root, {'kind': 'decide', 'change_id': cid, 'stage': 'intent', 'decision': 'accepted',
                  'actor': 'Owner', 'reason': 'Reviewed exact mandatory authorization request.'})
    return core.read(root)


def specification_action(root, cid='C1'):
    draft = product.template(root, cid, 'design')
    for mapping in draft['mappings']:
        mapping['reason'] = 'Required authorization wording is retained exactly.'
    value = detail.empty_details('design')
    value['requirement_details'] = [{'requirement_id': r['id'], 'approach': r['text'], 'provenance': provenance()}
                                    for r in draft['content']['requirements']]
    value['condition_mappings'] = [{'id': 'condition-' + str(i), 'source_refs': [],
        'product_criterion_id': m['product_criterion_id'],
        'requirement_ids': [next(c['requirement_id'] for c in draft['content']['criteria'] if c['id'] == m['specification_criterion_id'])],
        'criterion_ids': [m['specification_criterion_id']], 'decision': 'equivalent', 'reason': m['reason']}
        for i, m in enumerate(draft['mappings'])]
    return {'kind': 'product_specification_v2', 'change_id': cid, 'content': draft['content'],
            'mappings': draft['mappings'], 'details': value, 'actor': 'Author'}


def accept(root, cid, stage):
    return act(root, {'kind': 'decide', 'change_id': cid, 'stage': stage, 'decision': 'accepted',
                     'actor': 'Owner', 'reason': 'Reviewed complete current companion and base artifact.'})


def plan_action(root, cid='C1'):
    draft = product.template(root, cid, 'plan')
    content = draft['content']
    content.update(files=['accounts.py'], ownership_scopes=[{'owner': 'Author', 'files': ['accounts.py']}],
        tasks=[{'id': 'design-policy', 'title': 'Define persisted authorization boundary', 'owner': 'Author', 'depends_on': []},
               {'id': 'implement-policy', 'title': 'Implement mandatory authorization', 'owner': 'Author', 'depends_on': ['design-policy']},
               {'id': 'verify-policy', 'title': 'Verify restart and rejection', 'owner': 'Author', 'depends_on': ['design-policy', 'implement-policy']}],
        verification_strategy='Review rejection before and after restart.', rollback_assessment='Restore original source and retain records.')
    with SafeRoot(root) as safe:
        state, _ = core.storage.load(safe)
    change = next(c for c in state['changes'] if c['id'] == cid)
    criteria = change['artifacts']['design'][-1]['content']['criteria']
    value = detail.empty_details('plan')
    value['task_details'] = [{'task_id': t['id'], 'approach': t['title'], 'deliverable': 'Mandatory authorization remains enforced.',
        'files': ['accounts.py'], 'criterion_ids': [c['id'] for c in criteria],
        'obligations': [{'criterion_id': c['id'], 'kind': 'human', 'check_id': '',
                         'question': 'Does unauthenticated access remain rejected after restart?'} for c in criteria],
        'supporting_rationale': '', 'supports_task_ids': [], 'file_free_rationale': '', 'provenance': provenance()}
        for t in content['tasks']]
    value['rollback'] = {'assessment': 'Restore original module without discarding persisted records.',
                         'steps': ['Restore reviewed original accounts.py bytes.'], 'unavailable_reason': ''}
    return {'kind': 'product_plan_v2', 'change_id': cid, 'content': content,
            'files': documents.observe_files(root, ['accounts.py']), 'details': value, 'actor': 'Author'}


def planned(root):
    selected(root)
    action = specification_action(root)
    act(root, action)
    accept(root, 'C1', 'design')
    plan = plan_action(root)
    act(root, plan)
    accept(root, 'C1', 'plan')
    return action, plan


def test_a07_a08_a09_multiple_predecessors_and_shared_contribution_are_distinct(tmp_path):
    _, plan = planned(tmp_path)
    view = core.read(tmp_path)
    assert plan['content']['tasks'][2]['depends_on'] == ['design-policy', 'implement-policy']
    report = next(p for p in view['product']['planning'] if p['change_id'] == 'C1' and p['stage'] == 'plan')
    assert not report['blockers']
    assert report['coverage']['criteria'][0]['contributor_change_ids'] == ['C1', 'C2']
    assert any('integration' in r for r in view['changes'][0]['gates']['review_release']['reasons'])
    packet = adapters.manual_handoff(tmp_path, view['changes'][0])
    assert packet['planningDetail'] == plan['details']
    assert packet['planningBinding']['detail_revision'] == report['detail_revision']


def test_a10_incomplete_valid_detail_resumes_without_stage_approval(tmp_path):
    selected(tmp_path)
    action = specification_action(tmp_path)
    action['details']['requirement_details'][0]['approach'] = ''
    act(tmp_path, action)
    first = core.read(tmp_path)
    second = core.read(tmp_path)
    assert second == first
    report = next(p for p in second['product']['planning'] if p['change_id'] == 'C1' and p['stage'] == 'design')
    assert report['details'] == action['details'] and 'planning_requirement_detail_incomplete' in report['blockers']
    assert not second['changes'][0]['gates']['design']['accepted']


def test_a11_identical_base_with_changed_companion_invalidates_approval_and_manual_execution(tmp_path):
    _, plan = planned(tmp_path)
    before = core.read(tmp_path)
    old = adapters.manual_handoff(tmp_path, before['changes'][0])
    changed = copy.deepcopy(plan)
    changed['details']['task_details'][0]['approach'] += ' Preserve original policy IDs during recovery.'
    act(tmp_path, changed)
    after = core.read(tmp_path)
    assert after['changes'][0]['artifacts']['plan'][-1]['digest'] == before['changes'][0]['artifacts']['plan'][-1]['digest']
    assert not after['changes'][0]['gates']['plan']['accepted']
    with pytest.raises(Exception) as failure:
        adapters.manual_handoff(tmp_path, after['changes'][0])
    assert getattr(failure.value, 'code', str(failure.value)) in ('plan_approval_missing', 'planning_detail_stale', 'product_dependency_plan_required')
    accept(tmp_path, 'C1', 'plan')
    fresh = adapters.manual_handoff(tmp_path, core.read(tmp_path)['changes'][0])
    assert fresh['planDigest'] == old['planDigest'] and fresh['packetDigest'] != old['packetDigest']
    assert fresh['planningBinding'] != old['planningBinding']


@pytest.mark.parametrize('fault', ['unknown_file', 'wrong_owner', 'unknown_check', 'cycle'])
def test_a07_invalid_plan_scope_owner_check_and_graph_cannot_publish(tmp_path, fault):
    selected(tmp_path)
    act(tmp_path, specification_action(tmp_path))
    accept(tmp_path, 'C1', 'design')
    action = plan_action(tmp_path)
    if fault == 'unknown_file': action['details']['task_details'][0]['files'] = ['outside.py']
    elif fault == 'wrong_owner': action['content']['tasks'][0]['owner'] = 'Other'
    elif fault == 'unknown_check': action['details']['task_details'][0]['obligations'][0].update(kind='verification', check_id='invented-pass', question='')
    else: action['content']['tasks'][0]['depends_on'] = ['verify-policy']
    before = core.read(tmp_path)
    try:
        preview = core.prepare(tmp_path, action, before['revision'])
    except core.DevelopmentError:
        assert core.read(tmp_path) == before
        return
    if preview['blockers']:
        with pytest.raises(core.DevelopmentError): core.commit(tmp_path, action, before['revision'], preview['approval_id'])
    else:
        result = core.commit(tmp_path, action, before['revision'], preview['approval_id'])['view']
        assert not result['changes'][0]['gates']['plan']['accepted']
        assert any(p['blockers'] for p in result['product']['planning'] if p['change_id'] == 'C1' and p['stage'] == 'plan')


def test_a05_stable_requirement_split_reorder_and_removed_mandatory_target(tmp_path):
    selected(tmp_path)
    action = specification_action(tmp_path)
    original = action['content']['requirements'][0]
    added = dict(original, id='restart-authorization', text='Authorization remains mandatory after restart.')
    action['content']['requirements'].append(added)
    criterion = dict(action['content']['criteria'][0], id='restart-security',
                     requirement_id=added['id'], text=added['text'])
    action['content']['criteria'].append(criterion)
    action['mappings'].append(dict(action['mappings'][0], specification_criterion_id=criterion['id'],
                                   reason='Split immediate rejection and persisted restart condition.'))
    action['details']['requirement_details'].append({'requirement_id': added['id'],
        'approach': added['text'], 'provenance': provenance()})
    condition = action['details']['condition_mappings'][0]
    condition.update(decision='split', requirement_ids=[original['id'], added['id']],
        criterion_ids=[action['content']['criteria'][0]['id'], criterion['id']],
        reason='Both required rejection and restart persistence retain the complete condition.')
    act(tmp_path, action)
    accept(tmp_path, 'C1', 'design')
    action['content']['requirements'].reverse()
    action['content']['criteria'].reverse()
    action['details']['requirement_details'].reverse()
    act(tmp_path, action)
    accept(tmp_path, 'C1', 'design')
    report = next(p for p in core.read(tmp_path)['product']['planning'] if p['change_id'] == 'C1' and p['stage'] == 'design')
    assert set(report['details']['condition_mappings'][0]['criterion_ids']) == {criterion['id'], action['content']['criteria'][1]['id']}
    assert report['content']['criteria'][0]['requirement_id'] == 'restart-authorization'
    weakened = copy.deepcopy(action)
    weakened['content']['criteria'][0]['required'] = False
    result = act(tmp_path, weakened)
    current = next(p for p in result['product']['planning'] if p['change_id'] == 'C1' and p['stage'] == 'design')
    assert 'product_required_criterion_weakened' in current['blockers']
    assert not result['changes'][0]['gates']['design']['accepted']
    omitted = copy.deepcopy(action)
    omitted['content']['criteria'].pop(0)
    before = core.read(tmp_path)
    with pytest.raises(core.DevelopmentError):
        core.prepare(tmp_path, omitted, before['revision'])
    assert core.read(tmp_path) == before


def test_a06_mandatory_conflict_requires_scope_review_and_cannot_be_silenced_by_valid_ids(tmp_path):
    selected(tmp_path)
    action = specification_action(tmp_path)
    action['details']['conflicts'] = [{'id': 'authorization-conflict',
        'text': 'A proposed source says authorization is optional, contradicting the required accepted condition.',
        'blocking': True, 'disposition': 'unresolved', 'impact': 'Would permit unauthorized access.',
        'provenance': provenance()}]
    result = act(tmp_path, action)
    report = next(p for p in result['product']['planning'] if p['change_id'] == 'C1' and p['stage'] == 'design')
    assert 'planning_conflict_unresolved' in report['blockers']
    action['details']['conflicts'][0]['disposition'] = 'upstream-review-required'
    result = act(tmp_path, action)
    report = next(p for p in result['product']['planning'] if p['change_id'] == 'C1' and p['stage'] == 'design')
    assert 'planning_scope_amendment_required' in report['blockers']
    assert not result['changes'][0]['gates']['design']['accepted']


def test_a16_bridge_cli_and_readable_export_bind_same_current_companion(tmp_path, capsys):
    import argparse
    import cc_panel_bridge as bridge
    import cc_product_cli as cli
    _, plan = planned(tmp_path)
    request = {'version': 1, 'id': 'planning-read', 'operation': 'product_planning_preview_v1',
        'project_root': str(tmp_path), 'change_id': 'C1', 'stage': 'plan'}
    response = bridge.dispatch(request)
    assert response['status'] == 'ok', response
    report = response['result']['planning_preview']
    args = argparse.Namespace(product_command='planning-preview', change_id='C1', stage='plan', json_output=True)
    assert cli.dispatch_product_command(args, tmp_path, argparse.ArgumentParser()) == 0
    output = json.loads(capsys.readouterr().out)
    assert output == report
    path = 'docs/controlcoding/plan.md'
    preview = documents.stage_export_preview(tmp_path, 'C1', 'plan', 'project_managed', path)
    assert preview['detail_revision'] == report['detail_revision']
    assert preview['selection_revision'] == report['selection_revision']
    documents.stage_export_commit(tmp_path, 'C1', 'plan', 'project_managed', path, preview)
    untouched = documents.stage_import_preview(tmp_path, 'C1', 'plan', path)
    assert not untouched['blockers'], untouched
    assert untouched['action']['details'] == plan['details']
    file = tmp_path / path
    marker, readable = file.read_text(encoding='utf-8').split('\n', 1)
    original = plan['details']['task_details'][0]['approach']
    edited = original + ' Retain authorization across recovery.'
    # Change the visible detail field while retaining the bound canonical marker.
    token = hashlib.sha256(json.dumps(('details', 'task_details', '@design-policy', 'approach')).encode()).hexdigest()[:24]
    begin = readable.index('<!-- cc-planning-field:' + token + ':begin -->')
    end = readable.index('<!-- cc-planning-field:' + token + ':end -->', begin)
    readable = readable[:begin] + readable[begin:end].replace(original, edited) + readable[end:]
    file.write_text(marker + '\n' + readable, encoding='utf-8')
    imported = documents.stage_import_preview(tmp_path, 'C1', 'plan', path)
    assert not imported['blockers'], imported
    assert imported['action']['details']['task_details'][0]['approach'] == edited
    saved = act(tmp_path, imported['action'])
    assert not saved['changes'][0]['gates']['plan']['accepted']
    assert imported['action']['content'] == plan['content']
    with pytest.raises(core.DevelopmentError, match='stage_import_base_stale'):
        documents.stage_import_preview(tmp_path, 'C1', 'plan', path)


def test_a10_exact_companion_size_and_metadata_overhead_are_distinct(tmp_path):
    selected(tmp_path)
    action = specification_action(tmp_path)
    details = action['details']
    details['facts'] = [{'id': 'bounded-' + str(i), 'kind': 'decision', 'text': '', 'provenance': provenance()}
                        for i in range(9)]
    def encoded(value):
        return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    available = 128 * 1024 - len(encoded(details))
    for fact in details['facts']:
        amount = min(16384, available)
        fact['text'] = 'x' * amount
        available -= amount
    assert available == 0 and len(encoded(details)) == 128 * 1024
    assert detail.validate_details(details, 'design', action['content']) == details
    extra = copy.deepcopy(details)
    extra['facts'][-1]['text'] += 'x'
    with pytest.raises(core.DevelopmentError, match='planning_detail_size_limit'):
        detail.validate_details(extra, 'design', action['content'])
    # Payload allowance is not an envelope/domain storage reservation.
    result = act(tmp_path, action)
    with SafeRoot(tmp_path) as safe:
        state, _, _ = __import__('cc_development_store').read_state(safe)
    envelope = state['product']['detailed']['details'][-1]
    assert len(encoded(envelope)) > len(encoded(details))
    assert result['product']['planning'][0]['detail_revision'] != 'absent'


def test_a10_detail_capacity_failure_leaves_original_acceptance_and_control_consistent(tmp_path, monkeypatch):
    import cc_development_store as store
    _, plan = planned(tmp_path)
    before = core.read(tmp_path)
    manifest = (tmp_path / '.controlcoding/development.json').read_bytes()
    changed = copy.deepcopy(plan)
    changed['details']['task_details'][0]['approach'] += ' Current reviewed recovery approach.'
    preview = core.prepare(tmp_path, changed, before['revision'])
    def full(*args, **kwargs):
        raise store.StoreError('planning_quota_exceeded')
    monkeypatch.setattr(store, 'ensure_planning_capacity', full)
    with pytest.raises(core.DevelopmentError, match='planning_quota_exceeded'):
        core.commit(tmp_path, changed, before['revision'], preview['approval_id'])
    assert (tmp_path / '.controlcoding/development.json').read_bytes() == manifest
    assert core.read(tmp_path) == before and before['changes'][0]['gates']['plan']['accepted']


def test_a10_real_detail_domain_accounting_and_exact_retained_quota_boundary(tmp_path, monkeypatch):
    import json
    import cc_development_store as store
    from cc_evidence_inputs import SafeRoot
    planned(tmp_path)
    before = core.read(tmp_path)
    manifest = (tmp_path / '.controlcoding/development.json').read_bytes()
    objects = list((tmp_path / store.OBJECTS).glob('*.json'))
    detail_objects = [path for path in objects if b'task_details' in path.read_bytes()]
    assert detail_objects
    assert all(json.loads(path.read_bytes())['domain'] == 'planning' for path in detail_objects)
    expected = sum(len(path.read_bytes()) for path in objects
                   if json.loads(path.read_bytes())['domain'] == 'planning')
    with SafeRoot(tmp_path) as safe:
        assert store.ensure_planning_capacity(safe) == expected
        monkeypatch.setattr(store, 'PLANNING_LIMIT', expected)
        assert store.ensure_planning_capacity(safe) == expected
        with pytest.raises(store.StoreError, match='planning_quota_exceeded'):
            store.ensure_planning_capacity(safe, 1)
    assert (tmp_path / '.controlcoding/development.json').read_bytes() == manifest
    assert core.read(tmp_path) == before
