"""Strict Product planning detail, profiles and derived coverage. No approval owner."""
from __future__ import annotations
from cc_development_lib.method import canonical, digest
from cc_development_lib.schema import DevelopmentError, identifier, revision, json_copy

CONTRACT = 'detailed_contract_v1'
FIELDS = {
 'product_adoption_save':('document','base_revision','sources','resolutions','actor'),
 'product_planning_upgrade':('change_id','selection_revision','actor','reason'),
 'product_specification_v2':('change_id','content','mappings','details','actor'),
 'product_plan_v2':('change_id','content','files','details','actor'),
}
COMMON = {'schema_version','contract','sources','facts','conflicts'}
PROVENANCE = {'kind','source_refs','product_criterion_ids','rationale'}
OBLIGATION = {'criterion_id','kind','check_id','question'}

def require(ok,code):
    if not ok: raise DevelopmentError(code)

def exact(value,fields): require(type(value) is dict and set(value)==set(fields),'invalid_planning_detail')

def narrative(value): require(type(value) is str and len(value)<=16384 and '\x00' not in value,'invalid_planning_detail')

def rows(value,maximum=128): require(type(value) is list and len(value)<=maximum,'invalid_planning_detail')

def ids(value):
    rows(value)
    for item in value: identifier(item)
    require(len(value)==len(set(value)),'invalid_planning_reference')

def source_refs(value,sources):
    rows(value)
    seen=set()
    for ref in value:
        exact(ref,{'source_id','item_id'})
        identifier(ref['source_id']);identifier(ref['item_id'])
        key=(ref['source_id'],ref['item_id'])
        require(key in sources and key not in seen,'invalid_planning_reference');seen.add(key)

def provenance(value,sources):
    exact(value,PROVENANCE)
    require(value['kind'] in ('source','product','explicit'),'invalid_planning_detail')
    source_refs(value['source_refs'],sources);ids(value['product_criterion_ids']);narrative(value['rationale'])
    require(value['kind']!='explicit' or bool(value['rationale'].strip()),'invalid_planning_detail')
    require(value['kind']!='source' or bool(value['source_refs']),'invalid_planning_reference')
    require(value['kind']!='product' or bool(value['product_criterion_ids']),'invalid_planning_reference')

def empty_details(stage):
    result={'schema_version':1,'contract':CONTRACT,'sources':[],'facts':[],'conflicts':[]}
    if stage=='design':result.update(requirement_details=[],condition_mappings=[])
    elif stage=='plan':result.update(task_details=[],rollback={'assessment':'','steps':[],'unavailable_reason':''})
    else: raise DevelopmentError('invalid_stage')
    return result

def validate_details(details,stage,content):
    exact(details,COMMON|({'requirement_details','condition_mappings'} if stage=='design' else {'task_details','rollback'}))
    require(type(details['schema_version']) is int and details['schema_version']==1 and details['contract']==CONTRACT,'invalid_planning_contract')
    require(len(canonical(details))<=128*1024,'planning_detail_size_limit')
    from cc_product_documents import validate_source,source_items
    rows(details['sources'],64);sources=set();source_ids=set()
    for source in details['sources']:
        validate_source(None,source,current=False,reviewed=False)
        require(source['source_id'] not in source_ids,'invalid_planning_reference');source_ids.add(source['source_id'])
        sources.update((source['source_id'],item['id']) for item in source_items(source))
    rows(details['facts']);seen=set()
    for fact in details['facts']:
        exact(fact,{'id','kind','text','provenance'});identifier(fact['id'])
        require(fact['id'] not in seen and fact['kind'] in ('assumption','decision','alternative','interface','risk'),'invalid_planning_detail');seen.add(fact['id'])
        narrative(fact['text']);provenance(fact['provenance'],sources)
    rows(details['conflicts']);seen=set()
    for conflict in details['conflicts']:
        exact(conflict,{'id','text','blocking','disposition','impact','provenance'});identifier(conflict['id'])
        require(conflict['id'] not in seen and type(conflict['blocking']) is bool and conflict['disposition'] in ('unresolved','resolved','upstream-review-required'),'invalid_planning_detail');seen.add(conflict['id'])
        narrative(conflict['text']);narrative(conflict['impact']);provenance(conflict['provenance'],sources)
    if stage=='design':
        req={r['id'] for r in content['requirements']};crit={r['id'] for r in content['criteria']}
        rows(details['requirement_details']);seen=set()
        for row in details['requirement_details']:
            exact(row,{'requirement_id','approach','provenance'});identifier(row['requirement_id'])
            require(row['requirement_id'] in req and row['requirement_id'] not in seen,'invalid_planning_reference');seen.add(row['requirement_id'])
            narrative(row['approach']);provenance(row['provenance'],sources)
        rows(details['condition_mappings']);seen=set()
        for row in details['condition_mappings']:
            exact(row,{'id','source_refs','product_criterion_id','requirement_ids','criterion_ids','decision','reason'});identifier(row['id']);identifier(row['product_criterion_id'])
            require(row['id'] not in seen and row['decision'] in ('equivalent','split','unresolved'),'invalid_planning_detail');seen.add(row['id'])
            source_refs(row['source_refs'],sources);ids(row['requirement_ids']);ids(row['criterion_ids']);narrative(row['reason'])
            require(set(row['requirement_ids'])<=req and set(row['criterion_ids'])<=crit,'invalid_planning_reference')
    else:
        tasks={r['id'] for r in content['tasks']};files=set(content['files']);rows(details['task_details']);seen=set()
        from cc_development_lib.schema import source_path
        for row in details['task_details']:
            exact(row,{'task_id','approach','deliverable','files','criterion_ids','obligations','supporting_rationale','supports_task_ids','file_free_rationale','provenance'})
            identifier(row['task_id']);require(row['task_id'] in tasks and row['task_id'] not in seen,'invalid_planning_reference');seen.add(row['task_id'])
            for field in ('approach','deliverable','supporting_rationale','file_free_rationale'):narrative(row[field])
            rows(row['files'],64)
            for path in row['files']:source_path(path)
            require(len(row['files'])==len(set(row['files'])) and set(row['files'])<=files,'invalid_planning_reference')
            ids(row['criterion_ids']);ids(row['supports_task_ids']);require(set(row['supports_task_ids'])<=tasks and row['task_id'] not in row['supports_task_ids'],'invalid_planning_reference')
            rows(row['obligations']);keys=set()
            for ob in row['obligations']:
                exact(ob,OBLIGATION);identifier(ob['criterion_id']);require(ob['kind'] in ('verification','invariants','human'),'invalid_planning_detail');narrative(ob['question'])
                if ob['kind']=='human':require(ob['check_id']=='','invalid_planning_detail')
                else:identifier(ob['check_id']);require(ob['question']=='','invalid_planning_detail')
                key=(ob['criterion_id'],ob['kind'],ob['check_id']);require(key not in keys,'invalid_planning_reference');keys.add(key)
            provenance(row['provenance'],sources)
        exact(details['rollback'],{'assessment','steps','unavailable_reason'})
        narrative(details['rollback']['assessment']);narrative(details['rollback']['unavailable_reason']);rows(details['rollback']['steps'])
        for step in details['rollback']['steps']:narrative(step)
    return json_copy(details)

