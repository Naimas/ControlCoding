"""Real revision transactions and negative controls for scope and dependency gates."""
import copy
import pytest
from test_cc_development_core import act,logical_state,corrupt_logical_state
import cc_development_lib as core
import cc_product as product
from cc_development_lib.method import digest
from test_cc_product_core import document,saved,detailed_action

def accept(root,doc):
    view=saved(root,doc)
    return act(root,{'kind':'product_decide','product_revision':view['product']['current_revision'],'decision':'accepted','reason':'Reviewed every condition','actor':'Owner'})

def two_features():
    doc=document();f=copy.deepcopy(doc['features'][0]);f.update(id='reports',title='Reports',problem='Members need reports',outcome='Members read accurate reports',criteria=[{'id':'reports-success','text':'Report totals are correct','required':True}]);doc['features'].append(f)
    return doc

def select_action(root,doc=None,shared=False):
    doc=doc or two_features();accept(root,doc)
    if not core.read(root)['enabled']:act(root,{'kind':'activate','profile':'routine'})
    features=[f['id'] for f in doc['features']]
    changes=[{'change_id':'C1','title':'Accounts','owner':'Builder','risk':'routine','feature_ids':[features[0]]},{'change_id':'C2','title':'Reports','owner':'Builder','risk':'routine','feature_ids':[features[-1]]}]
    criteria=[{'criterion_id':c['id'],'owner_change_id':'C1' if f['id']==features[0] else 'C2','contributor_change_ids':['C1'] if f['id']==features[0] else ['C2'],'integration_check':None} for f in doc['features'] for c in f['criteria']]
    if shared:
        changes[1]['feature_ids']=[features[0]]
        for own in criteria:own.update(contributor_change_ids=['C1','C2'],integration_check={'kind':'verification','id':'integration'})
    return {'kind':'product_select','product_revision':core.read(root)['product']['current_revision'],'increment_id':'I1','title':'Combined increment','features':features,'changes':changes,'criteria':criteria,'dependencies':[],'actor':'Owner'}

def selected(root,doc=None,shared=False):return act(root,select_action(root,doc,shared))

def decide(root,change,stage):return act(root,{'kind':'decide','change_id':change,'stage':stage,'decision':'accepted','actor':'Reviewer','reason':'Reviewed exact current conditions'})

def specification(root,change):
    decide(root,change,'intent');draft=product.template(root,change,'design')
    for mapping in draft['mappings']:mapping['reason']='Same selected condition and behavior.'
    act(root,detailed_action(root,{'kind':'product_specification','change_id':change,'content':draft['content'],'mappings':draft['mappings'],'actor':'Author'}))
    return decide(root,change,'design')

def scope_action(root,decision='adopt'):
    view=core.read(root)
    return {'kind':'product_scope','increment_id':'I1','selection_revision':view['product']['increments'][0]['selection_revision'],'product_revision':view['product']['current_revision'],'decision':decision,'reason':'Reviewed impact and retained conditions','actor':'Owner'}

def test_atomic_multifeature_handoff_has_explicit_unapproved_ownership(tmp_path):
    action=select_action(tmp_path);before=core.read(tmp_path);p=core.prepare(tmp_path,action,before['revision']);assert not p['blockers']
    result=core.commit(tmp_path,action,before['revision'],p['approval_id'])['view']
    assert len(result['changes'])==2 and all(not c['decisions'] for c in result['changes'])
    assert result['product']['increments'][0]['ownership']==action['criteria']
    assert core.commit(tmp_path,action,before['revision'],p['approval_id'])['receipt']['idempotent']

def test_stale_second_scope_edit_cannot_mix_immutable_revisions(tmp_path):
    selected(tmp_path);doc=two_features();doc['features'][0]['outcome']='Members create secure accounts';accept(tmp_path,doc)
    before=core.read(tmp_path);a=scope_action(tmp_path);competing={**a,'reason':'Second concurrent review'};p=core.prepare(tmp_path,competing,before['revision']);assert not p['blockers']
    act(tmp_path,a)
    with pytest.raises(ValueError,match='revision_conflict'):core.commit(tmp_path,competing,before['revision'],p['approval_id'])
    state=logical_state(tmp_path);assert len(state['product']['evolution']['selections'])==2

