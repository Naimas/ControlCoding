"""Independent progress expectations over real Product owner transitions."""
import copy
import json
from pathlib import Path

import pytest

from test_cc_development_core import act, decide
from test_cc_product_core import document, saved, handed, planned, detailed_action
import cc_development_lib as development
import cc_product as product
import cc_product_progress as progress
import cc_product_progress_observation as observations
from cc_evidence_inputs import SafeRoot, Budget
from cc_product_progress_contract import ProgressError, validate_query, decode_cursor, encode_cursor


def test_empty_read_is_same_canonical_project_and_creates_nothing(tmp_path):
    value=progress.read(tmp_path)
    assert value['status']=='empty' and not value['enabled']
    assert value['project_identity']==development.read(tmp_path)['project_identity']
    assert value['summary']['outcome']=='not_established'
    assert value['summary']['next_actions'][0]['destination']=='Product'
    assert list(tmp_path.iterdir())==[]


def test_saved_scope_and_accepted_intent_are_distinct_without_delivery(tmp_path):
    saved(tmp_path)
    draft=progress.read(tmp_path)
    assert draft['scope']['document_state']=='draft'
    assert draft['rows'][0]['summary']['outcome']=='planned'
    assert draft['summary']['coverage']['required']=={
        'total':1,'mapped':0,'implemented':0,'verified':0,'released':0,'missing':1,'unknown':0}
    handed_root=tmp_path/'active';handed_root.mkdir();handed(handed_root)
    assert progress.read(handed_root)['summary']['outcome']=='planned'
    decide(handed_root,'intent')
    assert progress.read(handed_root)['summary']['outcome']=='in_progress'


def test_zero_conditions_never_imply_completion(tmp_path):
    doc=document();doc['features'][0]['criteria']=[];saved(tmp_path,doc)
    value=progress.read(tmp_path)
    assert value['summary']['outcome']=='planned'
    assert value['summary']['coverage']['required']['total']==0


def test_optional_conditions_have_their_own_denominator(tmp_path):
    doc=document();doc['features'][0]['criteria'].append({'id':'optional','text':'Optional convenience','required':False})
    saved(tmp_path,doc)
    value=progress.read(tmp_path,view='feature',entity_id='signup')
    assert value['summary']['coverage']['required']['total']==1
    assert value['summary']['coverage']['optional']['total']==1
    assert len(value['rows'])==2 and value['rows'][1]['data']['required'] is False


def test_real_plan_task_order_branch_join_and_no_completion_owner(tmp_path):
    planned(tmp_path)
    from cc_product_documents import observe_files
    existing=development.read(tmp_path)['changes'][0]['artifacts']['plan'][-1]['content']
    draft={'content':copy.deepcopy(existing),'files':observe_files(tmp_path,existing['files'])}
    draft['content']['tasks']=[{'id':'root','title':'Start','owner':'Author','depends_on':[]},
        {'id':'left','title':'Left','owner':'Author','depends_on':['root']},
        {'id':'right','title':'Right','owner':'Author','depends_on':['root']},
        {'id':'join','title':'Join','owner':'Author','depends_on':['left','right']}]
    action=detailed_action(tmp_path,{'kind':'product_plan','change_id':'C1','content':draft['content'],
        'files':draft['files'],'actor':'Author'})
    act(tmp_path,action)
    value=progress.read(tmp_path,view='change',entity_id='C1')
    rows=value['rows'];by_id={r['data']['task_id']:r for r in rows}
    assert [r['data']['task_id'] for r in rows]==['root','left','right','join']
    assert by_id['root']['data']['branch'] and by_id['join']['data']['join']
    assert by_id['join']['data']['depends_on']==[by_id['left']['id'],by_id['right']['id']]
    assert all(r['data']['completion']=='not_tracked' for r in rows)
    original_ids={r['id'] for r in rows}
    action['content']['tasks'][0]['title']='Revised start';action['details']['task_details'][0]['approach']='Revised approach'
    act(tmp_path,action)
    newer=progress.read(tmp_path,view='change',entity_id='C1')
    assert original_ids.isdisjoint(r['id'] for r in newer['rows'])


def test_title_reorder_and_independent_addition_preserve_applicable_progress(tmp_path):
    planned(tmp_path)
    before=progress.read(tmp_path,view='feature',entity_id='signup')
    doc=document();doc['title']='Renamed app';doc['features'][0]['title']='Create account'
    extra=copy.deepcopy(doc['features'][0]);extra.update(id='reports',title='Reports')
    extra['criteria']=[{'id':'report-condition','text':'A separate report appears','required':True}]
    doc['features'].insert(0,extra);saved(tmp_path,doc)
    after=progress.read(tmp_path,view='feature',entity_id='signup')
    assert after['summary']['outcome']==before['summary']['outcome']=='in_progress'
    assert after['summary']['coverage']==before['summary']['coverage']
    assert after['scope']['feature_revision']!=before['scope']['feature_revision']
    assert after['summary']['next_actions'][0]['feature_revision']==after['scope']['feature_revision']


