"""Actual manual planning decisions, atomic scope and immutable recovery."""
import copy
import pytest
from test_cc_development_core import act, decide, logical_state, corrupt_logical_state
import cc_development_lib as core
import cc_product as product
import cc_development_history as history
import cc_development_bundle as bundle
import cc_development_store as store
from cc_product_documents import observe_files,observe_source
from cc_evidence_inputs import SafeRoot

def document():
    return {'id':'app','title':'Accounts app','language':'English','goals':['Let people manage accounts'],'users':['Member'],'constraints':['Keep private data private'],'non_goals':['Billing'],'questions':[],
            'chapters':[{'id':'accounts','title':'Accounts','content':'Member accounts','feature_ids':['signup']}],
            'features':[{'id':'signup','title':'Sign up','problem':'Members need an account','outcome':'Members can create an account','users':['Member'],'priority':'normal','criteria':[{'id':'signup-success','text':'Valid details create exactly one account','required':True}],'dependencies':[],'risks':[],'questions':[],'source_references':[]}], 'sources':[],'notes':''}

def saved(root,doc=None):
    doc=doc or document(); before=core.read(root)
    return act(root,{'kind':'product_save','document':doc,'base_revision':before['product']['current_revision'],'actor':'Author'})

def accepted(root):
    view=saved(root)
    return act(root,{'kind':'product_decide','product_revision':view['product']['current_revision'],'decision':'accepted','actor':'Owner','reason':'Scope reviewed'})

def handed(root):
    accepted(root);act(root,{'kind':'activate','profile':'routine'})
    return act(root,{'kind':'product_increment','product_revision':core.read(root)['product']['current_revision'],'feature_id':'signup','increment_id':'I1','change_id':'C1','title':'First accounts increment','owner':'Owner','risk':'routine','actor':'Author'})

def detailed_action(root,action):
    """Author complete reviewed detail for existing fresh fixture payloads.

    This creates real v2 drafts; it never supplies gate success or approvals.
    Deliberate malformed/incomplete base content remains malformed/incomplete.
    """
    from cc_product_planning import empty_details
    action=copy.deepcopy(action);stage='design' if action['kind']=='product_specification' else 'plan'
    action['kind'] += '_v2';details=empty_details(stage);view=core.read(root)
    details['sources']=copy.deepcopy((view['product']['document'] or {}).get('sources',[]))
    if stage=='design':
        criteria=action['content']['criteria'];product_ids=list(dict.fromkeys(m['product_criterion_id'] for m in action['mappings']))
    else:
        change=next(c for c in view['changes'] if c['id']==action['change_id'])
        criteria=change['artifacts']['design'][-1]['content']['criteria']
        review=next(d for d in reversed(view['product']['drafts']) if d['change_id']==action['change_id'] and d['stage']=='design')
        product_ids=list(dict.fromkeys(m['product_criterion_id'] for m in review['mappings']))
    provenance={'kind':'product' if product_ids else 'explicit','source_refs':[],'product_criterion_ids':product_ids,
                'rationale':'The fixture preserves the reviewed selected conditions explicitly.' if product_ids
                            else 'Incomplete author draft: requirement wording is retained pending explicit Product condition mappings.'}
    if stage=='design':
        details['requirement_details']=[{'requirement_id':r['id'],'approach':r['text'],'provenance':copy.deepcopy(provenance)} for r in action['content']['requirements']]
        details['condition_mappings']=[{'id':'condition-'+str(i),'source_refs':[],'product_criterion_id':m['product_criterion_id'],
            'requirement_ids':[next(c['requirement_id'] for c in criteria if c['id']==m['specification_criterion_id'])],
            'criterion_ids':[m['specification_criterion_id']],'decision':'equivalent','reason':m['reason']} for i,m in enumerate(action['mappings'])]
    else:
        obligations=[]
        for c in criteria:
            obligations += [{'criterion_id':c['id'],'kind':check['kind'],'check_id':check['id'],'question':''} for check in c['checks']]
            if c['proof'] in ('human','both'):obligations.append({'criterion_id':c['id'],'kind':'human','check_id':'','question':'Does the observed result satisfy: '+c['text']+'?'})
        details['task_details']=[{'task_id':t['id'],'approach':t['title'],'deliverable':'Deliver the selected behavior and retain the inspected proof obligations.',
            'files':action['content']['files'],'criterion_ids':[c['id'] for c in criteria],'obligations':copy.deepcopy(obligations),
            'supporting_rationale':'','supports_task_ids':[],'file_free_rationale':'Analysis of the selected behavior.' if not action['content']['files'] else '',
            'provenance':copy.deepcopy(provenance)} for t in action['content']['tasks']]
        details['rollback']={'assessment':action['content']['rollback_assessment'],'steps':['Restore the reviewed original source and preserve retained records.'],'unavailable_reason':''}
    action['details']=details
    return action