def test_adoption_changes_only_impacted_feature_and_preserves_sibling_decision_bytes(tmp_path):
    selected(tmp_path);specification(tmp_path,'C1');before=specification(tmp_path,'C2');old=copy.deepcopy(before['changes'][1]['decisions'])
    doc=two_features();doc['features'][0]['criteria'][0]['text']='Valid and verified details create exactly one account';accept(tmp_path,doc)
    view=act(tmp_path,scope_action(tmp_path));c1,c2=view['changes']
    assert c2['decisions']==old and c2['gates']['design']['outcome']=='allowed' and c2['gates']['design']['accepted']
    assert c1['gates']['design']['outcome']!='allowed' and not c1['gates']['intent']['accepted']
    assert len(c1['artifacts']['intent'])==2 and len(c2['artifacts']['intent'])==1
    assert core.read(tmp_path)['changes'][1]['decisions']==old

def test_distinct_criteria_same_feature_preserve_unchanged_contributor(tmp_path):
    doc=document();doc['features'][0]['criteria'].append({'id':'account-delete','text':'Member can delete their account','required':True})
    action=select_action(tmp_path,doc);action['criteria'][1].update(owner_change_id='C2',contributor_change_ids=['C2']);act(tmp_path,action)
    specification(tmp_path,'C1');old=specification(tmp_path,'C2')['changes'][1]['decisions']
    doc['features'][0]['criteria'][0]['text']='Valid verified details create one account';accept(tmp_path,doc)
    view=act(tmp_path,scope_action(tmp_path));assert view['changes'][1]['decisions']==old
    assert view['changes'][1]['gates']['design']['accepted'] and view['changes'][1]['gates']['design']['outcome']=='allowed'

@pytest.mark.parametrize('change',('constraint','required','risk'))
def test_stricter_policy_cannot_keep_original_scope(tmp_path,change):
    selected(tmp_path);doc=two_features()
    if change=='constraint':doc['constraints'].append('Encrypt all private records')
    elif change=='required':doc['features'][0]['criteria'][0]['text']='Accounts require identity verification'
    else:doc['features'][0]['risks'].append('Security policy requires additional review')
    accept(tmp_path,doc);before=core.read(tmp_path);p=core.prepare(tmp_path,scope_action(tmp_path,'keep'),before['revision'])
    assert p['blockers']==['product_stricter_policy_cannot_keep']

def test_keep_records_considered_revision_and_next_relevant_revision_requires_new_review(tmp_path):
    selected(tmp_path);doc=two_features();doc['features'][0]['outcome']='Members create an account with clear feedback';accept(tmp_path,doc)
    view=act(tmp_path,scope_action(tmp_path,'keep'));assert not view['product']['evolution']['impacts'][0]['required']
    doc['features'][0]['outcome']='Members create and inspect their account';accept(tmp_path,doc)
    assert core.read(tmp_path)['product']['evolution']['impacts'][0]['required']

def test_defer_explicit_resume_and_cancel_is_terminal_history_retained(tmp_path):
    selected(tmp_path);act(tmp_path,scope_action(tmp_path,'defer'))
    assert 'product_scope_defer' in core.read(tmp_path)['changes'][0]['gates']['intent']['reasons']
    act(tmp_path,scope_action(tmp_path,'keep'));decide(tmp_path,'C1','intent')
    act(tmp_path,scope_action(tmp_path,'cancel'));p=core.prepare(tmp_path,scope_action(tmp_path,'adopt'),core.read(tmp_path)['revision']);assert p['blockers']==['product_scope_cancelled']
    assert core.read(tmp_path)['changes'][0]['decisions']

