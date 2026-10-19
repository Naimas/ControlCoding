"""Durable bounded planning dialogue. Providers never run inside this owner."""
from __future__ import annotations
import copy
import datetime
import json
import math
import hashlib
from pathlib import Path
import re
import stat

import cc_development_store as store
import cc_development_sessions as sessions
import cc_evidence_inputs as inputs
from cc_layout import managed_relative

MAX_EVENTS = 64
MAX_BYTES = 256 * 1024
FINISH_RESERVE = MAX_BYTES
CONTEXT_BYTES = 48000
OUTPUT_BYTES = 32768
TOKEN_FIELDS = {'schema_version','session_id','session_revision','request_id','proposal_digest','configuration_revision','action_digest'}
CONFIG_FIELDS = {'role','role_policy_path','role_policy_revision','capability_identity','template_identity','extraction_policy_identity','config'}
OPTION_FIELDS = {'target','feature_id','change_id','document_id','editable','sources','messages','configuration','limits'}
LIMIT_FIELDS = {'max_output_tokens','timeout_seconds','max_context_chars','max_requests','output_bytes'}
DETAIL_TARGETS=('specification_v2','implementation_plan_v2')
TARGETS = ('dialogue','product','feature','specification','plan',*DETAIL_TARGETS)
BLOCKERS = {'planning_configuration_changed','planning_base_changed','planning_product_changed','planning_source_changed','planning_response_incomplete','planning_proposal_invalid','planning_question_limit'}

def terminal_reservation(configuration,limits):
    """Bound the exact ASCII object encoding for reserve plus terminal event.

    A BMP/control character costs at most six serialized bytes; a supplementary
    character costs twelve and at least four wire bytes. Both the 12,000-character
    cap and the output-byte cap apply. Configuration is persisted in both events.
    Metadata allowance covers two bounded actors, error, usage, codes and heads.
    """
    chars=min(12000,limits['output_bytes']);extra=min(3*chars,max(0,limits['output_bytes']-chars))
    text_bytes=6*chars+2*extra
    return text_bytes+2*len(store._encoded(configuration))+2000*12+16384

def require(value, code):
    if not value: raise store.StoreError(code)

def exact(value, fields, code='planning_schema_invalid'):
    require(type(value) is dict and set(value)==set(fields),code)

def text(value, maximum=16000, blank=False):
    require(type(value) is str and len(value)<=maximum and '\x00' not in value and (blank or value.strip()),'planning_text_invalid')

def strings(value, maximum=64):
    require(type(value) is list and len(value)<=maximum,'planning_list_invalid')
    for item in value:text(item)

def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()

def stamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()

def _head(safe, session_id):
    sessions._id(session_id)
    item=safe.read(sessions._path(safe,session_id),inputs.Budget(total=8192,per_file=8192),missing=True)
    if item is None:return None
    require(item[1].st_nlink==1,'session_head_unsafe')
    head=json.loads(item[0],object_pairs_hook=inputs._unique_object)
    require(type(head) is dict,'session_head_invalid')
    if head.get('schemaVersion')==1:return head
    exact(head,{'schemaVersion','kind','id','object','reservedBytes'},'session_head_invalid')
    require(type(head['schemaVersion']) is int and head['schemaVersion']==2 and head['kind']=='planning_session_head' and head['id']==session_id,'session_head_invalid')
    sessions._revision(head['object'])
    require(head['object']!='absent' and type(head['reservedBytes']) is int and 0<=head['reservedBytes']<=FINISH_RESERVE,'planning_reservation_invalid')
    return head

def history(safe,head,object_reader=None):
    from cc_development_lib.storage import identity
    ref=head['object'];items=[];seen=set();total=len(sessions._bytes(head))
    def fetch(r):
        envelope=store.get_raw_object(safe,r)
        require(envelope['domain']=='planning','session_object_domain_invalid')
        return store.validate_raw_object(r,envelope)
    while ref is not None:
        require(ref not in seen and len(items)<MAX_EVENTS,'planning_history_invalid');seen.add(ref)
        node=(object_reader or fetch)(ref)
        exact(node,{'schemaVersion','kind','id','projectIdentity','previous','createdAt','actor','operation','sequence','action'},'planning_history_invalid')
        require(node['schemaVersion']==2 and type(node['schemaVersion']) is int and node['kind']=='planning_session_event' and node['id']==head['id'] and node['projectIdentity']==identity(safe),'planning_history_invalid')
        sessions._revision(node['previous'] or 'absent');text(node['actor'],160)
        require(datetime.datetime.fromisoformat(node['createdAt']).tzinfo is not None,'session_timestamp_invalid')
        exact(node['operation'],{'id','requestDigest','publicDigest'});sessions._revision(node['operation']['id']);sessions._revision(node['operation']['requestDigest']);sessions._revision(node['operation']['publicDigest'])
        require('absent' not in node['operation'].values(),'session_operation_invalid')
        require(type(node['sequence']) is int and 0<=node['sequence']<MAX_EVENTS,'planning_history_invalid')
        total+=store.object_storage_bytes(node,domain='planning');items.append({'revision':ref,'node':node});ref=node['previous']
    items.reverse();ops=set()
    for i,item in enumerate(items):
        n=item['node'];require(n['sequence']==i and n['operation']['id'] not in ops,'planning_history_invalid');ops.add(n['operation']['id'])
        require(inputs.digest(n['action'])==n['operation']['requestDigest'],'session_request_binding_invalid')
    require(total<=MAX_BYTES,'session_size_limit')
    state=project(items,total)
    outstanding=next((r for r in state['requests'] if r['status'] in ('prepared','reserved')),None)
    require(head['reservedBytes']==(outstanding['reserved_bytes'] if outstanding else 0),'planning_reservation_invalid')
    return items,total

