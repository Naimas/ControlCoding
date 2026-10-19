"""V2 draft proposals retain one-change authority and terminal request policy."""
import copy
import json

import pytest

from test_cc_product_planning_detail import selected, specification_action
from test_cc_planning import create, options, prepare, reserve, finish, result
from test_cc_development_core import act
import cc_planning as planning
import cc_development_lib as core


def request(root, mode='reviewed'):
    selected(root)
    create(root)
    value = options(root, 'specification_v2')
    value.update(change_id='C1', feature_id='signup', editable=[{'kind': 'artifact', 'id': 'C1', 'fields': ['details']}])
    value['configuration']['config']['mode'] = mode
    # Saved role policy is the exact observed configuration for this request.
    policy = __import__('pathlib').Path(value['configuration']['role_policy_path'])
    content = json.loads(policy.read_text())
    content['roles']['concierge']['mode'] = mode
    policy.write_text(json.dumps(content))
    value['configuration']['role_policy_revision'] = __import__('hashlib').sha256(policy.read_bytes()).hexdigest()
    view, preview, _ = prepare(root, value)
    view, _ = reserve(root, view, preview)
    details = specification_action(root)['details']
    proposal = {'schema_version': 2, 'request_id': preview['request']['id'], 'edits': [
        {'op': 'set', 'kind': 'artifact', 'id': 'C1', 'field': 'details', 'value': details}],
        'claims': [{'edit_index': 0, 'kind': 'proposed_decision', 'source_id': '', 'section_id': '',
                    'reason': 'Proposed reviewed approach preserving required authorization.'}],
        'questions': [], 'conflicts': [], 'omissions': []}
    return view, preview, proposal


@pytest.mark.parametrize('mode', ['handoff', 'reviewed', 'direct'])
def test_a15_v2_manual_reviewed_and_direct_results_save_only_bound_draft(tmp_path, mode):
    view, preview, proposal = request(tmp_path, mode)
    view, _ = finish(tmp_path, view, preview, result(json.dumps(proposal)))
    review = planning.dispatch(tmp_path, {'operation': 'planning_review', 'session_id': 'S1',
        'request_id': preview['request']['id'], 'current_binding': preview['request']['configuration'], 'actor': 'Owner'})
    assert not review['blockers'], review
    assert review['action']['kind'] == 'product_specification_v2'
    assert review['action']['change_id'] == 'C1' and review['planning_review']['schema_version'] == 2
    before = core.read(tmp_path)
    after = act(tmp_path, review['action'])
    assert not after['changes'][0]['gates']['design']['accepted']
    assert after['changes'][1]['artifacts'] == before['changes'][1]['artifacts']
    assert view['session']['usage']['attempts'] == 1
    assert view['session']['usage']['network_calls'] == (0 if mode == 'handoff' else 1)


def test_a15_v2_reply_cannot_mutate_unsolicited_sibling_or_survive_base_drift(tmp_path):
    view, preview, proposal = request(tmp_path)
    malicious = copy.deepcopy(proposal)
    malicious['edits'][0]['id'] = 'C2'
    with pytest.raises(ValueError, match='planning_edit_outside_scope'):
        planning.validate_proposal(malicious, preview['request'])
    view, _ = finish(tmp_path, view, preview, result(json.dumps(proposal)))
    act(tmp_path, specification_action(tmp_path))
    before = core.read(tmp_path)
    review = planning.dispatch(tmp_path, {'operation': 'planning_review', 'session_id': 'S1',
        'request_id': preview['request']['id'], 'current_binding': preview['request']['configuration'], 'actor': 'Owner'})
    assert review['action'] is None and 'planning_base_changed' in review['blockers']
    assert core.read(tmp_path) == before


def test_a15_cancelled_v2_request_retains_result_without_partial_draft_or_retry(tmp_path):
    view, preview, proposal = request(tmp_path)
    before = core.read(tmp_path)
    view, _ = finish(tmp_path, view, preview, result('User cancelled the one requested attempt.', outcome='cancelled'))
    assert view['session']['requests'][0]['status'] == 'cancelled'
    review = planning.dispatch(tmp_path, {'operation': 'planning_review', 'session_id': 'S1',
        'request_id': preview['request']['id'], 'current_binding': preview['request']['configuration'], 'actor': 'Owner'})
    assert review['action'] is None and review['blockers']
    assert core.read(tmp_path) == before and view['session']['usage']['attempts'] == 1
