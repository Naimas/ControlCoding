"""Discriminating Core planning contract controls; no forged approval or evidence."""
import copy
import json
import pytest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import cc_development_lib as core
import cc_product_planning as planning
import cc_product_documents as documents
from cc_evidence_inputs import SafeRoot
from test_cc_development_core import act
from test_cc_product_planning_detail import selected, specification_action, accept, plan_action, planned

def state(root):
    with SafeRoot(root) as safe:return core.storage.load(safe)[0]

def test_new_profile_cannot_be_disabled_by_legacy_action(tmp_path):
    selected(tmp_path);action=specification_action(tmp_path)
    action['kind']='product_specification';del action['details']
    before=core.read(tmp_path);preview=core.prepare(tmp_path,action,before['revision'])
    assert 'planning_detail_required' in preview['blockers']
    assert core.read(tmp_path)==before

def test_required_new_specification_condition_needs_delivery_and_proof(tmp_path):
    selected(tmp_path);spec=specification_action(tmp_path)
    spec['content']['criteria'].append({'id':'restart-recovery','requirement_id':spec['content']['requirements'][0]['id'],'text':'Authorization remains mandatory after recovery.','required':True,'proof':'human','checks':[]})
    act(tmp_path,spec);accept(tmp_path,'C1','design')
    plan=plan_action(tmp_path)
    for task in plan['details']['task_details']:
        task['criterion_ids']=[c for c in task['criterion_ids'] if c!='restart-recovery']
        task['obligations']=[o for o in task['obligations'] if o['criterion_id']!='restart-recovery']
    result=act(tmp_path,plan)
    report=next(r for r in result['product']['planning'] if r['change_id']=='C1' and r['stage']=='plan')
    assert 'planning_criterion_uncovered:restart-recovery' in report['blockers']
    assert 'planning_proof_obligation_missing:restart-recovery' in report['blockers']
    assert not result['changes'][0]['gates']['plan']['accepted']

def test_requirement_provenance_cannot_claim_other_scope(tmp_path):
    selected(tmp_path);action=specification_action(tmp_path)
    action['details']['requirement_details'][0]['provenance']['product_criterion_ids']=['unselected-security']
    result=act(tmp_path,action)
    report=next(r for r in result['product']['planning'] if r['change_id']=='C1' and r['stage']=='design')
    assert 'planning_product_reference_unknown' in report['blockers']
    assert not result['changes'][0]['gates']['design']['accepted']

@pytest.mark.parametrize('line_ending',['\n','\r\n'],ids=['LF','CRLF'])
def test_stable_stage_export_and_import_parse_visible_task_edit(tmp_path,line_ending):
    _,plan=planned(tmp_path)
    preview=documents.stage_export_preview(tmp_path,'C1','plan','project_managed','plan.md')
    assert len(preview['content'].encode())<=128*1024
    documents.stage_export_commit(tmp_path,'C1','plan','project_managed','plan.md',preview)
    unchanged=documents.stage_import_preview(tmp_path,'C1','plan','plan.md')
    assert not unchanged['blockers'] and not unchanged['differences']
    raw=(tmp_path/'plan.md').read_text(encoding='utf-8')
    expected=plan['details']['task_details'][0]['approach']
    prefix='## Planning detail / Task instructions / Approach\n'
    assert prefix+expected+'\n' in raw
    modified=raw.replace(prefix+expected+'\n',prefix+expected+' Preserve rejected requests during rollback.\n',1)
    (tmp_path/'plan.md').write_bytes(modified.replace('\n',line_ending).encode('utf-8'))
    edited=documents.stage_import_preview(tmp_path,'C1','plan','plan.md')
    assert not edited['blockers'] and edited['differences']
    import hashlib
    assert edited['review']['sha256']==hashlib.sha256((tmp_path/'plan.md').read_bytes()).hexdigest()
    assert 'Preserve rejected requests' in edited['action']['details']['task_details'][0]['approach']
    result=act(tmp_path,edited['action'])
    assert not result['changes'][0]['gates']['plan']['accepted']