def test_added_criterion_requires_explicit_reviewed_replacement_and_no_forged_pass(tmp_path):
    selected(tmp_path);doc=two_features();doc['features'][0]['criteria'].append({'id':'confirm','text':'Member sees confirmation','required':True});accept(tmp_path,doc)
    before=core.read(tmp_path);a=scope_action(tmp_path);assert core.prepare(tmp_path,a,before['revision'])['blockers']==['product_scope_replacement_required']
    current=before['product']['increments'][0];a['replacement']={'features':[f['feature_id'] for f in current['features']],'changes':current['changes'],'criteria':current['ownership']+[{'criterion_id':'confirm','owner_change_id':'C1','contributor_change_ids':['C1'],'integration_check':None}],'dependencies':current['dependencies']}
    view=act(tmp_path,a);assert len(view['product']['increments'][0]['criteria'])==3
    fake={'kind':'product_integration','increment_id':'I1','selection_revision':view['product']['increments'][0]['selection_revision'],'criterion_id':'confirm','candidates':[{'change_id':'C1','candidate_identity':'a'*64}],'actor':'Owner','passed':True}
    with pytest.raises(ValueError,match='invalid_action_fields'):core.prepare(tmp_path,fake,view['revision'])

def dependency(action,doc,consumer='C2',target='C1',feature='signup',result='specification_accepted',gate='plan'):
    f=next(f for f in doc['features'] if f['id']==feature)
    return {'consumer_change_id':consumer,'target_change_id':target,'target_feature_id':feature,'target_feature_revision':digest(f),'target_selection_revision':'same_increment','required_result':result,'gate':gate}

def test_exact_internal_dependency_unlocks_only_after_current_predecessor_specification(tmp_path):
    doc=two_features();a=select_action(tmp_path,doc);a['dependencies']=[dependency(a,doc)];act(tmp_path,a)
    view=core.read(tmp_path);assert view['product']['evolution']['dependency_status'][0]['state']=='blocked'
    specification(tmp_path,'C1');assert core.read(tmp_path)['product']['evolution']['dependency_status'][0]['state']=='allowed'
    content=product.template(tmp_path,'C1','design')['content'];content['criteria'][0]['text']='New behavior requiring review'
    act(tmp_path,{'kind':'artifact','change_id':'C1','stage':'design','content':content,'actor':'Author'})
    assert core.read(tmp_path)['product']['evolution']['dependency_status'][0]['state']=='blocked'

@pytest.mark.parametrize('negative',('cycle','missing','older','self'))
def test_dependency_graph_rejects_cycles_missing_and_wrong_exact_revision(tmp_path,negative):
    doc=two_features();a=select_action(tmp_path,doc);a['dependencies']=[dependency(a,doc)]
    if negative=='cycle':a['dependencies'].append(dependency(a,doc,'C1','C2','reports'))
    elif negative=='missing':a['dependencies'][0]['target_change_id']='Missing'
    elif negative=='older':a['dependencies'][0]['target_feature_revision']='f'*64
    else:a['dependencies'][0]['target_change_id']='C2';a['dependencies'][0]['target_feature_id']='reports';a['dependencies'][0]['target_feature_revision']=digest(doc['features'][1])
    before=core.read(tmp_path);assert core.prepare(tmp_path,a,before['revision'])['blockers'];assert not core.read(tmp_path)['changes']

def test_dependency_adoption_preserves_old_edges_as_readable_blocked_history(tmp_path):
    doc=two_features();a=select_action(tmp_path,doc);a['dependencies']=[dependency(a,doc)];act(tmp_path,a)
    specification(tmp_path,'C1');doc['features'][0]['outcome']='Changed accounts behavior';accept(tmp_path,doc)
    # Explicit replacement updates the internal feature revision; an external
    # selection would keep its old pin and block until separately reviewed.
    a=scope_action(tmp_path);selection=core.read(tmp_path)['product']['increments'][0]
    a['replacement']={'features':['signup','reports'],'changes':selection['changes'],'criteria':selection['ownership'],'dependencies':[dependency(a,doc)]}
    act(tmp_path,a);assert core.read(tmp_path)['product']['evolution']['dependency_status'][0]['state']=='blocked'