def specification(root):
    handed(root);decide(root,'intent')
    draft=product.template(root,'C1','design')
    for row in draft['mappings']:row['reason']='Compared the original condition with the required specification behavior.'
    return act(root,detailed_action(root,{'kind':'product_specification','change_id':'C1','content':draft['content'],'mappings':draft['mappings'],'actor':'Author'}))

def test_read_save_restart_disabled_and_no_memory(tmp_path):
    assert core.read(tmp_path)['product']['document'] is None and not list(tmp_path.iterdir())
    saved(tmp_path)
    resumed=core.read(tmp_path)
    assert resumed['product']['document']==document() and not resumed['enabled'] and not resumed['changes']
    assert not (tmp_path/'memory').exists()

def test_reverted_content_cannot_revive_old_acceptance(tmp_path):
    old=accepted(tmp_path)['product']['accepted_revision']; edited=document();edited['title']='Changed app'
    saved(tmp_path,edited);restored=saved(tmp_path,document())
    assert restored['product']['current_revision']!=old and restored['product']['accepted_revision']=='absent'

def test_handoff_is_atomic_unapproved_and_exact_replay(tmp_path):
    accepted(tmp_path)
    action={'kind':'product_increment','product_revision':core.read(tmp_path)['product']['current_revision'],'feature_id':'signup','increment_id':'I1','change_id':'C1','title':'First','owner':'Owner','risk':'routine','actor':'Author'}
    before=core.read(tmp_path);preview=core.prepare(tmp_path,action,before['revision'])
    assert preview['blockers']==['method_not_active'] and not before['changes']
    act(tmp_path,{'kind':'activate','profile':'routine'});before=core.read(tmp_path);preview=core.prepare(tmp_path,action,before['revision'])
    result=core.commit(tmp_path,action,before['revision'],preview['approval_id'])
    repeated=core.commit(tmp_path,action,before['revision'],preview['approval_id'])
    assert repeated['receipt']['idempotent'] and repeated['view']['revision']==result['view']['revision']
    assert len(repeated['view']['changes'])==len(repeated['view']['product']['increments'])==1
    assert repeated['view']['changes'][0]['decisions']==[]

def test_semantic_mapping_required_and_bound_to_actual_spec_revision(tmp_path):
    specification(tmp_path);decide(tmp_path,'design')
    change=core.read(tmp_path)['changes'][0];content=copy.deepcopy(change['artifacts']['design'][-1]['content']);content['criteria'][0]['text']='Different behavior'
    act(tmp_path,{'kind':'artifact','stage':'design','change_id':'C1','content':content,'actor':'Author'})
    assert 'product_criterion_mapping_review_required' in core.read(tmp_path)['changes'][0]['gates']['design']['reasons']

def test_required_criterion_cannot_be_weakened_by_mapping(tmp_path):
    specification(tmp_path);draft=product.template(tmp_path,'C1','design');draft['content']['criteria'][0]['required']=False;draft['mappings'][0]['reason']='Claimed equivalence'
    act(tmp_path,detailed_action(tmp_path,{'kind':'product_specification','change_id':'C1','content':draft['content'],'mappings':draft['mappings'],'actor':'Author'}))
    assert 'product_required_criterion_weakened' in core.read(tmp_path)['changes'][0]['gates']['design']['reasons']

def test_independent_addition_title_and_chapter_reorder_preserve_scope(tmp_path):
    specification(tmp_path);decide(tmp_path,'design');doc=document();doc['features'][0]['title']='Create an account'
    new=copy.deepcopy(doc['features'][0]);new.update(id='reports',title='Reports',criteria=[{'id':'report-success','text':'Members read reports','required':True}]);doc['features'].append(new)
    doc['chapters'].insert(0,{'id':'reports-chapter','title':'Reports','content':'Reports','feature_ids':['reports']})
    saved(tmp_path,doc)
    gate=core.read(tmp_path)['changes'][0]['gates']['design']
    assert gate['accepted'] and gate['outcome']=='allowed'