def project(items,total):
    state={'schema_version':2,'id':items[0]['node']['id'],'project_identity':items[0]['node']['projectIdentity'],'task_id':'','events':[], 'turns':[],'questions':[],'choices':[],'requests':[],'proposals':[],'usage':{'attempts':0,'network_calls':0,'input_tokens':0,'output_tokens':0,'cost':0,'unknown_usage':0,'unknown_cost':0},'reviewer_seconds':0,'retained_bytes':total,'reserved_bytes':0}
    for index,item in enumerate(items):
        n=item['node'];a=n['action'];kind=a.get('kind');state['events'].append({'revision':item['revision'],'kind':kind,'created_at':n['createdAt'],'actor':n['actor']})
        if index==0:
            exact(a,{'kind','task_id'});require(kind=='create','planning_history_invalid');sessions._id(a['task_id']);state['task_id']=a['task_id'];continue
        require(kind in ('append','prepare','reserve','finish'),'planning_history_invalid')
        if kind=='append':
            exact(a,{'kind','role','text','questions','choices','reviewer_seconds'})
            require(not any(r['status'] in ('prepared','reserved') for r in state['requests']),'planning_pending_request')
            require(a['role'] in ('user','assistant'),'session_role_invalid');text(a['text']);strings(a['questions']);strings(a['choices'])
            require(type(a['reviewer_seconds']) is int and 0<=a['reviewer_seconds']<=86400,'planning_effort_invalid')
            state['turns'].append({'role':a['role'],'text':a['text'],'actor':n['actor'],'created_at':n['createdAt']});state['questions']=list(dict.fromkeys(state['questions']+a['questions']));state['choices']=list(dict.fromkeys(state['choices']+a['choices']));strings(state['questions']);strings(state['choices']);state['reviewer_seconds']+=a['reviewer_seconds']
        elif kind=='prepare':
            exact(a,{'kind','request'});r=a['request'];validate_request(r)
            require(r['session_id']==state['id'] and r['task_id']==state['task_id'] and r['project_identity']==state['project_identity'] and r['session_revision']==items[index-1]['revision'],'planning_request_binding_invalid')
            require(not any(x['status'] in ('prepared','reserved') or x['request']['id']==r['id'] for x in state['requests']),'planning_pending_request')
            state['requests'].append({'request':r,'status':'prepared','result':None,'proposal':None,'blockers':[],'attempts':0,'reserved_bytes':r['reservation_bytes']})
            user_messages=[m for m in r['messages'] if m['role']=='user' and not m['content'].startswith('Selected planning context: ')]
            if user_messages:state['turns'].append({'role':'user','text':user_messages[-1]['content'],'actor':n['actor'],'created_at':n['createdAt']})
        else:
            fields={'kind','request_id','current_binding'}|({'result','blockers','proposal'} if kind=='finish' else set())
            exact(a,fields);rows=[r for r in state['requests'] if r['request']['id']==a['request_id']];require(len(rows)==1,'planning_request_unknown');row=rows[0]
            validate_configuration(a['current_binding'],derived=True)
            if kind=='reserve':
                require(row['status']=='prepared' and a['current_binding']==row['request']['configuration'],'planning_attempt_invalid');row['status']='reserved';row['attempts']=1;row['reserved_bytes']-=store.object_storage_bytes(n,domain='planning');require(row['reserved_bytes']>0,'planning_reservation_invalid')
                state['usage']['attempts']+=1;state['usage']['network_calls']+=int(row['request']['configuration']['config']['mode']!='handoff')
            else:
                require(row['status'] in ('prepared','reserved') and (row['status']=='reserved' or a['result']['outcome'] in ('cancelled','interrupted')),'planning_attempt_invalid')
                validate_result(a['result'],row['request']);strings(a['blockers']);require(set(a['blockers'])<=BLOCKERS,'planning_blocker_invalid');row.update(status=a['result']['outcome'],result=a['result'],proposal=a['proposal'],blockers=a['blockers'],reserved_bytes=0)
                require(a['proposal'] is None,'planning_history_invalid')
                if a['result']['outcome']=='succeeded' and row['request']['target']!='dialogue' and not a['result']['incomplete'] and 'planning_proposal_invalid' not in a['blockers']:
                    proposal=parse_proposal(a['result']['text'],row['request']);row['proposal']=proposal;state['proposals'].append({'request_id':row['request']['id'],'proposal':proposal})
                u=a['result']['usage']
                if row['attempts']:
                    if u['input_tokens'] is None or u['output_tokens'] is None:state['usage']['unknown_usage']+=1
                    if u['cost'] is None:state['usage']['unknown_cost']+=1
                    for key in ('input_tokens','output_tokens','cost'):
                        if u[key] is not None:state['usage'][key]+=u[key]
                if a['result']['text']:state['turns'].append({'role':'assistant','text':a['result']['text'],'actor':n['actor'],'created_at':n['createdAt']})
                if row['proposal'] is not None and 'planning_question_limit' not in row['blockers']:state['questions']=list(dict.fromkeys(state['questions']+row['proposal']['questions']));strings(state['questions'])
    attempts=[r for r in state['requests'] if r['attempts']]
    known={k:sum(r['result']['usage'][k] for r in attempts if r['result'] is not None and r['result']['usage'][k] is not None) for k in ('input_tokens','output_tokens','cost')}
    state['usage']['known_subtotal']=known
    for k in ('input_tokens','output_tokens','cost'):
        state['usage'][k]=known[k] if all(r['result'] is not None and r['result']['usage'][k] is not None for r in attempts) else None
    currencies={r['result']['usage']['currency'] for r in attempts if r['result'] is not None and r['result']['usage']['cost'] is not None}
    state['usage']['currency']=next(iter(currencies)) if len(currencies)==1 and None not in currencies else None
    if attempts and state['usage']['currency'] is None:state['usage']['cost']=None
    state['usage']['unknown_usage']=sum(r['result'] is None or r['result']['usage']['input_tokens'] is None or r['result']['usage']['output_tokens'] is None for r in attempts)
    state['usage']['unknown_cost']=sum(r['result'] is None or r['result']['usage']['cost'] is None or r['result']['usage']['currency'] is None for r in attempts)
    state['usage']['elapsed_ms']=sum(r['result']['elapsed_ms'] for r in attempts if r['result'] is not None)
    state['reserved_bytes']=sum(r['reserved_bytes'] for r in state['requests']);return state