def domain(product):return product.get('detailed',{'profiles':[],'details':[],'adoptions':[]})

def profile(product,change_id):return next((r for r in reversed(domain(product)['profiles']) if r['change_id']==change_id),None)

def latest_detail(product,change_id,stage):return next((r for r in reversed(domain(product)['details']) if r['change_id']==change_id and r['stage']==stage),None)

def creation_method(change,root=None):
    history=change.get('history',[])
    if 'history_base' in change and root is not None:
        from cc_development_history import full_history
        history=full_history(root,change)
    creation=next((r for r in history if r.get('action',{}).get('kind')=='create'),None)
    return creation.get('result',{}).get('method') if creation and type(creation.get('result')) is dict else None

def trusted_legacy(change,root=None):
    from cc_development_compatibility import PRIOR,RETAINED,RETENTION,PRODUCT,DISCUSSION,EVOLUTION
    return creation_method(change,root) in (PRIOR,RETAINED,RETENTION,PRODUCT,DISCUSSION,EVOLUTION)

def applicability(product,change,root=None):
    from cc_product import increment_for
    if not increment_for(product,change['id']):return 'legacy'
    current_profile=profile(product,change['id'])
    if current_profile:
        original=creation_method(change,root)
        if original is not None and current_profile['creation_method']!=original:return 'corrupt'
        return 'detailed'
    return 'legacy' if trusted_legacy(change,root) else 'corrupt'

def make_profile(change,selection,origin,actor,reason,timestamp,prior='absent'):
    row={'change_id':change['id'],'contract':CONTRACT,'origin':origin,'prior_contract':prior,'creation_method':json_copy(change['method']),'selection_revision':selection,'actor':actor,'reason':reason,'timestamp':timestamp}
    row['revision']=digest(row);return row

def add_profile(product,row):product.setdefault('detailed',{'profiles':[],'details':[],'adoptions':[]})['profiles'].append(row)

def checks(root):
    if root is None:return []
    from pathlib import Path
    from cc import _evidence_prepare
    result=[]
    for kind in ('verification','invariants'):
        observed=_evidence_prepare(Path(root.path),kind)
        if observed['issues']:continue
        for row in observed['executable']:
            result.append({'kind':kind,'id':row['id'],'title':row.get('description',row['id']) or row['id']})
    return result

def source_manifest(details):return digest(details['sources'])

def make_detail(product,change,stage,selection,action,timestamp):
    from cc_development_lib.evaluator import latest
    upstream=latest(change,'intent' if stage=='design' else 'design');previous=latest_detail(product,change['id'],'design') if stage=='plan' else None
    row={'schema_version':1,'contract':CONTRACT,'change_id':change['id'],'stage':stage,'profile_revision':profile(product,change['id'])['revision'],'selection_revision':selection,
         'artifact_revision':digest(action['content']),'upstream_artifact_revision':upstream['revision'],'upstream_detail_revision':previous['revision'] if previous else 'absent',
         'source_manifest_digest':source_manifest(action['details']),'details':json_copy(action['details']),'actor':action['actor'],'timestamp':timestamp}
    row['revision']=digest(row);return row

def review_revision(review):
    field='mappings' if 'mappings' in review else 'files'
    payload={k:review[k] for k in ('selection_revision','artifact_revision',field)}
    if 'detail_revision' in review:payload.update(detail_revision=review['detail_revision'],profile_revision=review['profile_revision'])
    return digest(payload)