def test_changed_selected_behavior_or_shared_constraint_blocks(tmp_path):
    specification(tmp_path);decide(tmp_path,'design');doc=document();doc['constraints'].append('Must use two-factor authentication');saved(tmp_path,doc)
    gate=core.read(tmp_path)['changes'][0]['gates']['design']
    assert 'product_selected_scope_changed_review_unsupported' in gate['reasons']

def test_unknown_associations_resealed_state_rejected(tmp_path):
    handed(tmp_path);state=logical_state(tmp_path);state['product']['increments'][0]['change_id']='forged'
    corrupt_logical_state(tmp_path,state)
    with pytest.raises(ValueError,match='product_history_projection_mismatch'):core.read(tmp_path)

def test_dependencies_do_not_unlock_unsupported_handoff(tmp_path):
    doc=document();doc['features'][0]['dependencies']=[{'feature_id':'other'}];saved(tmp_path,doc)
    act(tmp_path,{'kind':'product_decide','product_revision':core.read(tmp_path)['product']['current_revision'],'decision':'accepted','actor':'Owner','reason':'Reviewed'})
    act(tmp_path,{'kind':'activate','profile':'routine'})
    action={'kind':'product_increment','product_revision':core.read(tmp_path)['product']['current_revision'],'feature_id':'signup','increment_id':'I1','change_id':'C1','title':'First','owner':'Owner','risk':'routine','actor':'Author'}
    assert core.prepare(tmp_path,action,core.read(tmp_path)['revision'])['blockers']==['product_dependencies_unsupported']

def test_checkpoint_cold_product_replay_and_detached_complete_backup(tmp_path):
    action={'kind':'product_save','document':document(),'base_revision':'absent','actor':'Author'}
    preview=core.prepare(tmp_path,action,'absent');core.commit(tmp_path,action,'absent',preview['approval_id'])
    checkpoint=history.checkpoint_prepare(tmp_path);history.checkpoint_commit(tmp_path,checkpoint['revision'],checkpoint['approval_id'],'Owner')
    after=core.read(tmp_path);retry=core.commit(tmp_path,action,'absent',preview['approval_id'])
    assert retry['receipt']['idempotent'] and retry['view']['revision']==after['revision']
    with SafeRoot(tmp_path) as safe:
        with core.storage.transaction(safe):archive=bundle.collect(safe)
        assert any(v['domain']=='planning' for v in archive['objects'].values())
        bundle.validate(safe,archive)
        damaged=copy.deepcopy(archive);ref=next(k for k,v in damaged['objects'].items() if v['domain']=='planning');del damaged['objects'][ref]
        damaged['integrity']=store.hashlib.sha256(store._encoded({k:v for k,v in damaged.items() if k!='integrity'})).hexdigest()
        with pytest.raises(ValueError):bundle.validate(safe,damaged)

def planned(root):
    specification(root);decide(root,'design')
    draft=product.template(root,'C1','plan')
    draft['content'].update(files=['accounts.py'],ownership_scopes=[{'owner':'Author','files':['accounts.py']}],tasks=[{'id':'implement-accounts','title':'Implement account validation and persistence','owner':'Author','depends_on':[]}],verification_strategy='An independent reviewer checks valid and invalid details, duplicate submission and persistence after restart.',rollback_assessment='Revert the accounts module and preserve stored records before restoring the previous interface.')
    files=observe_files(root,['accounts.py'])
    return act(root,detailed_action(root,{'kind':'product_plan','change_id':'C1','content':draft['content'],'files':files,'actor':'Author'}))

def test_complete_journey_plan_approval_then_existing_file_drift(tmp_path):
    (tmp_path/'accounts.py').write_text('def create_account(): pass\n')
    planned(tmp_path);decide(tmp_path,'plan')
    assert core.read(tmp_path)['changes'][0]['gates']['plan']['accepted']
    (tmp_path/'accounts.py').write_text('def create_account(): return False\n')
    assert 'product_plan_source_changed' in core.read(tmp_path)['changes'][0]['gates']['plan']['reasons']