def read_safe(safe,session_id):
    head=_head(safe,session_id)
    if head is None:return {'revision':'absent','session':None}
    if head['schemaVersion']==1:return sessions.read_safe_v1(safe,session_id)
    items,total=history(safe,head);return {'revision':head['object'],'session':project(items,total)}

def validate_configuration(c,derived=False):
    exact(c,CONFIG_FIELDS|({'configuration_revision','generation_identity'} if derived else set()))
    require(c['role'] in ('concierge','architect'),'planning_role_invalid')
    text(c['role_policy_path'],2048);sessions._revision(c['role_policy_revision']);sessions._revision(c['capability_identity'])
    require(c['template_identity']=='pw02-planning-v1' and c['extraction_policy_identity']=='pw02-selected-v1','planning_policy_invalid')
    fields={'enabled','provider','model','context','mode','setupProposals','maxOutputTokens','timeoutSeconds','maxContextChars','maxRequests','systemPrompt','generation'}
    exact(c['config'],fields);config=c['config']
    require(config['enabled'] is True and config['provider'] in ('ollama','openai') and config['mode'] in ('reviewed','direct','handoff'),'planning_configuration_invalid')
    text(config['model'],160,config['mode']=='handoff');text(config['systemPrompt'],4000)
    for k,lo,hi in [('maxOutputTokens',128,8192),('timeoutSeconds',5,120),('maxContextChars',0,16000),('maxRequests',1,100)]:require(type(config[k]) is int and lo<=config[k]<=hi,'planning_configuration_invalid')
    require(type(config['generation']) is dict and type(config['setupProposals']) is bool and config['context'] in ('none','memory','setup'),'planning_configuration_invalid')
    g=config['generation'];exact(g,{'effort','thinking','reasoningMode','verbosity','temperature','format','summary'},'planning_generation_invalid')
    require(g['effort'] in ('default','none','minimal','low','medium','high','xhigh','max') and g['reasoningMode'] in ('default','standard','pro') and g['verbosity'] in ('default','low','medium','high') and g['format'] in ('text','json') and type(g['summary']) is bool,'planning_generation_invalid')
    require(g['thinking'] is None or type(g['thinking']) is bool or type(g['thinking']) is str and 0<len(g['thinking'])<=40 and not any(ord(c)<32 for c in g['thinking']),'planning_generation_invalid')
    require(g['temperature'] is None or type(g['temperature']) in (int,float) and math.isfinite(g['temperature']) and 0<=g['temperature']<=2,'planning_generation_invalid')
    if derived:require(c['configuration_revision']==inputs.digest(config) and c['generation_identity']==inputs.digest(config['generation']),'planning_configuration_invalid')

def observe_configuration(c,root=None):
    validate_configuration(c,derived='configuration_revision' in c)
    path=Path(c['role_policy_path']);require(path.is_absolute(),'planning_role_policy_invalid')
    with inputs.SafeRoot(path.parent) as external:
        raw,info=external.read(path.name,inputs.Budget(total=131072,per_file=131072))
        require(info.st_nlink==1,'planning_role_policy_invalid');value=json.loads(raw,object_pairs_hook=inputs._unique_object)
        exact(value,{'schemaVersion','project','roles'},'planning_role_policy_invalid')
        if root is not None:
            physical=str(Path(root).resolve());physical=physical.lower() if __import__('os').name=='nt' else physical
            require(value['project']==physical,'planning_role_project_changed')
        require(value['schemaVersion']==4 and type(value['roles']) is dict and value['roles'].get(c['role'])==c['config'] and hashlib.sha256(raw).hexdigest()==c['role_policy_revision'],'planning_configuration_changed')
        external.validate()
    result=copy.deepcopy(c);result['configuration_revision']=inputs.digest(c['config']);result['generation_identity']=inputs.digest(c['config']['generation']);return result

def validate_request(r):
    exact(r,{'id','session_id','task_id','project_identity','session_revision','control_revision','product_revision','selection_revision','artifact_revision','target','feature_id','change_id','document_id','editable','sources','base','configuration','limits','messages','context_digest','prompt_identity','reservation_bytes'} | ({'detail_contract'} if r.get('target') in DETAIL_TARGETS else set()))
    if r.get('target') in DETAIL_TARGETS:
        exact(r['detail_contract'],{'profile_revision','selection_revision','upstream_artifact_revision','upstream_detail_revision','source_manifest_digest'})
        for value in r['detail_contract'].values():sessions._revision(value)
    require(r['target'] in TARGETS,'planning_target_invalid');validate_configuration(r['configuration'],True)
    require(type(r['reservation_bytes']) is int and r['reservation_bytes']==terminal_reservation(r['configuration'],r['limits']) and r['reservation_bytes']<=MAX_BYTES,'planning_reservation_invalid')
    for k in ('session_revision','control_revision','product_revision','selection_revision','artifact_revision','context_digest','prompt_identity'):sessions._revision(r[k])
    sessions._id(r['id']);sessions._id(r['session_id']);sessions._id(r['task_id']);validate_options({k:r[k] for k in ('target','feature_id','change_id','document_id','editable','messages','configuration','limits')}|{'sources':[{'source_id':s['source_id'],'section_id':s['section_id']} for s in r['sources']]},derived=True)
    for source in r['sources']:exact(source,{'source_id','section_id','sha256','text'});sessions._revision(source['sha256']);text(source['text'],128*1024,True)
    expected={k:v for k,v in r.items() if k not in ('id','messages','prompt_identity')};require(r['id']=='R-'+inputs.digest(expected)[:32] and r['prompt_identity']==inputs.digest(r['messages']),'planning_request_binding_invalid')

