"""Genuine frozen retention predecessor, never relabelled current fixtures."""
import copy
import json
import os
import subprocess
import sys
import pytest
from test_cc_development_migration import generate_prior_project
from test_cc_product_core import saved
import cc_development_lib as core
from cc_development_compatibility import RETENTION
import cc_development_migration as migration
import cc_development_store as store
import cc_development_history as history
import cc_development_bundle as bundle
from cc_evidence_inputs import SafeRoot

@pytest.fixture
def retention_project(tmp_path):
    runtime=os.environ.get('CC_TEST_PW00_RETENTION_RUNTIME')
    if not runtime:pytest.skip('Exact retained 0.6.1 runtime required')
    generate_prior_project(tmp_path,runtime,RETENTION)
    code="""import sys;sys.path.insert(0,sys.argv[1]);import cc_development_history as h;p=h.checkpoint_prepare(sys.argv[2]);h.checkpoint_commit(sys.argv[2],p['revision'],p['approval_id'],'Owner')"""
    subprocess.run([sys.executable,'-B','-c',code,runtime,str(tmp_path)],check=True,capture_output=True,text=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    return tmp_path

def test_exact_compact_retention_upgrade_keeps_history_and_product_backup(retention_project):
    root=retention_project;raw=(root/'.controlcoding/development.json').read_bytes()
    with SafeRoot(root) as safe:
        original,_,manifest=store.read_state(safe);old_history=history.full_history(safe,original['changes'][0]);old_objects={p:p.read_bytes() for p in (root/'.controlcoding/development/objects').glob('*.json')}
    preview=migration.preview(root);assert preview['source_method']==RETENTION and not preview['blockers']
    result=migration.migrate(root,preview['expected_revision'],preview['approval_id'],'Owner')
    view=core.read(root);assert view['changes'][0]['gates']['plan']['accepted'] and not view['changes'][0]['gates']['build']['accepted']
    old_retry=original['retention']['activation']
    retry=core.commit(root,old_retry['action'],old_retry['expected_revision'],old_retry['approval_id'])
    assert retry['receipt']['idempotent'] and retry['view']['revision']==view['revision']
    saved(root)
    checkpoint=history.checkpoint_prepare(root);history.checkpoint_commit(root,checkpoint['revision'],checkpoint['approval_id'],'Owner')
    with SafeRoot(root) as safe:
        current,_,manifest=store.read_state(safe)
        assert history.full_history(safe,current['changes'][0])==old_history
        assert store.read_archive_bytes(safe,result['archive']['archive_ref'])==raw
        migration.validate_metadata(safe,current,manifest,full=True)
        with core.storage.transaction(safe):archive=bundle.collect(safe)
        bundle.validate(safe,archive)
    assert all(path.read_bytes()==data for path,data in old_objects.items())
    replay=migration.migrate(root,preview['expected_revision'],preview['approval_id'],'Owner')
    assert replay['replayed']

def test_unknown_retention_identity_never_adopted(retention_project):
    path=retention_project/'.controlcoding/development.json';manifest=json.loads(path.read_text());manifest['method']['digest']='f'*64
    path.write_text(json.dumps(manifest));before=path.read_bytes()
    with pytest.raises(ValueError):migration.preview(retention_project)
    assert path.read_bytes()==before
