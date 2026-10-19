"""Authentic frozen PW02 upgrade: original approvals, sessions and backup closure."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from test_cc_development_core import act
from test_cc_product_core import document
import cc_development_lib as core
import cc_development_migration as migration
import cc_development_bundle as bundle
import cc_development_store as store
import cc_development_sessions as sessions
import cc_planning as planning
from cc_evidence_inputs import SafeRoot
from cc_development_compatibility import DISCUSSION,EVOLUTION,trusted_chain
from cc_development_lib.method import IDENTITY

@pytest.fixture
def authentic_pw02(tmp_path):
    runtime=os.environ.get('CC_TEST_PW02_RUNTIME')
    if not runtime:pytest.skip('Authentic immutable PW02 runtime required through CC_TEST_PW02_RUNTIME; mandatory local acceptance supplies it')
    assert Path(runtime,'cc_product.py').is_file()
    program=r'''
import sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import cc_development_lib as c,cc_product as p,cc_development_sessions as sessions,cc_planning as planning
from cc_development_lib.method import IDENTITY
root=Path(sys.argv[2]);doc=json.loads(sys.argv[3]);assert IDENTITY==json.loads(sys.argv[4])
def act(a):
 v=c.read(root);pr=c.prepare(root,a,v['revision']);assert not pr['blockers'],pr;return c.commit(root,a,v['revision'],pr['approval_id'])['view']
def accept(stage):act({'kind':'decide','change_id':'C1','stage':stage,'decision':'accepted','actor':'Owner','reason':'Reviewed original PW02 revision'})
v=act({'kind':'product_save','document':doc,'base_revision':'absent','actor':'Author'})
act({'kind':'product_decide','product_revision':v['product']['current_revision'],'decision':'accepted','actor':'Owner','reason':'Reviewed whole product'})
act({'kind':'activate','profile':'critical'})
act({'kind':'product_increment','product_revision':c.read(root)['product']['current_revision'],'feature_id':'signup','increment_id':'I1','change_id':'C1','title':'Accounts','owner':'OriginalOwner','risk':'critical','actor':'Author'})
accept('intent');d=p.template(root,'C1','design')
for m in d['mappings']:m['reason']='The selected product condition is preserved exactly.'
act({'kind':'product_specification','change_id':'C1','content':d['content'],'mappings':d['mappings'],'actor':'Author'});accept('design')
d=p.template(root,'C1','plan');d['content'].update(files=['accounts.py'],ownership_scopes=[{'owner':'Author','files':['accounts.py']}],tasks=[{'id':'implement','title':'Implement account persistence and validation','owner':'Author','depends_on':[]}],verification_strategy='Review duplicate valid and invalid accounts after restart.',rollback_assessment='Revert module, retain persisted records.')
act({'kind':'product_plan','change_id':'C1','content':d['content'],'files':[{'path':'accounts.py','kind':'new','sha256':'absent'}],'actor':'Author'});accept('plan')
v=c.read(root);sessions.create(root,'legacy',v['revision'],'Owner','1'*64);sessions.append(root,'legacy',sessions.read(root,'legacy')['revision'],'2'*64,'Owner','user','Preserve original retained discussion')
planning.dispatch(root,{'operation':'planning_create','session_id':'PW02discussion','operation_id':'3'*64,'actor':'Owner','task_id':'ProductPlanning'})
print(json.dumps(v))
'''
    result=subprocess.run([sys.executable,'-B','-c',program,runtime,str(tmp_path),json.dumps(document()),json.dumps(DISCUSSION)],cwd=tmp_path,capture_output=True,text=True,timeout=120,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    assert result.returncode==0,result.stdout+result.stderr
    return tmp_path,json.loads(result.stdout)

def test_authentic_pw02_approvals_original_objects_sessions_and_backup_survive(authentic_pw02):
    root,before=authentic_pw02;old={p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()};raw=(root/'.controlcoding/development.json').read_bytes()
    preview=migration.preview(root);assert preview['source_method']==DISCUSSION and preview['target_method']==IDENTITY and not preview['blockers']
    assert old=={p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    result=migration.migrate(root,preview['expected_revision'],preview['approval_id'],'Owner')
    after=core.read(root);change=after['changes'][0]
    assert change['method']==DISCUSSION and change['decisions']==before['changes'][0]['decisions']
    assert change['gates']['plan']['accepted'] and change['gates']['plan']['outcome']=='allowed'
    assert after['product']['increments'][0]['changes'][0]['owner']=='OriginalOwner' and after['product']['increments'][0]['changes'][0]['risk']=='critical'
    assert sessions.read(root,'legacy')['session']['turns'][0]['text']=='Preserve original retained discussion'
    assert planning.dispatch(root,{'operation':'planning_read','session_id':'PW02discussion'})['session']['task_id']=='ProductPlanning'
    for path,data in old.items():
        if path.as_posix().startswith(('.controlcoding/development/objects/','.controlcoding/development/sessions/')):assert (root/path).read_bytes()==data
    # Change current planning roots; archived predecessor planning objects may
    # now be detached from the live root but must remain part of backup closure.
    doc=document();doc['title']='Accounts product after PW03 upgrade'
    act(root,{'kind':'product_save','document':doc,'base_revision':after['product']['current_revision'],'actor':'Author'})
    with SafeRoot(root) as safe:
        assert store.read_archive_bytes(safe,result['archive']['archive_ref'])==raw
        state,_,manifest=store.read_state(safe);migration.validate_metadata(safe,state,manifest,full=True)
        with core.storage.transaction(safe):backup=bundle.collect(safe)
        bundle.validate(safe,backup)
    assert migration.migrate(root,preview['expected_revision'],preview['approval_id'],'Owner')['replayed']

def test_predecessor_planning_pointer_cannot_introduce_unknown_object_type():
    archive='a'*64;part='b'*64;root='c'*64;planning_root='d'*64
    raw=json.dumps({'schema_version':2,'method':DISCUSSION,'kind':'development_control','state_ref':root,'archives':[]}).encode()
    payloads={archive:{'kind':'exact_bytes','sha256':__import__('hashlib').sha256(raw).hexdigest(),'size':len(raw),'parts':[part]},part:__import__('base64').b64encode(raw).decode(),root:{'type':'planning','ref':planning_root},planning_root:{'type':'unsupported','value':True}}
    with pytest.raises(ValueError,match='migration_state_invalid'):migration.collect_references({'archives':[{'archive_ref':archive}]},payloads.__getitem__)

def test_exact_pw02_pair_only_unknown_digest_cannot_claim_compatibility():
    bad=dict(DISCUSSION,digest='a'*64)
    with pytest.raises(ValueError,match='unsupported_compatibility_pair'):trusted_chain(bad,IDENTITY)
    chain=trusted_chain(DISCUSSION,IDENTITY)
    assert len(chain)==3 and chain[0]['target']==EVOLUTION
    assert [p['policy'] for p in chain]==['pw03_revision_scope_dependencies_unaffected_decisions_v1','pw04_explicit_detail_upgrade_unaffected_decisions_v1','pw05_observed_progress_unaffected_planning_decisions_v1']
