"""Revision-bound product scope and dependency authority in the Core transaction.

No supplied passing booleans are accepted. Dependency and integration evidence
is read from the current canonical owners each time an actual gate is reached.
"""
from cc_development_lib.method import digest
from cc_development_lib.schema import DevelopmentError, identifier, revision, text, json_copy

FIELDS = {
    'product_select': ('product_revision','increment_id','title','features','changes','criteria','dependencies','actor'),
    'product_scope': ('increment_id','selection_revision','product_revision','decision','reason','actor'),
    'product_retire': ('feature_id','product_revision','successor_feature_id','reason','actor'),
    'product_link_incident': ('change_id','product_revision','feature_id','reason','actor'),
    'product_integration': ('increment_id','selection_revision','criterion_id','candidates','actor'),
}
KEYS=('selections','scope_decisions','retirements','incident_links','integration_reviews')
RESULTS=('specification_accepted','verified','released')
GATES=('design','plan','build','verify','review_release','maintain')

def require(ok, code):
    if not ok: raise DevelopmentError(code)

def empty(): return {key:[] for key in KEYS}

def exact(value, keys, code): require(type(value) is dict and set(value)==set(keys),code)

def ids(value, code):
    require(type(value) is list and 0<len(value)<=64,code)
    for item in value:identifier(item)
    require(len(set(value))==len(value),code)

def selection_fields(action):
    ids(action['features'],'invalid_product_selection_features')
    require(type(action['changes']) is list and 0<len(action['changes'])<=64,'invalid_product_selection_changes')
    changes=set()
    for row in action['changes']:
        exact(row,('change_id','title','owner','risk','feature_ids'),'invalid_product_selection_change')
        identifier(row['change_id']);text(row['title'],'title');text(row['owner'],'owner',limit=160)
        require(row['risk'] in ('routine','standard','critical'),'invalid_risk')
        ids(row['feature_ids'],'invalid_product_selection_features')
        require(set(row['feature_ids'])<=set(action['features']) and row['change_id'] not in changes,'invalid_product_selection_change')
        changes.add(row['change_id'])
    require(set(action['features'])=={f for c in action['changes'] for f in c['feature_ids']},'product_selected_feature_unassigned')
    require(type(action['criteria']) is list and 0<len(action['criteria'])<=256,'invalid_product_ownership')
    seen=set()
    for row in action['criteria']:
        exact(row,('criterion_id','owner_change_id','contributor_change_ids','integration_check'),'invalid_product_ownership')
        identifier(row['criterion_id']);identifier(row['owner_change_id']);ids(row['contributor_change_ids'],'invalid_product_contributors')
        require(row['criterion_id'] not in seen and row['owner_change_id'] in row['contributor_change_ids'] and set(row['contributor_change_ids'])<=changes,'invalid_product_ownership')
        seen.add(row['criterion_id'])
        check=row['integration_check']
        require(len(row['contributor_change_ids'])==1 or check is not None,'product_integration_check_required')
        if check is not None:
            exact(check,('kind','id'),'invalid_product_integration_check');identifier(check['id'])
            require(check['kind'] in ('verification','invariants'),'invalid_product_integration_check')
    require(type(action['dependencies']) is list and len(action['dependencies'])<=256,'invalid_product_dependencies')
    seen=set()
    for row in action['dependencies']:
        exact(row,('consumer_change_id','target_change_id','target_feature_id','target_feature_revision','target_selection_revision','required_result','gate'),'invalid_product_dependency')
        for key in ('consumer_change_id','target_change_id','target_feature_id'):identifier(row[key])
        revision(row['target_feature_revision']);require(row['target_feature_revision']!='absent','invalid_product_dependency_revision')
        if row['target_selection_revision']!='same_increment':
            revision(row['target_selection_revision']);require(row['target_selection_revision']!='absent','invalid_product_dependency_revision')
        require(row['consumer_change_id'] in changes and row['required_result'] in RESULTS and row['gate'] in GATES,'invalid_product_dependency')
        token=digest(row);require(token not in seen,'duplicate_product_dependency');seen.add(token)

def validate_action(action):
    kind=action['kind'];allowed={'kind',*FIELDS[kind]}
    optional={'predecessor_selection_revision'} if kind=='product_select' else {'replacement'} if kind=='product_scope' else set()
    require(allowed<=set(action)<=allowed|optional,'invalid_action_fields');text(action['actor'],'actor',limit=160)
    if 'product_revision' in action:revision(action['product_revision']);require(action['product_revision']!='absent','product_document_missing')
    for key in ('increment_id','feature_id','change_id','criterion_id'):
        if key in action:identifier(action[key])
    if 'reason' in action:text(action['reason'],'reason')
    if 'selection_revision' in action:revision(action['selection_revision']);require(action['selection_revision']!='absent','product_selection_missing')
    if kind=='product_select':
        text(action['title'],'title');selection_fields(action)
        if 'predecessor_selection_revision' in action:revision(action['predecessor_selection_revision'])
    if kind=='product_scope':
        require(action['decision'] in ('keep','adopt','cancel','defer'),'invalid_product_scope_decision')
        if 'replacement' in action:
            exact(action['replacement'],('features','changes','criteria','dependencies'),'invalid_product_scope_replacement');selection_fields(action['replacement'])
            require(action['decision']=='adopt','invalid_product_scope_replacement')
    if kind=='product_retire' and action['successor_feature_id'] is not None:identifier(action['successor_feature_id'])
    if kind=='product_integration':
        require(type(action['candidates']) is list and 0<len(action['candidates'])<=64,'invalid_product_integration_candidates')
        seen=set()
        for row in action['candidates']:
            exact(row,('change_id','candidate_identity'),'invalid_product_integration_candidates');identifier(row['change_id']);revision(row['candidate_identity'])
            require(row['candidate_identity']!='absent' and row['change_id'] not in seen,'invalid_product_integration_candidates');seen.add(row['change_id'])
    return json_copy(action)

