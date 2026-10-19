"""Authentic 0.10 profile continuity and original detached closure, without relabelling."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import cc_development_lib as core
import cc_development_migration as migration
import cc_development_store as store
import cc_development_bundle as bundle
import cc_development_archive as backups
import cc_development_sessions as sessions
import cc_development_history as history
from cc_development_compatibility import PLANNING
from cc_development_lib.method import IDENTITY, digest
from cc_evidence_inputs import SafeRoot
from test_cc_development_core import act, logical_state, corrupt_logical_state
from test_cc_product_core import document


PW04_PROGRAM = r'''
import json,sys
from pathlib import Path
runtime=Path(sys.argv[1]);sys.path[:0]=[str(runtime),str(runtime.parent/'tests')]
from cc_development_lib.method import IDENTITY
assert IDENTITY==json.loads(sys.argv[3]),IDENTITY
import cc_development_lib as core
assert Path(core.__file__).resolve().is_relative_to(runtime)
import cc_development_history as history,cc_development_sessions as sessions,cc_planning
sys.path.append(sys.argv[4])
import cc_product_progress_fixture as fixture
assert Path(fixture.core.__file__).resolve().is_relative_to(runtime)
full=sys.argv[5]=='full'
root=Path(sys.argv[2]);fixture.fixture_seed(root,with_origins=True,defer_origins=full)
if full:
 fixture.fixture_verify(root,change_ids=('C1',));fixture.fixture_release(root,link_child=True,change_ids=('C1',),checkpoint=False)
 fixture.fixture_finish_origins(root)
v=core.read(root);sessions.create(root,'original',v['revision'],'Owner','1'*64)
sessions.append(root,'original',sessions.read(root,'original')['revision'],'2'*64,'Owner','user','Original 0.10 detailed planning session.')
cc_planning.dispatch(root,{'operation':'planning_create','session_id':'discussion','operation_id':'3'*64,'actor':'Owner','task_id':'ProductPlanning'})
p=history.checkpoint_prepare(root);history.checkpoint_commit(root,p['revision'],p['approval_id'],'Owner')
print(json.dumps(core.read(root)))
'''


PW04_UPGRADED_PROGRAM = r'''
import json,sys
from pathlib import Path
runtime=Path(sys.argv[1]);sys.path[:0]=[str(runtime),str(runtime.parent/'tests')]
from cc_development_lib.method import IDENTITY
assert IDENTITY==json.loads(sys.argv[3]),IDENTITY
import cc_development_lib as core,cc_development_migration as migration,cc_development_history as history
from test_cc_development_core import act
from test_cc_product_planning_detail import specification_action,plan_action,accept
root=Path(sys.argv[2]);p=migration.preview(root)
assert not p['blockers'],p
migration.migrate(root,p['expected_revision'],p['approval_id'],'Owner')
view=core.read(root);selection=view['product']['increments'][0]['selection_revision']
act(root,{'kind':'product_planning_upgrade','change_id':'C1','selection_revision':selection,'actor':'Owner','reason':'Explicitly upgrade only this authentic older change.'})
act(root,specification_action(root,'C1'));accept(root,'C1','design')
act(root,plan_action(root,'C1'));accept(root,'C1','plan')
p=history.checkpoint_prepare(root);history.checkpoint_commit(root,p['revision'],p['approval_id'],'Owner')
print(json.dumps(core.read(root)))
'''


def generate_pw04(tmp_path, *, full):
    runtime = os.environ.get('CC_TEST_PW04_RUNTIME')
    if not runtime:
        pytest.skip('Authentic immutable 0.10 runtime required through CC_TEST_PW04_RUNTIME; mandatory local PW05 acceptance supplies it')
    runtime = Path(runtime).resolve()
    assert (runtime / 'cc_development_lib/method.py').is_file()
    assert (runtime.parent / 'tests/test_cc_product_planning_detail.py').is_file()
    generated = subprocess.run([sys.executable, '-B', '-c', PW04_PROGRAM, str(runtime), str(tmp_path), json.dumps(PLANNING),
                               str(Path(__file__).parent), 'full' if full else 'early'],
        # Complete original 0.10 fixture setup measured 806.29s.
        # 960s retains 153.71s setup reserve; early negative fixtures stay 300s.
        cwd=tmp_path, capture_output=True, text=True, encoding='utf-8', timeout=960 if full else 300,
        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1'})
    assert generated.returncode == 0, generated.stdout + generated.stderr
    original = json.loads(generated.stdout)
    assert {key: original['method'][key] for key in PLANNING} == PLANNING
    assert next(c for c in original['changes'] if c['id'] == 'C1')['gates']['plan']['accepted']
    return tmp_path, original


@pytest.fixture
def genuine_pw04(tmp_path):
    return generate_pw04(tmp_path, full=True)


@pytest.fixture(scope='module')
def genuine_pw04_early_baseline(tmp_path_factory):
    root, before = generate_pw04(tmp_path_factory.mktemp('pw04early'), full=False)
    files = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    folders = {p.relative_to(root) for p in root.rglob('*') if p.is_dir()}
    identity = root.stat().st_ino
    return root, before, files, folders, identity


@pytest.fixture
def genuine_pw04_early(genuine_pw04_early_baseline):
    root, before, files, folders, identity = genuine_pw04_early_baseline
    # Reuse authenticated generation at the SAME physical root. Restoring its
    # exact original bytes between independent attacks preserves root identity;
    # relocating/resealing the registry would invalidate the control.
    assert root.stat().st_ino == identity
    for path in root.rglob('*'):
        if path.is_file() and path.relative_to(root) not in files: path.unlink()
    for relative, raw in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_bytes() != raw: target.write_bytes(raw)
    for path in sorted((p for p in root.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if path.relative_to(root) not in folders: path.rmdir()
    assert root.stat().st_ino == identity
    return root, copy.deepcopy(before)


def test_a18_original_profiles_decisions_sessions_checkpoint_and_detached_restore(genuine_pw04):
    root, before = genuine_pw04
    manifest_path = root / '.controlcoding/development.json'
    raw = manifest_path.read_bytes()
    originals = {p.stem: p.read_bytes() for p in (root / '.controlcoding/development/objects').glob('*.json')}
    with SafeRoot(root) as safe:
        original_sessions = sessions.collect(safe)
    original_closure = bundle._closure(json.loads(raw), original_sessions,
                                      lambda ref: json.loads(originals[ref]))
    preview = migration.preview(root)
    assert preview['source_method'] == PLANNING and preview['target_method'] == IDENTITY
    assert not preview['blockers']
    assert manifest_path.read_bytes() == raw
    migrated = migration.migrate(root, preview['expected_revision'], preview['approval_id'], 'Owner')
    after = core.read(root)
    for key in ('selections', 'scope_decisions', 'retirements', 'incident_links', 'integration_reviews'):
        assert after['product']['evolution'][key] == before['product']['evolution'][key]
    assert after['product']['adoption'] == before['product']['adoption']
    for old, current in zip(before['changes'], after['changes']):
        assert old['method'] == current['method'] == PLANNING
        assert current['decisions'] == old['decisions']
        for stage in ('intent', 'design', 'plan'):
            assert current['gates'][stage]['accepted'] == old['gates'][stage]['accepted']
    assert any(c['releases'] for c in after['changes'])
    assert any(c.get('origin') for c in after['changes'])
    assert before['product']['evolution']['integration_reviews']
    shared = next(s for s in before['product']['evolution']['selections'] if s['id'] == 'I2')
    assert shared['ownership'][0]['contributor_change_ids'] == ['C2', 'C3'] and shared['dependencies']
    assert sessions.read(root, 'original')['session']['turns'][0]['text'] == 'Original 0.10 detailed planning session.'
    assert migration.migrate(root, preview['expected_revision'], preview['approval_id'], 'Owner')['replayed']
    with SafeRoot(root) as safe:
        assert store.read_archive_bytes(safe, migrated['archive']['archive_ref']) == raw
        state, _, manifest = store.read_state(safe)
        migration.validate_metadata(safe, state, manifest, full=True)
        profiles = copy.deepcopy(state['product']['detailed']['profiles'])
    assert {p['creation_method']['digest'] for p in profiles} == {PLANNING['digest']}
    assert {'product_select', 'product_increment', 'product_link_incident'} <= {p['origin'] for p in profiles}
    changed = copy.deepcopy(before['product']['document'])
    changed['title'] = 'Later current-method title preserves original planning.'
    act(root, {'kind': 'product_save', 'document': changed,
               'base_revision': after['product']['current_revision'], 'actor': 'Author'})
    checkpoint = history.checkpoint_prepare(root)
    history.checkpoint_commit(root, checkpoint['revision'], checkpoint['approval_id'], 'Owner')
    # Retry the original migration after later current writes/checkpoint, before
    # and after detached restoration, through its retained original operation.
    assert migration.migrate(root, preview['expected_revision'], preview['approval_id'], 'Owner')['replayed']
    with SafeRoot(root) as safe:
        with core.storage.transaction(safe):
            archive = bundle.collect(safe)
        bundle.validate(safe, archive)
    assert original_closure <= set(archive['objects'])
    expected = core.read(root)
    manifest_path.unlink()
    for path in (root / '.controlcoding/development/sessions').glob('*.json'): path.unlink()
    for path in (root / '.controlcoding/development/objects').glob('*.json'): path.unlink()
    restore = backups.prepare_restore(root, archive, 'Owner', 'Restore complete authentic profile closure.')
    assert not manifest_path.exists()
    assert not list((root / '.controlcoding/development/objects').glob('*.json'))
    assert [c['decisions'] for c in restore['view']['changes']] == [c['decisions'] for c in expected['changes']]
    backups.restore(root, archive, 'Owner', 'Restore complete authentic profile closure.', restore['approval_id'])
    assert core.read(root) == expected
    assert migration.migrate(root, preview['expected_revision'], preview['approval_id'], 'Owner')['replayed']
    assert {p.stem for p in (root / '.controlcoding/development/objects').glob('*.json')} == set(archive['objects'])
    assert all((root / '.controlcoding/development/objects' / (ref + '.json')).read_bytes() == originals[ref]
               for ref in original_closure)
    with SafeRoot(root) as safe:
        assert store.read_archive_bytes(safe, migrated['archive']['archive_ref']) == raw


@pytest.mark.parametrize('fault', ['missing_profile', 'forged_creation_method', 'fully_resealed_creation_method'])
def test_a19_missing_or_resealed_original_profile_cannot_gain_legacy_applicability(genuine_pw04_early, fault):
    root, _ = genuine_pw04_early
    state = logical_state(root)
    profiles = state['product']['detailed']['profiles']
    if fault == 'missing_profile':
        profiles.pop()
    elif fault == 'fully_resealed_creation_method':
        from cc_development_compatibility import EVOLUTION
        from cc_product_planning import applicability, creation_method
        authentic_change = next(change for change in state['changes'] if change['id'] == 'C4')
        with SafeRoot(root) as safe:
            authentic_history = history.full_history(safe, authentic_change)
            _, _, manifest = store.read_state(safe)
            validation_state = history.compact_validation_state(
                safe, state, compatibility=manifest['compatibility'])
        creation = next(event for event in authentic_history if event['action']['kind'] == 'create')
        assert authentic_change['method'] == creation['result']['method'] == PLANNING
        validation_change = next(change for change in validation_state['changes'] if change['id'] == 'C4')
        assert 'history_base' not in validation_change
        assert creation_method(validation_change) == PLANNING
        original = next(p for p in profiles if p['change_id'] == 'C4')
        assert original['creation_method'] == PLANNING
        replacement = copy.deepcopy(original)
        replacement['creation_method'] = dict(EVOLUTION)
        replacement['revision'] = digest({k: v for k, v in replacement.items() if k != 'revision'})
        def replace(value):
            if type(value) is dict:
                if value == original: return copy.deepcopy(replacement)
                return {key: replace(item) for key, item in value.items()}
            if type(value) is list: return [replace(item) for item in value]
            return replacement['revision'] if value == original['revision'] else value
        state['product'] = replace(state['product'])
        previous = 'absent'
        for event in state['product']['events']:
            event['previous'] = previous
            event['digest'] = digest({k: v for k, v in event.items() if k != 'digest'})
            previous = event['digest']
        forged = next(p for p in state['product']['detailed']['profiles'] if p['change_id'] == 'C4')
        event_profile = next(p for e in state['product']['events'] for p in e.get('profiles', []) if p['change_id'] == 'C4')
        assert forged == event_profile == replacement
        assert forged['creation_method'] == EVOLUTION
        assert all(e['digest'] == digest({k: v for k, v in e.items() if k != 'digest'}) for e in state['product']['events'])
        # The fully resealed Product story cannot replace authenticated change
        # creation history, including when that history is checkpointed.
        assert applicability(state['product'], validation_change) == 'corrupt'
    else:
        profiles[0]['creation_method'] = dict(PLANNING, digest='f' * 64)
        profiles[0]['revision'] = digest({k: v for k, v in profiles[0].items() if k != 'revision'})
    corrupt_logical_state(root, state)
    attacked = (root / '.controlcoding/development.json').read_bytes()
    with pytest.raises(ValueError) as failure: migration.preview(root)
    if fault == 'fully_resealed_creation_method':
        assert failure.value.code == 'planning_profile_missing'
    assert (root / '.controlcoding/development.json').read_bytes() == attacked


def test_a19_stale_preview_unknown_method_and_unresolved_effects_do_not_write(genuine_pw04_early):
    root, _ = genuine_pw04_early
    manifest = root / '.controlcoding/development.json'
    raw = manifest.read_bytes()
    preview = migration.preview(root)
    manifest.write_bytes(raw + b'\n')
    changed = manifest.read_bytes()
    with pytest.raises(ValueError): migration.migrate(root, preview['expected_revision'], preview['approval_id'], 'Owner')
    assert manifest.read_bytes() == changed
    manifest.write_bytes(raw)
    unknown = json.loads(raw)
    unknown['method']['digest'] = 'f' * 64
    manifest.write_text(json.dumps(unknown), encoding='utf-8')
    changed = manifest.read_bytes()
    with pytest.raises(ValueError): migration.preview(root)
    assert manifest.read_bytes() == changed
    manifest.write_bytes(raw)
    folder = root / '.controlcoding/development_execution_receipts'
    folder.mkdir(exist_ok=True)
    unresolved = folder / 'unresolved.json'
    unresolved.write_text('{"state":"running"}', encoding='utf-8')
    blocked = migration.preview(root)
    assert blocked['blockers'] and manifest.read_bytes() == raw
    with pytest.raises(ValueError): migration.migrate(root, blocked['expected_revision'], blocked['approval_id'], 'Owner')
    assert manifest.read_bytes() == raw


def test_a19_policy_chain_has_seven_exact_steps_without_reordered_or_skipped_steps():
    import cc_development_compatibility as compatibility
    chain = compatibility.trusted_chain(compatibility.PRIOR, IDENTITY)
    assert len(chain) == 7
    assert chain[-1]['source'] == PLANNING and chain[-1]['target'] == IDENTITY
    assert compatibility._valid_chain(chain, IDENTITY)
    assert not compatibility._valid_chain(chain[:-1], IDENTITY)
    assert not compatibility._valid_chain(list(reversed(chain)), IDENTITY)
    with pytest.raises(ValueError): compatibility.trusted_pair(dict(PLANNING, digest='f' * 64), IDENTITY)


def test_a18_older_authentic_change_upgraded_by_original_010_keeps_creation_identity(tmp_path):
    import ast
    from cc_development_compatibility import EVOLUTION
    runtime03 = os.environ.get('CC_TEST_PW03_RUNTIME')
    runtime04 = os.environ.get('CC_TEST_PW04_RUNTIME')
    if not runtime03 or not runtime04:
        pytest.skip('Authentic immutable 0.9 and 0.10 runtimes required for original explicit detail upgrade; mandatory local acceptance supplies both')
    runtime04 = Path(runtime04).resolve()
    # Reuse the actual original PW04 fixture program, which invokes original
    # 0.9 source and asserts its fixed identity before creating any history.
    tree = ast.parse((runtime04.parent / 'tests/test_cc_product_planning_upgrade.py').read_text(encoding='utf-8-sig'))
    fixture = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'genuine_pw03')
    program = next(ast.literal_eval(n.value) for n in fixture.body if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == 'program' for t in n.targets))
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1'}
    original = subprocess.run([sys.executable, '-B', '-c', program, runtime03, str(tmp_path), json.dumps(EVOLUTION)],
                             cwd=tmp_path, env=env, capture_output=True, text=True, encoding='utf-8', timeout=300)
    assert original.returncode == 0, original.stdout + original.stderr
    initial = json.loads(original.stdout)
    upgraded = subprocess.run([sys.executable, '-B', '-c', PW04_UPGRADED_PROGRAM, str(runtime04), str(tmp_path), json.dumps(PLANNING)],
                             cwd=tmp_path, env=env, capture_output=True, text=True, encoding='utf-8', timeout=300)
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
    before = json.loads(upgraded.stdout)
    assert {key: before['method'][key] for key in PLANNING} == PLANNING
    assert before['changes'][0]['method'] == EVOLUTION
    sibling_decisions = copy.deepcopy(before['changes'][1]['decisions'])
    p = migration.preview(tmp_path)
    assert not p['blockers']
    migration.migrate(tmp_path, p['expected_revision'], p['approval_id'], 'Owner')
    after = core.read(tmp_path)
    assert after['changes'][0]['method'] == EVOLUTION
    assert after['changes'][0]['gates']['plan']['accepted']
    assert after['changes'][1]['decisions'] == sibling_decisions == initial['changes'][1]['decisions']
    with SafeRoot(tmp_path) as safe:
        state, _, _ = store.read_state(safe)
    profiles = state['product']['detailed']['profiles']
    assert len(profiles) == 1
    assert profiles[0]['origin'] == 'explicit_upgrade' and profiles[0]['creation_method'] == EVOLUTION
    assert profiles[0]['change_id'] == 'C1'
    from cc_product_planning import applicability
    with SafeRoot(tmp_path) as safe:
        assert applicability(state['product'], state['changes'][0], safe) == 'detailed'
        assert applicability(state['product'], state['changes'][1], safe) == 'legacy'
