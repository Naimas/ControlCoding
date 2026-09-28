"""Canonical projections on disposable projects; no adopter command execution."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cc
import cc_project_map_controls as controls
import cc_project_map_definition as definition
import cc_project_map_sources as sources


def write(root, path, value):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv('CC_ACTIVE_MODULE', raising=False)
    (tmp_path / 'api.py').write_text('def run(): return 1\n', encoding='utf-8')
    (tmp_path / 'store.py').write_text('def save(): return 2\n', encoding='utf-8')
    return tmp_path


def args(root):
    request = dict(project_root=str(root), project_id='fixture', observed_at='2026-09-21T12:00:00Z', design_paths=[])
    preview = sources.preview_project_map_scope(request)['preview_id']
    review = definition.observe_reviewed(request, preview)['review']
    return request, preview, review['source_snapshot'], review['revision']


def observe(root):
    bound = args(root)
    return controls.observe_controls(*bound, controls.preview_controls(*bound)['scope_id'])


def bundle(result):
    return result['projection']['bundle']


def constraints(result, title):
    identifier = next(n['id'] for n in bundle(result)['nodes'] if n['title'] == title)
    return [c for c in bundle(result)['constraints'] if c['subject'] == identifier]


def registry(root, states=('completed', 'blocked')):
    write(root, controls.REGISTRY, {'schemaVersion': 'cc-feature-state/v1', 'wipLimit': 10,
          'features': [{'id': 'feature-' + state, 'title': state.title(), 'state': state,
                        'scope': 'api.py', 'acceptanceCriteria': ['CANARY criterion body'],
                        'verificationReceipts': [{'status': 'passed', 'secret': 'CANARY'}]} for state in states]})


def policy(root, mode='enforce'):
    write(root, '.controlcoding/active_module.json', {'module': 'api', 'mode': mode})
    write(root, '.feature-lock.json', {'version': 1, 'module': 'api', 'owns': ['api.py'],
          'shared_write': [], 'may_read': ['store.py']})


def test_empty_preview_and_read_preserve_all_bytes(project, monkeypatch):
    before = {str(p.relative_to(project)): p.read_bytes() for p in project.rglob('*') if p.is_file()}
    bound = args(project)
    monkeypatch.setattr(controls.Projection, 'features', lambda _: pytest.fail('preview read registry'))
    scope = controls.preview_controls(*bound)
    assert scope['host_coverage'] == 'unknown' and scope['limits']['helper_seconds'] == 15
    monkeypatch.undo()
    result = controls.observe_controls(*bound, scope['scope_id'])
    assert result['controls']['features']['state'] == 'absent'
    assert all(not g['current_required_pass'] for g in result['controls']['gates'])
    assert before == {str(p.relative_to(project)): p.read_bytes() for p in project.rglob('*') if p.is_file()}


def test_all_feature_states_are_reported_not_verified_or_mapped_to_code(project):
    registry(project, tuple(controls.cc_feature.FEATURE_STATES))
    result = observe(project)
    assert result['controls']['features']['count'] == 7
    statuses = result['projection']['statuses']
    assert all(not s['verified_accepted'] for s in statuses)
    assert any('feature_reported_blocked' in s['impediments'] for s in statuses)
    assert next(s for s in statuses if s['reported_lifecycle']['state'] == 'completed')['delivery'] == 'accepted'
    assert all(n['lifecycle']['owner'] != 'feature' for n in bundle(result)['nodes'] if n['kind'] == 'file')
    assert 'CANARY' not in json.dumps(result)


@pytest.mark.parametrize('change', ['schema', 'duplicates', 'wip', 'criteria', 'malformed'])
def test_invalid_registry_never_partially_promotes_features(project, change):
    registry(project)
    path = project / controls.REGISTRY
    value = json.loads(path.read_text())
    if change == 'schema': value['schemaVersion'] = 'unknown'
    if change == 'duplicates': value['features'].append(value['features'][0])
    if change == 'wip': value['wipLimit'] = True
    if change == 'criteria': value['features'][1]['acceptanceCriteria'] = [True]
    write(project, controls.REGISTRY, value)
    if change == 'malformed': path.write_text('{CANARY')
    result = observe(project)
    assert result['controls']['features']['state'] == 'invalid'
    assert not any(n['kind'] == 'feature' for n in bundle(result)['nodes'])
    assert 'CANARY' not in json.dumps(result)


@pytest.mark.parametrize('mode,expected', [('enforce', 'deny'), ('warn', 'unknown'), ('audit', 'unknown')])
def test_perimeter_uses_owner_matchers_and_may_read_never_grants(project, mode, expected):
    policy(project, mode)
    result = observe(project)
    assert constraints(result, 'api.py')[0]['decision'] == 'allow'
    assert constraints(result, 'store.py')[0]['decision'] == expected
    assert all(c['stage'] == 'predicted' and c['coverage'] == 'unknown'
               for name in ('api.py', 'store.py') for c in constraints(result, name))
    assert all(c['effective_observation'] == 'unknown' for s in result['projection']['statuses'] for c in s['constraints'])
    write(project, '.feature-lock.json', {'version': 1, 'module': 'api', 'owns': ['api.py'], 'shared_write': ['store.py']})
    assert constraints(observe(project), 'store.py')[0]['rule'] == 'shared_write_match'


@pytest.mark.parametrize('case', ['process', 'invalid-process', 'duplicate', 'missing', 'invalid-active'])
def test_active_context_conflicts_are_not_global_file_denials(project, monkeypatch, case):
    policy(project)
    if case == 'process': monkeypatch.setenv('CC_ACTIVE_MODULE', 'foreign')
    if case == 'invalid-process': monkeypatch.setenv('CC_ACTIVE_MODULE', 'CANARY secret')
    if case == 'duplicate': write(project, 'nested/.feature-lock.json', {'version': 1, 'module': 'api', 'owns': []})
    if case == 'missing': (project / '.feature-lock.json').unlink()
    if case == 'invalid-active': write(project, '.controlcoding/active_module.json', {'module': 'api', 'mode': 'unsafe'})
    result = observe(project)
    assert result['controls']['active_module']['coverage'] == 'conflicting'
    assert not constraints(result, 'api.py') and not constraints(result, 'store.py')
    assert 'CANARY' not in json.dumps(result)


def test_canonical_precedence_protection_approval_and_no_token_consumption(project):
    policy(project)
    write(project, '.claude/active_module.json', {'module': 'foreign', 'mode': 'enforce'})
    write(project, '.controlcoding/cc_config.json', {'protected_zones': {'deny': ['api.py'], 'warn': ['store.py']}})
    write(project, '.controlcoding/lift_request.json', {'status': 'PENDING', 'file': 'api.py', 'approval_token': 'CANARY', 'reason': 'CANARY'})
    write(project, '.controlcoding/hooks_lifted.json', {'approval_token': 'CANARY', 'status': 'ACTIVE'})
    (project / 'check_boundaries.py').write_text('pass\n')
    registry(project)
    before = {str(p): p.read_bytes() for p in project.rglob('*') if p.is_file()}
    result = observe(project)
    assert result['controls']['active_module']['module'] == 'api'
    assert {c['kind'] for c in constraints(result, 'api.py')} == {'policy', 'feature_perimeter', 'approval'}
    assert any(c['decision'] == 'deny' and c['rule'] == 'self_protection' for c in constraints(result, 'check_boundaries.py'))
    assert not any(c['kind'] == 'approval' for c in constraints(result, 'store.py'))
    assert next(c for c in constraints(result, 'store.py') if c['kind'] == 'policy')['decision'] == 'unknown'
    assert 'CANARY' not in json.dumps(result)
    assert before == {str(p): p.read_bytes() for p in project.rglob('*') if p.is_file()}


@pytest.mark.parametrize('config', [{'protected_zones': {'deny': 3}}, {'protected_zones': [{'path': '../private', 'level': 'deny'}]}], ids=['bad-list', 'escape'])
def test_bad_configuration_retains_mandatory_protection(project, config):
    write(project, '.controlcoding/cc_config.json', config)
    (project / 'templates/hooks').mkdir(parents=True)
    (project / 'templates/hooks/custom.py').write_text('pass\n')
    result = observe(project)
    assert 'invalid_protected_zones' in result['controls']['notices']
    assert any(c['decision'] == 'deny' for c in constraints(result, 'custom.py'))


def contract(root, kind='verification', fail=False):
    rows = [{'id': f'check-{i}', 'kind': 'targeted', 'required': True,
             'command': f'"{Path(sys.executable).as_posix()}" -B -c "' + ('raise SystemExit(1)' if fail else 'pass') + '"'} for i in range(2)]
    data = {'schemaVersion': 1, 'requiredKinds': ['targeted'], 'suites': rows}
    if kind == 'invariants':
        data = {'schemaVersion': 1, 'invariants': [{**r, 'kind': 'domain', 'domain': 'testing', 'severity': 'warning',
                 'status': 'active', 'property': 'fixture', 'threshold': 'zero'} for r in rows]}
    write(root, 'controlcoding.' + kind + '.json', data)


def run_fixture(root, kind='verification', subset=False):
    # Only this test helper executes the explicit disposable-fixture commands.
    opts = {('suite_ids' if kind == 'verification' else 'invariant_ids'): ['check-0']} if subset else {}
    with contextlib.redirect_stdout(io.StringIO()):
        return (cc.cmd_verify_run if kind == 'verification' else cc.cmd_invariants_run)(root, json_output=True, **opts)


def assert_gate_parity(root, kind):
    prepared = cc._evidence_prepare(root, kind)
    owner = cc._evidence_history(root, kind, prepared)
    result = observe(root)
    gate = next(g for g in result['controls']['gates'] if g['kind'] == kind)
    assert (gate['state'], gate['current_required_pass'], gate['reasons']) == (
        owner['assessment']['state'], owner['assessment']['currentRequiredPass'], owner['assessment']['reasons'])
    assert gate['outcome'] == (owner['latest'] or {}).get('status', 'unknown')
    assert all(a['subject'] == next(n['id'] for n in bundle(result)['nodes'] if n['kind'] == 'system') for a in bundle(result)['assessments'])
    assert result['projection']['summary']['verified_units'] == 0
    return gate


@pytest.mark.parametrize('kind', ['verification', 'invariants'])
def test_real_receipt_current_newer_subset_stale_and_failed_owner_parity(project, kind):
    contract(project, kind)
    assert run_fixture(project, kind) == 0
    assert assert_gate_parity(project, kind)['current_required_pass']
    assert run_fixture(project, kind, subset=True) == 0
    gate = assert_gate_parity(project, kind)
    assert gate['outcome'] == 'passed_subset' and not gate['current_required_pass']
    (project / 'api.py').write_text('pass # source changed\n')
    assert not assert_gate_parity(project, kind)['current_required_pass']
    contract(project, kind, fail=True)
    assert run_fixture(project, kind) != 0
    gate = assert_gate_parity(project, kind)
    assert gate['outcome'] == 'failed' and not gate['current_required_pass']


@pytest.mark.parametrize('case', ['invalid', 'incomplete', 'context'])
def test_newer_or_changed_evidence_never_falls_back_to_green(project, case, monkeypatch):
    contract(project)
    assert run_fixture(project) == 0
    folder = project / '.controlcoding/verification_receipts'
    if case == 'context':
        monkeypatch.setattr(cc.cc_evidence_inputs, 'runner_context', lambda *_: {'complete': False, 'digest': None, 'reasons': ['fixture_unknown']})
    else:
        assert run_fixture(project, subset=True) == 0
        prepared = cc._evidence_prepare(project, 'verification')
        receipt = project / cc._evidence_history(project, 'verification', prepared)['latest']['path']
        assert len(list(folder.glob('*.json'))) == 2  # Older full pass remains on disk.
        if case == 'invalid': receipt.write_text('{CANARY invalid')
        else:
            data = json.loads(receipt.read_text())
            data.update(status='running', executionState='running', finishedAt=None)
            receipt.write_text(json.dumps(data))
    assert not assert_gate_parity(project, 'verification')['current_required_pass']


def test_declared_environment_change_uses_canonical_context_without_value_leak(project, monkeypatch):
    contract(project)
    path = project / 'controlcoding.verification.json'
    value = json.loads(path.read_text())
    value['evidenceInputs'] = {'schemaVersion': 1, 'environmentNames': ['MAP_FIXTURE_ENV'], 'extraPaths': ['.env']}
    write(project, path.name, value)
    (project / '.env').write_text('CANARY extra body')
    monkeypatch.setenv('MAP_FIXTURE_ENV', 'original')
    assert run_fixture(project) == 0
    assert assert_gate_parity(project, 'verification')['current_required_pass']
    monkeypatch.setenv('MAP_FIXTURE_ENV', 'CANARY changed environment')
    bound = args(project)
    preview = controls.preview_controls(*bound)
    assert preview['environment_names'] == ['MAP_FIXTURE_ENV'] and preview['extra_paths'] == ['.env']
    gate = assert_gate_parity(project, 'verification')
    assert gate['state'] == 'context_changed' and not gate['current_required_pass']
    assert 'CANARY' not in json.dumps(observe(project))


def test_preview_binding_and_changed_source(project):
    bound = args(project)
    preview = controls.preview_controls(*bound)
    with pytest.raises(controls.ControlsError, match='preview_mismatch'):
        controls.observe_controls(*bound, '0' * 64)
    (project / 'api.py').write_text('pass # changed\n')
    with pytest.raises(controls.ControlsError, match='stale_snapshot'):
        controls.observe_controls(*bound, preview['scope_id'])


def test_contract_scope_change_requires_new_preview(project):
    bound = args(project)
    preview = controls.preview_controls(*bound)
    contract(project)
    with pytest.raises(controls.ControlsError, match='preview_mismatch'):
        controls.observe_controls(*bound, preview['scope_id'])


@pytest.mark.parametrize('case', ['oversize', 'hardlink', 'concurrent', 'time'])
def test_unsafe_or_changing_control_input_clears_observation(project, case, monkeypatch):
    registry(project)
    if case == 'oversize': (project / controls.REGISTRY).write_bytes(b' ' * (1048576 + 1))
    if case == 'hardlink': os.link(project / controls.REGISTRY, project / '.controlcoding/alias.json')
    if case == 'concurrent':
        original = controls.Projection.gates
        def mutate(self):
            original(self)
            (project / controls.REGISTRY).write_text('{}')
        monkeypatch.setattr(controls.Projection, 'gates', mutate)
    if case == 'time': monkeypatch.setattr(controls.Inputs, 'tick', lambda _: controls.require(False, 'time_limit'))
    with pytest.raises((controls.ControlsError, PermissionError)):
        observe(project)