def common_semantics(feature):
    return digest({k:v for k,v in feature.items() if k not in ('title','priority','criteria')})

def legacy_selection(increment,action,document):
    feature=next(f for f in document['features'] if f['id']==increment['feature_id'])
    return {**increment,'previous':'absent','features':[{'feature_id':increment['feature_id'],'feature_revision':increment['feature_revision'],'feature_semantics':increment['feature_semantics'],'common_semantics':common_semantics(feature),'title':feature['title'],'problem':feature['problem'],'outcome':feature['outcome'],'criteria':increment['criteria'],'users':increment['users'],'risks':increment['risks']}],
            'changes':[{'change_id':increment['change_id'],'title':increment['title'],'owner':action['owner'],'risk':action['risk'],'feature_ids':[increment['feature_id']]}],
            'ownership':[{'criterion_id':c['id'],'owner_change_id':increment['change_id'],'contributor_change_ids':[increment['change_id']],'integration_check':None} for c in increment['criteria']], 'dependencies':[]}

def selections(product):
    actions={event['action']['increment_id']:event['action'] for event in product.get('events',[]) if event['action']['kind']=='product_increment'}
    return [legacy_selection(i,actions[i['id']],next(r['document'] for r in product['revisions'] if r['revision']==i['product_revision'])) for i in product.get('increments',[])]+product.get('evolution',empty())['selections']

def active_selections(product):
    rows={}
    for row in selections(product):rows[row['id']]=row
    return list(rows.values())

def active_selection(product, increment_id):
    return next((r for r in active_selections(product) if r['id']==increment_id),None)

def active_feature_selections(product,feature_ids):
    """Current lineage heads per feature; delivered predecessors stay indexed."""
    all_rows=selections(product);by_revision={s['selection_revision']:s for s in all_rows};heads=active_selections(product);result=[]
    for row in heads:
        relevant=set(feature_ids)&{f['feature_id'] for f in row['features']}
        for successor in heads:
            if successor['id']==row['id']:continue
            overlap=relevant&{f['feature_id'] for f in successor['features']}
            previous=successor['previous'];visited=set()
            while overlap and previous!='absent' and previous in by_revision:
                require(previous not in visited,'product_selection_lineage_cycle');visited.add(previous)
                if previous==row['selection_revision']:
                    relevant-=overlap;break
                previous=by_revision[previous]['previous']
        if relevant:result.append(row)
    return result

def for_change(product, change_id):
    row=next((r for r in active_selections(product) if change_id in {c['change_id'] for c in r['changes']}),None)
    if row is None:return None
    wanted={c['criterion_id'] for c in row['ownership'] if change_id in c['contributor_change_ids']}
    assigned=next(c for c in row['changes'] if c['change_id']==change_id)
    feature=next(f for f in row['features'] if f['feature_id'] in assigned['feature_ids'])
    return {**row,'change_id':change_id,'feature_id':feature['feature_id'],'feature_revision':feature['feature_revision'],'feature_semantics':feature['feature_semantics'],
            'criteria':[c for c in row['criteria'] if c['id'] in wanted]}

def selection_applicable(product,change_id,old_revision):
    current=for_change(product,change_id)
    old=next((s for s in selections(product) if s['selection_revision']==old_revision and change_id in {c['change_id'] for c in s['changes']}),None)
    if current is None or old is None:return False
    def semantics(selection):
        assignment=next(c for c in selection['changes'] if c['change_id']==change_id)
        own=[r for r in selection['ownership'] if change_id in r['contributor_change_ids']]
        wanted={r['criterion_id'] for r in own}
        return {'features':[{k:f[k] for k in ('feature_id','common_semantics','users','risks')} for f in selection['features'] if f['feature_id'] in assignment['feature_ids']],
                'criteria':[c for c in selection['criteria'] if c['id'] in wanted],
                'ownership':own,'constraints':selection['constraints'],'non_goals':selection['non_goals'],'dependencies':[d for d in selection['dependencies'] if d['consumer_change_id']==change_id]}
    return semantics(old)==semantics(current)

