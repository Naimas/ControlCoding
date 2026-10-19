"""Genuine retained PW01 upgrade, exact decisions and immutable old sessions."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import cc_development_migration as migration
import cc_development_lib as core
from cc_development_compatibility import PRODUCT,trusted_chain
from cc_development_lib.method import IDENTITY
from test_cc_product_core import document

@pytest.fixture
def pw01_project(tmp_path):
    runtime=os.environ.get('CC_TEST_PW01_RUNTIME')
    if not runtime:pytest.skip('Authentic retained PW01 runtime supplied through CC_TEST_PW01_RUNTIME is required')
    assert Path(runtime,'cc_product.py').is_file(),'Supplied authentic predecessor runtime is invalid.'
    program=r'''
import sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import cc_development_lib as c
import cc_product as p
import cc_development_sessions as sessions
from cc_development_lib.method import IDENTITY
root=Path(sys.argv[2]);doc=json.loads(sys.argv[3]);expected=json.loads(sys.argv[4]);assert IDENTITY==expected
def act(a):
 s=c.read(root);v=c.prepare(root,a,s['revision']);assert not v['blockers'],v;return c.commit(root,a,s['revision'],v['approval_id'])['view']
def decide(stage):act({'kind':'decide','change_id':'C1','stage':stage,'decision':'accepted','actor':'Owner','reason':'Reviewed old exact revision'})
v=act({'kind':'product_save','document':doc,'base_revision':'absent','actor':'Author'})
act({'kind':'product_decide','product_revision':v['product']['current_revision'],'decision':'accepted','actor':'Owner','reason':'Reviewed scope'})
act({'kind':'activate','profile':'routine'})
act({'kind':'product_increment','product_revision':c.read(root)['product']['current_revision'],'feature_id':'signup','increment_id':'I1','change_id':'C1','title':'Accounts','owner':'Owner','risk':'routine','actor':'Author'})
decide('intent');d=p.template(root,'C1','design')
for m in d['mappings']:m['reason']='The proposed criterion preserves selected behavior.'
act({'kind':'product_specification','change_id':'C1','content':d['content'],'mappings':d['mappings'],'actor':'Author'});decide('design')
d=p.template(root,'C1','plan');d['content'].update(files=['accounts.py'],ownership_scopes=[{'owner':'Author','files':['accounts.py']}],tasks=[{'id':'implement','title':'Implement validation and durable account persistence','owner':'Author','depends_on':[]}],verification_strategy='Review valid, invalid and duplicate submissions after restart.',rollback_assessment='Revert module and retain stored records.')
act({'kind':'product_plan','change_id':'C1','content':d['content'],'files':[{'path':'accounts.py','kind':'new','sha256':'absent'}],'actor':'Author'});decide('plan')
v=c.read(root);sessions.create(root,'legacy',v['revision'],'Owner','1'*64);sessions.append(root,'legacy',sessions.read(root,'legacy')['revision'],'2'*64,'Owner','user','Preserve PW01 discussion')
print(json.dumps(v))
'''
    result=subprocess.run([sys.executable,'-B','-c',program,runtime,str(tmp_path),json.dumps(document()),json.dumps(PRODUCT)],cwd=tmp_path,capture_output=True,text=True,timeout=90)
    assert result.returncode==0,result.stdout+result.stderr
    return tmp_path,json.loads(result.stdout)

def test_genuine_pw01_upgrade_preserves_unaffected_accepted_product_plan_and_session(pw01_project):
    root,before=pw01_project;old={p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    preview=migration.preview(root);assert preview['source_method']==PRODUCT and preview['target_method']==IDENTITY
    assert old=={p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    migration.migrate(root,preview['expected_revision'],preview['approval_id'],'Owner')
    after=core.read(root);assert after['changes'][0]['decisions']==before['changes'][0]['decisions']
    assert after['changes'][0]['gates']['plan']['accepted'] and after['product']['document']==before['product']['document']
    import cc_development_sessions as sessions
    assert sessions.read(root,'legacy')['session']['turns'][0]['text']=='Preserve PW01 discussion'
    for path,raw in old.items():
        if path.as_posix().startswith('.controlcoding/development/objects/') or path.as_posix().startswith('.controlcoding/development/sessions/'):assert (root/path).read_bytes()==raw

def test_unknown_pw01_identity_cannot_acquire_compatibility():
    bad=dict(PRODUCT,digest='a'*64)
    with pytest.raises(ValueError):trusted_chain(bad,IDENTITY)