def test_proposed_new_file_becoming_existing_blocks_plan(tmp_path):
    planned(tmp_path)
    (tmp_path/'accounts.py').write_text('unexpected competing file\n')
    assert 'product_plan_source_changed' in core.read(tmp_path)['changes'][0]['gates']['plan']['reasons']

def test_source_selected_coverage_and_drift_block_stale_acceptance(tmp_path):
    source_path=tmp_path/'brief.md';source_path.write_text('# Accounts\nCreate accounts.\n# Billing\nCharge money.\n')
    doc=document();source=observe_source(tmp_path,'brief.md');source['sections'][0].update(selected=True,disposition='mapped',targets=['signup']);doc['sources']=[source];doc['features'][0]['source_references']=[source['source_id']]
    saved(tmp_path,doc)
    action={'kind':'product_decide','product_revision':core.read(tmp_path)['product']['current_revision'],'decision':'accepted','actor':'Owner','reason':'Only Accounts selected; Billing not reviewed'}
    act(tmp_path,action)
    source_path.write_text('# Accounts\nCreate accounts with two-factor authentication.\n# Billing\nCharge money.\n')
    assert core.prepare(tmp_path,action,core.read(tmp_path)['revision'])['blockers']
    assert source_path.read_text().startswith('# Accounts\nCreate accounts with')

def test_planning_quota_failure_preserves_control_and_activation(tmp_path,monkeypatch):
    saved(tmp_path);before=(tmp_path/'.controlcoding/development.json').read_bytes()
    def full(*args,**kwargs):raise store.StoreError('planning_quota_exceeded')
    monkeypatch.setattr(store,'ensure_planning_capacity',full)
    doc=document();doc['notes']='New planning payload'
    action={'kind':'product_save','document':doc,'base_revision':core.read(tmp_path)['product']['current_revision'],'actor':'Author'}
    preview=core.prepare(tmp_path,action,core.read(tmp_path)['revision'])
    with pytest.raises(ValueError,match='planning_quota_exceeded'):core.commit(tmp_path,action,preview['revision'],preview['approval_id'])
    assert (tmp_path/'.controlcoding/development.json').read_bytes()==before
    assert act(tmp_path,{'kind':'activate','profile':'routine'})['enabled']
    checkpoint=history.checkpoint_prepare(tmp_path);history.checkpoint_commit(tmp_path,checkpoint['revision'],checkpoint['approval_id'],'Owner')
    with SafeRoot(tmp_path) as safe:
        state,_,manifest=store.read_state(safe)
        old=next(event for event in state['product']['events'] if event['action']['kind']=='product_save')
        archived=history._index_entries(state['retention']['replay_root'],lambda ref:store.get_object(safe,ref))
        receipt=next(history._receipt_value(ref,lambda ref:store.get_object(safe,ref)) for ref in archived.values() if history._receipt_value(ref,lambda ref:store.get_object(safe,ref))['action']==old['action'])
        with core.storage.transaction(safe):archive=bundle.collect(safe)
        bundle.validate(safe,archive)
    assert core.commit(tmp_path,receipt['action'],receipt['expected_revision'],receipt['approval_id'])['receipt']['idempotent']

def test_injected_manifest_publication_failure_has_no_half_child(tmp_path,monkeypatch):
    accepted(tmp_path);act(tmp_path,{'kind':'activate','profile':'routine'})
    action={'kind':'product_increment','product_revision':core.read(tmp_path)['product']['current_revision'],'feature_id':'signup','increment_id':'I1','change_id':'C1','title':'First','owner':'Owner','risk':'routine','actor':'Author'}
    before=core.read(tmp_path);preview=core.prepare(tmp_path,action,before['revision']);original=SafeRoot.write_atomic
    def failed(safe,path,*args,**kwargs):
        if str(path).endswith('development.json'):raise OSError('Injected publication failure')
        return original(safe,path,*args,**kwargs)
    monkeypatch.setattr(SafeRoot,'write_atomic',failed)
    with pytest.raises(ValueError):core.commit(tmp_path,action,before['revision'],preview['approval_id'])
    monkeypatch.setattr(SafeRoot,'write_atomic',original)
    after=core.read(tmp_path);assert after['revision']==before['revision'] and not after['changes'] and not after['product']['increments']
    assert len(core.commit(tmp_path,action,before['revision'],preview['approval_id'])['view']['changes'])==1

