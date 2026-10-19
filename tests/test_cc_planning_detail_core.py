"""Actual durable PW04 discussion review remains draft-only and source-bound."""
import copy
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import cc_development_lib as core
import cc_planning as planning
from test_cc_development_core import act
from test_cc_product_planning_detail import selected,specification_action
from test_cc_planning import create,options,prepare,reserve,finish,result

def reviewed(root):
    selected(root);draft=specification_action(root);create(root)
    o=options(root,'specification_v2');o.update(change_id='C1',feature_id='signup',editable=[{'kind':'artifact','id':'C1','fields':['details','mappings']}])
    view,preview,_=prepare(root,o);view,_=reserve(root,view,preview)
    proposal={'schema_version':2,'request_id':preview['request']['id'],'edits':[{'op':'set','kind':'artifact','id':'C1','field':key,'value':draft[key]} for key in ('details','mappings')],
              'claims':[{'edit_index':index,'kind':'proposed_decision','source_id':'','section_id':'','reason':'Preserve mandatory authorization and request explicit stage review.'} for index in range(2)],'questions':[],'conflicts':[],'omissions':[]}
    view,_=finish(root,view,preview,result(json.dumps(proposal)))
    review=planning.dispatch(root,{'operation':'planning_review','session_id':'S1','request_id':preview['request']['id'],'current_binding':preview['request']['configuration'],'actor':'Reviewer'})
    return draft,preview,review

def test_actual_v2_request_review_draft_and_cold_resume(tmp_path):
    _,preview,review=reviewed(tmp_path)
    assert preview['request']['detail_contract']['profile_revision']!='absent'
    assert not review['blockers'] and review['action']['kind']=='product_specification_v2'
    assert review['planning_review']['schema_version']==2 and review['planning_review']['detail_binding_digest']
    view=act(tmp_path,review['action'])
    assert not view['changes'][0]['gates']['design']['accepted']
    assert core.read(tmp_path)['product']['planning'][0]['details']==review['action']['details']

def test_saved_detailed_proposal_cannot_apply_after_canonical_detail_drift(tmp_path):
    draft,_,review=reviewed(tmp_path)
    changed=copy.deepcopy(draft);changed['details']['requirement_details'][0]['approach']+=' Recover original authorization IDs.'
    act(tmp_path,changed);before=core.read(tmp_path)
    with pytest.raises(ValueError):core.prepare(tmp_path,review['action'],before['revision'])
    assert core.read(tmp_path)==before

def test_versioned_reply_cannot_smuggle_sibling_change_or_approval():
    request={'id':'R-fixture','target':'specification_v2','editable':[{'kind':'artifact','id':'C1','fields':['details']}],'sources':[],'base':{'content':{},'details':{},'mappings':[]}}
    proposal={'schema_version':2,'request_id':'R-fixture','edits':[{'op':'set','kind':'artifact','id':'C2','field':'details','value':{}}],
              'claims':[{'edit_index':0,'kind':'proposed_decision','source_id':'','section_id':'','reason':'A sibling mutation'}],'questions':[],'conflicts':[],'omissions':[]}
    with pytest.raises(ValueError,match='planning_edit_outside_scope'):planning.validate_proposal(proposal,request)
    proposal['edits'][0]['id']='C1';proposal['approval']='accepted'
    with pytest.raises(ValueError,match='planning_proposal_invalid'):planning.validate_proposal(proposal,request)


CORPUS=json.loads((Path(__file__).parent/'fixtures'/'planning-quality-corpus-pw04-v1.json').read_text(encoding='utf-8'))['cases']

@pytest.mark.parametrize('case',CORPUS,ids=lambda case:case['id'])
def test_known_content_selected_context_preserves_mandatory_facts_without_sending(tmp_path,case):
    from cc_product_documents import observe_source
    from test_cc_product_core import document
    from test_cc_product_evolution import select_action
    doc=document();source=observe_source(tmp_path,text=case['source'])
    for item in source['sections']:item.update(selected=True,disposition='mapped',targets=['signup-success'],reason='Retain every selected mandatory fact for explicit human interpretation.')
    doc['sources']=[source];doc['features'][0]['source_references']=[source['source_id']]
    selection=select_action(tmp_path,doc);selection['changes'][1]['feature_ids']=['signup'];selection['criteria'][0].update(contributor_change_ids=['C1','C2'],integration_check={'kind':'verification','id':'security'})
    act(tmp_path,selection);create(tmp_path)
    o=options(tmp_path,'specification_v2');o.update(change_id='C1',editable=[{'kind':'artifact','id':'C1','fields':['details']}],sources=[{'source_id':source['source_id'],'section_id':source['sections'][0]['id']}])
    request=planning.dispatch(tmp_path,{'operation':'planning_preview','session_id':'S1','options':o})['request']
    messages='\n'.join(row['content'] for row in request['messages'])
    assert all(fact in messages for fact in case['required_facts'])
    assert planning.dispatch(tmp_path,{'operation':'planning_read','session_id':'S1'})['session']['usage']['network_calls']==0
    assert not core.read(tmp_path)['changes'][0]['gates']['design']['accepted']