def validate_options(o,derived=False):
    exact(o,OPTION_FIELDS);require(o['target'] in TARGETS,'planning_target_invalid')
    for k in ('feature_id','change_id','document_id'):text(o[k],80,True)
    validate_configuration(o['configuration'],derived);exact(o['limits'],LIMIT_FIELDS)
    config=o['configuration']['config'];limits=o['limits']
    for k,ck in [('max_output_tokens','maxOutputTokens'),('timeout_seconds','timeoutSeconds'),('max_context_chars','maxContextChars'),('max_requests','maxRequests')]:require(type(limits[k]) is int and 0<=limits[k]<=config[ck],'planning_request_limit')
    require(128<=limits['max_output_tokens']<=8192 and 5<=limits['timeout_seconds']<=120 and limits['max_requests']>=1 and type(limits['output_bytes']) is int and 1<=limits['output_bytes']<=OUTPUT_BYTES,'planning_request_limit')
    require(type(o['editable']) is list and len(o['editable'])<=64 and type(o['sources']) is list and len(o['sources'])<=64,'planning_request_limit')
    seen=set()
    for e in o['editable']:
        exact(e,{'kind','id','fields'});require(e['kind'] in ('document','chapter','feature','criterion','artifact'),'planning_edit_invalid');text(e['id'],80);strings(e['fields']);key=(e['kind'],e['id']);require(key not in seen,'planning_edit_invalid');seen.add(key)
    for s in o['sources']:exact(s,{'source_id','section_id'});text(s['source_id'],80);text(s['section_id'],80)
    require(type(o['messages']) is list and 1<=len(o['messages'])<=66,'planning_messages_invalid')
    for m in o['messages']:exact(m,{'role','content'});require(m['role'] in ('system','user','assistant'),'planning_messages_invalid');text(m['content'],48000)
    require(len(encoded(o['messages']))<=CONTEXT_BYTES,'planning_context_limit')

def blank_document(document_id):
    return {'id':document_id,'title':'','language':'English','goals':[],'users':[],'constraints':[],'non_goals':[],'questions':[],'chapters':[],'features':[],'sources':[],'notes':''}

def request_preview(safe,session_id,options):
    from cc_development_lib import storage
    import cc_product
    import cc_product_documents
    validate_options(options);current=read_safe(safe,session_id);require(current['session'] is not None and current['session'].get('schema_version')==2,'planning_session_v2_required')
    state,revision=storage.load(safe,locked=True);product=cc_product.view(state);o=copy.deepcopy(options);configuration=observe_configuration(o['configuration'],safe.path)
    require(not any(r['status'] in ('prepared','reserved') for r in current['session']['requests']),'planning_pending_request')
    require(sum(r['attempts'] for r in current['session']['requests'])<o['limits']['max_requests'],'planning_request_limit')
    doc=product['document'];base=doc or blank_document(o['document_id']);selection='absent';artifact='absent'
    if o['target'] in ('feature',):require(doc is not None and any(f['id']==o['feature_id'] for f in doc['features']),'planning_feature_unknown')
    detail_contract=None
    if o['target'] in DETAIL_TARGETS:
        import cc_product_planning
        stage='design' if o['target']=='specification_v2' else 'plan'
        planning=cc_product_planning.preview(safe,state,o['change_id'],stage)
        require(planning['applicability']=='detailed','planning_detail_required')
        base={'content':planning['content'],'details':planning['details'],'mappings':planning['mappings']} if stage=='design' else {'content':planning['content'],'details':planning['details'],'files':planning['files']}
        selection=planning['selection_revision'];artifact=inputs.digest(planning['content'])
        detail_contract={'profile_revision':planning['profile']['revision'],'selection_revision':selection,'upstream_artifact_revision':planning['upstream_artifact_revision'],'upstream_detail_revision':planning['upstream_detail_revision'],'source_manifest_digest':cc_product_planning.source_manifest(planning['details'])}
    if o['target'] in ('specification','plan'):
        require(any(c['id']==o['change_id'] for c in state['changes']),'planning_change_unknown')
        increment=next((i for i in product['increments'] if i['change_id']==o['change_id']),None);require(increment is not None,'planning_selection_required');selection=increment['selection_revision']
        stage='design' if o['target']=='specification' else 'plan';draft=next((d for d in product['drafts'] if d['change_id']==o['change_id'] and d['stage']==stage),None)
        from cc_development_lib import snapshot
        template=cc_product.template(safe.path,o['change_id'],stage,snapshot=snapshot(safe,state,revision,locked=True));base=copy.deepcopy(draft['content'] if draft else template['content']);artifact=draft['artifact_revision'] if draft else 'absent'
    sources=[]
    for selected in o['sources']:
        source=next((s for s in (doc or {}).get('sources',[]) if s['source_id']==selected['source_id']),None);require(source is not None,'planning_source_unknown');cc_product_documents.validate_source(safe.path,source,current=True,reviewed=False)
        section=next((s for s in cc_product_documents.source_items(source) if s['id']==selected['section_id']),None);require(section is not None,'planning_source_unknown');sources.append({**selected,'sha256':source['sha256'],'text':section['text']})
    selected_base=[]
    for e in o['editable']:
        entity=_entity(base,e['kind'],e['id'],o['feature_id'])
        require(entity is not None or e['fields']==['*add'],'planning_edit_unknown')
        if entity is not None:
            for field in e['fields']:require(field in entity or field=='*remove','planning_edit_invalid')
            selected_base.append({'kind':e['kind'],'id':e['id'],'fields':{field:copy.deepcopy(entity[field]) for field in e['fields'] if field in entity}})
        else:selected_base.append({'kind':e['kind'],'id':e['id'],'fields':{'proposed_new':True}})
    context={'target':o['target'],'entities':selected_base,'sources':sources}
    context_text=encoded(context).decode();require(len(context_text)<=o['limits']['max_context_chars'] or not selected_base and not sources,'planning_context_limit')
    messages=copy.deepcopy(o['messages'])
    protocol='Discuss only; do not approve or execute.' if o['target']=='dialogue' else 'Return only exact PW02 JSON proposal: schema_version=1,request_id,edits,claims,questions,conflicts,omissions. No approvals, actors, upstream IDs or equivalence decisions. Each edit requires a selected-source citation or explicit assumption/proposed_decision.'
    if o['target'] in DETAIL_TARGETS:protocol=protocol.replace('PW02 JSON proposal: schema_version=1','PW04 JSON proposal: schema_version=2')
    messages.insert(0,{'role':'system','content':protocol})
    if selected_base or sources:messages.append({'role':'user','content':'Selected planning context: '+context_text})
    require(len(encoded(messages))<=CONTEXT_BYTES,'planning_context_limit')
    request={'session_id':session_id,'task_id':current['session']['task_id'],'project_identity':state['project_identity'],'session_revision':current['revision'],'control_revision':revision,'product_revision':product['current_revision'],'selection_revision':selection,'artifact_revision':artifact,'target':o['target'],'feature_id':o['feature_id'],'change_id':o['change_id'],'document_id':o['document_id'],'editable':o['editable'],'sources':sources,'base':selected_base if o['target'] in ('dialogue','product','feature') else base,'configuration':configuration,'limits':o['limits'],'messages':messages,'context_digest':inputs.digest({'context':context,'user_messages':o['messages']})}
    if detail_contract is not None:request['detail_contract']=detail_contract
    request['reservation_bytes']=terminal_reservation(configuration,o['limits'])
    request['id']='R-'+inputs.digest({k:v for k,v in request.items() if k!='messages'})[:32]
    if o['target']!='dialogue':
        request['messages'][0]['content']+=' Request ID: '+request['id']+'. Editable targets: '+encoded(o['editable']).decode()+'. Exact edits: {op:set|add|remove,kind:document|chapter|feature|criterion|artifact,id,field,value}; claims: {edit_index,kind:source|assumption|proposed_decision,source_id,section_id,reason}. Add uses field empty and full entity with stable id; remove value null. New feature shape: '+','.join(sorted(cc_product.FEATURE))+'. Every field required; source claims use selected exact IDs; assumptions blank source IDs and explicit reason.'
    require(len(encoded(request['messages']))<=CONTEXT_BYTES,'planning_context_limit')
    request['prompt_identity']=inputs.digest(request['messages'])
    return {'preview_id':inputs.digest(request),'request':request,'blockers':[],'limits':copy.deepcopy(o['limits'])}