def scope_report(product, selection, changes=()):
    from cc_product import current, feature_semantics
    row=current(product)
    if row is None:return None
    doc=row['document'];current_features={f['id']:f for f in doc['features']}
    changed=[];criterion_ids=[];new_features=[];old_ids=set();new_ids=set();common_changed=set()
    shared=doc['constraints']!=selection['constraints'] or doc['non_goals']!=selection['non_goals']
    stricter=shared
    for old in selection['features']:
        old_ids|={c['id'] for c in old['criteria']}
        new=current_features.get(old['feature_id'])
        if new:
            new_features.append({'feature_id':new['id'],'feature_revision':digest(new),'criteria':new['criteria'],'users':new['users'] or doc['users'],'risks':new['risks'],'title':new['title'],'problem':new['problem'],'outcome':new['outcome']})
            new_ids|={c['id'] for c in new['criteria']}
        common=new is None or common_semantics(new)!=old['common_semantics'] or (new['users'] or doc['users'])!=old['users']
        different=common or feature_semantics(new)!=old['feature_semantics']
        if different or shared:
            changed.append(old['feature_id'])
            if common or shared:
                common_changed.add(old['feature_id']);criterion_ids.extend(c['id'] for c in old['criteria'])
            else:
                new_criteria={c['id']:c for c in new['criteria']}
                criterion_ids.extend(c['id'] for c in old['criteria'] if new_criteria.get(c['id'])!=c)
        # Any risk change and any new/modified required condition is a policy
        # floor. Text classification never grants a keep-old exception.
        if new:
            old_criteria={c['id']:c for c in old['criteria']}
            stricter|=new['risks']!=old['risks'] or any(c['required'] and old_criteria.get(c['id'])!=c for c in new['criteria'])
    impacted=[c['change_id'] for c in selection['changes'] if set(c['feature_ids']) & common_changed or any(o['criterion_id'] in criterion_ids and c['change_id'] in o['contributor_change_ids'] for o in selection['ownership'])]
    if new_ids-old_ids:
        impacted=list(dict.fromkeys(impacted+[c['change_id'] for c in selection['changes'] if set(c['feature_ids']) & set(changed)]))
    decisions=[d for d in product.get('evolution',empty())['scope_decisions'] if d['increment_id']==selection['id'] and d['selection_revision']==selection['selection_revision'] and d['product_revision']==row['revision']]
    resolved=bool(decisions and decisions[-1]['decision'] in ('keep','adopt'))
    return {'increment_id':selection['id'],'selection_revision':selection['selection_revision'],'product_revision':row['revision'],'change_ids':impacted,'criterion_ids':sorted(set(criterion_ids)),
            'stricter_policy':bool(stricter),'required':bool(changed) and not resolved,'closed_change_ids':[c['id'] for c in changes if c['closed'] and c['id'] in impacted],
            'old_features':selection['features'],'new_features':new_features,'added_criteria':sorted(new_ids-old_ids),'retired_criteria':sorted(old_ids-new_ids),
            'reason':'Requirements changed; review the impact' if changed else 'Selected scope is unchanged'}

def selection_record(product, action, previous='absent'):
    from cc_product import current,feature_semantics
    row=current(product);doc=row['document'];features=[]
    for feature_id in action['features']:
        feature=next((f for f in doc['features'] if f['id']==feature_id),None);require(feature is not None,'product_feature_not_found')
        features.append({'feature_id':feature_id,'feature_revision':digest(feature),'feature_semantics':feature_semantics(feature),'common_semantics':common_semantics(feature),'title':feature['title'],'problem':feature['problem'],'outcome':feature['outcome'],'criteria':feature['criteria'],'users':feature['users'] or doc['users'],'risks':feature['risks']})
        require(not any(r['feature_id']==feature_id for r in product.get('evolution',empty())['retirements']),'product_feature_retired')
        for declared in feature['dependencies']:
            require(type(declared) is dict and set(declared)<= {'feature_id','feature_revision','required_result','gate'} and 'feature_id' in declared,'product_feature_dependency_unknown')
            consumers={c['change_id'] for c in action['changes'] if feature_id in c['feature_ids']}
            require(all(any(dep['consumer_change_id']==consumer and dep['target_feature_id']==declared['feature_id'] and all(dep[{'feature_revision':'target_feature_revision'}.get(k,k)]==v for k,v in declared.items() if k!='feature_id') for dep in action['dependencies']) for consumer in consumers),'product_feature_dependencies_unreviewed')
    criteria=[c for f in features for c in f['criteria']];owned={r['criterion_id'] for r in action['criteria']}
    require(owned=={c['id'] for c in criteria},'product_criterion_ownership_incomplete')
    for own in action['criteria']:
        feature_id=next(f['feature_id'] for f in features if own['criterion_id'] in {c['id'] for c in f['criteria']})
        require(all(feature_id in c['feature_ids'] for c in action['changes'] if c['change_id'] in own['contributor_change_ids']),'product_contributor_feature_mismatch')
    first=features[0]
    result={'id':action['increment_id'],'title':action['title'],'product_revision':row['revision'],'previous':previous,'features':features,'changes':action['changes'],'ownership':action['criteria'],'dependencies':action['dependencies'],
            'feature_id':first['feature_id'],'feature_revision':first['feature_revision'],'feature_semantics':first['feature_semantics'],'change_id':action['changes'][0]['change_id'],
            'criteria':criteria,'constraints':doc['constraints'],'non_goals':doc['non_goals'],'users':sorted({u for f in features for u in f['users']}),'risks':[r for f in features for r in f['risks']]}
    result['selection_revision']=digest(result)
    return result

