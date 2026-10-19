"""Independent planning publication/quota/replay and detached recovery controls."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import cc_planning as planning
import cc_development_store as store
import cc_development_sessions as sessions
import cc_development_bundle as bundle
import cc_development_archive as archive
import cc_evidence_inputs as inputs
from cc_evidence_inputs import SafeRoot
from test_cc_planning import options, prepare, reserve, proposal, result, finish


def create(root):
    return planning.dispatch(root, {'operation': 'planning_create', 'session_id': 'S1',
        'operation_id': 'a' * 64, 'actor': 'Author', 'task_id': 'T1'})


def append_payload(revision):
    return {'operation': 'planning_append', 'session_id': 'S1', 'expected_revision': revision,
        'operation_id': 'b' * 64, 'actor': 'Author', 'role': 'user', 'text': 'A retained independent question',
        'questions': ['Which fields are mandatory?'], 'choices': [], 'reviewer_seconds': 7}


def objects(root):
    return {p.name: p.read_bytes() for p in (root / '.controlcoding/development/objects').glob('*.json')}


def test_orphan_object_retry_at_exact_quota_does_not_charge_it_twice(tmp_path, monkeypatch):
    initial = create(tmp_path)
    payload = append_payload(initial['revision'])
    before = objects(tmp_path)
    real = SafeRoot.write_atomic
    def interrupt_head(self, relative, value, **kwargs):
        if relative.endswith('/sessions/S1.json'):
            raise OSError('Injected head publication interruption')
        return real(self, relative, value, **kwargs)
    with monkeypatch.context() as injected:
        injected.setattr(SafeRoot, 'write_atomic', interrupt_head)
        with pytest.raises(OSError): planning.dispatch(tmp_path, payload)
    orphan = objects(tmp_path)
    assert len(orphan) == len(before) + 1
    assert planning.dispatch(tmp_path, {'operation': 'planning_read', 'session_id': 'S1'})['revision'] == initial['revision']
    with SafeRoot(tmp_path) as safe:
        used = store.ensure_planning_capacity(safe)
    monkeypatch.setattr(store, 'PLANNING_LIMIT', used)
    retried = planning.dispatch(tmp_path, payload)
    assert retried['session']['questions'] == ['Which fields are mandatory?']
    assert retried['session']['reviewer_seconds'] == 7
    assert objects(tmp_path) == orphan
    replay = planning.dispatch(tmp_path, payload)
    assert replay['revision'] == retried['revision'] and objects(tmp_path) == orphan


def test_detached_session_bundle_rejects_hash_valid_control_domain(tmp_path):
    create(tmp_path)
    with SafeRoot(tmp_path) as safe:
        with __import__('cc_development_lib').storage.transaction(safe):
            collected = bundle.collect(safe)
    # A hash-valid object can still have the wrong authority/storage domain.
    ref = collected['sessions']['heads']['S1']['object']
    bad = copy.deepcopy(collected['objects'][ref])
    bad['domain'] = 'control'
    bad_ref = __import__('hashlib').sha256(store._encoded(bad)).hexdigest()
    invalid = copy.deepcopy(collected)
    invalid['sessions']['heads']['S1']['object'] = bad_ref
    invalid['sessions']['objects'] = [bad_ref if x == ref else x for x in invalid['sessions']['objects']]
    invalid['objects'][bad_ref] = bad
    del invalid['objects'][ref]
    invalid['integrity'] = inputs.digest({k: v for k, v in invalid.items() if k != 'integrity'})
    with SafeRoot(tmp_path) as safe:
        with pytest.raises(ValueError, match='session_object_domain_invalid'):
            bundle.validate(safe, invalid)


def test_cancel_prepared_request_releases_capacity_without_an_attempt(tmp_path):
    create(tmp_path)
    view, preview, _ = prepare(tmp_path)
    assert view['session']['reserved_bytes'] > 0
    cancelled, payload = finish(tmp_path, view, preview, result('', 'cancelled'))
    assert cancelled['session']['reserved_bytes'] == 0
    assert cancelled['session']['usage']['attempts'] == 0
    assert cancelled['session']['usage']['network_calls'] == 0
    assert planning.dispatch(tmp_path, payload)['revision'] == cancelled['revision']


def test_reserved_request_backup_restore_keeps_unknown_attempt_and_terminal_credit(tmp_path):
    create(tmp_path)
    view, preview, _ = prepare(tmp_path)
    reserved, payload = reserve(tmp_path, view, preview)
    captured = archive.backup(tmp_path)
    assert captured['sessions']['heads']['S1']['reservedBytes'] > 0
    (tmp_path / '.controlcoding/development.json').unlink()
    (tmp_path / '.controlcoding/development/sessions/S1.json').unlink()
    recovery = archive.prepare_restore(tmp_path, captured, 'Owner', 'Restore interrupted planning request')
    archive.restore(tmp_path, captured, 'Owner', 'Restore interrupted planning request', recovery['approval_id'])
    restored = planning.dispatch(tmp_path, {'operation': 'planning_read', 'session_id': 'S1'})
    assert restored['revision'] == reserved['revision']
    assert restored['session']['requests'][0]['status'] == 'reserved'
    assert restored['session']['usage']['attempts'] == 1
    assert restored['session']['reserved_bytes'] == reserved['session']['reserved_bytes']
    assert planning.dispatch(tmp_path, payload)['revision'] == reserved['revision']
    terminal, _ = finish(tmp_path, restored, preview, result('', 'interrupted'))
    assert terminal['session']['reserved_bytes'] == 0
    assert terminal['session']['usage']['attempts'] == 1


def source_options(root):
    from test_cc_product_core import document, saved
    from cc_product_documents import observe_source
    path = root / 'brief.md'
    path.write_text('# Selected\nA mandatory local-only requirement.\n', encoding='utf-8')
    source = observe_source(root, 'brief.md')
    source['sections'][0]['selected'] = True
    doc = document()
    doc['sources'] = [source]
    saved(root, doc)
    create(root)
    configured = options(root)
    configured['sources'] = [{'source_id': source['source_id'], 'section_id': source['sections'][0]['id']}]
    return configured, path


def test_lost_prepare_reply_replays_original_request_after_source_drift(tmp_path):
    configured, source = source_options(tmp_path)
    original, preview, payload = prepare(tmp_path, configured)
    source.write_text('# Selected\nChanged after durable prepare.\n', encoding='utf-8')
    replay = planning.dispatch(tmp_path, payload)
    assert replay['revision'] == original['revision']
    assert replay['session']['requests'][0]['request'] == preview['request']
    assert replay['session']['reserved_bytes'] == original['session']['reserved_bytes']


def test_lost_finish_reply_replays_terminal_once_after_source_drift(tmp_path):
    configured, source = source_options(tmp_path)
    view, preview, _ = prepare(tmp_path, configured)
    view, _ = reserve(tmp_path, view, preview)
    completed, payload = finish(tmp_path, view, preview, result(json.dumps(proposal(preview['request']))))
    source.write_text('# Selected\nChanged after terminal commit.\n', encoding='utf-8')
    replay = planning.dispatch(tmp_path, payload)
    assert replay['revision'] == completed['revision']
    assert replay['session']['usage']['attempts'] == 1
    review = planning.dispatch(tmp_path, {'operation': 'planning_review', 'session_id': 'S1',
        'request_id': preview['request']['id'], 'current_binding': preview['request']['configuration'], 'actor': 'Owner'})
    assert review['action'] is None and 'planning_source_changed' in review['blockers']


def test_orphan_finish_retry_reuses_original_result_after_policy_drift(tmp_path, monkeypatch):
    create(tmp_path)
    view, preview, _ = prepare(tmp_path)
    view, _ = reserve(tmp_path, view, preview)
    value = result(json.dumps(proposal(preview['request'])))
    payload = {'operation': 'planning_finish', 'session_id': 'S1', 'expected_revision': view['revision'],
        'operation_id': 'e' * 64, 'actor': 'Owner', 'request_id': preview['request']['id'],
        'current_binding': preview['request']['configuration'], 'result': value}
    before = objects(tmp_path)
    real = SafeRoot.write_atomic
    def interrupt_head(self, relative, value, **kwargs):
        if relative.endswith('/sessions/S1.json'):
            raise OSError('Injected terminal head interruption')
        return real(self, relative, value, **kwargs)
    with monkeypatch.context() as injected:
        injected.setattr(SafeRoot, 'write_atomic', interrupt_head)
        with pytest.raises(OSError): planning.dispatch(tmp_path, payload)
    orphan = objects(tmp_path)
    assert len(orphan) == len(before) + 1
    Path(preview['request']['configuration']['role_policy_path']).write_text('{}')
    with SafeRoot(tmp_path) as safe:
        used = store.ensure_planning_capacity(safe)
    monkeypatch.setattr(store, 'PLANNING_LIMIT', used)
    completed = planning.dispatch(tmp_path, payload)
    assert completed['session']['requests'][0]['result'] == value
    assert completed['session']['usage']['attempts'] == 1
    assert completed['session']['reserved_bytes'] == 0
    assert objects(tmp_path) == orphan
    review = planning.dispatch(tmp_path, {'operation': 'planning_review', 'session_id': 'S1',
        'request_id': preview['request']['id'], 'current_binding': preview['request']['configuration'], 'actor': 'Owner'})
    assert review['action'] is None and 'planning_configuration_changed' in review['blockers']


def test_terminal_credit_covers_accepted_worst_case_serialization_at_quota(tmp_path, monkeypatch):
    create(tmp_path)
    view, preview, _ = prepare(tmp_path)
    view, _ = reserve(tmp_path, view, preview)
    with SafeRoot(tmp_path) as safe:
        used = store.ensure_planning_capacity(safe)
    monkeypatch.setattr(store, 'PLANNING_LIMIT', used)
    value = result('\u0001' * 12000, 'failed')
    value['error'] = 'e' * 2000
    terminal, _ = finish(tmp_path, view, preview, value)
    assert terminal['session']['reserved_bytes'] == 0
    assert terminal['session']['requests'][0]['result'] == value
    assert terminal['session']['usage']['attempts'] == 1


def test_terminal_credit_covers_unicode_output_and_max_prompt_binding_at_quota(tmp_path, monkeypatch):
    import hashlib
    create(tmp_path)
    configured = options(tmp_path)
    configured['configuration']['config']['systemPrompt'] = '\u00e9' * 4000
    policy = Path(configured['configuration']['role_policy_path'])
    role_file = json.loads(policy.read_bytes())
    role_file['roles']['concierge'] = configured['configuration']['config']
    policy.write_text(json.dumps(role_file), encoding='utf-8')
    configured['configuration']['role_policy_revision'] = hashlib.sha256(policy.read_bytes()).hexdigest()
    view, preview, _ = prepare(tmp_path, configured)
    # Consume the actual reserve-event cost before measuring remaining terminal
    # credit. Lower the real global quota, not its evaluator or byte accounting.
    view, _ = reserve(tmp_path, view, preview)
    with SafeRoot(tmp_path) as safe:
        used = store.ensure_planning_capacity(safe)
    monkeypatch.setattr(store, 'PLANNING_LIMIT', used)
    # Mixed supplementary/control characters attain a larger escaped envelope
    # than all-supplementary text while respecting both advertised Core bounds.
    value = result('\U0001f600' * 6922 + '\u0001' * 5076 + '\u00e9' * 2, 'failed')
    value['error'] = '\U0001f600' * 2000
    assert len(value['text']) <= 12000 and len(value['text'].encode('utf-8')) == 32768
    terminal = planning.dispatch(tmp_path, {'operation': 'planning_finish', 'session_id': 'S1',
        'expected_revision': view['revision'], 'operation_id': 'e' * 64,
        'actor': '\U0001f600' * 160, 'request_id': preview['request']['id'],
        'current_binding': preview['request']['configuration'], 'result': value})
    assert terminal['session']['reserved_bytes'] == 0
    assert terminal['session']['requests'][0]['result'] == value
    assert terminal['session']['usage']['attempts'] == 1