def detail_binding(row):return {'contract':row['contract'],'profile_revision':row['profile_revision'],'selection_revision':row['selection_revision'],'artifact_revision':row['artifact_revision'],'detail_revision':row['revision'],'upstream_detail_revision':row['upstream_detail_revision'],'source_manifest_digest':row['source_manifest_digest']}


def coverage(root,product,change,stage,content,details,mappings=()):
    from cc_product import increment_for
    from cc_product_documents import source_items,validate_source
    increment=increment_for(product,change['id']);criteria=increment['criteria'] if increment else []
    blockers=[];selected_sources=[];whole_sources=[];conditions=[];task_rows=[];criterion_rows=[]
    selected_source_ids=set();inventory_sources=details['sources'];condition_detail=details
    if increment:
        pinned=next((r for r in product['revisions'] if r['revision']==increment['product_revision']),None)
        accepted_sources=pinned['document']['sources'] if pinned else []
        accepted_by_id={s['source_id']:s for s in accepted_sources}
        assignment=next((r for r in increment.get('changes',[]) if r['change_id']==change['id']),None)
        feature_ids=set(assignment['feature_ids']) if assignment else {increment['feature_id']}
        relevant_targets=feature_ids|{r['id'] for r in criteria}|({pinned['document']['id']} if pinned else set())
        relevant_features=[f for f in pinned['document']['features'] if f['id'] in feature_ids] if pinned else []
        inventory_sources=[s for s in accepted_sources if any(s['source_id'] in f['source_references'] for f in relevant_features) or any(i['selected'] and set(i['targets'])&relevant_targets for i in source_items(s))]
        for source in details['sources']:
            if source['source_id'] not in accepted_by_id or source != accepted_by_id[source['source_id']]:blockers.append('planning_source_scope_changed')
    if stage=='plan':
        design_detail=latest_detail(product,change['id'],'design')
        condition_detail=design_detail['details'] if design_detail else empty_details('design')
    for source in inventory_sources:
        items=source_items(source);selected=[i for i in items if i['selected']];reviewed=[i for i in selected if i['disposition']!='unresolved']
        summary={'source_id':source['source_id'],'total':len(items),'selected':len(selected),'reviewed':len(reviewed),'unresolved':len(selected)-len(reviewed)}
        selected_sources.append(summary);whole_sources.append({**summary,'reviewed':sum(i['disposition']!='unresolved' for i in items),'unresolved':sum(i['disposition']=='unresolved' for i in items)})
        if summary['unresolved']:blockers.append('selected_source_unresolved')
        if root is not None:
            try:validate_source(root.path,source,current=True,reviewed=False)
            except (OSError,ValueError):blockers.append('source_observation_stale')
        selected_source_ids.add(source['source_id'])
        for item in selected:
            matches=[r for r in condition_detail.get('condition_mappings',[]) if {'source_id':source['source_id'],'item_id':item['id']} in r['source_refs']]
            if item['disposition']=='mapped' and set(item['targets'])&{r['id'] for r in criteria} and not matches:blockers.append('planning_source_condition_unmapped')
            conditions.append({'source_id':source['source_id'],'item_id':item['id'],'product_criterion_ids':sorted({r['product_criterion_id'] for r in matches}),'requirement_ids':sorted({i for r in matches for i in r['requirement_ids']}),'criterion_ids':sorted({i for r in matches for i in r['criterion_ids']}),'task_ids':sorted({task['task_id'] for task in details.get('task_details',[]) if set(task['criterion_ids'])&{i for r in matches for i in r['criterion_ids']}}),'disposition':item['disposition'],'reason':item['reason']})
    wanted={r['id'] for r in criteria}
    structured=details['facts']+details['conflicts']+details.get('requirement_details',[])+details.get('task_details',[])
    for fact in structured:
        if not set(fact['provenance']['product_criterion_ids'])<=wanted:blockers.append('planning_product_reference_unknown')
    for fact in details['facts']:
        if not set(fact['provenance']['product_criterion_ids'])<=wanted:blockers.append('planning_product_reference_unknown')
        if not fact['text'].strip():blockers.append('planning_fact_incomplete')
    for conflict in details['conflicts']:
        if conflict['disposition']=='upstream-review-required':blockers.append('planning_scope_amendment_required')
        elif conflict['blocking'] and conflict['disposition']!='resolved':blockers.append('planning_conflict_unresolved')
        elif not conflict['blocking'] and conflict['disposition']=='unresolved' and not conflict['impact'].strip():blockers.append('planning_question_disposition_required')
    if stage=='design':
        spec_by_id={r['id']:r for r in content['criteria']};detail_req={r['requirement_id'] for r in details['requirement_details']}
        if detail_req!={r['id'] for r in content['requirements']}:blockers.append('planning_requirement_detail_missing')
        if any(not r['approach'].strip() for r in details['requirement_details']):blockers.append('planning_requirement_detail_incomplete')
        base_pairs={(r['product_criterion_id'],r['specification_criterion_id']) for r in mappings};detail_pairs=set()
        for mapping in details['condition_mappings']:
            if mapping['product_criterion_id'] not in wanted:blockers.append('planning_product_reference_unknown')
            if mapping['decision']=='unresolved' or not mapping['reason'].strip() or not mapping['criterion_ids']:blockers.append('planning_condition_mapping_incomplete')
            detail_pairs.update((mapping['product_criterion_id'],c) for c in mapping['criterion_ids'])
            if set(mapping['requirement_ids'])!={spec_by_id[c]['requirement_id'] for c in mapping['criterion_ids']}:blockers.append('planning_requirement_mapping_mismatch')
        if detail_pairs!=base_pairs:blockers.append('planning_condition_mapping_mismatch')
        available={(r['kind'],r['id']) for r in checks(root)}
        for spec in content['criteria']:
            if root is not None and any((r['kind'],r['id']) not in available for r in spec['checks']):blockers.append('planning_check_unknown')
        for criterion in criteria:
            local=[r['specification_criterion_id'] for r in mappings if r['product_criterion_id']==criterion['id']]
            reasons=[]
            if criterion['required'] and not local:reasons.append('planning_criterion_uncovered')
            if criterion['required'] and any(not spec_by_id[c]['required'] for c in local):reasons.append('product_required_criterion_weakened')
            blockers.extend(reasons)
            own=next((r for r in increment.get('ownership',[]) if r['criterion_id']==criterion['id']),None)
            criterion_rows.append({'product_criterion_id':criterion['id'],'specification_criterion_ids':local,'required':criterion['required'],'owner_change_id':own['owner_change_id'] if own else change['id'],'contributor_change_ids':own['contributor_change_ids'] if own else [change['id']],'task_ids':[],'obligations':[],'blockers':reasons})
    else:
        from cc_development_lib.evaluator import latest
        design=latest(change,'design');specs={r['id']:r for r in design['content']['criteria']} if design else {}
        task_by_id={r['id']:r for r in content['tasks']};details_by_id={r['task_id']:r for r in details['task_details']}
        if set(task_by_id)!=set(details_by_id):blockers.append('planning_task_detail_missing')
        owners={}
        for scope in content['ownership_scopes']:
            if type(scope) is not dict:blockers.append('planning_scoped_owners_required');continue
            for path in scope['files']:owners[path]=scope['owner']
        if set(owners)!=set(content['files']):blockers.append('planning_file_owner_missing')
        available={(r['kind'],r['id']) for r in checks(root)}
        for row in details['task_details']:
            reasons=[];supporting=not bool(row['criterion_ids'])
            if not row['approach'].strip() or not row['deliverable'].strip():reasons.append('planning_task_incomplete')
            if supporting and (not row['supporting_rationale'].strip() or not row['supports_task_ids'] or not any(details_by_id.get(i,{}).get('criterion_ids') for i in row['supports_task_ids'])):reasons.append('planning_supporting_task_unbound')
            if not row['files'] and not row['file_free_rationale'].strip():reasons.append('planning_file_free_reason_required')
            if any(owners.get(p)!=task_by_id[row['task_id']]['owner'] for p in row['files']):reasons.append('planning_task_owner_mismatch')
            if not set(row['criterion_ids'])<=set(specs):reasons.append('planning_criterion_unknown')
            for ob in row['obligations']:
                if ob['criterion_id'] not in row['criterion_ids']:reasons.append('planning_obligation_criterion_missing')
                if ob['kind']=='human' and not ob['question'].strip():reasons.append('planning_human_question_required')
                elif root is not None and ob['kind']!='human' and (ob['kind'],ob['check_id']) not in available:reasons.append('planning_check_unknown')
            blockers.extend(reasons);task_rows.append({'task_id':row['task_id'],'criterion_ids':row['criterion_ids'],'files':row['files'],'obligations':row['obligations'],'supporting':supporting,'blockers':list(dict.fromkeys(reasons))})
        for criterion_id,spec in specs.items():
            if not spec['required']:continue
            delivery=[task for task in details['task_details'] if criterion_id in task['criterion_ids']]
            if not delivery:blockers.append('planning_criterion_uncovered:'+criterion_id)
            actual={(o['kind'],o['check_id']) for task in delivery for o in task['obligations'] if o['criterion_id']==criterion_id}
            if any((c['kind'],c['id']) not in actual for c in spec['checks']) or spec['proof'] in ('human','both') and ('human','') not in actual:blockers.append('planning_proof_obligation_missing:'+criterion_id)
        spec_review=next((r for r in reversed(product['specifications']) if r['change_id']==change['id']),None)
        for criterion in criteria:
            local=[r['specification_criterion_id'] for r in (spec_review or {}).get('mappings',[]) if r['product_criterion_id']==criterion['id']];reasons=[];deliveries=[t for t in details['task_details'] if set(t['criterion_ids'])&set(local)]
            obligations=[ob for task in deliveries for ob in task['obligations'] if ob['criterion_id'] in local]
            for criterion_id in local:
                spec=specs.get(criterion_id)
                if not spec:reasons.append('planning_criterion_unknown');continue
                covered=any(criterion_id in task['criterion_ids'] for task in deliveries)
                if spec['required'] and not covered:reasons.append('planning_criterion_uncovered')
                actual={(o['kind'],o['check_id']) for o in obligations if o['criterion_id']==criterion_id}
                if spec['required'] and any((c['kind'],c['id']) not in actual for c in spec['checks']):reasons.append('planning_proof_obligation_missing')
                if spec['required'] and spec['proof'] in ('human','both') and ('human','') not in actual:reasons.append('planning_proof_obligation_missing')
            own=next((r for r in increment.get('ownership',[]) if r['criterion_id']==criterion['id']),None)
            blockers.extend(reasons);criterion_rows.append({'product_criterion_id':criterion['id'],'specification_criterion_ids':local,'required':criterion['required'],'owner_change_id':own['owner_change_id'] if own else change['id'],'contributor_change_ids':own['contributor_change_ids'] if own else [change['id']],'task_ids':[t['task_id'] for t in deliveries],'obligations':obligations,'blockers':list(dict.fromkeys(reasons))})
        rollback=details['rollback']
        if not rollback['assessment'].strip() or not (any(s.strip() for s in rollback['steps']) or rollback['unavailable_reason'].strip()):blockers.append('planning_rollback_incomplete')
    return {'selected_sources':selected_sources,'whole_sources':whole_sources,'conditions':conditions,'criteria':criterion_rows,'tasks':task_rows,'blockers':list(dict.fromkeys(blockers))}