def test_unknown_declared_feature_dependency_cannot_be_satisfied_by_unrelated_edge(tmp_path):
    doc=two_features();doc['features'][1]['dependencies']=[{'feature_id':'Missing'}];a=select_action(tmp_path,doc);a['dependencies']=[dependency(a,doc)]
    assert core.prepare(tmp_path,a,core.read(tmp_path)['revision'])['blockers']==['product_feature_dependencies_unreviewed']

def test_shared_criterion_requires_current_final_proof_and_rejects_gate_deadlock(tmp_path):
    doc=document();a=select_action(tmp_path,doc,shared=True);act(tmp_path,a)
    view=core.read(tmp_path);assert view['product']['evolution']['integration_status'][0]['state']=='blocked'
    assert 'product_final_integration_proof_required' in view['changes'][0]['gates']['review_release']['reasons']
    assert 'product_final_integration_proof_required' not in view['changes'][0]['gates']['verify']['reasons']
    other=tmp_path/'other';other.mkdir();a=select_action(other,doc,shared=True);a['dependencies']=[dependency(a,doc,'C2','C1','signup','released','verify')]
    assert core.prepare(other,a,core.read(other)['revision'])['blockers']==['product_dependency_integration_deadlock']

def test_retirement_and_succession_preserve_original_selection_history(tmp_path):
    before=selected(tmp_path);act(tmp_path,{'kind':'product_retire','feature_id':'signup','product_revision':before['product']['current_revision'],'successor_feature_id':'reports','reason':'Reports supersede old workflow','actor':'Owner'})
    state=logical_state(tmp_path);assert state['product']['evolution']['selections'][0]['selection_revision']==before['product']['increments'][0]['selection_revision']
    assert core.read(tmp_path)['product']['evolution']['retirements'][0]['successor_feature_id']=='reports'

def test_resealed_ownership_mutation_is_not_authority(tmp_path):
    selected(tmp_path);state=logical_state(tmp_path);state['product']['evolution']['selections'][0]['ownership'][0]['owner_change_id']='C2';corrupt_logical_state(tmp_path,state)
    with pytest.raises(ValueError):core.read(tmp_path)

def test_legacy_handoff_cannot_bypass_new_selection_or_retirement(tmp_path):
    view=selected(tmp_path)
    action={'kind':'product_increment','product_revision':view['product']['current_revision'],'feature_id':'signup','increment_id':'OldRoute','change_id':'OldRouteChild','title':'Duplicate','owner':'Owner','risk':'routine','actor':'Owner'}
    assert core.prepare(tmp_path,action,view['revision'])['blockers']==['product_increment_scope_exists']
    other=tmp_path/'retired';other.mkdir();view=accept(other,document());act(other,{'kind':'activate','profile':'routine'})
    view=act(other,{'kind':'product_retire','feature_id':'signup','product_revision':view['product']['current_revision'],'successor_feature_id':None,'reason':'Retire feature before implementation','actor':'Owner'})
    action['product_revision']=view['product']['current_revision'];assert core.prepare(other,action,view['revision'])['blockers']==['product_feature_retired']