def graph_valid(product, proposed):
    rows=[r for r in active_selections(product) if r['id']!=proposed['id']]+[proposed]
    by_change={c['change_id']:r for r in rows for c in r['changes']};edges={c:[] for c in by_change}
    for row in rows:
        for dep in row['dependencies']:
            target=by_change.get(dep['target_change_id']);require(target is not None,'product_dependency_target_missing')
            selection_revision=row['selection_revision'] if dep['target_selection_revision']=='same_increment' else dep['target_selection_revision']
            if row is proposed:require(target['selection_revision']==selection_revision,'product_dependency_selection_stale')
            feature=next((f for f in target['features'] if f['feature_id']==dep['target_feature_id']),None)
            if row is proposed:require(feature and feature['feature_revision']==dep['target_feature_revision'],'product_dependency_feature_revision_missing')
            if row is proposed:require(dep['target_feature_id'] in next(c['feature_ids'] for c in target['changes'] if c['change_id']==dep['target_change_id']),'product_dependency_target_feature_mismatch')
            if dep['target_selection_revision']=='same_increment':require(target['id']==row['id'],'product_dependency_internal_target_missing')
            edges[dep['consumer_change_id']].append(dep['target_change_id'])
    visiting=set();done=set()
    def visit(node):
        require(node not in visiting,'product_dependency_cycle')
        if node in done:return
        visiting.add(node)
        for child in edges[node]:visit(child)
        visiting.remove(node);done.add(node)
    for node in edges:visit(node)
    # A feature DAG can still create an impossible stage dependency when final
    # integration needs a consumer's verification before its predecessor release.
    stages=('intent',)+GATES
    stage_edges={(c,s):[] for c in by_change for s in stages}
    for change_id in by_change:
        for i,s in enumerate(stages):
            if i:stage_edges[(change_id,s)].append((change_id,stages[i-1]))
    for row in rows:
        for dep in row['dependencies']:
            required={'specification_accepted':'design','verified':'verify','released':'review_release'}[dep['required_result']]
            stage_edges[(dep['consumer_change_id'],dep['gate'])].append((dep['target_change_id'],required))
        shared={c for own in row['ownership'] if own['integration_check'] is not None for c in own['contributor_change_ids']}
        for child in row['changes']:
            for contributor in shared:stage_edges[(child['change_id'],'review_release')].append((contributor,'verify'))
    visiting=set();done=set()
    def stage_visit(node):
        require(node not in visiting,'product_dependency_integration_deadlock')
        if node in done:return
        visiting.add(node)
        for child in stage_edges[node]:stage_visit(child)
        visiting.remove(node);done.add(node)
    for node in stage_edges:stage_visit(node)

def guard(root,change,stage,*,state=None):
    """Used at real worker/job/handoff/apply paths, never supplied UI outcomes."""
    from cc_evidence_inputs import SafeRoot
    from cc_product import gate_reasons
    def assess(reader):
        canonical=state if state is not None else _state(reader)
        product=canonical.get('product',{})
        authoritative=next((c for c in canonical['changes'] if c['id']==change['id']),None)
        observed=change
        if for_change(product,change['id']) is not None:
            from cc_development_lib.evaluator import upstream
            require(authoritative and upstream(change,'build')==upstream(authoritative,'build') and change['method']==authoritative['method'],'product_execution_upstream_stale')
            observed=authoritative
        reasons=gate_reasons(reader,observed,stage,product,state=canonical)
        require(not reasons,'product_gate_blocked:'+','.join(reasons))
        return observed
    if isinstance(root,SafeRoot):return assess(root)
    with SafeRoot(root) as reader:return assess(reader)

def _state(root):
    from cc_development_store import read_state
    return read_state(root)[0] or {'changes':[]}

def dependency_assessment(root,change,stage,product,state,selection,dep,stack=()):
    """Assess one declared edge without assigning aggregate failure to peers."""
    reasons=[]
    if stage not in GATES or dep['consumer_change_id']!=change['id'] or GATES.index(stage)<GATES.index(dep['gate']):
        return {'state':'allowed','reasons':[]}
    target=next((c for c in state['changes'] if c['id']==dep['target_change_id']),None)
    selected=for_change(product,dep['target_change_id'])
    if target is None or selected is None:return {'state':'blocked','reasons':['product_dependency_target_missing']}
    expected=selection['selection_revision'] if dep['target_selection_revision']=='same_increment' else dep['target_selection_revision']
    feature=next((f for f in selected['features'] if f['feature_id']==dep['target_feature_id']),None)
    if selected['selection_revision']!=expected or not feature or feature['feature_revision']!=dep['target_feature_revision']:
        return {'state':'blocked','reasons':['product_dependency_target_stale']}
    if target['id'] in stack+(change['id'],):return {'state':'blocked','reasons':['product_dependency_cycle']}
    from cc_development_lib.evaluator import accepted,evaluate
    required_stage={'specification_accepted':'design','verified':'verify','released':'review_release'}[dep['required_result']]
    gates=evaluate(root,json_copy(target),through_stage=required_stage,product=product,state=state,dependency_stack=stack+(change['id'],))
    if not accepted(target,required_stage) or gates[required_stage]['outcome']!='allowed':
        reasons.append('product_dependency_'+dep['required_result']+'_required')
    elif dep['required_result']=='released':
        from cc_development_adapters import assess_release
        release=target['releases'][-1] if target['releases'] else None
        if not release or release['status']!='observed' or release['candidate_identity']!=(target.get('candidate') or {}).get('digest') or assess_release(root.path,target,release)['state']!='allowed':
            reasons.append('product_dependency_released_required')
    return {'state':'blocked' if reasons else 'allowed','reasons':reasons}


def dependency_reasons(root,change,stage,product,state=None,stack=()):
    selection=for_change(product,change['id'])
    if selection is None:return []
    state=state if state is not None else _state(root)
    return list(dict.fromkeys(reason for dep in selection['dependencies']
        for reason in dependency_assessment(root,change,stage,product,state,selection,dep,stack)['reasons']))


