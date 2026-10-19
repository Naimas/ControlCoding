"""Exact predecessor applicability and read-only historical inspection."""
import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cc_development_compatibility as compat


def target():
    from cc_development_lib.method import IDENTITY
    return dict(IDENTITY)


def test_boundary_checker_qualifies_once_and_does_not_trust_later_policy_mutation(monkeypatch):
    current = target()
    calls = []
    monkeypatch.setattr(compat, '_trusted_target', lambda value: calls.append(copy.deepcopy(value)))
    policy = compat._policy(compat.PLANNING, current, 'pw05_observed_progress_unaffected_planning_decisions_v1')
    checker = compat.identity_checker(current, [policy])
    for _ in range(100):
        assert checker(compat.PLANNING) and checker(current)
    assert calls == [current]
    policy['source']['digest'] = 'f' * 64
    assert not checker(policy['source'])
    assert checker(compat.PLANNING)
    # A new validation boundary always requalifies installed owner bytes.
    compat.identity_checker(current, [compat._policy(compat.PLANNING, current, 'pw05_observed_progress_unaffected_planning_decisions_v1')])
    assert calls == [current, current]


def state():
    return {'schema_version': 1, 'enabled': False, 'profile': 'routine',
            'method': dict(compat.PRIOR), 'project_identity': 'c' * 64,
            'changes': [], 'operations': []}


@pytest.mark.parametrize('method,accepted_count', [(compat.PRIOR, 2048), (compat.RETAINED, 32768)])
def test_historical_inspection_uses_source_registry_capacity(method, accepted_count):
    value = state()
    value['method'] = copy.deepcopy(method)
    value['operations'] = [{} for _ in range(accepted_count)]
    result = compat.inspect_state(value)
    assert result['operationCount'] == accepted_count
    assert result['strictHistoricalValidationRequired'] and not result['migrationPerformed']
    value['operations'].append({})
    with pytest.raises(compat.CompatibilityError, match='unsupported_historical_state'):
        compat.inspect_state(value)


def test_retained_registry_accepts_extended_original_change_history_for_inspection():
    value = state()
    value['method'] = copy.deepcopy(compat.RETAINED)
    previous = 'absent'
    events = []
    for index in range(2049):
        event = {'action': {'index': index}, 'result': {}, 'timestamp': 'retained', 'previous': previous}
        event['digest'] = hashlib.sha256(json.dumps(event, sort_keys=True, ensure_ascii=True,
                                                   separators=(',', ':')).encode()).hexdigest()
        previous = event['digest']
        events.append(event)
    value['changes'] = [{'id': 'C1', 'method': copy.deepcopy(compat.PRIOR), 'closed': False, 'history': events}]
    result = compat.inspect_state(value)
    assert result['changes'][0]['historyRecords'] == 2049
    assert result['strictHistoricalValidationRequired']
    value['method'] = copy.deepcopy(compat.PRIOR)
    with pytest.raises(compat.CompatibilityError, match='invalid_historical_change'):
        compat.inspect_state(value)


def test_known_pair_preserves_exact_original_and_requires_current_execution_checks():
    original = dict(compat.PLANNING)
    current = target()
    policy = compat.trusted_pair(original, current)
    assert compat.change_identity_allowed(original, current, policy)
    assert policy['applicable_decision_stages'] == ['intent', 'design', 'plan']
    assert policy['execution_evidence'] == 'historical_requires_current_checks'
    assert original == compat.PLANNING and current == target()


@pytest.mark.parametrize('field,value', [('version', '0.4.0'), ('digest', 'e' * 64),
                                      ('implementation_digest', 'f' * 64), ('evaluator_version', '2')])
def test_modified_predecessor_is_not_trusted(field, value):
    source = {**compat.PLANNING, field: value}
    with pytest.raises(compat.CompatibilityError, match='unsupported_compatibility_pair'):
        compat.trusted_pair(source, target())


@pytest.mark.parametrize('mutation', [lambda p: p.update(equivalent=True),
                                    lambda p: p.update(policy='user_equivalent'),
                                    lambda p: p.update(applicable_decision_stages=['build']),
                                    lambda p: p['target'].update(digest='d' * 64)])