def test_external_target_adoption_succeeds_old_exact_dependency_remains_readable_and_blocked(tmp_path):
    doc=two_features();a=select_action(tmp_path,doc);a.update(features=['signup'],changes=[a['changes'][0]],criteria=[a['criteria'][0]])
    view=act(tmp_path,a);first=view['product']['increments'][0]
    consumer={'kind':'product_select','product_revision':view['product']['current_revision'],'increment_id':'I2','title':'Reports','features':['reports'],'changes':[{'change_id':'C2','title':'Reports','owner':'Builder','risk':'routine','feature_ids':['reports']}],
              'criteria':[{'criterion_id':'reports-success','owner_change_id':'C2','contributor_change_ids':['C2'],'integration_check':None}],
              'dependencies':[{'consumer_change_id':'C2','target_change_id':'C1','target_feature_id':'signup','target_feature_revision':digest(doc['features'][0]),'target_selection_revision':first['selection_revision'],'required_result':'specification_accepted','gate':'plan'}],'actor':'Owner'}
    act(tmp_path,consumer);specification(tmp_path,'C1');assert core.read(tmp_path)['product']['evolution']['dependency_status'][0]['state']=='allowed'
    doc['features'][0]['outcome']='Create and inspect accounts';accept(tmp_path,doc);act(tmp_path,scope_action(tmp_path))
    view=core.read(tmp_path);status=view['product']['evolution']['dependency_status'][0]
    assert status['state']=='blocked' and 'product_dependency_target_stale' in status['reasons']
    assert len(logical_state(tmp_path)['product']['evolution']['selections'])==3

def test_retirement_can_name_retained_feature_after_removal_and_rejects_missing_id(tmp_path):
    selected(tmp_path);doc=two_features();doc['features']=doc['features'][1:];doc['chapters'][0]['feature_ids']=[];view=accept(tmp_path,doc)
    action={'kind':'product_retire','feature_id':'Missing','product_revision':view['product']['current_revision'],'successor_feature_id':'reports','reason':'Retire removed scope','actor':'Owner'}
    assert core.prepare(tmp_path,action,view['revision'])['blockers']==['product_feature_not_found']
    action['feature_id']='signup';view=act(tmp_path,action)
    assert view['product']['evolution']['retirements'][0]['feature_id']=='signup'
    assert len(logical_state(tmp_path)['product']['evolution']['selections'])==1

def close_product_change(root,change_id):
    from test_cc_product_evolution_acceptance import plan_change,build_change,receipts,verify_change,stage
    plan_change(root,change_id,'accounts.py');candidate=build_change(root,change_id,'accounts.py');receipts(root);verify_change(root,change_id)
    act(root,{'kind':'review','change_id':change_id,'findings':[],'actor':'Reviewer','reason':'Reviewed fixture behavior'})
    stage(root,change_id,'review_release',{'candidate_identity':candidate,'review_findings':[],'finding_resolutions':[],'required_approvals':[],'environment':'local','release_artifact_identity':candidate,'rollback_readiness':'Retain previous source','observation_provenance':'self_reported'})
    decide(root,change_id,'review_release')
    act(root,{'kind':'release','change_id':change_id,'environment':'local','artifact_digest':candidate,'status':'self_reported','actor':'Reviewer','reason':'Manual local record'})
    act(root,{'kind':'observation','change_id':change_id,'signals':[{'id':'S1','description':'Fixture outcome observed'}],'actor':'Reviewer'})
    stage(root,change_id,'maintain',{'deployed_artifact_identity':candidate,'observation_scope':'Disposable fixture','observation_window':'One local run','signals':['S1'],'findings':[],'dispositions':[],'followup_owners':['Owner']})
    decide(root,change_id,'maintain')
    return act(root,{'kind':'close','change_id':change_id,'actor':'Owner','reason':'Observed local outcome reviewed'})