def combined(root,selection,ownership,state):
    from cc_development_lib.evaluator import capture_candidate,upstream
    candidates=[];files={}
    for change_id in ownership['contributor_change_ids']:
        change=next((c for c in state['changes'] if c['id']==change_id),None)
        require(change and change.get('candidate'),'product_integration_candidate_missing')
        candidate=change['candidate'];current=capture_candidate(root,change,[f['path'] for f in candidate['files']])
        require(current['digest']==candidate['digest'] and candidate['upstream']==upstream(change,'build'),'product_integration_candidate_stale')
        candidates.append({'change_id':change_id,'candidate_identity':candidate['digest']})
        for item in candidate['files']:
            require(item['path'] not in files or files[item['path']]==item,'product_integration_conflicting_files');files[item['path']]=item
    require(len(files)<=256,'product_integration_file_limit')
    return sorted(candidates,key=lambda r:r['change_id']),digest({'selection_revision':selection['selection_revision'],'criterion_id':ownership['criterion_id'],'candidates':sorted(candidates,key=lambda r:r['change_id']),'files':sorted(files.values(),key=lambda r:r['path'])}),sorted(files)

def integration_observation(root,check,candidate_paths):
    import cc
    prepared=cc._evidence_prepare(root.path,check['kind']);history=cc._evidence_history(root.path,check['kind'],prepared);assessment=history['assessment'];latest=history.get('latest')
    require(assessment['currentRequiredPass'],'product_integration_current_proof_required')
    entries=(latest or {}).get('suites' if check['kind']=='verification' else 'invariants',[])
    require(check['id'] in prepared['required'] and any(r['id']==check['id'] and r['status']=='passed' for r in entries),'product_integration_check_unproved')
    # The final receipt must actually cover the combined files. A fresh
    # candidate on an ignored/generated path cannot borrow a receipt whose
    # inventory never included that path. Reuse the exact inventory owner,
    # comparing its content identity rather than exposing per-file metadata.
    from pathlib import Path
    import cc_evidence
    from cc_evidence_inputs import capture_inputs,runner_context,execution_context
    from cc_development_adapters import read_json
    # History exposes a privacy-filtered summary, not the raw receipt. Read its
    # canonical path through the validated owner accessor and re-assess that
    # exact record against current inputs, contracts and execution context.
    receipt,_=read_json(root.path,latest['path'])
    require(receipt.get('id')==latest['id'] and not cc_evidence.validate_receipt(receipt,check['kind'],entries=prepared['executable'],contracts=prepared['contracts']),'product_integration_receipt_changed')
    current=capture_inputs(root.path,prepared['policy'])
    context={'runner':runner_context(Path(cc.__file__).parent),'executionContext':execution_context(root.path,prepared['policy'],prepared['executable'])}
    assessed=cc_evidence.assess_receipt(receipt,current,prepared['contracts'],context,cc_evidence.make_plan(prepared['executable'],prepared['required']),entries=prepared['executable'])
    require(assessed['currentRequiredPass'],'product_integration_current_proof_required')
    policy=json_copy(prepared['policy']);policy['extraPaths']=sorted(set(policy['extraPaths'])|set(candidate_paths))
    covered=capture_inputs(root.path,policy);observed=receipt['inputs']['after']
    require(covered['complete'] and covered['contentDigest']==observed['contentDigest'] and covered['fileCount']==observed['fileCount'],'product_integration_candidate_not_covered')
    return digest(receipt)

def integration_assessment(root,selection,own,product,state):
    """Criterion-specific final combined proof, preserving existing owner rules."""
    if own['integration_check'] is None:return {'state':'not_required','reasons':[]}
    from cc_product_progress_observation import current
    observer=current();key=('integration',selection['selection_revision'],own['criterion_id'])
    if observer is not None and key in observer.memo:return observer.memo[key]
    reviews=[r for r in product.get('evolution',empty())['integration_reviews'] if r['selection_revision']==selection['selection_revision'] and r['criterion_id']==own['criterion_id']]
    reasons=[]
    if not reviews:reasons.append('product_final_integration_proof_required')
    else:
        try:
            from cc_development_lib.evaluator import accepted,evaluate
            for change_id in own['contributor_change_ids']:
                child=next((c for c in state['changes'] if c['id']==change_id),None)
                require(child and accepted(child,'verify') and evaluate(root,json_copy(child),through_stage='verify',product=product,state=state)['verify']['outcome']=='allowed','product_integration_contribution_unverified')
            candidates,identity,paths=combined(root,selection,own,state);observation=integration_observation(root,own['integration_check'],paths)
            require(reviews[-1]['candidates']==candidates and reviews[-1]['combined_identity']==identity and reviews[-1]['observation_revision']==observation,'product_final_integration_proof_stale')
        except (OSError,ValueError) as error:reasons.append(getattr(error,'code','product_final_integration_proof_unreadable'))
    result={'state':'blocked' if reasons else 'allowed','reasons':list(dict.fromkeys(reasons))}
    if observer is not None:observer.memo[key]=result
    return result


def integration_reasons(root,change,stage,product,state=None):
    if stage not in ('review_release','maintain'):return []
    selection=for_change(product,change['id'])
    if selection is None:return []
    state=state if state is not None else _state(root)
    return list(dict.fromkeys(reason for own in selection['ownership']
        for reason in integration_assessment(root,selection,own,product,state)['reasons']))