def test_caller_flags_and_policy_tampering_cannot_authorize(mutation):
    policy = compat.trusted_pair(compat.PLANNING, target())
    mutation(policy)
    assert not compat.change_identity_allowed(compat.PLANNING, target(), policy)
    assert not compat.change_identity_allowed(target(), target(), policy)


def test_unknown_target_version_and_extra_identity_fields_fail():
    for current in ({**target(), 'version': '0.12.0'}, {**target(), 'equivalent': True},
                    {**target(), 'digest': 'a' * 64}):
        assert current != target()
        with pytest.raises(compat.CompatibilityError):
            compat.trusted_pair(compat.PLANNING, current)
    assert not compat.change_identity_allowed(compat.PRIOR, target(), {'equivalent': True})


def test_exact_cd10_chain_cannot_skip_or_reorder_frozen_predecessor():
    chain = compat.trusted_chain(compat.PRIOR, target())
    assert chain[0] == compat.trusted_pair(compat.PRIOR, compat.RETAINED)
    assert compat.change_identity_allowed(compat.PRIOR, target(), chain)
    assert compat.change_identity_allowed(compat.RETAINED, target(), chain)
    assert not compat.change_identity_allowed(compat.PRIOR, target(), list(reversed(chain)))
    assert not compat.change_identity_allowed(compat.PRIOR, target(), chain[1:])
    with pytest.raises(compat.CompatibilityError):
        compat.trusted_pair(compat.PRIOR, target())


def test_inspection_preserves_candidate_and_history_without_creating_acceptance():
    value = state()
    record = {'action': {'kind': 'create', 'change_id': 'example'},
              'result': {}, 'timestamp': '2026-10-02T12:00:00Z', 'previous': 'absent'}
    record['digest'] = hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=True,
                                                separators=(',', ':')).encode()).hexdigest()
    value['enabled'] = True
    value['changes'] = [{'id': 'example', 'method': dict(compat.PRIOR), 'closed': False,
                         'history': [record], 'candidate': {'digest': 'd' * 64}, 'decisions': []}]
    before = copy.deepcopy(value)
    result = compat.inspect_state(value, target())
    assert value == before
    assert result['changes'][0]['historyHead'] == record['digest']
    assert result['strictHistoricalValidationRequired'] and not result['migrationPerformed']
    assert 'decisions' not in result['changes'][0]
    value['changes'][0]['history'][0]['result']['approved'] = True
    with pytest.raises(compat.CompatibilityError, match='invalid_history'):
        compat.inspect_state(value)


def write_registry(root):
    controls = root / '.controlcoding'
    controls.mkdir()
    (controls / 'development.json').write_text(json.dumps(state()), encoding='utf-8')
    return controls


@pytest.mark.parametrize('directory,kind,status', [
    ('development_job_receipts', 'monitor', 'running'),
    ('development_job_receipts', 'deploy', 'blocked'),
    ('development_job_receipts', 'rollback', 'blocked'),
    ('development_execution_receipts', 'execution', 'running'),
    ('development_apply_receipts', 'apply', 'partial'),
])
def test_interrupted_effects_block_and_inspection_writes_nothing(tmp_path, directory, kind, status):
    controls = write_registry(tmp_path)
    receipts = controls / directory
    receipts.mkdir()
    (receipts / 'example.json').write_text(json.dumps({'state': status, 'kind': kind}), encoding='utf-8')
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result = compat.inspect_project(tmp_path)
    assert result['migrationBlocked'] and len(result['blockingEffects']) == 1
    assert before == {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


def test_unknown_or_duplicate_registry_does_not_claim_migration(tmp_path, capsys):
    controls = write_registry(tmp_path)
    path = controls / 'development.json'
    path.write_text('{"schema_version":1,"schema_version":1}', encoding='utf-8')
    assert compat.main(['--project-root', str(tmp_path)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report['error'] == 'duplicate_json_key' and report['migrationPerformed'] is False


def test_invalid_receipt_is_not_ignored(tmp_path):
    controls = write_registry(tmp_path)
    receipts = controls / 'development_job_receipts'
    receipts.mkdir()
    (receipts / 'invalid.json').write_text('{"state":"unknown"}', encoding='utf-8')
    with pytest.raises(compat.CompatibilityError, match='invalid_receipt_state'):
        compat.inspect_project(tmp_path)