def test_repeated_linked_requests_use_exact_predecessor_keep_closed_history(tmp_path):
    from test_cc_product_evolution_acceptance import combined_contracts
    combined_contracts(tmp_path);a=select_action(tmp_path,document());a['changes']=a['changes'][:1];act(tmp_path,a)
    close_product_change(tmp_path,'C1');first=core.read(tmp_path)['product']['increments'][0];original=copy.deepcopy(core.read(tmp_path)['changes'][0])
    second={**a,'increment_id':'I2','title':'Follow-up accounts','predecessor_selection_revision':first['selection_revision'],'changes':[{'change_id':'C2','title':'Follow-up accounts','owner':'Builder','risk':'routine','feature_ids':['signup']}],
            'criteria':[{'criterion_id':'signup-success','owner_change_id':'C2','contributor_change_ids':['C2'],'integration_check':None}]}
    act(tmp_path,second);close_product_change(tmp_path,'C2');previous=core.read(tmp_path)['product']['increments'][1]
    third={**second,'increment_id':'I3','predecessor_selection_revision':previous['selection_revision'],'changes':[{'change_id':'C3','title':'Third accounts request','owner':'Builder','risk':'routine','feature_ids':['signup']}],
           'criteria':[{'criterion_id':'signup-success','owner_change_id':'C3','contributor_change_ids':['C3'],'integration_check':None}]}
    wrong={**third,'predecessor_selection_revision':'f'*64};view=core.read(tmp_path)
    assert core.prepare(tmp_path,wrong,view['revision'])['blockers']==['product_predecessor_selection_missing']
    stale={**third,'predecessor_selection_revision':first['selection_revision']};assert core.prepare(tmp_path,stale,view['revision'])['blockers']==['product_increment_scope_exists']
    view=act(tmp_path,third);assert [c['closed'] for c in view['changes']]==[True,True,False]
    assert view['changes'][0]['decisions']==original['decisions'] and view['changes'][0]['artifacts']==original['artifacts']
    assert view['product']['evolution']['selections'][-1]['previous']==previous['selection_revision']

def test_incident_increment_id_collision_cannot_replace_existing_selection(tmp_path):
    from cc_product_evolution import apply as evolution_apply
    from cc_evidence_inputs import SafeRoot
    a=select_action(tmp_path);a['increment_id']='incident-Emergency';act(tmp_path,a)
    act(tmp_path,{'kind':'create','change_id':'Emergency','title':'Emergency repair','owner':'Owner','risk':'routine'})
    from test_cc_development_core import intent
    act(tmp_path,{'kind':'artifact','change_id':'Emergency','stage':'intent','content':intent('Incident repair'),'actor':'Author'})
    state=logical_state(tmp_path);public_before=core.read(tmp_path)
    # This negative vector supplies only the typed origin to reach the collision
    # branch; it is never persisted or asserted to be an observed incident.
    state['changes'][-1]['origin']={'kind':'incident_followup','change_id':'parent','incident_id':'a'*64,'incident_revision':'b'*64}
    before=copy.deepcopy(state)
    action={'kind':'product_link_incident','change_id':'Emergency','product_revision':public_before['product']['current_revision'],'feature_id':'signup','reason':'Review emergency product link','actor':'Owner'}
    with SafeRoot(tmp_path) as safe:
        with pytest.raises(ValueError,match='product_increment_scope_exists'):evolution_apply(safe,state,action,'negative')
    assert state==before and core.read(tmp_path)==public_before

def test_supplied_old_handoff_after_adoption_cannot_borrow_old_plan_approval(tmp_path):
    from test_cc_product_evolution_acceptance import combined_contracts,plan_change
    from cc_development_adapters import manual_handoff
    from cc_evidence_inputs import EvidenceError
    combined_contracts(tmp_path);a=select_action(tmp_path,document());a['changes']=a['changes'][:1];act(tmp_path,a);plan_change(tmp_path,'C1','accounts.py')
    old=copy.deepcopy(core.read(tmp_path)['changes'][0]);assert manual_handoff(tmp_path,old)['planDigest']==old['artifacts']['plan'][-1]['digest']
    doc=document();doc['features'][0]['outcome']='Members create accounts with reviewed feedback';accept(tmp_path,doc);act(tmp_path,scope_action(tmp_path))
    with pytest.raises(ValueError,match='product_execution_upstream_stale|product_gate_blocked:planning_detail_stale'):manual_handoff(tmp_path,old)
    current=core.read(tmp_path)['changes'][0]
    with pytest.raises(ValueError,match='plan_approval_missing|product_gate_blocked:planning_detail_stale'):manual_handoff(tmp_path,current)
    assert not current['gates']['plan']['accepted'] and current['decisions']==old['decisions']