def scope_reasons(product,selection,change_id):
    retired={r['feature_id'] for r in product.get('evolution',empty())['retirements']}
    assigned=set(next(c['feature_ids'] for c in selection['changes'] if c['change_id']==change_id))
    if retired & assigned:return ['product_selected_feature_retired']
    decisions=[d for d in product.get('evolution',empty())['scope_decisions'] if d['increment_id']==selection['id']]
    if decisions and decisions[-1]['decision'] in ('cancel','defer'):return ['product_scope_'+decisions[-1]['decision']]
    report=scope_report(product,selection)
    return ['product_requirements_changed_review_required'] if report and report['required'] and change_id in report['change_ids'] else []

def _intent(product,selection,change_id):
    from cc_product import current
    doc=current(product)['document'];assignment=next(c for c in selection['changes'] if c['change_id']==change_id);features=[f for f in doc['features'] if f['id'] in assignment['feature_ids']]
    wanted={c['criterion_id'] for c in selection['ownership'] if change_id in c['contributor_change_ids']}
    return {'problem':'\n'.join(f['problem'] for f in features),'outcome':'\n'.join(f['outcome'] for f in features),'affected_users_and_systems':sorted({u for f in features for u in (f['users'] or doc['users'])}),
            'constraints':doc['constraints'],'non_goals':doc['non_goals'],'open_questions':[q for f in features for q in f['questions']],'success_measures':[c['text'] for f in features for c in f['criteria'] if c['id'] in wanted]}

