"""Actual planning persistence, bounded requests and ordinary draft authority."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import cc_planning as planning
import cc_development_lib as core
import cc_development_sessions as sessions
from cc_evidence_inputs import digest
from test_cc_product_core import document,saved,handed

def create(root):
    return planning.dispatch(root,{'operation':'planning_create','session_id':'S1','operation_id':'a'*64,'actor':'Owner','task_id':'T1'})

def options(root,target='product'):
    role={'enabled':True,'provider':'ollama','model':'fixture','context':'none','mode':'reviewed','setupProposals':False,'maxOutputTokens':2048,'timeoutSeconds':30,'maxContextChars':16000,'maxRequests':20,'systemPrompt':'Plan carefully','generation':{'effort':'default','thinking':None,'reasoningMode':'default','verbosity':'default','temperature':0,'format':'text','summary':False}}
    policy=root.parent/(root.name+'-roles.json');policy.write_text(json.dumps({'schemaVersion':4,'project':str(root.resolve()).lower() if sys.platform=='win32' else str(root.resolve()),'roles':{'concierge':role}}))
    return {'target':target,'feature_id':'','change_id':'','document_id':'app','editable':[{'kind':'document','id':'app','fields':['title']}],'sources':[],'messages':[{'role':'user','content':'Suggest a clear title'}],'configuration':{'role':'concierge','role_policy_path':str(policy),'role_policy_revision':hashlib.sha256(policy.read_bytes()).hexdigest(),'capability_identity':'b'*64,'template_identity':'pw02-planning-v1','extraction_policy_identity':'pw02-selected-v1','config':role},'limits':{'max_output_tokens':2048,'timeout_seconds':30,'max_context_chars':16000,'max_requests':20,'output_bytes':32768}}

def prepare(root,o=None):
    o=o or options(root);view=planning.dispatch(root,{'operation':'planning_read','session_id':'S1'});preview=planning.dispatch(root,{'operation':'planning_preview','session_id':'S1','options':o})
    payload={'operation':'planning_prepare','session_id':'S1','expected_revision':view['revision'],'operation_id':'c'*64,'actor':'Owner','options':o,'preview_id':preview['preview_id']}
    return planning.dispatch(root,payload),preview,payload

def reserve(root,view,preview):
    payload={'operation':'planning_reserve','session_id':'S1','expected_revision':view['revision'],'operation_id':'d'*64,'actor':'Owner','request_id':preview['request']['id'],'current_binding':preview['request']['configuration']}
    return planning.dispatch(root,payload),payload

def proposal(request,title='A better app'):
    return {'schema_version':1,'request_id':request['id'],'edits':[{'op':'set','kind':'document','id':'app','field':'title','value':title}],'claims':[{'edit_index':0,'kind':'proposed_decision','source_id':'','section_id':'','reason':'Suggested readable presentation title'}],'questions':['Which users matter?'],'conflicts':[],'omissions':[]}

def result(value,outcome='succeeded'):
    return {'outcome':outcome,'text':value,'incomplete':False,'usage':{'input_tokens':None,'output_tokens':None,'cost':None,'currency':None},'elapsed_ms':12,'error':'','provenance':'provider_unverified'}

def finish(root,view,preview,value):
    payload={'operation':'planning_finish','session_id':'S1','expected_revision':view['revision'],'operation_id':'e'*64,'actor':'Owner','request_id':preview['request']['id'],'current_binding':preview['request']['configuration'],'result':value}
    return planning.dispatch(root,payload),payload

def test_fresh_read_no_write_explicit_create_disabled_and_invalid_create_no_write(tmp_path):
    assert planning.dispatch(tmp_path,{'operation':'planning_read','session_id':'S1'})=={'revision':'absent','session':None}
    assert not list(tmp_path.iterdir())
    with pytest.raises(ValueError):planning.dispatch(tmp_path,{'operation':'planning_create','session_id':'../bad','operation_id':'a'*64,'actor':'Owner','task_id':'T1'})
    assert not list(tmp_path.iterdir())
    view=create(tmp_path);assert not core.read(tmp_path)['enabled'] and view['session']['requests']==[]

def test_real_request_dialogue_history_replay_and_unknown_usage(tmp_path):
    create(tmp_path);view,p,prepared=prepare(tmp_path);assert planning.dispatch(tmp_path,prepared)['revision']==view['revision'];assert view['session']['turns'][-1]['role']=='user'
    assert p['request']['id'] in p['request']['messages'][0]['content']
    view,sent=reserve(tmp_path,view,p);assert planning.dispatch(tmp_path,sent)['session']['usage']['attempts']==1
    view,completed=finish(tmp_path,view,p,result(json.dumps(proposal(p['request']))));assert view['session']['usage']['unknown_cost']==1 and view['session']['usage']['network_calls']==1
    Path(p['request']['configuration']['role_policy_path']).write_text('{}')
    assert planning.dispatch(tmp_path,completed)['revision']==view['revision']
    review=planning.dispatch(tmp_path,{'operation':'planning_review','session_id':'S1','request_id':p['request']['id'],'current_binding':p['request']['configuration'],'actor':'Owner'})
    assert review['action'] is None and 'planning_configuration_changed' in review['blockers']

def test_distinct_question_distinct_request_and_bounded_large_unrelated_context(tmp_path):
    doc=document();doc['notes']='x'*16000;saved(tmp_path,doc);create(tmp_path);o=options(tmp_path)
    first=planning.dispatch(tmp_path,{'operation':'planning_preview','session_id':'S1','options':o});o['messages'][0]['content']='Another title please';second=planning.dispatch(tmp_path,{'operation':'planning_preview','session_id':'S1','options':o})
    assert first['request']['id']!=second['request']['id'] and 'x'*100 not in json.dumps(first['request'])

def test_effective_draft_token_guard_exact_manual_detach_and_ordinary_approval(tmp_path):
    saved(tmp_path);create(tmp_path);v,p,_=prepare(tmp_path);v,_=reserve(tmp_path,v,p);v,_=finish(tmp_path,v,p,result(json.dumps(proposal(p['request']))))
    review=planning.dispatch(tmp_path,{'operation':'planning_review','session_id':'S1','request_id':p['request']['id'],'current_binding':p['request']['configuration'],'actor':'Owner'})
    before=core.read(tmp_path);assert not before['enabled'];preview=core.prepare(tmp_path,review['action'],before['revision']);assert not preview['blockers'];core.commit(tmp_path,review['action'],before['revision'],preview['approval_id'])
    assert core.read(tmp_path)['product']['document']['title']=='A better app' and core.read(tmp_path)['product']['accepted_revision']=='absent'
    # Historical saved provenance is structural, never reread against live policy.
    Path(p['request']['configuration']['role_policy_path']).write_text('{}');assert core.read(tmp_path)['product']['document']['title']=='A better app'

@pytest.mark.parametrize('bad',['unknown_source','missing_claim','authority','wrong_request','outside_scope'])
def test_invalid_output_retained_never_promoted(tmp_path,bad):
    create(tmp_path);v,p,_=prepare(tmp_path);v,_=reserve(tmp_path,v,p);q=proposal(p['request'])
    if bad=='unknown_source':q['claims'][0].update(kind='source',source_id='fake',section_id='fake')
    elif bad=='missing_claim':q['claims']=[]
    elif bad=='authority':q['approval']='accepted'
    elif bad=='wrong_request':q['request_id']='wrong'
    else:q['edits'][0]['field']='constraints'
    v,_=finish(tmp_path,v,p,result(json.dumps(q)));assert v['session']['requests'][0]['result']['text'] and v['session']['requests'][0]['proposal'] is None
    assert not core.read(tmp_path)['product']['document']

def test_v1_bytes_and_archive_domain_preserved(tmp_path):
    create(tmp_path);revision=core.read(tmp_path)['revision'];sessions.create(tmp_path,'legacy',revision,'Owner','1'*64);sessions.append(tmp_path,'legacy',sessions.read(tmp_path,'legacy')['revision'],'2'*64,'Owner','user','Legacy question')
    before=(tmp_path/'.controlcoding/development/sessions/legacy.json').read_bytes();view=planning.dispatch(tmp_path,{'operation':'planning_read','session_id':'legacy'});assert view['session']['turns'][0]['text']=='Legacy question';assert before==(tmp_path/'.controlcoding/development/sessions/legacy.json').read_bytes()

@pytest.mark.parametrize('operation',['add','remove'])
@pytest.mark.parametrize('field',[None,7,[],{},True])
def test_invalid_add_remove_field_retains_terminal_response(tmp_path,operation,field):
    if operation=='remove':saved(tmp_path)
    before=core.read(tmp_path)['product']['document'];entity_id='signup' if operation=='remove' else 'new'
    create(tmp_path);o=options(tmp_path);o['editable']=[{'kind':'feature','id':entity_id,'fields':['*'+operation]}]
    v,p,_=prepare(tmp_path,o);v,_=reserve(tmp_path,v,p);q=proposal(p['request'])
    q['edits']=[{'op':operation,'kind':'feature','id':entity_id,'field':field,'value':{'id':entity_id,'source_references':[]} if operation=='add' else None}]
    raw=json.dumps(q);v,payload=finish(tmp_path,v,p,result(raw));row=v['session']['requests'][0]
    assert row['status']!='reserved' and row['result']['text']==raw and row['proposal'] is None
    assert planning.dispatch(tmp_path,payload)['revision']==v['revision']
    reopened=planning.dispatch(tmp_path,{'operation':'planning_read','session_id':'S1'})['session']['requests'][0]
    assert reopened['result']['text']==raw and reopened['proposal'] is None
    review=planning.dispatch(tmp_path,{'operation':'planning_review','session_id':'S1','request_id':p['request']['id'],'current_binding':p['request']['configuration'],'actor':'Owner'})
    assert review['action'] is None and core.read(tmp_path)['product']['document']==before

@pytest.mark.parametrize('malformed',['duplicate','NaN','Infinity','-Infinity','1e400'])
def test_malformed_json_response_retains_terminal_usage(tmp_path,malformed):
    create(tmp_path);v,p,_=prepare(tmp_path);v,_=reserve(tmp_path,v,p);q=proposal(p['request']);raw=json.dumps(q)
    if malformed=='duplicate':raw=raw.replace('"schema_version": 1','"schema_version": 1, "schema_version": 1')
    else:raw=raw.replace('"A better app"',malformed)
    receipt=result(raw);receipt['usage'].update(input_tokens=11,output_tokens=7,cost=0.25,currency='USD')
    v,payload=finish(tmp_path,v,p,receipt);row=v['session']['requests'][0]
    assert row['status']=='succeeded' and row['result']==receipt and row['proposal'] is None and 'planning_proposal_invalid' in row['blockers']
    assert v['session']['usage']['input_tokens']==11 and v['session']['usage']['output_tokens']==7
    assert planning.dispatch(tmp_path,payload)['revision']==v['revision']
    reopened=planning.dispatch(tmp_path,{'operation':'planning_read','session_id':'S1'})
    json.dumps(reopened,allow_nan=False)
    assert reopened['session']['requests'][0]['result']==receipt

def test_finish_serialized_worst_case_fits_reserved_budget(tmp_path):
    create(tmp_path);v,p,_=prepare(tmp_path);v,_=reserve(tmp_path,v,p)
    r=result('\u0001'*12000,'failed');r['error']='e'*2000
    v,_=finish(tmp_path,v,p,r);assert v['session']['reserved_bytes']==0 and v['session']['usage']['attempts']==1

@pytest.mark.parametrize('drift',['action','configuration','session','source'])
def test_proposal_effective_save_revalidates_after_review(tmp_path,drift):
    doc=document()
    if drift=='source':
        from cc_product_documents import observe_source
        (tmp_path/'brief.md').write_text('# Accounts\nCreate safely\n');source=observe_source(tmp_path,'brief.md');doc['sources']=[source]
    saved(tmp_path,doc);create(tmp_path);o=options(tmp_path)
    if drift=='source':o['sources']=[{'source_id':source['source_id'],'section_id':source['sections'][0]['id']}]
    v,p,_=prepare(tmp_path,o);v,_=reserve(tmp_path,v,p);v,_=finish(tmp_path,v,p,result(json.dumps(proposal(p['request']))))
    review=planning.dispatch(tmp_path,{'operation':'planning_review','session_id':'S1','request_id':p['request']['id'],'current_binding':p['request']['configuration'],'actor':'Owner'});before=core.read(tmp_path)
    if drift=='action':review['action']['document']['title']='Changed after review'
    elif drift=='configuration':Path(o['configuration']['role_policy_path']).write_text('{}')
    elif drift=='source':(tmp_path/'brief.md').write_text('Competing source edit')
    else:planning.dispatch(tmp_path,{'operation':'planning_append','session_id':'S1','expected_revision':v['revision'],'operation_id':'f'*64,'actor':'Owner','role':'user','text':'New question','questions':[],'choices':[],'reviewer_seconds':0})
    with pytest.raises(ValueError):core.prepare(tmp_path,review['action'],before['revision'])
    assert core.read(tmp_path)['product']['document']['title']==doc['title']

def test_specification_proposal_has_pending_human_mapping_and_plan_files_owner_observed(tmp_path):
    import cc_product
    from test_cc_development_core import decide,act
    handed(tmp_path);decide(tmp_path,'intent');create(tmp_path);o=options(tmp_path,'specification');o.update(change_id='C1',feature_id='signup',editable=[{'kind':'artifact','id':'C1','fields':['decisions']}])
    v,p,_=prepare(tmp_path,o);v,_=reserve(tmp_path,v,p);q=proposal(p['request']);q['edits']=[{'op':'set','kind':'artifact','id':'C1','field':'decisions','value':['Use local persistence with input validation']}]
    v,_=finish(tmp_path,v,p,result(json.dumps(q)));r=planning.dispatch(tmp_path,{'operation':'planning_review','session_id':'S1','request_id':p['request']['id'],'current_binding':p['request']['configuration'],'actor':'Owner'})
    from test_cc_product_core import detailed_action
    assert r['action']['mappings']==[]
    before=core.read(tmp_path);blocked=core.prepare(tmp_path,r['action'],before['revision'])
    assert 'planning_detail_required' in blocked['blockers'] and core.read(tmp_path)==before
    # Explicit manual authoring retains the proposed content without claiming the v1 review token.
    manual=dict(r['action']);manual.pop('planning_review',None)
    act(tmp_path,detailed_action(tmp_path,manual))
    assert 'planning_criterion_uncovered' in core.read(tmp_path)['changes'][0]['gates']['design']['reasons']
    template=cc_product.template(tmp_path,'C1','design')
    for mapping in template['mappings']:mapping['reason']='Human compared and confirms preserved exact behavior.'
    template['content']['decisions']=r['action']['content']['decisions']
    act(tmp_path,detailed_action(tmp_path,{'kind':'product_specification','change_id':'C1','content':template['content'],'mappings':template['mappings'],'actor':'Owner'}));decide(tmp_path,'design')
    # A separate request keeps new upstream inputs distinct and requires no old approval replay.
    planning.dispatch(tmp_path,{'operation':'planning_create','session_id':'S2','operation_id':'9'*64,'actor':'Owner','task_id':'T2'})
    o=options(tmp_path,'plan');o.update(change_id='C1',feature_id='signup',editable=[{'kind':'artifact','id':'C1','fields':['files']}])
    current=planning.dispatch(tmp_path,{'operation':'planning_read','session_id':'S2'});p=planning.dispatch(tmp_path,{'operation':'planning_preview','session_id':'S2','options':o});v=planning.dispatch(tmp_path,{'operation':'planning_prepare','session_id':'S2','expected_revision':current['revision'],'operation_id':'8'*64,'actor':'Owner','options':o,'preview_id':p['preview_id']})
    v=planning.dispatch(tmp_path,{'operation':'planning_reserve','session_id':'S2','expected_revision':v['revision'],'operation_id':'7'*64,'actor':'Owner','request_id':p['request']['id'],'current_binding':p['request']['configuration']});q=proposal(p['request']);q['edits']=[{'op':'set','kind':'artifact','id':'C1','field':'files','value':['accounts.py']}]
    planning.dispatch(tmp_path,{'operation':'planning_finish','session_id':'S2','expected_revision':v['revision'],'operation_id':'6'*64,'actor':'Owner','request_id':p['request']['id'],'current_binding':p['request']['configuration'],'result':result(json.dumps(q))})
    r=planning.dispatch(tmp_path,{'operation':'planning_review','session_id':'S2','request_id':p['request']['id'],'current_binding':p['request']['configuration'],'actor':'Owner'});assert r['action']['files']==[{'path':'accounts.py','kind':'new','sha256':'absent'}]
    before=core.read(tmp_path);assert 'planning_detail_required' in core.prepare(tmp_path,r['action'],before['revision'])['blockers']
    manual=dict(r['action']);manual.pop('planning_review',None)
    act(tmp_path,detailed_action(tmp_path,manual))

def test_pending_usage_unknown_and_partial_known_subtotal(tmp_path):
    create(tmp_path);v,p,_=prepare(tmp_path);v,_=reserve(tmp_path,v,p);assert v['session']['usage']['input_tokens'] is None and v['session']['usage']['cost'] is None
    value=result('Failure','failed');value['usage'].update(input_tokens=12,output_tokens=None,cost=None);v,_=finish(tmp_path,v,p,value)
    assert v['session']['usage']['input_tokens']==12 and v['session']['usage']['output_tokens'] is None and v['session']['usage']['known_subtotal']['input_tokens']==12