def guard_reasons(root,product,change,stage):
    from cc_product import increment_for
    applicable=applicability(product,change,root)
    if applicable=='legacy':return []
    if applicable=='corrupt':return ['planning_profile_missing']
    if stage=='intent':return []
    from cc_development_lib.evaluator import latest
    from cc_product_evolution import selection_applicable
    reasons=[]
    for current_stage in ('design','plan'):
        if current_stage=='plan' and stage=='design':continue
        row=latest_detail(product,change['id'],current_stage);base=latest(change,current_stage);p=profile(product,change['id'])
        review=next((r for r in reversed(product['specifications' if current_stage=='design' else 'plans']) if r['change_id']==change['id']),None)
        if row and review and review.get('detail_revision')==row['revision']:
            reasons.extend(coverage(root,product,change,current_stage,review['content'],row['details'],review.get('mappings',[]))['blockers'])
        if not row:reasons.append('planning_detail_missing');continue
        if not base or row['artifact_revision']!=base['revision'] or row['profile_revision']!=p['revision'] or not selection_applicable(product,change['id'],row['selection_revision']):reasons.append('planning_detail_stale');continue
        upstream=latest(change,'intent' if current_stage=='design' else 'design');up_detail=latest_detail(product,change['id'],'design') if current_stage=='plan' else None
        if not upstream or row['upstream_artifact_revision']!=upstream['revision'] or row['upstream_detail_revision']!=(up_detail['revision'] if up_detail else 'absent'):reasons.append('planning_detail_stale')
        review=next((r for r in reversed(product['specifications' if current_stage=='design' else 'plans']) if r['change_id']==change['id']),None)
        if not review or review.get('detail_revision')!=row['revision']:reasons.append('planning_detail_stale');continue
    return list(dict.fromkeys(reasons))