def test_unchanged_base_and_forged_source_token_cannot_hide_unknown_notes(tmp_path):
    planned(tmp_path);preview=documents.stage_export_preview(tmp_path,'C1','plan','project_managed','plan.md')
    documents.stage_export_commit(tmp_path,'C1','plan','project_managed','plan.md',preview)
    with (tmp_path/'plan.md').open('a',encoding='utf-8') as file:file.write('Unresolved source condition: never allow anonymous access.\n')
    imported=documents.stage_import_preview(tmp_path,'C1','plan','plan.md')
    assert 'stage_unknown_notes_review_required' in imported['blockers']
    action=imported['action'];action['source_review']=imported['review']
    before=core.read(tmp_path);prepared=core.prepare(tmp_path,action,before['revision'])
    assert 'product_source_review_stale' in prepared['blockers']
    assert core.read(tmp_path)==before


def test_actual_declared_automated_obligations_publish_and_unknown_contract_does_not(tmp_path):
    contract={'schemaVersion':1,'requiredKinds':[],'suites':[{'id':'security','kind':'targeted','required':True,'command':'python -c "print(123)"','description':'Check authorization after restart','timeoutSeconds':30}]}
    (tmp_path/'controlcoding.verification.json').write_text(json.dumps(contract),encoding='utf-8')
    selected(tmp_path);spec=specification_action(tmp_path)
    for criterion in spec['content']['criteria']:criterion.update(proof='automated',checks=[{'kind':'verification','id':'security'}])
    saved=act(tmp_path,spec);assert saved['product']['drafts'][0]['published']
    assert saved['changes'][0]['gates']['design']['outcome']=='allowed'
    accept(tmp_path,'C1','design');plan=plan_action(tmp_path)
    for criterion in spec['content']['criteria']:plan['content']['criterion_check_mapping'][criterion['id']]=criterion['checks']
    for task in plan['details']['task_details']:
        for obligation in task['obligations']:obligation.update(kind='verification',check_id='security',question='')
    act(tmp_path,plan);accept(tmp_path,'C1','plan')
    current=core.read(tmp_path);assert current['changes'][0]['gates']['plan']['accepted']
    assert current['changes'][0]['gates']['verify']['outcome']!='allowed'
    (tmp_path/'controlcoding.verification.json').write_text(json.dumps({'schemaVersion':999,'suites':[{'id':'security'}]}),encoding='utf-8')
    stale=core.read(tmp_path)
    assert 'planning_check_unknown' in stale['changes'][0]['gates']['plan']['reasons']

def test_exact_repeated_detail_save_preserves_approved_binding(tmp_path):
    _,plan=planned(tmp_path);before=core.read(tmp_path)
    after=act(tmp_path,copy.deepcopy(plan))
    original=next(r for r in before['product']['planning'] if r['change_id']=='C1' and r['stage']=='plan')
    repeated=next(r for r in after['product']['planning'] if r['change_id']=='C1' and r['stage']=='plan')
    assert repeated['detail_revision']==original['detail_revision']
    assert after['changes'][0]['gates']['plan']['accepted']


def test_first_legacy_increment_projects_fresh_profile_and_replays(tmp_path):
    from test_cc_product_core import accepted
    accepted(tmp_path); act(tmp_path,{'kind':'activate','profile':'routine'})
    before=core.read(tmp_path)
    action={'kind':'product_increment','product_revision':before['product']['current_revision'],'feature_id':'signup','increment_id':'I1','change_id':'C1','title':'First','owner':'Owner','risk':'routine','actor':'Author'}
    act(tmp_path,action)
    resumed=core.read(tmp_path)
    canonical=state(tmp_path)
    profile=planning.profile(canonical['product'],'C1')
    assert profile['contract']==planning.CONTRACT
    assert profile['selection_revision']==canonical['product']['increments'][0]['selection_revision']
    assert planning.applicability(canonical['product'],canonical['changes'][0])=='detailed'
    assert core.read(tmp_path)==resumed


def test_unpublished_required_weakening_keeps_specific_gate_diagnostic(tmp_path):
    selected(tmp_path);draft=specification_action(tmp_path)
    draft['content']['criteria'][0]['required']=False
    act(tmp_path,draft)
    view=core.read(tmp_path)
    reasons=view['changes'][0]['gates']['design']['reasons']
    assert 'product_required_criterion_weakened' in reasons
    assert not view['changes'][0]['gates']['design']['accepted']
    assert not view['product']['drafts'][-1]['published']
    assert core.read(tmp_path)==view