def test_changed_required_condition_does_not_reuse_old_mapping(tmp_path):
    planned(tmp_path);doc=document();doc['features'][0]['criteria'][0]['text']='Now require a separate second factor'
    saved(tmp_path,doc)
    value=progress.read(tmp_path,view='feature',entity_id='signup')
    assert value['summary']['outcome']=='planned'
    assert value['summary']['coverage']['required']['mapped']==0
    assert value['summary']['coverage']['required']['missing']==1
    assert value['summary']['attention']=='needs_review' and value['summary']['currentness']=='stale'


def test_overview_pages_whole_rows_and_rejects_changed_observation(tmp_path):
    doc=document();base=doc['features'][0];doc['chapters']=[];doc['features']=[]
    for index in range(40):
        feature=copy.deepcopy(base);feature.update(id='F'+str(index),title='Feature '+str(index)+' Ω')
        feature['criteria']=[{'id':'P'+str(index),'text':'Condition '+str(index),'required':True}]
        doc['features'].append(feature)
    saved(tmp_path,doc)
    first=progress.read(tmp_path)
    assert len(first['rows'])==32 and first['pagination']['total']==40
    second=progress.read(tmp_path,cursor=first['pagination']['next_cursor'],observation_id=first['observation']['observation_id'])
    assert len(second['rows'])==8 and second['pagination']['next_cursor'] is None
    assert set(r['id'] for r in first['rows']).isdisjoint(r['id'] for r in second['rows'])
    doc['features'][0]['title']='Changed title';saved(tmp_path,doc)
    with pytest.raises(ProgressError,match='progress_observation_changed'):
        progress.read(tmp_path,cursor=first['pagination']['next_cursor'],observation_id=first['observation']['observation_id'])


@pytest.mark.parametrize('patch',[{'view':'unknown'},{'view':'feature'},{'entity_id':'C1'},
    {'collection':'tasks'},{'observation_id':'ABC'},{'cursor':'abc'},{'extra':False}])
def test_invalid_selectors_fail_before_owner_load(tmp_path,monkeypatch,patch):
    query={'view':'overview','entity_id':None,'collection':None,'cursor':None,'observation_id':None}|patch
    called=[]
    monkeypatch.setattr(development.storage,'load',lambda *_a,**_k:called.append(True))
    with pytest.raises(ProgressError):
        validate_query(query)
    assert not called


def test_global_accounting_counts_repeated_and_missing_reads(tmp_path):
    (tmp_path/'bytes.txt').write_bytes(b'1234567')
    with observations.observing() as observer:
        with SafeRoot(tmp_path) as safe:
            safe.read('bytes.txt',Budget());safe.read('bytes.txt',Budget())
            assert safe.read('absent',Budget(),missing=True) is None
            safe.listing('',Budget())
        assert observer.input_bytes==14
        assert observer.read_entries>=5
        assert observer.recheck()
        assert observer.input_bytes==21


def test_initial_registry_drift_retries_with_same_accounting(tmp_path,monkeypatch):
    saved(tmp_path);original=development.storage.load;changed=[]
    def racing(root,**kwargs):
        value=original(root,**kwargs)
        if not changed:
            changed.append(True)
            doc=document();doc['title']='Concurrent new document'
            saved(tmp_path,doc)
        return value
    monkeypatch.setattr(development.storage,'load',racing)
    value=progress.read(tmp_path)
    assert value['scope']['document_title']=='Concurrent new document'
    assert value['observation']['accounting']['attempts']==2


def test_deadline_and_aggregate_byte_limit_cannot_be_hidden_by_adapter(tmp_path,monkeypatch):
    monkeypatch.setattr(observations,'MAX_BYTES',1)
    with pytest.raises(ProgressError,match='progress_limits_exhausted'):
        progress.read(tmp_path)


def test_unknown_reason_is_visible_and_conservative():
    assert progress.attention('future_core_blocker')=='not_established'
    value=progress.summarize(planned=True,reasons=[progress.reason('future_core_blocker')])
    assert value['outcome']=='planned' and value['attention']=='not_established'


def test_exact_edge_results_do_not_copy_one_consumer_blocker_to_every_edge(tmp_path):
    from test_cc_product_evolution import select_action,two_features,dependency,specification
    doc=two_features();action=select_action(tmp_path,doc)
    action['dependencies']=[dependency(action,doc,result='specification_accepted',gate='plan'),
                            dependency(action,doc,result='verified',gate='build')]
    act(tmp_path,action);specification(tmp_path,'C1')
    value=progress.read(tmp_path,view='increment',entity_id='I1',collection='dependencies')
    edges={r['data']['required_result']:r['data'] for r in value['rows']}
    assert edges['specification_accepted']['state']=='allowed'
    assert edges['specification_accepted']['reasons']==[]
    assert edges['verified']['state']=='blocked'
    assert edges['verified']['reasons']==['product_dependency_verified_required']
    assert all(row['precision']=='edge' for row in edges.values())