def approved_detail(root,state,change_id,stage='plan'):
    from cc_development_lib import change_for
    from cc_development_lib.evaluator import accepted,evaluate
    product=state.get('product',{});change=change_for(state,change_id)
    if applicability(product,change,root)=='legacy':return None
    reasons=guard_reasons(root,product,change,stage)
    require(not reasons,'planning_detail_not_current:'+','.join(reasons))
    assessment=evaluate(root,json_copy(change),through_stage=stage,product=product,state=state)[stage]
    prior_decisions=[d for d in change['decisions'] if d['stage']==stage]
    failure='planning_detail_stale' if prior_decisions and prior_decisions[-1]['decision']=='accepted' else 'planning_detail_approval_required'
    require(accepted(change,stage,product) and assessment['accepted'] and assessment['outcome']=='allowed',failure)
    row=latest_detail(product,change_id,stage)
    return {'content':json_copy(row['details']),'binding':detail_binding(row)}

def preview(root,state,change_id,stage):
    require(stage in ('design','plan'),'invalid_stage')
    from cc_development_lib import change_for
    from cc_development_lib.evaluator import latest
    from cc_product import increment_for,template
    product=state.get('product',{});change=change_for(state,change_id);selection=increment_for(product,change_id)
    require(selection is not None,'product_linked_change_required')
    review=next((r for r in reversed(product['specifications' if stage=='design' else 'plans']) if r['change_id']==change_id),None)
    row=latest_detail(product,change_id,stage);upstream=latest(change,'intent' if stage=='design' else 'design');updetail=latest_detail(product,change_id,'design') if stage=='plan' else None
    if review:content=json_copy(review['content']);mappings=json_copy(review.get('mappings',[]));files=json_copy(review.get('files',[]))
    else:
        if upstream:
            snapshot={'changes':state['changes'],'product':__import__('cc_product').view(state,include_planning=False)}
            proposed=template(root.path if root is not None else '.',change_id,stage,snapshot=snapshot);content=proposed['content'];mappings=proposed['mappings'];files=proposed['files']
        else:
            from cc_development_lib.method import FIELDS as BASE
            content={k:('absent' if k.startswith('upstream_') else {} if k=='criterion_check_mapping' else '' if k in ('verification_strategy','rollback_assessment') else []) for k in BASE[stage]};mappings=[];files=[]
    details=json_copy(row['details']) if row else empty_details(stage)
    if row is None:
        pinned=next((r for r in product['revisions'] if r['revision']==selection['product_revision']),None)
        assignment=next((r for r in selection.get('changes',[]) if r['change_id']==change_id),None)
        assigned=set(assignment['feature_ids']) if assignment else {selection['feature_id']}
        relevant=assigned|{r['id'] for r in selection['criteria']}|({pinned['document']['id']} if pinned else set())
        source_features=[f for f in pinned['document']['features'] if f['id'] in assigned] if pinned else []
        from cc_product_documents import source_items
        details['sources']=json_copy([s for s in pinned['document']['sources'] if any(s['source_id'] in f['source_references'] for f in source_features) or any(i['selected'] and set(i['targets'])&relevant for i in source_items(s))]) if pinned else []
    result=coverage(root,product,change,stage,content,details,mappings)
    if len(canonical(details))>128*1024:result['blockers'].append('planning_detail_size_limit')
    return {'schema_version':1,'change_id':change_id,'stage':stage,'profile':json_copy(profile(product,change_id)),'applicability':applicability(product,change,root),'selection_revision':selection['selection_revision'],'upstream_artifact_revision':upstream['revision'] if upstream else 'absent','upstream_detail_revision':updetail['revision'] if updetail else 'absent','content':content,'mappings':mappings,'files':files,'details':details,'detail_revision':row['revision'] if row else 'absent','checks':checks(root),'coverage':result,'blockers':result['blockers']}