def _entity(base,kind,id,feature_id):
    if kind=='artifact':return base
    if kind=='document':return base if base.get('id')==id else None
    if kind in ('feature','chapter'):return next((e for e in base.get(kind+'s',[]) if e['id']==id),None)
    if kind=='criterion':
        feature=next((f for f in base.get('features',[]) if f['id']==feature_id),None)
        return next((e for e in (feature or {}).get('criteria',[]) if e['id']==id),None)

def validate_result(result,request):
    exact(result,{'outcome','text','incomplete','usage','elapsed_ms','error','provenance'});require(result['outcome'] in ('succeeded','failed','cancelled','interrupted') and type(result['incomplete']) is bool,'planning_result_invalid')
    text(result['text'],12000,True);text(result['error'],2000,True);require(len(result['text'].encode())<=request['limits']['output_bytes'],'planning_output_limit')
    require(request['configuration']['config']['mode']!='handoff' or len(result['text'])<=12000,'planning_manual_output_limit')
    require(type(result['elapsed_ms']) is int and 0<=result['elapsed_ms']<=86400000 and result['provenance'] in ('provider_unverified','external_unverified'),'planning_result_invalid')
    exact(result['usage'],{'input_tokens','output_tokens','cost','currency'})
    for k in ('input_tokens','output_tokens'):require(result['usage'][k] is None or type(result['usage'][k]) is int and 0<=result['usage'][k]<=100000000,'planning_usage_invalid')
    cost=result['usage']['cost'];require(cost is None or type(cost) in (int,float) and math.isfinite(cost) and cost>=0,'planning_usage_invalid');require(result['usage']['currency'] is None or type(result['usage']['currency']) is str and len(result['usage']['currency'])<=16,'planning_usage_invalid')

def parse_proposal(raw,request):
    proposal=json.loads(raw,object_pairs_hook=inputs._unique_object)
    # Reject non-finite constants and numeric overflow before retaining a
    # derived proposal that cannot be represented by canonical JSON.
    json.dumps(proposal,allow_nan=False)
    validate_proposal(proposal,request)
    return proposal

