"""Authentic 0.9 executable predecessor, never relabelled current constants."""
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
import cc_planning
from cc_evidence_inputs import SafeRoot
from cc_development_compatibility import EVOLUTION
from cc_development_lib.method import IDENTITY
from test_cc_development_core import act
from test_cc_product_core import document


@pytest.fixture
def genuine_pw03(tmp_path):
    runtime = os.environ.get('CC_TEST_PW03_RUNTIME')
    if not runtime:
        pytest.skip('Authentic immutable 0.9 runtime required through CC_TEST_PW03_RUNTIME; mandatory local PW04 acceptance supplies it')
    retained = Path(runtime).parent
    assert (retained / 'tests/test_cc_product_evolution_acceptance.py').is_file()
    program = r'''
import sys,json
from pathlib import Path
sys.path[:0]=[sys.argv[1],str(Path(sys.argv[1]).parent/'tests')]
import cc_development_lib as c,cc_development_sessions as sessions,cc_planning as planning
from cc_development_lib.method import IDENTITY
from test_cc_product_evolution_acceptance import combined_contracts,accept_document,shared_selection,plan_change,build_change,receipts,verify_change,latest_selection,change,act,two_features
assert IDENTITY==json.loads(sys.argv[3]),IDENTITY
root=Path(sys.argv[2]);combined_contracts(root);accept_document(root,two_features())
act(root,{'kind':'activate','profile':'routine'});act(root,shared_selection(root))
for change_id,filename in [('C1','accounts.py'),('C2','reports.py')]:
 plan_change(root,change_id,filename);build_change(root,change_id,filename)
receipts(root);verify_change(root,'C1');verify_change(root,'C2')
selection=latest_selection(root)
act(root,{'kind':'product_integration','actor':'Verifier','increment_id':'I1','selection_revision':selection['selection_revision'],'criterion_id':'signup-success','candidates':[{'change_id':cid,'candidate_identity':change(root,cid)['candidate']['digest']} for cid in ('C1','C2')]})
act(root,{'kind':'product_select','product_revision':c.read(root)['product']['current_revision'],'increment_id':'I2','title':'Reports dependent increment','features':['reports'],'changes':[{'change_id':'C3','title':'Reports','owner':'Owner','risk':'routine','feature_ids':['reports']}],'criteria':[{'criterion_id':'report-success','owner_change_id':'C3','contributor_change_ids':['C3'],'integration_check':None}],'dependencies':[{'consumer_change_id':'C3','target_change_id':'C1','target_feature_id':'signup','target_feature_revision':selection['features'][0]['feature_revision'],'target_selection_revision':selection['selection_revision'],'required_result':'verified','gate':'build'}],'actor':'Owner'})
v=c.read(root);sessions.create(root,'legacy',v['revision'],'Owner','1'*64);sessions.append(root,'legacy',sessions.read(root,'legacy')['revision'],'2'*64,'Owner','user','Original 0.9 discussion retained.')
planning.dispatch(root,{'operation':'planning_create','session_id':'discussion','operation_id':'3'*64,'actor':'Owner','task_id':'ProductPlanning'})
import cc_development_history as history
p=history.checkpoint_prepare(root);history.checkpoint_commit(root,p['revision'],p['approval_id'],'Owner')
print(json.dumps(c.read(root)))
'''
    result = subprocess.run([sys.executable, '-B', '-c', program, runtime, str(tmp_path), json.dumps(EVOLUTION)],
        cwd=tmp_path, capture_output=True, text=True, timeout=180,
        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    assert result.returncode == 0, result.stdout + result.stderr
    return tmp_path, json.loads(result.stdout)


def test_a13_genuine_09_contributors_dependencies_sessions_original_archive_and_cold_retry(genuine_pw03):
    root, before = genuine_pw03
    raw = (root / '.controlcoding/development.json').read_bytes()
    originals = {p.relative_to(root): p.read_bytes() for p in (root / '.controlcoding/development/objects').glob('*.json')}
    with SafeRoot(root) as safe:
        original_sessions = sessions.collect(safe)
    original_objects = {path.stem: json.loads(data) for path, data in originals.items()}
    original_closure = bundle._closure(json.loads(raw), original_sessions, original_objects.__getitem__)
    assert original_closure <= set(original_objects)
    preview = migration.preview(root)
    assert preview['source_method'] == EVOLUTION and preview['target_method'] == IDENTITY
    assert not preview['blockers']
    assert (root / '.controlcoding/development.json').read_bytes() == raw
    result = migration.migrate(root, preview['expected_revision'], preview['approval_id'], 'Owner')
    after = core.read(root)
    assert after['product']['evolution']['selections'] == before['product']['evolution']['selections']
    assert after['product']['evolution']['integration_reviews'] == before['product']['evolution']['integration_reviews']
    for old, current in zip(before['changes'], after['changes']):
        assert current['method'] == old['method'] == EVOLUTION
        assert current['decisions'] == old['decisions']
    assert all(c['gates']['plan']['accepted'] for c in after['changes'][:2])
    assert sessions.read(root, 'legacy')['session']['turns'][0]['text'] == 'Original 0.9 discussion retained.'
    assert cc_planning.dispatch(root, {'operation': 'planning_read', 'session_id': 'discussion'})['session']['task_id'] == 'ProductPlanning'
    for path, data in originals.items():
        assert (root / path).read_bytes() == data
    assert migration.migrate(root, preview['expected_revision'], preview['approval_id'], 'Owner')['replayed']
    with SafeRoot(root) as safe:
        assert store.read_archive_bytes(safe, result['archive']['archive_ref']) == raw
        state, _, manifest = store.read_state(safe)
        migration.validate_metadata(safe, state, manifest, full=True)
    changed = document()
    changed['title'] = 'Later current-method document detaches original objects.'
    act(root, {'kind': 'product_save', 'document': changed, 'base_revision': after['product']['current_revision'], 'actor': 'Author'})
    with SafeRoot(root) as safe:
        with core.storage.transaction(safe):
            archive = bundle.collect(safe)
        bundle.validate(safe, archive)
        assert store.read_archive_bytes(safe, result['archive']['archive_ref']) == raw
    assert all((root / path).read_bytes() == data for path, data in originals.items())
    # The closed bundle preserves registered predecessor roots and their full
    # reachable closure, not unrelated objects left by intermediate commits.
    original_state_ref = json.loads(raw)['state_ref']
    assert original_state_ref in archive['objects']
    assert original_closure <= set(archive['objects'])
    # Every omitted intermediate object is outside the authenticated original
    # predecessor closure as well as the complete declared backup closure.
    omitted_intermediate = set(original_objects) - set(archive['objects'])
    assert omitted_intermediate and omitted_intermediate.isdisjoint(original_closure)
    reachable_originals = {path: data for path, data in originals.items()
                           if path.stem in archive['objects']}
    assert reachable_originals
    # Restore from detached bundle bytes after later current-method writes. No
    # original object left on disk may silently repair an incomplete bundle.
    expected = core.read(root)
    (root / '.controlcoding/development.json').unlink()
    for path in (root / '.controlcoding/development/sessions').glob('*.json'):
        path.unlink()
    for path in (root / '.controlcoding/development/objects').glob('*.json'):
        path.unlink()
    restore = backups.prepare_restore(root, archive, 'Owner', 'Restore complete detached predecessor closure.')
    assert not (root / '.controlcoding/development.json').exists()
    assert not list((root / '.controlcoding/development/objects').glob('*.json'))
    for original, proposed in zip(expected['changes'], restore['view']['changes']):
        assert proposed['history'] == original['history']
        assert proposed.get('history_base') == original.get('history_base')
        assert proposed.get('history_retention') == original.get('history_retention')
        assert proposed['decisions'] == original['decisions']
    backups.restore(root, archive, 'Owner', 'Restore complete detached predecessor closure.', restore['approval_id'])
    assert core.read(root) == expected
    assert {path.stem for path in (root / '.controlcoding/development/objects').glob('*.json')} == set(archive['objects'])
    assert all((root / path).read_bytes() == data for path, data in reachable_originals.items())
    with SafeRoot(root) as safe:
        assert store.read_archive_bytes(safe, result['archive']['archive_ref']) == raw


def test_a14_explicit_legacy_planning_upgrade_preserves_sibling_and_requires_detail(genuine_pw03):
    root, _ = genuine_pw03
    p = migration.preview(root)
    migration.migrate(root, p['expected_revision'], p['approval_id'], 'Owner')
    before = core.read(root)
    assert before['changes'][0]['gates']['design']['accepted']
    assert before['changes'][0]['gates']['plan']['accepted']
    selection = before['product']['increments'][0]['selection_revision']
    action = {'kind': 'product_planning_upgrade', 'change_id': 'C1', 'selection_revision': selection,
              'actor': 'Owner', 'reason': 'Enable reviewed actionable task detail for this exact change.'}
    preview = core.prepare(root, action, before['revision'])
    assert not preview['blockers'], preview['blockers']
    result = core.commit(root, action, before['revision'], preview['approval_id'])['view']
    assert result['changes'][0]['decisions'] == before['changes'][0]['decisions']
    assert result['changes'][0]['gates']['intent']['accepted']
    assert not result['changes'][0]['gates']['design']['accepted']
    assert not result['changes'][0]['gates']['plan']['accepted']
    assert result['changes'][1]['decisions'] == before['changes'][1]['decisions']
    assert result['changes'][1]['gates']['design']['accepted']
    assert result['changes'][1]['gates']['plan']['accepted']
    assert core.commit(root, action, before['revision'], preview['approval_id'])['receipt']['idempotent']


def test_a13_unknown_effect_identity_and_stale_preview_leave_original_control_unchanged(genuine_pw03):
    root, _ = genuine_pw03
    manifest = root / '.controlcoding/development.json'
    original = manifest.read_bytes()
    receipt_dir = root / '.controlcoding/development_execution_receipts'
    receipt_dir.mkdir(exist_ok=True)
    receipt = receipt_dir / 'unreconciled.json'
    receipt.write_text(json.dumps({'state': 'running'}), encoding='utf-8')
    preview = migration.preview(root)
    assert preview['blockers'] and manifest.read_bytes() == original
    with pytest.raises(core.DevelopmentError):
        migration.migrate(root, preview['expected_revision'], preview['approval_id'], 'Owner')
    assert manifest.read_bytes() == original
    receipt.write_text(json.dumps({'state': 'unknown'}), encoding='utf-8')
    with pytest.raises(ValueError): migration.preview(root)
    assert manifest.read_bytes() == original
    receipt.unlink()
    unknown = json.loads(original)
    unknown['method']['digest'] = 'f' * 64
    manifest.write_text(json.dumps(unknown), encoding='utf-8')
    corrupted = manifest.read_bytes()
    with pytest.raises(ValueError): migration.preview(root)
    assert manifest.read_bytes() == corrupted
    manifest.write_bytes(original)
    preview = migration.preview(root)
    manifest.write_bytes(original + b'\n')
    fresh = manifest.read_bytes()
    with pytest.raises(core.DevelopmentError):
        migration.migrate(root, preview['expected_revision'], preview['approval_id'], 'Owner')
    assert manifest.read_bytes() == fresh