def validate_action(action):
    from cc_product import validate_document,validate_action as base_validate
    kind=action['kind'];allowed={'kind',*FIELDS[kind]}
    optional={'planning_review','source_review'} if kind in ('product_specification_v2','product_plan_v2') else set()
    require(allowed<=set(action)<=allowed|optional,'invalid_action_fields')
    from cc_development_lib.schema import text
    text(action['actor'],'actor',limit=160)
    if kind in ('product_specification_v2','product_plan_v2'):
        stage='design' if kind=='product_specification_v2' else 'plan'
        old={k:v for k,v in action.items() if k not in ('details','planning_review','source_review')};old['kind']='product_specification' if stage=='design' else 'product_plan';base_validate(old)
        validate_details(action['details'],stage,action['content'])
        if 'planning_review' in action:
            import cc_planning
            cc_planning.validate_review_token(action)
        if 'source_review' in action:
            exact(action['source_review'],{'path','sha256','base_revision','detail_revision','proposal_digest'})
            from cc_product_documents import export_destination
            export_destination(None,'project_managed',action['source_review']['path'])
            for field in ('sha256','base_revision','detail_revision','proposal_digest'):revision(action['source_review'][field])
            require(action['source_review']['proposal_digest']==digest({k:v for k,v in action.items() if k not in ('source_review','planning_review')}),'product_source_review_stale')
    elif kind=='product_planning_upgrade':
        identifier(action['change_id']);revision(action['selection_revision']);require(action['selection_revision']!='absent','planning_selection_stale');text(action['reason'],'reason')
    else:
        validate_document(action['document']);revision(action['base_revision'])
        require(action['sources']==action['document']['sources'],'invalid_source_manifest')
        rows(action['resolutions']);seen=set();sources={(s['source_id'],i['id']) for s in action['sources'] for i in __import__('cc_product_documents').source_items(s)}
        targets={action['document']['id']}|{r['id'] for r in action['document']['chapters']}|{r['id'] for r in action['document']['features']}|{r['id'] for f in action['document']['features'] for r in f['criteria']}
        for row in action['resolutions']:
            exact(row,{'id','source_refs','conflicting_targets','decision','rationale'});identifier(row['id']);require(row['id'] not in seen,'invalid_planning_reference');seen.add(row['id'])
            source_refs(row['source_refs'],sources);ids(row['conflicting_targets']);require(set(row['conflicting_targets'])<=targets,'invalid_planning_reference');narrative(row['rationale']);require(row['decision'] in ('unresolved','resolved','upstream-review-required'),'invalid_planning_detail');require(row['decision']!='resolved' or bool(row['rationale'].strip()),'invalid_planning_detail')
    return json_copy(action)

def write_event(product,action,result,timestamp,profiles=()):
    event={'action':action,'result':result,'previous':product['events'][-1]['digest'] if product['events'] else 'absent','timestamp':timestamp}
    if profiles:event['profiles']=json_copy(profiles)
    event['digest']=digest(event);product['events'].append(event)