def validate_proposal(proposal,request):
    exact(proposal,{'schema_version','request_id','edits','claims','questions','conflicts','omissions'},'planning_proposal_invalid')
    require(type(proposal['schema_version']) is int and proposal['schema_version']==(2 if request['target'] in DETAIL_TARGETS else 1) and proposal['request_id']==request['id'],'planning_proposal_binding_invalid')
    require(type(proposal['edits']) is list and 1<=len(proposal['edits'])<=64 and type(proposal['claims']) is list and len(proposal['claims'])<=128,'planning_proposal_invalid')
    for k in ('questions','conflicts','omissions'):strings(proposal[k])
    used=set()
    for i,e in enumerate(proposal['edits']):
        exact(e,{'op','kind','id','field','value'},'planning_proposal_invalid');require(e['op'] in ('set','add','remove') and type(e['field']) is str,'planning_edit_invalid')
        allowed=next((a for a in request['editable'] if a['kind']==e['kind'] and a['id']==e['id']),None);require(allowed is not None,'planning_edit_outside_scope')
        permission=e['field'] if e['op']=='set' else '*'+e['op'];require(permission in allowed['fields'],'planning_edit_outside_scope')
        require(e['field'] not in ('id','features','chapters','sources','upstream_intent_revision','upstream_specification_revision','source_references') and not e['field'].startswith('approved'),'planning_owner_field_forbidden')
        require(e['op']=='set' or e['field']=='' and (e['op']!='remove' or e['value'] is None),'planning_edit_invalid')
        key=(e['kind'],e['id'],e['field']);require(key not in used,'planning_edit_conflict');used.add(key)
        if request['target']=='feature':require(e['kind'] in ('feature','criterion') and (e['kind']=='criterion' or e['id']==request['feature_id']),'planning_edit_outside_scope')
        if request['target'] in ('specification','plan',*DETAIL_TARGETS):require(e['kind']=='artifact','planning_edit_outside_scope')
        if request['target'] in DETAIL_TARGETS:require(e['op']=='set' and e['field'] in request['base'],'planning_edit_outside_scope')
        if e['kind']=='document':require(e['op']=='set','planning_edit_invalid')
        if e['op']=='add':
            require(type(e['value']) is dict and e['value'].get('id')==e['id'],'planning_edit_invalid')
            if e['kind']=='feature':require(type(e['value'].get('source_references')) is list and set(e['value']['source_references']) <= {s['source_id'] for s in request['sources']},'planning_source_outside_scope')
    covered=set()
    for claim in proposal['claims']:
        exact(claim,{'edit_index','kind','source_id','section_id','reason'},'planning_claim_invalid');i=claim['edit_index'];require(type(i) is int and 0<=i<len(proposal['edits']) and claim['kind'] in ('source','assumption','proposed_decision'),'planning_claim_invalid');text(claim['reason']);covered.add(i)
        if claim['kind']=='source':require(any(s['source_id']==claim['source_id'] and s['section_id']==claim['section_id'] for s in request['sources']),'planning_source_unknown')
        else:require(claim['source_id']==claim['section_id']=='','planning_claim_invalid')
    require(covered==set(range(len(proposal['edits']))),'planning_claim_required')
    for index,e in enumerate(proposal['edits']):
        if e['op']=='add' and e['kind']=='feature':
            require(set(e['value']['source_references']) <= {c['source_id'] for c in proposal['claims'] if c['edit_index']==index and c['kind']=='source'},'planning_source_claim_required')

def binding_reasons(safe,request,current_binding):
    from cc_development_lib import storage
    import cc_product_documents
    reasons=[]
    if current_binding!=request['configuration']:reasons.append('planning_configuration_changed')
    try:observe_configuration(request['configuration'],safe.path)
    except (ValueError,OSError):reasons.append('planning_configuration_changed')
    state,revision=storage.load(safe,locked=True)
    if revision!=request['control_revision']:reasons.append('planning_base_changed')
    import cc_product
    product=cc_product.view(state)
    if product['current_revision']!=request['product_revision']:reasons.append('planning_product_changed')
    for selected in request['sources']:
        source=next((s for s in (product['document'] or {}).get('sources',[]) if s['source_id']==selected['source_id']),None)
        try:
            require(source is not None and source['sha256']==selected['sha256'],'planning_source_changed');cc_product_documents.validate_source(safe.path,source,current=True,reviewed=False)
        except (ValueError,OSError):reasons.append('planning_source_changed')
    if request['target'] in DETAIL_TARGETS:
        try:
            import cc_product_planning
            stage='design' if request['target']=='specification_v2' else 'plan';now=cc_product_planning.preview(safe,state,request['change_id'],stage)
            binding={'profile_revision':now['profile']['revision'],'selection_revision':now['selection_revision'],'upstream_artifact_revision':now['upstream_artifact_revision'],'upstream_detail_revision':now['upstream_detail_revision'],'source_manifest_digest':cc_product_planning.source_manifest(now['details'])}
            if now['applicability']!='detailed' or binding!=request['detail_contract']:reasons.append('planning_base_changed')
        except (OSError,ValueError,TypeError):reasons.append('planning_base_changed')
    return sorted(set(reasons))