def apply(root,state,action,timestamp):
    from cc_product import current,accepted_revision,complete
    from cc_development_lib import apply as development_apply,change_for
    product=state['product'];evolution=product.setdefault('evolution',empty());kind=action['kind'];row=current(product)
    if 'product_revision' in action:
        require(row and action['product_revision']==row['revision']==accepted_revision(product),'accepted_product_revision_required');complete(root,row['document'])
    if kind=='product_select':
        require(state['enabled'],'method_not_active');require(active_selection(product,action['increment_id']) is None,'product_increment_scope_exists')
        predecessor=action.get('predecessor_selection_revision','absent');previous=next((s for s in selections(product) if s['selection_revision']==predecessor),None)
        require(predecessor=='absent' or previous is not None,'product_predecessor_selection_missing')
        overlaps=active_feature_selections(product,action['features'])
        for other in overlaps:
            require(previous is not None and other['selection_revision']==previous['selection_revision'],'product_increment_scope_exists')
            require(all(change_for(state,c['change_id'])['closed'] for c in other['changes']),'product_predecessor_change_open')
            from cc_development_lib.method import PROFILES
            floor=max(PROFILES.index(change_for(state,c['change_id'])['risk']) for c in other['changes'])
            require(all(PROFILES.index(c['risk'])>=floor for c in action['changes']),'product_scope_risk_floor')
        result=selection_record(product,action,predecessor);graph_valid(product,result)
        for child in action['changes']:
            state=development_apply(root,state,{'kind':'create',**{k:child[k] for k in ('change_id','title','owner','risk')}},timestamp=timestamp)
            state=development_apply(root,state,{'kind':'artifact','change_id':child['change_id'],'stage':'intent','actor':action['actor'],'content':_intent(product,result,child['change_id'])},timestamp=timestamp)
        product=state['product'];product.setdefault('evolution',empty())['selections'].append(result)
    elif kind=='product_scope':
        selection=active_selection(product,action['increment_id']);require(selection and selection['selection_revision']==action['selection_revision'],'product_selection_revision_conflict')
        previous_decisions=[d for d in evolution['scope_decisions'] if d['increment_id']==selection['id']]
        require(not previous_decisions or previous_decisions[-1]['decision']!='cancel','product_scope_cancelled')
        report=scope_report(product,selection,state['changes']);require(not report['closed_change_ids'],'product_closed_scope_requires_linked_request')
        require(action['decision']!='keep' or not report['stricter_policy'],'product_stricter_policy_cannot_keep')
        result={k:action[k] for k in FIELDS[kind]}|{'timestamp':timestamp,'impact':report,'adopted_selection':None}
        if action['decision']=='adopt':
            require(not report['added_criteria'] and not report['retired_criteria'] or 'replacement' in action,'product_scope_replacement_required')
            replacement=action.get('replacement',{'features':[f['feature_id'] for f in selection['features']],'changes':selection['changes'],'criteria':selection['ownership'],'dependencies':selection['dependencies']})
            require({c['change_id'] for c in replacement['changes']}=={c['change_id'] for c in selection['changes']},'product_scope_change_assignment_mismatch')
            from cc_development_lib.method import PROFILES
            for child in replacement['changes']:
                require(PROFILES.index(child['risk'])>=PROFILES.index(change_for(state,child['change_id'])['risk']),'product_scope_risk_floor')
                require(child['risk']==change_for(state,child['change_id'])['risk'],'product_scope_policy_change_requires_new_request')
            new=selection_record(product,{**replacement,'increment_id':selection['id'],'title':selection['title']},selection['selection_revision']);graph_valid(product,new)
            # Review changed assignments/dependencies even with identical text.
            projected=json_copy(product);projected.setdefault('evolution',empty())['selections'].append(new)
            affected=list(dict.fromkeys(report['change_ids']+[c['change_id'] for c in selection['changes'] if not selection_applicable(projected,c['change_id'],selection['selection_revision'])]))
            for change_id in affected:
                require(not change_for(state,change_id)['closed'],'product_closed_scope_requires_linked_request')
                state=development_apply(root,state,{'kind':'artifact','change_id':change_id,'stage':'intent','actor':action['actor'],'content':_intent(product,new,change_id)},timestamp=timestamp)
            product=state['product'];evolution=product.setdefault('evolution',empty());evolution['selections'].append(new);result['adopted_selection']=new
        evolution['scope_decisions'].append(result)
    elif kind=='product_retire':
        require(any(f['id']==action['feature_id'] for revision_row in product['revisions'] for f in revision_row['document']['features']),'product_feature_not_found')
        require(not any(r['feature_id']==action['feature_id'] for r in evolution['retirements']),'product_feature_already_retired')
        successor=action['successor_feature_id'];require(successor is None or successor!=action['feature_id'] and any(f['id']==successor for f in row['document']['features']),'product_successor_missing')
        result={k:action[k] for k in FIELDS[kind]}|{'timestamp':timestamp};evolution['retirements'].append(result)
    elif kind=='product_link_incident':
        child=change_for(state,action['change_id']);require(child.get('origin') and child['origin']['kind']=='incident_followup','product_incident_followup_required')
        require(not child['closed'],'change_closed');require(for_change(product,child['id']) is None,'product_incident_already_linked')
        require(not any(r['change_id']==child['id'] for r in evolution['incident_links']),'product_incident_already_linked')
        require(active_selection(product,'incident-'+child['id']) is None,'product_increment_scope_exists')
        feature=next((f for f in row['document']['features'] if f['id']==action['feature_id']),None);require(feature is not None,'product_feature_not_found')
        selection_action={'increment_id':'incident-'+child['id'],'title':child['title'],'features':[feature['id']],'changes':[{'change_id':child['id'],'title':child['title'],'owner':child['owner'],'risk':child['risk'],'feature_ids':[feature['id']]}],
                          'criteria':[{'criterion_id':c['id'],'owner_change_id':child['id'],'contributor_change_ids':[child['id']],'integration_check':None} for c in feature['criteria']],'dependencies':[]}
        selection=selection_record(product,selection_action);graph_valid(product,selection)
        prior_decisions=[digest(d) for d in child['decisions']]
        from cc_development_lib.evaluator import latest
        existing=latest(child,'intent')['content'];intent=_intent(product,selection,child['id'])
        for key in ('problem','outcome'):intent[key]=existing[key]+'\n\n'+intent[key]
        for key in ('affected_users_and_systems','constraints','non_goals','open_questions','success_measures'):
            intent[key]=json_copy(existing[key])+[v for v in intent[key] if v not in existing[key]]
        state=development_apply(root,state,{'kind':'artifact','change_id':child['id'],'stage':'intent','actor':action['actor'],'content':intent},timestamp=timestamp)
        product=state['product'];evolution=product.setdefault('evolution',empty())
        result={k:action[k] for k in FIELDS[kind]}|{'timestamp':timestamp,'origin':json_copy(child['origin']),'selection':selection,'prior_decision_revisions':prior_decisions};evolution['incident_links'].append(result);evolution['selections'].append(selection)
    else:
        selection=active_selection(product,action['increment_id']);require(selection and selection['selection_revision']==action['selection_revision'],'product_selection_revision_conflict')
        own=next((r for r in selection['ownership'] if r['criterion_id']==action['criterion_id']),None);require(own and own['integration_check'],'product_integration_check_required')
        candidates,identity,paths=combined(root,selection,own,state);require(sorted(action['candidates'],key=lambda r:r['change_id'])==candidates,'product_integration_candidate_mismatch')
        from cc_development_lib.evaluator import accepted,evaluate
        for change_id in own['contributor_change_ids']:
            child=change_for(state,change_id);require(accepted(child,'verify') and evaluate(root,json_copy(child),through_stage='verify',product=product,state=state)['verify']['outcome']=='allowed','product_integration_contribution_unverified')
            require(action['actor']!=(child.get('candidate') or {}).get('actor'),'independent_actor_required')
        observation=integration_observation(root,own['integration_check'],paths)
        result={k:action[k] for k in FIELDS[kind]}|{'timestamp':timestamp,'combined_identity':identity,'observation_revision':observation};evolution['integration_reviews'].append(result)
    product=state['product'];profiles=[]
    if kind in ('product_select','product_link_incident'):
        from cc_product_planning import make_profile,add_profile,trusted_legacy
        ids_to_profile=[c['change_id'] for c in action['changes']] if kind=='product_select' else [action['change_id']]
        selection=result if kind=='product_select' else result['selection']
        for change_id in ids_to_profile:
            child=change_for(state,change_id)
            if kind=='product_link_incident' and trusted_legacy(child,root):continue
            profile=make_profile(child,selection['selection_revision'],kind,action['actor'],'New Product linkage',timestamp)
            add_profile(product,profile);profiles.append(profile)
    event={'action':action,'result':result,'previous':product['events'][-1]['digest'] if product['events'] else 'absent','timestamp':timestamp}
    if profiles:event['profiles']=profiles
    event['digest']=digest(event);product['events'].append(event)
    return state