def test_owner_proof_expiry_during_emission_cannot_publish_current_claim(tmp_path,monkeypatch):
    from datetime import datetime,timedelta,timezone
    saved(tmp_path);original=progress.validate_response;calls=[]
    def expiring(value):
        checked=original(value)
        calls.append(True)
        observations.current().expires((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat())
        return checked
    monkeypatch.setattr(progress,'validate_response',expiring)
    with pytest.raises(ProgressError,match='progress_observation_changed'):
        progress.read(tmp_path)
    assert len(calls)==2


def test_receipt_directory_membership_change_is_part_of_shared_binding(tmp_path):
    folder=tmp_path/'receipts';folder.mkdir()
    with observations.observing() as observer:
        with SafeRoot(tmp_path) as safe:
            safe.listing('receipts',Budget())
        (folder/'new-failed.json').write_text('{"status":"failed"}',encoding='utf-8')
        assert observer.recheck() is False


def test_actual_response_byte_paging_preserves_whole_rows_and_indivisible_errors(tmp_path,monkeypatch):
    doc=document();base=doc['features'][0];doc['chapters']=[];doc['features']=[]
    for index in range(12):
        feature=copy.deepcopy(base);feature.update(id='F'+str(index),title='Feature '+str(index),outcome='Ω'*300)
        feature['criteria']=[{'id':'P'+str(index),'text':'One exact condition','required':True}]
        doc['features'].append(feature)
    saved(tmp_path,doc)
    monkeypatch.setattr(progress,'MAX_RESPONSE_BYTES',16000)
    value=progress.read(tmp_path)
    assert 0<len(value['rows'])<12 and len(progress.serialized(value))<=16000
    cursor=decode_cursor(value['pagination']['next_cursor'])
    assert cursor['offset']==len(value['rows']) and value['pagination']['total']==12
    isolated=tmp_path/'single';isolated.mkdir();saved(isolated)
    monkeypatch.setattr(progress,'MAX_RESPONSE_BYTES',524288)
    ordinary=progress.read(isolated)
    monkeypatch.setattr(progress,'MAX_RESPONSE_BYTES',len(progress.serialized(ordinary))-100)
    with pytest.raises(ProgressError,match='progress_record_too_large'):
        progress.read(isolated)


def test_keep_adopt_defer_cancel_are_explicit_scope_states(tmp_path):
    from test_cc_product_evolution import selected,two_features,accept,scope_action,decide as owner_decide
    selected(tmp_path);owner_decide(tmp_path,'C1','intent')
    doc=two_features();doc['features'][0]['outcome']='Account creation includes readable feedback'
    accept(tmp_path,doc);act(tmp_path,scope_action(tmp_path,'keep'))
    kept=progress.read(tmp_path,view='feature',entity_id='signup')
    assert kept['scope']['decision']=='keep'
    assert kept['summary']['outcome']=='planned' and kept['summary']['currentness']=='stale'
    selected_scope=progress.read(tmp_path,view='increment',entity_id='I1')
    assert selected_scope['scope']['decision']=='keep'
    doc['features'][0]['outcome']='Account creation includes a separate confirmation'
    accept(tmp_path,doc);act(tmp_path,scope_action(tmp_path,'adopt'))
    adopted=progress.read(tmp_path,view='feature',entity_id='signup')
    assert adopted['scope']['decision']=='adopt'
    assert adopted['scope']['selection_revision']!=kept['scope']['selection_revision']
    act(tmp_path,scope_action(tmp_path,'defer'))
    deferred=progress.read(tmp_path,view='increment',entity_id='I1')
    assert deferred['scope']['decision']=='defer' and deferred['summary']['outcome'] not in ('implemented','verified','released')
    act(tmp_path,scope_action(tmp_path,'cancel'))
    cancelled=progress.read(tmp_path,view='increment',entity_id='I1')
    assert cancelled['scope']['decision']=='cancel'
    assert cancelled['summary']['attention']=='blocked'


def test_retired_removed_feature_is_historical_and_excluded_from_current_rollup(tmp_path):
    from test_cc_product_evolution import selected,two_features,accept
    selected(tmp_path)
    doc=two_features();doc['features']=doc['features'][1:];doc['chapters'][0]['feature_ids']=[]
    accepted=accept(tmp_path,doc)
    act(tmp_path,{'kind':'product_retire','feature_id':'signup','product_revision':accepted['product']['current_revision'],
        'successor_feature_id':'reports','reason':'Keep original scope as retained history','actor':'Owner'})
    value=progress.read(tmp_path)
    assert value['summary']['coverage']['required']['total']==1
    retired=next(r for r in value['rows'] if r['id']=='signup')
    assert retired['scope']['role']=='retired' and retired['summary']['currentness']=='not_applicable'
    assert progress.read(tmp_path,view='feature',entity_id='signup')['scope']['role']=='retired'
    selected=progress.read(tmp_path,view='increment',entity_id='I1',collection='criteria')
    assert next(r for r in selected['rows'] if r['id']=='signup-success')['scope']['role']=='retired'
    assert next(r for r in selected['rows'] if r['id']=='reports-success')['scope']['role']=='active'


def test_source_replacement_during_real_reader_retries_and_publishes_only_new_observation(tmp_path,monkeypatch):
    source=tmp_path/'accounts.py';source.write_text('value = 1\n',encoding='utf-8')
    planned(tmp_path);original=progress.Projection.project;changed=[]
    def replacing(self,query):
        value=original(self,query)
        if not changed:
            changed.append(True);source.write_text('value = 2\n',encoding='utf-8')
        return value
    monkeypatch.setattr(progress.Projection,'project',replacing)
    value=progress.read(tmp_path)
    assert value['observation']['accounting']['attempts']==2
    assert value['summary']['currentness']=='stale'
    assert value['summary']['outcome'] not in ('implemented','verified','released')


def test_superseded_increment_remains_inspectable_without_qualifying_successor(tmp_path):
    from test_cc_product_evolution import select_action,close_product_change
    from test_cc_product_evolution_acceptance import combined_contracts
    combined_contracts(tmp_path)
    action=select_action(tmp_path,document());action['changes']=action['changes'][:1]
    act(tmp_path,action);close_product_change(tmp_path,'C1')
    first=progress.read(tmp_path,view='increment',entity_id='I1')
    successor={**action,'increment_id':'I2','title':'New exact request',
        'predecessor_selection_revision':first['scope']['selection_revision'],
        'changes':[{'change_id':'C2','title':'Successor','owner':'Builder','risk':'routine','feature_ids':['signup']}],
        'criteria':[{'criterion_id':'signup-success','owner_change_id':'C2','contributor_change_ids':['C2'],'integration_check':None}]}
    act(tmp_path,successor)
    historical=progress.read(tmp_path,view='increment',entity_id='I1')
    assert historical['scope']['role']=='superseded'
    assert historical['summary']['currentness']=='not_applicable'
    historical_criteria=progress.read(tmp_path,view='increment',entity_id='I1',collection='criteria')
    assert all(row['scope']['role']=='superseded' for row in historical_criteria['rows'])
    current=progress.read(tmp_path)
    assert current['summary']['coverage']['required']['total']==1
    assert current['summary']['outcome']=='planned'
    assert progress.read(tmp_path,view='increment',entity_id='I2')['scope']['role']=='active'


def test_kept_selection_condition_navigation_binds_old_feature_revision(tmp_path):
    from test_cc_product_evolution import select_action,two_features,accept,scope_action
    doc=two_features();doc['features'][0]['criteria'].append({'id':'optional-feedback','text':'Original optional feedback','required':False})
    act(tmp_path,select_action(tmp_path,doc))
    before=progress.read(tmp_path,view='increment',entity_id='I1',collection='criteria')
    old=next(r for r in before['rows'] if r['id']=='optional-feedback')
    doc['features'][0]['criteria'][1]['text']='New optional feedback'
    accept(tmp_path,doc);act(tmp_path,scope_action(tmp_path,'keep'))
    selected=progress.read(tmp_path,view='increment',entity_id='I1',collection='criteria')
    retained=next(r for r in selected['rows'] if r['id']=='optional-feedback')
    assert retained['data']['text']=='Original optional feedback'
    assert retained['target']['selection_revision']==selected['scope']['selection_revision']
    assert retained['target']['feature_revision']==old['scope']['feature_revision']
    current=progress.read(tmp_path,view='feature',entity_id='signup')
    changed=next(r for r in current['rows'] if r['id']=='optional-feedback')
    assert changed['target']['feature_revision']==current['scope']['feature_revision']
    assert changed['target']['feature_revision']!=retained['target']['feature_revision']


def test_compacted_release_collection_does_not_claim_complete_archive(tmp_path):
    import cc_development_history as history
    planned(tmp_path)
    checkpoint=history.checkpoint_prepare(tmp_path)
    history.checkpoint_commit(tmp_path,checkpoint['revision'],checkpoint['approval_id'],'Owner')
    value=progress.read(tmp_path,view='change',entity_id='C1',collection='releases')
    assert value['pagination']['total'] is None
    assert value['pagination']['total_complete'] is False


def test_stable_stricter_owner_read_failure_is_not_reported_as_input_drift(tmp_path):
    from cc_evidence_inputs import EvidenceError
    (tmp_path/'source.bin').write_bytes(b'1234')
    with observations.observing() as observer:
        with SafeRoot(tmp_path) as safe:
            with pytest.raises(EvidenceError,match='byte_limit'):
                safe.read('source.bin',Budget(per_file=2,total=2))
        assert observer.recheck() is True


def test_accepted_plan_before_any_build_is_current_authoring_progress(tmp_path):
    planned(tmp_path);decide(tmp_path,'plan')
    value=progress.read(tmp_path)
    assert value['summary']['outcome']=='in_progress'
    assert value['summary']['currentness']=='current'
    assert value['summary']['attention']=='none'
    assert value['summary']['next_actions']


def test_same_file_with_different_owner_read_bounds_stays_coherent(tmp_path):
    from cc_evidence_inputs import EvidenceError
    (tmp_path/'source.bin').write_bytes(b'1234')
    with observations.observing() as observer:
        with SafeRoot(tmp_path) as safe:
            safe.read('source.bin',Budget(per_file=8,total=8))
            with pytest.raises(EvidenceError,match='byte_limit'):
                safe.read('source.bin',Budget(per_file=2,total=2))
            safe.read('source.bin',Budget(per_file=4,total=4))
        assert observer.recheck() is True


def test_historical_evidence_preserves_candidate_selection_after_real_adoption(tmp_path):
    from test_cc_product_evolution import accept,scope_action
    (tmp_path/'accounts.py').write_text('value = 1\n')
    planned(tmp_path);decide(tmp_path,'plan')
    act(tmp_path,{'kind':'candidate','change_id':'C1','files':['accounts.py'],'actor':'Author'})
    act(tmp_path,{'kind':'evidence','change_id':'C1','records':[{'id':'inspection','kind':'manual'}],'actor':'Reviewer'})
    before=progress.read(tmp_path,view='change',entity_id='C1',collection='evidence')['rows'][0]
    assert before['scope']['selection_revision'] is not None
    assert before['scope']['feature_revision'] is not None
    doc=document();doc['features'][0]['criteria'][0]['text']='An explicit account confirmation is required'
    accept(tmp_path,doc);act(tmp_path,scope_action(tmp_path,'adopt'))
    after=progress.read(tmp_path,view='change',entity_id='C1',collection='evidence')['rows'][0]
    assert after['scope']['selection_revision']==before['scope']['selection_revision']
    assert after['scope']['feature_revision']==before['scope']['feature_revision']
    assert after['scope']['selected_feature_revisions']==before['scope']['selected_feature_revisions']
    assert after['scope']['role']=='superseded'
    assert after['target']['selection_revision']==before['scope']['selection_revision']
    assert after['data']['state']=='unknown'


def test_assessed_criterion_reason_uses_exact_id_and_preserves_unknown_codes():
    rows=[{'id':'condition:with:colons','reasons':['criterion_check_unproved:combined','future_owner_blocker']}]
    known='condition:with:colons:criterion_check_unproved:combined'
    classified=progress.reason(known,assessed_criteria=rows,future=True)
    assert classified['code']==known and classified['attention']=='none'
    assert progress.reason(known,assessed_criteria=rows)['attention']=='blocked'
    unknown='condition:with:colons:future_owner_blocker'
    assert progress.reason(unknown,assessed_criteria=rows,future=True)['attention']=='not_established'
    assert progress.reason('other:criterion_check_unproved:combined',assessed_criteria=rows,future=True)['attention']=='not_established'
    assert progress.reason('verification:incomplete',future=True)['attention']=='not_established'
    assert progress.reason('verification:failed',future=True)['attention']=='blocked'


def test_actual_current_single_build_remains_implemented_before_verification(tmp_path):
    from cc_product_progress_fixture import fixture_seed,fixture_build
    fixture_seed(tmp_path,shared=False)
    fixture_build(tmp_path,change_ids=('C1',))
    for view,entity in [('feature','signup'),('change','C1')]:
        value=progress.read(tmp_path,view=view,entity_id=entity,collection='criteria')
        assert value['summary']['outcome']=='implemented'
        assert value['summary']['currentness']=='current'
        assert value['summary']['attention']=='none'
        required=value['summary']['coverage']['required']
        assert required['implemented']==required['total']==1
        assert required['verified']==required['unknown']==0
        assert any(r['code']=='signup-success:criterion_check_unproved:combined' for r in value['summary']['reasons'])
        contribution=value['rows'][0]['data']['contributions'][0]
        assert contribution['implemented'] and not contribution['verified']
        assert 'signup-success:criterion_check_unproved:combined' in contribution['reasons']


def test_unknown_contribution_reason_is_kept_when_future_gate_was_not_materialized(tmp_path,monkeypatch):
    from cc_development_lib import evaluator
    planned(tmp_path)
    original=evaluator.evaluate
    def with_unknown(*args,**kwargs):
        gates=original(*args,**kwargs)
        gates['verify']['reasons'].append('future_owner_blocker')
        return gates
    monkeypatch.setattr(evaluator,'evaluate',with_unknown)
    value=progress.read(tmp_path,view='feature',entity_id='signup')
    assert any(r['code']=='future_owner_blocker' and r['attention']=='not_established'
               for r in value['summary']['reasons'])
    assert value['summary']['currentness']=='unknown'
    assert value['summary']['outcome'] not in ('implemented','verified','released')


def test_same_file_multiple_bounds_becoming_directory_rechecks_as_changed(tmp_path):
    source=tmp_path/'input.bin';source.write_bytes(b'1234')
    with observations.observing() as observer:
        with SafeRoot(tmp_path) as safe:
            safe.read('input.bin',Budget(per_file=8,total=8))
            safe.read('input.bin',Budget(per_file=16,total=16))
        assert len(observer.transcript)==2
        assert observer.read_entries==2 and observer.input_bytes==8
        source.unlink();source.mkdir()
        assert observer.recheck() is False
        assert observer.changed and not observer.verifying
        assert observer.read_entries==4 and observer.input_bytes==8


def test_actual_reader_retries_unsafe_multi_bound_input_then_returns_explicit_error(tmp_path,monkeypatch):
    saved(tmp_path)
    source=tmp_path/'input.bin';source.write_bytes(b'1234')
    original=progress.Projection.project;observed=[];first_counts=[]
    def racing(self,query):
        value=original(self,query)
        observed.append(self.observer)
        self.safe.read('input.bin',Budget(per_file=8,total=8))
        self.safe.read('input.bin',Budget(per_file=16,total=16))
        if len(observed)==1:
            first_counts.append((self.observer.read_entries,self.observer.input_bytes))
            source.unlink();source.mkdir()
        return value
    monkeypatch.setattr(progress.Projection,'project',racing)
    with pytest.raises(ProgressError,match='^unsafe_file_type$'):
        progress.read(tmp_path)
    assert len(observed)==2 and observed[0] is observed[1]
    observer=observed[0]
    assert observer.attempts==2
    assert observer.read_entries>first_counts[0][0]+2
    assert observer.input_bytes>=first_counts[0][1]


def test_actual_rollup_rows_require_complete_non_null_summaries(tmp_path):
    handed(tmp_path)
    queries=[{}, {'view':'feature','entity_id':'signup','collection':'criteria'},
             {'view':'feature','entity_id':'signup','collection':'increments'},
             {'view':'increment','entity_id':'I1','collection':'changes'}]
    seen=set()
    for query in queries:
        value=progress.read(tmp_path,**query)
        row=value['rows'][0];seen.add(row['kind'])
        assert row['summary'] is not None
        for invalid in (None,{'outcome':'verified'}):
            attacked=copy.deepcopy(value);attacked['rows'][0]['summary']=invalid
            with pytest.raises(ProgressError,match='progress_invalid_response'):
                progress.validate_response(attacked)
    assert seen=={'feature','criterion','increment','change'}


def test_leaf_row_schema_accepts_null_or_complete_summary_without_relaxing_data(tmp_path):
    from cc_product_progress_contract import validate_row
    handed(tmp_path)
    value=progress.read(tmp_path)
    event=development.read(tmp_path)['changes'][0]['history'][0]
    revision='a'*64
    variants={
        'dependency':{'consumer_change_id':'C2','target_feature_id':'signup','target_feature_revision':revision,
            'target_selection_revision':revision,'target_change_id':'C1','required_result':'released',
            'gate':'build','state':'blocked','reasons':['product_dependency_released_required'],'precision':'edge'},
        'task':{'change_id':'C1','plan_revision':revision,'detail_revision':None,'profile_revision':None,
            'task_id':'T1','owner':'Author','deliverable':'Retain inspected result','criterion_ids':[],
            'files':[],'depends_on':[],'supports_task_ids':[],'declaration_order':0,'topological_order':0,
            'branch':False,'join':False,'completion':'not_tracked'},
        'evidence':{'change_id':'C1','candidate_identity':None,'kind':'human','reference':'inspection',
            'state':'unknown','reasons':[]},
        'incident':{'change_id':'C1','incident_id':revision,'state':'needs_review','linked_change_ids':[]},
        'release':{'change_id':'C1','release_identity':revision,'candidate_identity':None,'artifact_identity':None,
            'environment':'local','status':'prepared','historical':True,'assessment':{'state':'unknown','reasons':[]}},
        'history_ref':{'change_id':'C1','partial':False,'retained_records':2},
        'event':{'change_id':'C1','event':event}}
    for kind,data in variants.items():
        row={'kind':kind,'id':'fact','title':'Retained fact','scope':value['scope'],
             'summary':None,'target':None,'data':data}
        validate_row(row)
        row['summary']=copy.deepcopy(value['summary']);validate_row(row)
        row['summary']={'outcome':'verified'}
        with pytest.raises(ProgressError,match='progress_invalid_response'):
            validate_row(row)
        row['summary']=None;row['data']={**data,'unexpected':True}
        with pytest.raises(ProgressError,match='progress_invalid_response'):
            validate_row(row)


def _snapshot_input_git(root):
    import subprocess
    def git(*args):
        return subprocess.check_output(['git','-c','core.autocrlf=false','-c','core.hooksPath=',
            '-c','user.name=Snapshot tester','-c','user.email=snapshot@example.invalid',*args],
            cwd=root,stderr=subprocess.STDOUT)
    (root/'tracked.py').write_bytes(b'value = 1\n')
    git('init','-q');git('add','tracked.py');git('commit','-q','-m','Initial inputs')
    return git


def test_snapshot_inputs_reuses_complete_git_identity_and_copies_results(tmp_path,monkeypatch):
    import cc_evidence_inputs as inputs
    _snapshot_input_git(tmp_path)
    original=inputs._capture_inputs;calls=[]
    def capture(root,policy,**kwargs):
        calls.append(copy.deepcopy(policy));return original(root,policy,**kwargs)
    monkeypatch.setattr(inputs,'_capture_inputs',capture)
    policy={'extraPaths':[]}
    with SafeRoot(tmp_path) as safe:
        with inputs.snapshot_inputs(safe):
            first=inputs.capture_inputs(tmp_path,policy)
            assert first['complete']
            expected=copy.deepcopy(first)
            first['revision']['head']='caller mutation'
            assert inputs.capture_inputs(tmp_path,policy)==expected
            assert len(calls)==1
        assert len(calls)==2  # A real uncached Git/source recapture closes the scope.
    assert inputs._SNAPSHOT_INPUTS.get() is None


@pytest.mark.parametrize('drift',['source','untracked','index','head','unsafe'])
def test_snapshot_inputs_rejects_real_git_or_unsafe_input_drift(tmp_path,drift):
    import cc_evidence_inputs as inputs
    git=_snapshot_input_git(tmp_path);policy={'extraPaths':[]}
    with SafeRoot(tmp_path) as safe:
        with pytest.raises(inputs.EvidenceError,match='development_inputs_changed'):
            with inputs.snapshot_inputs(safe):
                before=inputs.capture_inputs(tmp_path,policy)
                assert before['complete']
                if drift=='untracked':(tmp_path/'new.py').write_bytes(b'new = True\n')
                elif drift=='unsafe':
                    (tmp_path/'tracked.py').unlink();(tmp_path/'tracked.py').mkdir()
                else:
                    (tmp_path/'tracked.py').write_bytes(b'value = 2\n')
                    if drift in ('index','head'):git('add','tracked.py')
                    if drift=='head':git('commit','-q','-m','Changed inputs')
                    if drift in ('index','head'):
                        (tmp_path/'tracked.py').write_bytes(b'value = 1\n')
                        if drift=='head':git('add','tracked.py')
                        actual=inputs._capture_inputs(tmp_path,policy)
                        assert actual['complete'] and actual['contentDigest']==before['contentDigest']
                        assert actual['revision']!=before['revision']
                assert inputs.capture_inputs(tmp_path,policy)==before
    assert inputs._SNAPSHOT_INPUTS.get() is None


def test_snapshot_inputs_keys_full_policy_and_root_and_copies_recheck_policy(tmp_path,monkeypatch):
    import cc_evidence_inputs as inputs
    other=tmp_path/'other';other.mkdir()
    (tmp_path/'source.py').write_bytes(b'one\n');(other/'source.py').write_bytes(b'two\n')
    original=inputs._capture_inputs;calls=[]
    def capture(root,policy,**kwargs):
        calls.append((Path(root),copy.deepcopy(policy)));return original(root,policy,**kwargs)
    monkeypatch.setattr(inputs,'_capture_inputs',capture)
    policy={'extraPaths':[],'contract':'first'}
    with SafeRoot(tmp_path) as safe:
        with inputs.snapshot_inputs(safe):
            inputs.capture_inputs(tmp_path,policy)
            policy['contract']='second'
            inputs.capture_inputs(tmp_path,policy)
            inputs.capture_inputs(other,policy)
            assert len(calls)==3
        assert calls[3:]==calls[:3]  # Caller mutation cannot rewrite the first policy.
    assert inputs._SNAPSHOT_INPUTS.get() is None


def test_snapshot_inputs_bypasses_explicit_budget_and_does_not_cache_incomplete(tmp_path,monkeypatch):
    import cc_evidence_inputs as inputs
    (tmp_path/'source.py').write_bytes(b'long enough\n')
    original=inputs._capture_inputs;calls=[]
    def capture(root,policy,**kwargs):
        calls.append(kwargs.get('budget'));return original(root,policy,**kwargs)
    monkeypatch.setattr(inputs,'_capture_inputs',capture)
    policy={'extraPaths':[]}
    with SafeRoot(tmp_path) as safe:
        with inputs.snapshot_inputs(safe):
            assert inputs.capture_inputs(tmp_path,policy)['complete']
            for _ in range(2):
                tiny=Budget(per_file=1,total=1)
                assert not inputs.capture_inputs(tmp_path,policy,budget=tiny)['complete']
            assert len(calls)==3 and all(value is not None for value in calls[1:])
        assert len(calls)==4
        (tmp_path/'directory').mkdir();invalid={'extraPaths':['directory']}
        with inputs.snapshot_inputs(safe):
            assert not inputs.capture_inputs(tmp_path,invalid)['complete']
            assert not inputs.capture_inputs(tmp_path,invalid)['complete']
        assert len(calls)==6  # Incomplete results have no cached entry or recheck.


def test_snapshot_inputs_preserves_active_progress_accounting_and_original_cache(tmp_path,monkeypatch):
    import cc_evidence_inputs as inputs
    (tmp_path/'source.py').write_bytes(b'value = 1\n')
    original=inputs._capture_inputs;calls=[]
    def capture(root,policy,**kwargs):
        calls.append(True);return original(root,policy,**kwargs)
    monkeypatch.setattr(inputs,'_capture_inputs',capture)
    policy={'extraPaths':[]}
    with SafeRoot(tmp_path) as safe:
        with inputs.snapshot_inputs(safe):
            inputs.capture_inputs(tmp_path,policy)
            with observations.observing() as observer:
                with inputs.snapshot_inputs(safe):
                    first=inputs.capture_inputs(tmp_path,policy);used=observer.input_bytes
                    assert inputs.capture_inputs(tmp_path,policy)==first
                    assert observer.input_bytes==used>0
                    assert any(key[0]=='inputs' for key in observer.memo)
                assert len(calls)==3  # Ordinary memo did not intercept either call.
        assert len(calls)==4
    assert inputs._SNAPSHOT_INPUTS.get() is None


def test_snapshot_inputs_nested_and_exceptional_scopes_restore_context(tmp_path,monkeypatch):
    import cc_evidence_inputs as inputs
    (tmp_path/'source.py').write_bytes(b'value = 1\n')
    original=inputs._capture_inputs;calls=[]
    def capture(root,policy,**kwargs):
        calls.append(True);return original(root,policy,**kwargs)
    monkeypatch.setattr(inputs,'_capture_inputs',capture)
    policy={'extraPaths':[]}
    with SafeRoot(tmp_path) as safe:
        with inputs.snapshot_inputs(safe):
            inputs.capture_inputs(tmp_path,policy)
            with inputs.snapshot_inputs(safe):inputs.capture_inputs(tmp_path,policy)
            assert len(calls)==3
            inputs.capture_inputs(tmp_path,policy)
            assert len(calls)==3
            with pytest.raises(RuntimeError,match='original failure'):
                with inputs.snapshot_inputs(safe):
                    inputs.capture_inputs(tmp_path,policy)
                    raise RuntimeError('original failure')
            inputs.capture_inputs(tmp_path,policy)
            assert len(calls)==4
        assert len(calls)==5
        with inputs.snapshot_inputs(safe):inputs.capture_inputs(tmp_path,policy)
        assert len(calls)==7
    assert inputs._SNAPSHOT_INPUTS.get() is None


def test_snapshot_inputs_validates_retained_root_after_uncached_rechecks(tmp_path,monkeypatch):
    import cc_evidence_inputs as inputs
    (tmp_path/'source.py').write_bytes(b'value = 1\n')
    original=inputs._capture_inputs;calls=[]
    def capture(root,policy,**kwargs):
        calls.append(True);return original(root,policy,**kwargs)
    monkeypatch.setattr(inputs,'_capture_inputs',capture)
    with SafeRoot(tmp_path) as safe:
        def changed_root():
            assert len(calls)==2  # Initial capture and final uncached recapture.
            raise inputs.EvidenceError('project_identity_changed')
        monkeypatch.setattr(safe,'validate',changed_root)
        with pytest.raises(inputs.EvidenceError,match='project_identity_changed'):
            with inputs.snapshot_inputs(safe):inputs.capture_inputs(tmp_path,{'extraPaths':[]})
    assert inputs._SNAPSHOT_INPUTS.get() is None


def test_actual_prepared_current_and_changed_document_snapshots_have_separate_input_scopes(tmp_path,monkeypatch):
    import cc_evidence_inputs as inputs
    (tmp_path/'source.py').write_bytes(b'value = 1\n')
    original=inputs._capture_inputs;calls=[];views=[];view=product.view
    def capture(root,policy,**kwargs):
        calls.append(True);return original(root,policy,**kwargs)
    def observed_view(state,**kwargs):
        first=inputs.capture_inputs(tmp_path,{'extraPaths':[]})
        assert inputs.capture_inputs(tmp_path,{'extraPaths':[]})==first
        value=view(state,**kwargs);views.append(value['document']);return value
    monkeypatch.setattr(inputs,'_capture_inputs',capture)
    monkeypatch.setattr(product,'view',observed_view)
    with SafeRoot(tmp_path) as safe:
        state=development.storage.empty(safe);doc=document()
        action={'kind':'product_save','document':doc,'base_revision':'absent','actor':'Author'}
        prepared=development.prepared(safe,state,'absent',action)
        assert not prepared['blockers'] and prepared['view']['product']['document']==doc
        assert views==[None,doc]
        assert len(calls)==4  # Current/proposed each initial + uncached final capture.
    assert inputs._SNAPSHOT_INPUTS.get() is None