def _write(safe,session_id,expected,operation_id,actor,action,public_digest):
    sessions._id(session_id);sessions._revision(expected);sessions._revision(operation_id);require(operation_id!='absent','session_operation_invalid');text(actor,160)
    head=_head(safe,session_id);require(head is None or head['schemaVersion']==2,'planning_session_v2_required');items,total=history(safe,head) if head else ([],0)
    for item in items:
        if item['node']['operation']['id']==operation_id:
            require(item['node']['operation']['requestDigest']==inputs.digest(action) and item['node']['actor']==actor,'session_idempotency_conflict');return read_safe(safe,session_id)
    require((head['object'] if head else 'absent')==expected,'session_revision_conflict')
    require(bool(items)==(action['kind']!='create'),'session_create_invalid')
    from cc_development_lib.storage import identity
    node={'schemaVersion':2,'kind':'planning_session_event','id':session_id,'projectIdentity':identity(safe),'previous':head['object'] if head else None,'createdAt':stamp(),'actor':actor,'operation':{'id':operation_id,'requestDigest':inputs.digest(action),'publicDigest':public_digest},'sequence':len(items),'action':copy.deepcopy(action)}
    # An object can survive a failed head publication. Reuse its original time
    # and bytes so an exact retry never spends a second terminal reservation.
    try:entries=safe.listing(managed_relative(safe.path,store.OBJECTS.rstrip('/')),inputs.Budget(entries=store.MAX_OBJECTS))
    except FileNotFoundError:entries=[]
    reused=False
    for name,_ in entries:
        if not name.endswith('.json'):continue
        envelope=store.get_raw_object(safe,name[:-5]);candidate=store.validate_raw_object(name[:-5],envelope)
        if envelope['domain']=='planning' and type(candidate) is dict and candidate.get('kind')=='planning_session_event' and candidate.get('id')==session_id and candidate.get('operation',{}).get('id')==operation_id:
            require(candidate['operation']['publicDigest']==public_digest and candidate['actor']==actor and candidate['previous']==node['previous'] and candidate['sequence']==node['sequence'],'session_idempotency_conflict');node=candidate;action=candidate['action'];reused=True;break
    require(len(items)<MAX_EVENTS,'session_turn_limit')
    future=items+[{'revision':'0'*64,'node':node}];state=project(future,total+store.object_storage_bytes(node,domain='planning'));reservation=state['reserved_bytes']
    require(total+store.object_storage_bytes(node,domain='planning')+reservation<=MAX_BYTES,'session_size_limit')
    if action['kind']=='prepare':require(len(items)+3<=MAX_EVENTS,'session_turn_limit')
    credit=(session_id,head['object']) if head and head['reservedBytes'] and action['kind'] in ('reserve','finish') else None
    node_size=store.object_storage_bytes(node,domain='planning')
    # Reservation already counts as used capacity. A terminal/reserve node spends
    # that credit; initial prepare accounts for the newly held credit once.
    prospective={'schemaVersion':2,'kind':'planning_session_head','id':session_id,'object':'0'*64,'reservedBytes':reservation}
    head_growth=len(sessions._bytes(prospective))-(len(sessions._bytes(head)) if head else 0)
    require(total+node_size+head_growth+reservation<=MAX_BYTES,'session_size_limit')
    if credit is None:store.ensure_planning_capacity(safe,(0 if reused else node_size)+reservation+max(0,head_growth))
    ref=store.put_object(safe,node,domain='planning',reservation=credit)
    newhead={'schemaVersion':2,'kind':'planning_session_head','id':session_id,'object':ref,'reservedBytes':reservation}
    safe.write_atomic(sessions._path(safe,session_id),newhead);store._durable(safe,sessions.SESSIONS+'/'+session_id+'.json');safe.validate();return read_safe(safe,session_id)

def review_safe(safe,session_id,request_id,current_binding,actor):
    import cc_product
    from cc_development_lib import storage
    current=read_safe(safe,session_id);require(current['session'] is not None,'planning_session_unknown');row=next((r for r in current['session']['requests'] if r['request']['id']==request_id),None);require(row is not None,'planning_request_unknown')
    reasons=list(row['blockers'])+binding_reasons(safe,row['request'],current_binding)
    if row['status']!='succeeded' or row['proposal'] is None:reasons.append('planning_proposal_unavailable')
    if reasons:return {'action':None,'revision':row['request']['control_revision'],'planning_review':None,'blockers':sorted(set(reasons))}
    request=row['request'];proposal=row['proposal'];state,revision=storage.load(safe,locked=True)
    base=copy.deepcopy((cc_product.view(state)['document'] or blank_document(request['document_id'])) if request['target'] in ('product','feature') else request['base'])
    for edit in proposal['edits']:
        entity=_entity(base,edit['kind'],edit['id'],request['feature_id'])
        if edit['op']=='set':require(entity is not None,'planning_edit_unknown');entity[edit['field']]=copy.deepcopy(edit['value'])
        else:
            if edit['kind'] in ('feature','chapter'):collection=base[edit['kind']+'s']
            elif edit['kind']=='criterion':collection=next(f for f in base['features'] if f['id']==request['feature_id'])['criteria']
            else:raise store.StoreError('planning_edit_invalid')
            if edit['op']=='add':require(entity is None,'planning_edit_conflict');collection.append(copy.deepcopy(edit['value']))
            else:require(entity is not None,'planning_edit_unknown');collection.remove(entity)
    if request['target'] in ('product','feature'):action={'kind':'product_save','document':base,'base_revision':request['product_revision'],'actor':actor}
    elif request['target']=='specification':
        action={'kind':'product_specification','change_id':request['change_id'],'content':base,'mappings':[],'actor':actor}
    elif request['target']=='plan':
        from cc_product_documents import observe_files
        action={'kind':'product_plan','change_id':request['change_id'],'content':base,'files':observe_files(safe.path,base['files']),'actor':actor}
    elif request['target'] in DETAIL_TARGETS:
        stage='design' if request['target']=='specification_v2' else 'plan'
        require(set(base)==({'content','details','mappings'} if stage=='design' else {'content','details','files'}),'planning_proposal_invalid')
        action={'kind':'product_specification_v2' if stage=='design' else 'product_plan_v2','change_id':request['change_id'],'actor':actor,**base}
    else:raise store.StoreError('planning_proposal_unavailable')
    cc_product.validate_action(action)
    token={'schema_version':1,'session_id':session_id,'session_revision':current['revision'],'request_id':request_id,'proposal_digest':inputs.digest(proposal),'configuration_revision':request['configuration']['configuration_revision'],'action_digest':inputs.digest(action)}
    if request['target'] in DETAIL_TARGETS:token.update(schema_version=2,detail_binding_digest=inputs.digest(request['detail_contract']))
    action['planning_review']=token
    return {'action':action,'revision':revision,'planning_review':token,'blockers':[]}

def guard_action(safe,action):
    validate_review_token(action)
    token=action['planning_review']
    current=read_safe(safe,token['session_id']);require(current['revision']==token['session_revision'],'planning_session_changed');row=next((r for r in current['session']['requests'] if r['request']['id']==token['request_id']),None);require(row is not None,'planning_request_unknown')
    reviewed=review_safe(safe,token['session_id'],token['request_id'],row['request']['configuration'],action['actor']);require(not reviewed['blockers'],'planning_review_stale');require(reviewed['planning_review']==token and reviewed['action']==action,'planning_review_action_changed')