def test_half_filled_plan_is_durable_and_cannot_accept_old_artifact(tmp_path):
    planned(tmp_path);decide(tmp_path,'plan')
    draft=product.template(tmp_path,'C1','plan');draft['content']['verification_strategy']='Still deciding file scope'
    act(tmp_path,detailed_action(tmp_path,{'kind':'product_plan','change_id':'C1','content':draft['content'],'files':[],'actor':'Author'}))
    resumed=core.read(tmp_path);pending=next(d for d in resumed['product']['drafts'] if d['stage']=='plan')
    assert not pending['published'] and pending['content']==draft['content']
    assert 'product_plan_draft_pending' in resumed['changes'][0]['gates']['plan']['reasons']
    action={'kind':'decide','change_id':'C1','stage':'plan','decision':'accepted','actor':'Owner','reason':'Attempt stale acceptance'}
    assert core.prepare(tmp_path,action,resumed['revision'])['blockers']

def test_incomplete_semantic_review_persists_without_acceptance(tmp_path):
    specification(tmp_path);decide(tmp_path,'design');draft=product.template(tmp_path,'C1','design')
    act(tmp_path,detailed_action(tmp_path,{'kind':'product_specification','change_id':'C1','content':draft['content'],'mappings':draft['mappings'],'actor':'Author'}))
    assert 'product_semantic_review_reason_required' in core.read(tmp_path)['changes'][0]['gates']['design']['reasons']

def test_malformed_plan_is_rejected_even_with_empty_text(tmp_path):
    specification(tmp_path);draft=product.template(tmp_path,'C1','plan');draft['content']['tasks']=[{'id':'not-a-task'}]
    with pytest.raises(ValueError):core.prepare(tmp_path,{'kind':'product_plan_v2','change_id':'C1','content':draft['content'],'files':[],'details':__import__('cc_product_planning').empty_details('plan'),'actor':'Author'},core.read(tmp_path)['revision'])

def test_same_artifact_changed_review_requires_new_bound_acceptance(tmp_path):
    specification(tmp_path);decide(tmp_path,'design');draft=product.template(tmp_path,'C1','design')
    draft['mappings'][0]['reason']='A newly reviewed exact mapping rationale.'
    act(tmp_path,detailed_action(tmp_path,{'kind':'product_specification','change_id':'C1','content':draft['content'],'mappings':draft['mappings'],'actor':'Author'}))
    gate=core.read(tmp_path)['changes'][0]['gates']['design'];assert 'product_review_binding_changed' in gate['reasons']
    decide(tmp_path,'design');assert core.read(tmp_path)['changes'][0]['gates']['design']['outcome']=='allowed'
    act(tmp_path,detailed_action(tmp_path,{'kind':'product_specification','change_id':'C1','content':draft['content'],'mappings':draft['mappings'],'actor':'Author'}))
    assert core.read(tmp_path)['changes'][0]['gates']['design']['outcome']=='allowed'

def test_proposed_scope_gate_matches_committed_view(tmp_path):
    handed(tmp_path);doc=document();doc['features'][0]['outcome']='Different desired behavior'
    action={'kind':'product_save','document':doc,'base_revision':core.read(tmp_path)['product']['current_revision'],'actor':'Author'}
    before=core.read(tmp_path);preview=core.prepare(tmp_path,action,before['revision'])
    expected=preview['view']['changes'][0]['gates']['intent']['reasons']
    assert 'product_selected_scope_changed_review_unsupported' in expected
    assert core.commit(tmp_path,action,before['revision'],preview['approval_id'])['view']['changes'][0]['gates']['intent']['reasons']==expected

@pytest.mark.parametrize('attack',['inline','moved','wrong_domain'])
def test_resealed_planning_graph_domains_are_owner_defined(tmp_path,attack):
    saved(tmp_path)
    with SafeRoot(tmp_path) as safe:
        state,_,manifest=store.read_state(safe)
        if attack=='inline':manifest['state_ref']=store.put_object(safe,{'type':'value','value':state})
        else:
            top=store.get_object(safe,manifest['state_ref']);pairs=store.get_object(safe,top['parts'][0]);parts=dict(pairs)
            if attack=='moved':parts['method']=parts['product']
            else:
                payload={'type':'value','value':state['product']};child=store.put_object(safe,payload,domain='control')
                parts['product']=store.put_object(safe,{'type':'planning','ref':child})
            page=store.put_object(safe,[[k,v] for k,v in sorted(parts.items())]);manifest['state_ref']=store.put_object(safe,{'type':'dict','parts':[page]})
        safe.write_atomic('.controlcoding/development.json',manifest)
    before=(tmp_path/'.controlcoding/development.json').read_bytes()
    with pytest.raises(ValueError,match='planning_root_inline|planning_root_invalid|object_domain_invalid'):core.read(tmp_path)
    assert (tmp_path/'.controlcoding/development.json').read_bytes()==before