def validate_history(projected,event):
    action=validate_action(event['action']);result=event['result'];kind=action['kind'];evolution=projected.setdefault('evolution',empty())
    require(type(result) is dict,'invalid_product_evolution_result')
    if 'product_revision' in action:
        from cc_product import current,accepted_revision
        row=current(projected);require(row and action['product_revision']==row['revision']==accepted_revision(projected),'invalid_product_evolution_origin')
    if kind=='product_select':
        require(active_selection(projected,action['increment_id']) is None,'invalid_product_selection')
        predecessor=action.get('predecessor_selection_revision','absent')
        require(predecessor=='absent' or any(s['selection_revision']==predecessor for s in selections(projected)),'invalid_product_selection_predecessor')
        old_changes={c['change_id'] for s in selections(projected) for c in s['changes']}
        require(not old_changes & {c['change_id'] for c in action['changes']},'invalid_product_selection')
        expected=selection_record(projected,action,action.get('predecessor_selection_revision','absent'));require(expected==result,'invalid_product_selection');graph_valid(projected,result);evolution['selections'].append(result)
    elif kind=='product_scope':
        selection=active_selection(projected,action['increment_id']);require(selection and selection['selection_revision']==action['selection_revision'],'invalid_product_scope_origin')
        expected_keys=set(FIELDS[kind])|{'timestamp','impact','adopted_selection'};require(set(result)==expected_keys and all(result[k]==action[k] for k in FIELDS[kind]) and result['timestamp']==event['timestamp'],'invalid_product_scope_history')
        require(result['impact']==scope_report(projected,selection, [{'id':c,'closed':True} for c in result['impact']['closed_change_ids']]),'invalid_product_scope_impact')
        if action['decision']=='adopt':
            replacement=action.get('replacement',{'features':[f['feature_id'] for f in selection['features']],'changes':selection['changes'],'criteria':selection['ownership'],'dependencies':selection['dependencies']})
            expected=selection_record(projected,{**replacement,'increment_id':selection['id'],'title':selection['title']},selection['selection_revision']);require(result['adopted_selection']==expected,'invalid_product_adoption');graph_valid(projected,expected);evolution['selections'].append(expected)
        else:require(result['adopted_selection'] is None,'invalid_product_adoption')
        require(action['decision']!='keep' or not result['impact']['stricter_policy'],'product_stricter_policy_cannot_keep');evolution['scope_decisions'].append(result)
    elif kind=='product_retire':
        require(any(f['id']==action['feature_id'] for revision_row in projected['revisions'] for f in revision_row['document']['features']),'invalid_product_retirement')
        require(result=={k:action[k] for k in FIELDS[kind]}|{'timestamp':event['timestamp']},'invalid_product_retirement');evolution['retirements'].append(result)
    elif kind=='product_link_incident':
        require(set(result)==set(FIELDS[kind])|{'timestamp','origin','selection','prior_decision_revisions'} and all(result[k]==action[k] for k in FIELDS[kind]) and result['timestamp']==event['timestamp'],'invalid_product_incident_link');require(result['origin']['kind']=='incident_followup','invalid_product_incident_link')
        require(type(result['prior_decision_revisions']) is list and len(result['prior_decision_revisions'])<=2048,'invalid_product_incident_link')
        for value in result['prior_decision_revisions']:revision(value);require(value!='absent','invalid_product_incident_link')
        row=result['selection'];require(row['id']=='incident-'+action['change_id'] and active_selection(projected,row['id']) is None and row['selection_revision']==digest({k:v for k,v in row.items() if k!='selection_revision'}),'invalid_product_incident_link');evolution['incident_links'].append(result);evolution['selections'].append(row)
    else:
        require(set(result)==set(FIELDS[kind])|{'timestamp','combined_identity','observation_revision'} and all(result[k]==action[k] for k in FIELDS[kind]) and result['timestamp']==event['timestamp'],'invalid_product_integration_review')
        revision(result['combined_identity']);revision(result['observation_revision']);evolution['integration_reviews'].append(result)

def view(state,*,root=None):
    from cc_product import current
    product=state.get('product',{});result=json_copy(product.get('evolution',empty()));result['impacts']=[scope_report(product,s,state['changes']) for s in active_selections(product)];result['dependency_status']=[]
    row=current(product);result['feature_revisions']=[{'feature_id':f['id'],'revision':digest(f)} for f in row['document']['features']] if row else []
    result['scope_states']=[];result['integration_status']=[]
    for selection in active_selections(product):
        decisions=[d for d in result['scope_decisions'] if d['increment_id']==selection['id']]
        result['scope_states'].append({'increment_id':selection['id'],'selection_revision':selection['selection_revision'],'state':decisions[-1]['decision'] if decisions else 'selected'})
        for dep in selection['dependencies']:
            child=next((c for c in state['changes'] if c['id']==dep['consumer_change_id']),None)
            reasons=dependency_reasons(root,child,dep['gate'],product,state) if root is not None and child else ['product_dependency_current_evaluation_required']
            result['dependency_status'].append({**dep,'increment_id':selection['id'],'state':'blocked' if reasons else 'allowed','reasons':reasons})
        if any(o['integration_check'] is not None for o in selection['ownership']):
            child=next((c for c in state['changes'] if c['id']==selection['changes'][0]['change_id']),None)
            reasons=integration_reasons(root,child,'review_release',product,state) if root is not None and child else ['product_final_integration_proof_required']
            result['integration_status'].append({'increment_id':selection['id'],'selection_revision':selection['selection_revision'],'state':'blocked' if reasons else 'allowed','reasons':reasons})
    return result