def dispatch(root,payload):
    require(type(payload) is dict,'planning_schema_invalid');operation=payload.get('operation');fields={
      'planning_read':{'session_id'},'planning_create':{'session_id','operation_id','actor','task_id'},'planning_append':{'session_id','expected_revision','operation_id','actor','role','text','questions','choices','reviewer_seconds'},'planning_preview':{'session_id','options'},'planning_prepare':{'session_id','expected_revision','operation_id','actor','options','preview_id'},'planning_reserve':{'session_id','expected_revision','operation_id','actor','request_id','current_binding'},'planning_finish':{'session_id','expected_revision','operation_id','actor','request_id','current_binding','result'},'planning_review':{'session_id','request_id','current_binding','actor'}}
    require(operation in fields,'planning_operation_invalid');exact(payload,fields[operation]|{'operation'})
    if operation not in ('planning_read','planning_preview','planning_review'):
        sessions._id(payload['session_id']);sessions._revision(payload['operation_id']);require(payload['operation_id']!='absent','session_operation_invalid');text(payload['actor'],160)
        if operation=='planning_create':sessions._id(payload['task_id'])
    from cc_development_lib import storage
    with inputs.SafeRoot(root) as safe:
        if operation=='planning_read':
            storage.load(safe)
            if payload['session_id']:return read_safe(safe,payload['session_id'])
            heads=sessions.collect(safe)['heads'];return {'sessions':[{'id':id,'revision':h['object'],'schema_version':h['schemaVersion'],'task_id':read_safe(safe,id)['session'].get('task_id','')} for id,h in heads.items()]}
        if operation in ('planning_preview','planning_review'):
            storage.load(safe)
            if operation=='planning_preview':return request_preview(safe,payload['session_id'],payload['options'])
            return review_safe(safe,payload['session_id'],payload['request_id'],payload['current_binding'],payload['actor'])
        with storage.transaction(safe,payload['operation_id']):
            state,revision=storage.load(safe,locked=True)
            sessions._id(payload['session_id']);sessions._revision(payload['operation_id']);require(payload['operation_id']!='absent','session_operation_invalid');text(payload['actor'],160)
            if operation=='planning_create':sessions._id(payload['task_id'])
            head=_head(safe,payload['session_id'])
            if head is not None and head['schemaVersion']==2:
                items,_=history(safe,head)
                previous=next((i for i in items if i['node']['operation']['id']==payload['operation_id']),None)
                if previous is not None:
                    require(previous['node']['operation']['publicDigest']==inputs.digest(payload) and previous['node']['actor']==payload['actor'],'session_idempotency_conflict');return read_safe(safe,payload['session_id'])
            if operation=='planning_create':
                if revision=='absent':storage.save(safe,state,revision)
                require(len(sessions.collect(safe)['heads'])<sessions.MAX_SESSIONS or _head(safe,payload['session_id']) is not None,'session_count_limit');action={'kind':'create','task_id':payload['task_id']};expected='absent'
            else:
                current=read_safe(safe,payload['session_id']);require(current['session'] is not None,'planning_session_unknown');expected=payload['expected_revision']
                if operation=='planning_append':action={k:payload[k] for k in ('role','text','questions','choices','reviewer_seconds')}|{'kind':'append'}
                elif operation=='planning_prepare':
                    preview=request_preview(safe,payload['session_id'],payload['options']);require(preview['preview_id']==payload['preview_id'],'planning_preview_stale');action={'kind':'prepare','request':preview['request']}
                else:
                    row=next((r for r in current['session']['requests'] if r['request']['id']==payload['request_id']),None);require(row is not None,'planning_request_unknown');action={'kind':'reserve' if operation=='planning_reserve' else 'finish','request_id':payload['request_id'],'current_binding':payload['current_binding']}
                    if operation=='planning_reserve':require(not binding_reasons(safe,row['request'],payload['current_binding']),'planning_request_stale')
                    else:
                        result=payload['result'];validate_result(result,row['request']);reasons=binding_reasons(safe,row['request'],payload['current_binding']);proposal=None
                        # Preserve stale response/usage without letting a newly
                        # supplied larger configuration spend the pinned credit.
                        action['current_binding']=row['request']['configuration']
                        if result['incomplete']:reasons.append('planning_response_incomplete')
                        if result['outcome']=='succeeded' and row['request']['target']!='dialogue' and not result['incomplete']:
                            try:proposal=parse_proposal(result['text'],row['request'])
                            except (ValueError,TypeError,KeyError,RecursionError,inputs.EvidenceError):proposal=None;reasons.append('planning_proposal_invalid')
                        if proposal is not None and len(set(current['session']['questions']+proposal['questions']))>64:reasons.append('planning_question_limit')
                        action.update(result=result,blockers=sorted(set(reasons)),proposal=None)
            return _write(safe,payload['session_id'],expected,payload['operation_id'],payload['actor'],action,inputs.digest(payload))


def validate_review_token(action):
    token=action['planning_review'];detailed=action['kind'] in ('product_specification_v2','product_plan_v2')
    exact(token,TOKEN_FIELDS|({'detail_binding_digest'} if detailed else set()),'planning_review_invalid')
    require(type(token['schema_version']) is int and token['schema_version']==(2 if detailed else 1),'planning_review_invalid')
    sessions._id(token['session_id']);sessions._id(token['request_id'])
    for field in ('session_revision','proposal_digest','configuration_revision','action_digest') + (('detail_binding_digest',) if detailed else ()):
        sessions._revision(token[field]);require(token[field]!='absent','planning_review_invalid')
    require(token['action_digest']==inputs.digest({k:v for k,v in action.items() if k!='planning_review'}),'planning_review_action_changed')