def test_roundtrip_source_token_revalidates_file_and_proposal_at_commit(tmp_path):
    saved(tmp_path);path=tmp_path/'edited.md';path.write_text('Reviewed edited export\n')
    doc=document();doc['notes']='Reviewed import notes';before=core.read(tmp_path)
    action={'kind':'product_save','document':doc,'base_revision':before['product']['current_revision'],'actor':'Author','source_review':{'path':'edited.md','sha256':store.hashlib.sha256(path.read_bytes()).hexdigest(),'base_revision':before['product']['current_revision'],'proposal_digest':product.digest(doc)}}
    preview=core.prepare(tmp_path,action,before['revision']);assert not preview['blockers']
    path.write_text('Competing file edit\n')
    with pytest.raises(ValueError,match='approval_conflict|product_source_review_stale'):core.commit(tmp_path,action,before['revision'],preview['approval_id'])
    assert core.read(tmp_path)['product']['document']['notes']==''
    action['document']['notes']='Unreviewed proposal replacement'
    with pytest.raises(ValueError,match='product_source_review_stale'):core.prepare(tmp_path,action,before['revision'])

def test_shared_immutable_empty_leaves_decode_as_independent_entities(tmp_path):
    saved(tmp_path)
    checkpoint=history.checkpoint_prepare(tmp_path);history.checkpoint_commit(tmp_path,checkpoint['revision'],checkpoint['approval_id'],'Owner')
    with SafeRoot(tmp_path) as safe:
        state,_,manifest=store.read_state(safe)
        state['operations'].append({'uncommitted':'projection probe'})
        assert state['changes']==[]
        doc=state['product']['revisions'][0]['document'];doc['features'][0]['questions'].append('Feature-only question')
        assert doc['questions']==[] and doc['sources']==[]
        decoded=store.unpack_reference(manifest['state_ref'],lambda ref:store.get_object(safe,ref))
        assert decoded['operations']==[] and decoded['changes']==[]

def test_planning_materialization_saturation_preserves_operational_reserve(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'PLANNING_REPLAY_BYTES',24*1024)
    root_saved=None;first_receipt=None
    for index in range(50):
        before=core.read(tmp_path);doc=document();doc['notes']='Revision '+str(index)
        action={'kind':'product_save','document':doc,'base_revision':before['product']['current_revision'],'actor':'Author'}
        preview=core.prepare(tmp_path,action,before['revision'])
        try:result=core.commit(tmp_path,action,before['revision'],preview['approval_id'])
        except ValueError as error:
            assert 'planning_replay_limit' in str(error);break
        root_saved=result['view']['product']['document'];first_receipt=first_receipt or result['receipt']
    else:pytest.fail('Repeated planning revisions did not exercise replay bound')
    assert root_saved is not None and core.read(tmp_path)['product']['document']==root_saved
    act(tmp_path,{'kind':'activate','profile':'routine'})
    act(tmp_path,{'kind':'create','change_id':'Emergency','title':'Operational follow-up','owner':'Owner','risk':'routine'})
    checkpoint=history.checkpoint_prepare(tmp_path);history.checkpoint_commit(tmp_path,checkpoint['revision'],checkpoint['approval_id'],'Owner')
    assert core.commit(tmp_path,first_receipt['action'],first_receipt['expected_revision'],first_receipt['approval_id'])['receipt']['idempotent']
    assert core.read(tmp_path)['changes'][0]['id']=='Emergency'

def test_writer_charges_exact_reader_visits_with_repeated_parts(tmp_path):
    value={'repeated':['a'*140000,'a'*140000], 'dicts':[{'same':'b'*35000},{'same':'b'*35000}]}
    with SafeRoot(tmp_path) as safe:
        written=[0,0];reference=store._pack(safe,value,budget=written)
        decoded=[0,0];assert store._unpack(safe,reference,budget=decoded)==value
        assert written[:2]==decoded