def apply(root,state,action,timestamp):
    from cc_product import current,empty,increment_for,draft_published,scope_reasons
    from cc_development_lib import change_for,apply as development_apply
    from cc_development_lib.evaluator import latest
    product=state.setdefault('product',empty());kind=action['kind']
    if 'planning_review' in action:__import__('cc_planning').guard_action(root,action)
    if kind=='product_adoption_save':
        row=current(product);require(action['base_revision']==(row['revision'] if row else 'absent'),'product_revision_conflict')
        require(not row or row['document']['id']==action['document']['id'],'product_identity_changed')
        prior=domain(product)['adoptions'][-1] if domain(product)['adoptions'] else None
        if prior:
            required_resolution_ids={r['id'] for r in prior['resolutions'] if r['decision']!='resolved'}
            require(required_resolution_ids<={r['id'] for r in action['resolutions']},'product_adoption_conflict_review_required')
        from cc_product_documents import validate_source
        for source in action['sources']:validate_source(root.path,source,current=True,reviewed=False)
        record={'previous':action['base_revision'],'document':json_copy(action['document']),'actor':action['actor'],'timestamp':timestamp};record['revision']=digest(record)
        adoption={'document_revision':record['revision'],'sources':json_copy(action['sources']),'resolutions':json_copy(action['resolutions']),'actor':action['actor'],'timestamp':timestamp};adoption['revision']=digest(adoption)
        product['revisions'].append(record);product.setdefault('detailed',{'profiles':[],'details':[],'adoptions':[]})['adoptions'].append(adoption)
        result={'document':record,'adoption':adoption}
    else:
        change=change_for(state,action['change_id']);selection=increment_for(product,change['id']);require(selection is not None,'product_linked_change_required')
        require(not change['closed'],'change_closed');require(not scope_reasons(product,selection),'planning_selection_stale')
        if kind=='product_planning_upgrade':
            require(action['selection_revision']==selection['selection_revision'],'planning_selection_stale')
            require(not profile(product,change['id']),'planning_profile_already_enabled')
            require(trusted_legacy(change,root),'planning_predecessor_unproven')
            from cc_development_compatibility import inspect_effects
            from cc_development_lib.method import IDENTITY
            from cc_development_bundle import check_pending
            check_pending(root)
            require(not inspect_effects(root.path)['blockingEffects'],'planning_upgrade_blocked')
            result=make_profile({**change,'method':creation_method(change,root)},selection['selection_revision'],'explicit_upgrade',action['actor'],action['reason'],timestamp,prior='legacy');add_profile(product,result)
        else:
            require(applicability(product,change,root)=='detailed','planning_detail_required')
            stage='design' if kind=='product_specification_v2' else 'plan';upstream=latest(change,'intent' if stage=='design' else 'design');upfield='upstream_intent_revision' if stage=='design' else 'upstream_specification_revision'
            require(upstream and action['content'][upfield]==upstream['revision'],'product_draft_upstream_stale')
            if 'source_review' in action:
                import hashlib
                from cc_evidence_inputs import Budget
                reviewed=action['source_review'];raw,info=root.read(reviewed['path'],Budget(per_file=128*1024,total=128*1024));current_base=latest(change,stage);pending_review=next((r for r in reversed(product['specifications' if stage=='design' else 'plans']) if r['change_id']==change['id']),None);current_detail=latest_detail(product,change['id'],stage)
                require(info.st_nlink==1 and hashlib.sha256(raw).hexdigest()==reviewed['sha256'] and reviewed['base_revision']==(pending_review['artifact_revision'] if pending_review else current_base['revision'] if current_base else 'absent') and reviewed['detail_revision']==(current_detail['revision'] if current_detail else 'absent'),'product_source_review_stale')
                from cc_product_documents import stage_import_preview
                parsed=stage_import_preview(root.path,change['id'],stage,reviewed['path'],state=state)
                require(not parsed['blockers'] and parsed['action']=={k:v for k,v in action.items() if k!='planning_review'},'product_source_review_stale')
            current_detail=latest_detail(product,change['id'],stage)
            previous_review=next((r for r in reversed(product['specifications' if stage=='design' else 'plans']) if r['change_id']==change['id']),None)
            field='mappings' if stage=='design' else 'files'
            from cc_product_evolution import selection_applicable
            if current_detail and previous_review and previous_review['content']==action['content'] and previous_review[field]==action[field] and current_detail['details']==action['details'] and current_detail['profile_revision']==profile(product,change['id'])['revision'] and current_detail['upstream_artifact_revision']==upstream['revision'] and selection_applicable(product,change['id'],current_detail['selection_revision']):
                upstream_detail=latest_detail(product,change['id'],'design') if stage=='plan' else None
                if current_detail['upstream_detail_revision']==(upstream_detail['revision'] if upstream_detail else 'absent'):return state
            detail=make_detail(product,change,stage,selection['selection_revision'],action,timestamp)
            report=coverage(None,product,change,stage,action['content'],action['details'],action.get('mappings',[]))
            published=draft_published(stage,action['content']) and not report['blockers']
            if published:
                state=development_apply(root,state,{'kind':'artifact','stage':stage,'change_id':change['id'],'content':action['content'],'actor':action['actor']},timestamp=timestamp)
            product=state['product'];product.setdefault('detailed',{'profiles':[],'details':[],'adoptions':[]})['details'].append(detail)
            field='mappings' if stage=='design' else 'files'
            result={'change_id':change['id'],'artifact_revision':digest(action['content']),'selection_revision':selection['selection_revision'],'actor':action['actor'],'timestamp':timestamp,'content':json_copy(action['content']),'published':published,field:json_copy(action[field]),'detail_revision':detail['revision'],'profile_revision':detail['profile_revision'],'detail':detail}
            product['specifications' if stage=='design' else 'plans'].append(result)
    write_event(product,action,result,timestamp);return state


def views(root,state):
    from cc_product import increment_for
    result=[]
    for change in state.get('changes',[]):
        if increment_for(state.get('product',{}),change['id']) is not None:
            for stage in ('design','plan'):result.append(preview(root,state,change['id'],stage))
    return result


def validate_profiles(product,event,*,creation_methods=None):
    from cc_product import increment_for
    rows(event['profiles'],64);action=event['action'];kind=action['kind']
    require(kind in ('product_increment','product_select','product_link_incident'),'invalid_planning_profile_history')
    expected_ids={action['change_id']} if kind!='product_select' else {r['change_id'] for r in action['changes']}
    require({r['change_id'] for r in event['profiles']}<=expected_ids,'invalid_planning_profile_history')
    if kind!='product_link_incident':require({r['change_id'] for r in event['profiles']}==expected_ids,'invalid_planning_profile_history')
    for row in event['profiles']:
        require(type(row) is dict and set(row)=={'change_id','contract','origin','prior_contract','creation_method','selection_revision','actor','reason','timestamp','revision'},'invalid_planning_profile_history')
        selection_revision=event['result']['selection_revision'] if kind=='product_increment' else increment_for(product,row['change_id'])['selection_revision']
        require(type(creation_methods) is dict and row['change_id'] in creation_methods,'invalid_planning_profile_origin')
        expected=make_profile({'id':row['change_id'],'method':creation_methods[row['change_id']]},selection_revision,kind,action['actor'],'New Product change' if kind=='product_increment' else 'New Product linkage',event['timestamp'])
        require(row==expected and profile(product,row['change_id']) is None,'invalid_planning_profile_history');add_profile(product,row)

