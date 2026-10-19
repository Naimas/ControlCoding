"""Authentic predecessor command proof never becomes current after migration."""
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cc_development_jobs as jobs
import cc_development_migration as migration
import cc_development_lib as development
from cc_development_lib.policy import automation_qualification
from cc_development_compatibility import PRIOR
from test_cc_development_migration import prior_project


def test_real_prior_job_retained_but_engine_proof_rejected_and_current_plan_allowed(prior_project):
    script = r'''
import hashlib,json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import cc_development_lib as d
import cc_development_jobs as jobs
from cc_development_lib.method import IDENTITY
assert IDENTITY == json.loads(sys.argv[3])
root=Path(sys.argv[2])
def act(action):
    before=d.read(root); preview=d.prepare(root,action,before['revision'])
    assert not preview['blockers'],preview
    return d.commit(root,action,before['revision'],preview['approval_id'])
act({'kind':'candidate','change_id':'C1','actor':'Writer','files':['source.py']})
change=d.read(root)['changes'][0]
def put(path,value):
    destination=root/path
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(value),encoding='utf-8')
configuration={'implementationActor':'Writer','graderActor':'Independent grader','requiredCases':['local-case']}
for field,path in [('configDigest','config.json'),('taskDigest','task.json'),('graderDigest','grader.json')]:
    put(path,{'identity':path})
    configuration[field]=hashlib.sha256((root/path).read_bytes()).hexdigest()
observation={**{key:configuration[key] for key in ('configDigest','taskDigest','graderDigest','graderActor')},
             'candidateDigest':change['candidate']['digest'],'cases':[{'id':'local-case','passed':True}]}
put('observed.json',observation)
(root/'adapter.py').write_text("from pathlib import Path\nprint(Path('observed.json').read_text())\n",encoding='utf-8')
paths=['adapter.py','observed.json','config.json','task.json','grader.json']
spec={'id':'native-eval','kind':'agent_eval','argv':[sys.executable,'-B','adapter.py'],'timeoutSeconds':5,
      'outputLimitBytes':8192,'files':[{'path':path,'sha256':hashlib.sha256((root/path).read_bytes()).hexdigest()} for path in paths],
      'configuration':configuration}
put(jobs.MANIFEST,{'schemaVersion':1,'adapters':[spec]})
value={'kind':'agent_eval','adapter_id':'native-eval'}
preview=jobs.prepare(root,value,change)
result=jobs.execute(root,value,change,preview['approval_id'])
assert result['state']=='allowed',result
assessed=jobs.assess_job_observation(root,result,change,'agent_eval')
assert assessed['state']=='allowed' and assessed['qualification'] is False,assessed
record={'id':'original-eval','kind':'agent_eval','receiptId':result['receiptId'],'observation':result['observation']}
act({'kind':'evidence','change_id':'C1','actor':'Observer','records':[record]})
assert jobs.assess_job_observation(root,record,d.read(root)['changes'][0],'agent_eval')['state']=='allowed'
put('native-job.json',{'result':result,'record':record,'value':value,'oldPreview':preview})
'''
    observed = subprocess.run([sys.executable, '-B', '-c', script, os.environ['CC_TEST_PRIOR_RUNTIME'],
                               str(prior_project), json.dumps(PRIOR)], capture_output=True, text=True, timeout=60,
                              env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    assert observed.returncode == 0, observed.stderr
    retained = json.loads((prior_project / 'native-job.json').read_text())
    original = (prior_project / '.controlcoding/development.json').read_bytes()
    receipt_path = prior_project / retained['result']['receiptPath']
    receipt_before = receipt_path.read_bytes()
    selected = migration.preview(prior_project)
    migrated = migration.migrate(prior_project, selected['expected_revision'], selected['approval_id'], 'Owner')
    change = development.read(prior_project)['changes'][0]
    assert change['method'] == PRIOR
    rejected = jobs.assess_job_observation(prior_project, retained['record'], change, 'agent_eval')
    assert rejected['state'] == 'unknown' and rejected['reasons'] == ['job_engine_changed'], rejected
    assert receipt_path.read_bytes() == receipt_before
    assert change['evidence'][-1]['records'][0] == retained['record']
    current_plan = jobs.prepare(prior_project, retained['value'], change)
    assert current_plan['method'] == PRIOR and current_plan['enginePins'] != retained['oldPreview']['enginePins']
    current = jobs.execute(prior_project, retained['value'], change, current_plan['approval_id'])
    assert current['state'] == 'allowed'
    current_assessment = jobs.assess_job_observation(prior_project, current, change, 'agent_eval')
    assert current_assessment['state'] == 'allowed' and current_assessment['qualification'] is False
    assert automation_qualification(prior_project, change, {'evaluation_id': 'original-eval',
           'configuration_digest': retained['oldPreview']['configuration']['configDigest']})['state'] == 'blocked'
    import cc_development_store as store
    import cc_evidence_inputs as inputs
    with inputs.SafeRoot(prior_project) as safe:
        assert store.read_archive_bytes(safe, migrated['archive']['archive_ref']) == original