def validate_history(product,event,*,creation_methods=None):
    from cc_product import current,increment_for,draft_published
    action=validate_action(event['action']);result=event['result'];kind=action['kind'];timestamp=event['timestamp']
    if kind=='product_adoption_save':
        row=current(product);require(action['base_revision']==(row['revision'] if row else 'absent'),'invalid_product_history_revision')
        record={'previous':action['base_revision'],'document':action['document'],'actor':action['actor'],'timestamp':timestamp};record['revision']=digest(record)
        prior=domain(product)['adoptions'][-1] if domain(product)['adoptions'] else None
        if prior:require({r['id'] for r in prior['resolutions'] if r['decision']!='resolved'}<={r['id'] for r in action['resolutions']},'product_adoption_conflict_review_required')
        adoption={'document_revision':record['revision'],'sources':action['sources'],'resolutions':action['resolutions'],'actor':action['actor'],'timestamp':timestamp};adoption['revision']=digest(adoption)
        require(result=={'document':record,'adoption':adoption},'invalid_product_adoption_history')
        product['revisions'].append(record);product.setdefault('detailed',{'profiles':[],'details':[],'adoptions':[]})['adoptions'].append(adoption)
    elif kind=='product_planning_upgrade':
        selection=increment_for(product,action['change_id']);require(selection and selection['selection_revision']==action['selection_revision'] and profile(product,action['change_id']) is None,'invalid_planning_profile_history')
        require(type(result) is dict,'invalid_planning_profile_history')
        from cc_development_compatibility import PRIOR,RETAINED,RETENTION,PRODUCT,DISCUSSION,EVOLUTION
        require(result.get('creation_method') in (PRIOR,RETAINED,RETENTION,PRODUCT,DISCUSSION,EVOLUTION),'invalid_planning_profile_history')
        require(type(creation_methods) is dict and result['creation_method']==creation_methods.get(action['change_id']),'invalid_planning_profile_origin')
        expected=make_profile({'id':action['change_id'],'method':result['creation_method']},selection['selection_revision'],'explicit_upgrade',action['actor'],action['reason'],timestamp,'legacy');require(result==expected,'invalid_planning_profile_history');add_profile(product,result)
    else:
        selection=increment_for(product,action['change_id']);p=profile(product,action['change_id']);require(selection and p,'invalid_planning_detail_history')
        stage='design' if kind=='product_specification_v2' else 'plan';field='mappings' if stage=='design' else 'files';upfield='upstream_intent_revision' if stage=='design' else 'upstream_specification_revision';updetail=latest_detail(product,action['change_id'],'design') if stage=='plan' else None
        envelope={'schema_version':1,'contract':CONTRACT,'change_id':action['change_id'],'stage':stage,'profile_revision':p['revision'],'selection_revision':selection['selection_revision'],'artifact_revision':digest(action['content']),'upstream_artifact_revision':action['content'][upfield],'upstream_detail_revision':updetail['revision'] if updetail else 'absent','source_manifest_digest':source_manifest(action['details']),'details':action['details'],'actor':action['actor'],'timestamp':timestamp};envelope['revision']=digest(envelope)
        design=next((r for r in reversed(product['specifications']) if r['change_id']==action['change_id']),None)
        change={'id':action['change_id'],'artifacts':{'design':[{'content':design['content'],'revision':design['artifact_revision']}] if design else []}}
        published=draft_published(stage,action['content']) and not coverage(None,product,change,stage,action['content'],action['details'],action.get('mappings',[]))['blockers']
        expected={'change_id':action['change_id'],'artifact_revision':digest(action['content']),'selection_revision':selection['selection_revision'],'actor':action['actor'],'timestamp':timestamp,'content':action['content'],'published':published,field:action[field],'detail_revision':envelope['revision'],'profile_revision':envelope['profile_revision'],'detail':envelope}
        require(result==expected,'invalid_planning_detail_history')
        if field=='mappings':
            pairs={(r['product_criterion_id'],r['specification_criterion_id']) for r in action[field]};require(len(pairs)==len(action[field]),'duplicate_product_mapping')
            require({r['product_criterion_id'] for r in action[field]}<={r['id'] for r in selection['criteria']} and {r['specification_criterion_id'] for r in action[field]}<={r['id'] for r in action['content']['criteria']},'invalid_product_mapping_reference')
        else:require(len({r['path'] for r in action[field]})==len(action[field]),'duplicate_product_file_observation')
        product.setdefault('detailed',{'profiles':[],'details':[],'adoptions':[]})['details'].append(envelope);product['specifications' if stage=='design' else 'plans'].append(result)


def adoption_reasons(product):
    from cc_product import current
    row=current(product)
    adopted=domain(product)['adoptions'][-1] if row and domain(product)['adoptions'] else None
    if not adopted:return []
    return list(dict.fromkeys('product_adoption_conflict_unresolved' if r['decision']=='unresolved' else 'planning_scope_amendment_required' for r in adopted['resolutions'] if r['decision']!='resolved'))
