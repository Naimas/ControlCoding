"""Bounded manual product planning owned by the Development transaction."""
from cc_development_lib.method import digest, canonical
from cc_development_lib.schema import DevelopmentError, identifier, revision, text, json_copy, artifact

FIELDS = {
    'product_save': ('document', 'base_revision', 'actor'),
    'product_decide': ('product_revision', 'decision', 'actor', 'reason'),
    'product_increment': ('product_revision', 'feature_id', 'increment_id', 'change_id', 'title', 'owner', 'risk', 'actor'),
    'product_specification': ('change_id', 'content', 'mappings', 'actor'),
    'product_plan': ('change_id', 'content', 'files', 'actor'),
}
DOCUMENT = {'id','title','language','goals','users','constraints','non_goals','questions','chapters','features','sources','notes'}
from cc_product_evolution import FIELDS as EVOLUTION_FIELDS
FIELDS.update(EVOLUTION_FIELDS)
from cc_product_planning import FIELDS as DETAIL_FIELDS
FIELDS.update(DETAIL_FIELDS)
FEATURE = {'id','title','problem','outcome','users','priority','criteria','dependencies','risks','questions','source_references'}

def require(ok, code):
    if not ok: raise DevelopmentError(code)

def strings(value, name, limit=256):
    require(type(value) is list and len(value) <= limit, 'invalid_product_' + name)
    for item in value: text(item, name)

def narrative(value, name):
    require(type(value) is str and len(value) <= 16384 and '\x00' not in value, 'invalid_product_' + name)

def records(value, name, maximum):
    require(type(value) is list and len(value) <= maximum, 'product_' + name + '_limit')
    seen=set()
    for row in value:
        require(type(row) is dict and 'id' in row, 'invalid_product_' + name)
        identifier(row['id']); require(row['id'] not in seen, 'duplicate_product_id'); seen.add(row['id'])
    return seen

def validate_document(doc):
    require(type(doc) is dict and set(doc)==DOCUMENT, 'invalid_product_document_fields')
    require(len(canonical(doc)) <= 256*1024, 'product_document_size_limit')
    identifier(doc['id'])
    for key in ('title','language','notes'): narrative(doc[key], key)
    for key in ('goals','users','constraints','non_goals','questions'): strings(doc[key],key,64)
    features=records(doc['features'],'features',64)
    chapters=records(doc['chapters'],'chapters',24)
    require(not chapters & features and doc['id'] not in chapters|features,'ambiguous_product_id')
    criteria=set()
    for chapter in doc['chapters']:
        require(set(chapter)=={'id','title','content','feature_ids'}, 'invalid_product_chapter')
        narrative(chapter['title'],'chapter_title'); narrative(chapter['content'],'chapter_content')
        strings(chapter['feature_ids'],'feature_ids',64)
        require(len(set(chapter['feature_ids']))==len(chapter['feature_ids']) and set(chapter['feature_ids']) <= features, 'product_feature_reference_missing')
    for feature in doc['features']:
        require(set(feature)==FEATURE,'invalid_product_feature')
        for key in ('title','problem','outcome'): narrative(feature[key],key)
        require(feature['priority'] in ('low','normal','high','critical'),'invalid_product_priority')
        for key in ('users','risks','questions','source_references'): strings(feature[key],key,64)
        require(type(feature['dependencies']) is list and len(feature['dependencies'])<=64,'invalid_product_dependencies')
        ids=records(feature['criteria'],'criteria',64)
        require(not criteria & ids and not ids & (features|chapters|{doc['id']}),'duplicate_product_criterion'); criteria |= ids
        for row in feature['criteria']:
            require(set(row)=={'id','text','required'} and type(row['required']) is bool,'invalid_product_criterion')
            narrative(row['text'],'criterion')
    require(type(doc['sources']) is list and len(doc['sources'])<=64,'product_source_limit')
    from cc_product_documents import validate_source
    source_ids=set()
    for source in doc['sources']:
        validate_source(None,source,current=False,reviewed=False)
        require(source['source_id'] not in source_ids,'duplicate_product_source'); source_ids.add(source['source_id'])
    for feature in doc['features']:
        require(set(feature['source_references'])<=source_ids,'product_source_reference_missing')
    require(len(doc['questions'])+sum(len(f['questions']) for f in doc['features'])<=64,'product_question_limit')
    return json_copy(doc)

def validate_action(action):
    kind=action['kind']
    if kind in DETAIL_FIELDS:
        from cc_product_planning import validate_action as detail_action
        return detail_action(action)
    if kind in EVOLUTION_FIELDS:
        from cc_product_evolution import validate_action as evolution_action
        return evolution_action(action)
    allowed={'kind',*FIELDS[kind]}
    optional={'source_review'} if kind=='product_save' else set()
    if kind in ('product_save','product_specification','product_plan'):optional.add('planning_review')
    require(allowed <= set(action) <= allowed|optional,'invalid_action_fields')
    if 'planning_review' in action:
        import cc_planning
        review=action['planning_review'];cc_planning.exact(review,cc_planning.TOKEN_FIELDS,'planning_review_invalid')
        require(type(review['schema_version']) is int and review['schema_version']==1,'planning_review_invalid')
        identifier(review['session_id']);identifier(review['request_id'])
        for field in ('session_revision','proposal_digest','configuration_revision','action_digest'):revision(review[field]);require(review[field]!='absent','planning_review_invalid')
        require(review['action_digest']==digest({k:v for k,v in action.items() if k!='planning_review'}),'planning_review_action_changed')
    text(action['actor'],'actor',limit=160)
    if kind=='product_save':
        revision(action['base_revision']); validate_document(action['document'])
        if 'source_review' in action:
            review=action['source_review']
            require(type(review) is dict and set(review)=={'path','sha256','base_revision','proposal_digest'},'invalid_product_source_review')
            from cc_product_documents import export_destination
            export_destination(None,'project_managed',review['path'])
            for key in ('sha256','base_revision','proposal_digest'):revision(review[key])
            require(review['sha256']!='absent' and review['base_revision']==action['base_revision'] and review['proposal_digest']==digest(action['document']),'product_source_review_stale')
    if kind in ('product_decide','product_increment'):
        revision(action['product_revision']); require(action['product_revision']!='absent','product_document_missing')
    if kind=='product_decide':
        require(action['decision'] in ('accepted','rejected','deferred'),'invalid_product_decision'); text(action['reason'],'reason')
    if kind=='product_increment':
        for key in ('feature_id','increment_id','change_id'): identifier(action[key])
        text(action['title'],'title'); text(action['owner'],'owner',limit=160)
        require(action['risk'] in ('routine','standard','critical'),'invalid_risk')
    if kind in ('product_specification','product_plan'):
        identifier(action['change_id'])
        from cc_development_lib.method import FIELDS as ARTIFACT_FIELDS
        stage='design' if kind=='product_specification' else 'plan'
        require(type(action['content']) is dict and set(action['content'])==set(ARTIFACT_FIELDS[stage]),'invalid_artifact_fields')
        require(all(type(v) in (str,list,dict,bool) for v in action['content'].values()),'invalid_artifact_content')
        from cc_development_lib.schema import source_path
        if stage=='plan':
            require(type(action['content']['files']) is list and len(action['content']['files'])<=64,'invalid_files')
            for path in action['content']['files']:source_path(path)
        draft_published(stage,action['content'])
        rows=action['mappings' if kind=='product_specification' else 'files']
        require(type(rows) is list and len(rows)<=256,'invalid_product_mapping')
        for row in rows:
            if kind=='product_specification':
                require(type(row) is dict and set(row)=={'product_criterion_id','specification_criterion_id','decision','reason'},'invalid_product_mapping')
                identifier(row['product_criterion_id']); identifier(row['specification_criterion_id'])
                require(row['decision']=='equivalent','product_scope_amendment_unsupported'); narrative(row['reason'],'mapping_reason')
            else:
                from cc_development_lib.schema import source_path
                require(type(row) is dict and set(row)=={'path','kind','sha256'} and row['kind'] in ('existing','new'),'invalid_product_file_observation')
                source_path(row['path']); revision(row['sha256'])
                require((row['sha256']=='absent') == (row['kind']=='new'),'invalid_product_file_observation')
    return json_copy(action)

def empty():
    return {'revisions':[], 'decisions':[], 'increments':[], 'specifications':[], 'plans':[], 'events':[]}

def current(product):
    return product['revisions'][-1] if product.get('revisions') else None

def accepted_revision(product):
    row=current(product)
    decisions=[d for d in product['decisions'] if row and d['product_revision']==row['revision']]
    from cc_product_planning import adoption_reasons
    return row['revision'] if decisions and decisions[-1]['decision']=='accepted' and not adoption_reasons(product) else 'absent'

def complete(root,doc):
    for key in ('title','language'): text(doc[key],key)
    require(bool(doc['goals']) and bool(doc['users']) and bool(doc['features']),'product_document_incomplete')
    for feature in doc['features']:
        for key in ('title','problem','outcome'): text(feature[key],key)
        require(bool(feature['criteria']) and all(row['text'].strip() for row in feature['criteria']),'product_feature_incomplete')
    from cc_product_documents import validate_source
    targets={doc['id']}|{r['id'] for r in doc['chapters']}|{r['id'] for r in doc['features']}|{r['id'] for f in doc['features'] for r in f['criteria']}
    for source in doc['sources']:
        validate_source(root.path,source,current=True,reviewed=True)
        for section in __import__('cc_product_documents').source_items(source):
            if section['selected']:
                require(section['disposition']!='unresolved','product_source_section_unresolved')
                if section['disposition']=='mapped': require(bool(section['targets']) and set(section['targets'])<=targets,'product_source_target_missing')
                if section['disposition']=='excluded': text(section['reason'],'source_exclusion_reason')

def increment_for(product,change_id):
    from cc_product_evolution import for_change
    return for_change(product,change_id)

def decision_binding(product,change,stage):
    increment=increment_for(product or {},change['id'])
    if not increment or stage not in ('intent','design','plan'):return None
    binding={'selection_revision':increment['selection_revision'],'review_revision':'absent'}
    if stage!='intent':
        key='specifications' if stage=='design' else 'plans'
        review=next((r for r in reversed(product[key]) if r['change_id']==change['id']),None)
        if review:
            field='mappings' if stage=='design' else 'files'
            from cc_product_planning import review_revision
            binding['review_revision']=review_revision(review)
    return binding

def validate_change(product,change):
    increment=increment_for(product or {},change['id'])
    if not increment:return
    from cc_product_planning import applicability,profile,creation_method
    require(applicability(product,change)!='corrupt' or 'history_base' in change,'planning_profile_missing')
    current_profile=profile(product,change['id'])
    if current_profile and current_profile['origin']!='explicit_upgrade':
        origin=creation_method(change)
        require(origin is None and 'history_base' in change or current_profile['creation_method']==origin,'invalid_planning_profile_origin')
    for decision in change['decisions']:
        if decision['stage'] not in ('intent','design','plan'):continue
        binding=decision.get('product_binding')
        links=[r for r in (product or {}).get('evolution',{}).get('incident_links',[]) if r['change_id']==change['id']]
        if binding is None and any(digest(decision) in r['prior_decision_revisions'] for r in links):continue
        from cc_product_evolution import selections
        require(type(binding) is dict and set(binding)=={'selection_revision','review_revision'} and any(s['selection_revision']==binding['selection_revision'] and change['id'] in {c['change_id'] for c in s['changes']} for s in selections(product or {})),'invalid_product_decision_binding')
        revision(binding['review_revision'])
        if decision['stage']=='intent':require(binding['review_revision']=='absent','invalid_product_decision_binding')
        else:
            key='specifications' if decision['stage']=='design' else 'plans';field='mappings' if key=='specifications' else 'files'
            reviews=[r for r in product[key] if r['change_id']==change['id'] and r['artifact_revision']==decision['artifact_revision']]
            require(any(binding['review_revision']==__import__('cc_product_planning').review_revision(r) for r in reviews),'invalid_product_decision_binding')

def feature_semantics(feature):
    content={key:value for key,value in feature.items() if key not in ('title','priority')}
    content['criteria']=sorted(content['criteria'],key=lambda row:row['id'])
    return digest(content)

def scope_reasons(product,increment):
    from cc_product_evolution import scope_reasons as evolution_scope
    result=evolution_scope(product,increment,increment['change_id'])
    return result+(['product_selected_scope_changed_review_unsupported'] if 'product_requirements_changed_review_required' in result else [])

def draft_published(stage,content):
    try:artifact(stage,content);return True
    except DevelopmentError as error:
        incomplete={'missing_file_scope','missing_ownership_scopes','missing_tasks','missing_requirements','missing_criteria','missing_check_ids','invalid_verification_strategy','invalid_rollback_assessment','invalid_requirement','invalid_criterion','invalid_task_title','invalid_task_owner','invalid_scope_owner'}
        if error.code not in incomplete:raise
        # Incomplete values may remain empty; supplied malformed structures
        # must still pass the unchanged artifact schema after filling blanks.
        probe=json_copy(content)
        if stage=='plan':
            for key in ('verification_strategy','rollback_assessment'):
                if probe[key]=='':probe[key]='Unresolved draft choice'
            if not probe['files']:probe['files']=['draft-placeholder.txt']
            if not probe['ownership_scopes']:probe['ownership_scopes']=['Unassigned draft owner']
            if not probe['tasks']:probe['tasks']=[{'id':'DraftTask','title':'Unresolved task','owner':'Unassigned','depends_on':[]}]
            for task in probe['tasks']:
                if type(task) is dict:
                    for key in ('title','owner'):
                        if type(task.get(key)) is str and not task[key].strip():task[key]='Unresolved draft choice'
            for scope in probe['ownership_scopes']:
                if type(scope) is dict and type(scope.get('owner')) is str and not scope['owner'].strip():scope['owner']='Unassigned'
        else:
            if not probe['requirements']:probe['requirements']=[{'id':'DraftRequirement','text':'Unresolved requirement'}]
            if not probe['criteria']:probe['criteria']=[{'id':'DraftCriterion','requirement_id':probe['requirements'][0]['id'],'text':'Unresolved behavior','required':False,'proof':'human','checks':[]}]
            for criterion in probe['criteria']:
                if type(criterion) is dict and type(criterion.get('text')) is str and not criterion['text'].strip():criterion['text']='Unresolved behavior'
                if criterion.get('proof') in ('automated','both') and not criterion.get('checks'):
                    criterion['checks']=[{'kind':'verification','id':'DraftCheck'}]
            for requirement in probe['requirements']:
                if type(requirement) is dict and type(requirement.get('text')) is str and not requirement['text'].strip():requirement['text']='Unresolved requirement'
        artifact(stage,probe)
        return False

def gate_reasons(root,change,stage,product=None,*,state=None,dependency_stack=()):
    if product is None:
        from cc_development_store import read_state
        state,_,_=read_state(root); product=(state or {}).get('product',empty())
    increment=increment_for(product,change['id'])
    if not increment:return []
    reasons=scope_reasons(product,increment)
    from cc_product_planning import guard_reasons
    reasons += guard_reasons(root,product,change,stage)
    from cc_product_evolution import dependency_reasons,integration_reasons,selection_applicable
    reasons+=dependency_reasons(root,change,stage,product,state,dependency_stack)
    reasons+=integration_reasons(root,change,stage,product,state)
    pinned=next((r for r in product['revisions'] if r['revision']==increment['product_revision']),None)
    if pinned:
        from cc_product_documents import validate_source
        selected_ids=set(next(c['feature_ids'] for c in increment['changes'] if c['change_id']==change['id']))
        features=[f for f in pinned['document']['features'] if f['id'] in selected_ids]
        relevant_targets=selected_ids|{pinned['document']['id']}|{r['id'] for r in increment['criteria']}
        relevant_targets|={c['id'] for c in pinned['document']['chapters'] if selected_ids & set(c['feature_ids'])}
        for source in pinned['document']['sources']:
            if any(source['source_id'] in f['source_references'] for f in features) or any(s['selected'] and set(s['targets']) & relevant_targets for s in __import__('cc_product_documents').source_items(source)):
                try:validate_source(root.path,source,current=True,reviewed=True)
                except (OSError,ValueError):reasons.append('product_selected_source_changed')
    from cc_development_lib.evaluator import latest
    if stage in ('intent','design','plan'):
        decisions=[d for d in change['decisions'] if d['stage']==stage]
        if decisions and decisions[-1]['decision']=='accepted':
            binding=decisions[-1].get('product_binding',{})
            if not selection_applicable(product,change['id'],binding.get('selection_revision')):
                reasons.append('product_review_binding_changed')
            elif stage!='intent':
                key='specifications' if stage=='design' else 'plans';field='mappings' if stage=='design' else 'files'
                review=next((r for r in reversed(product[key]) if r['change_id']==change['id']),None)
                if not review or binding.get('review_revision')!=__import__('cc_product_planning').review_revision(review):reasons.append('product_review_binding_changed')
    if stage in ('design','plan','build','verify','review_release','maintain'):
        for draft_stage,key in (('design','specifications'),('plan','plans')):
            if draft_stage=='plan' and stage=='design':continue
            pending=next((r for r in reversed(product[key]) if r['change_id']==change['id']),None)
            if pending and (not pending['published'] or latest(change,draft_stage) is None or pending['artifact_revision']!=latest(change,draft_stage)['revision']):
                reasons.append('product_'+draft_stage+'_draft_pending')
    if stage=='design':
        spec=latest(change,'design')
        review=next((r for r in reversed(product['specifications']) if spec and r['change_id']==change['id'] and r['artifact_revision']==spec['revision'] and selection_applicable(product,change['id'],r['selection_revision'])),None)
        if not review:reasons.append('product_criterion_mapping_review_required')
        else:
            required={r['id'] for r in increment['criteria'] if r['required']}
            targets={r['id'] for r in spec['content']['criteria']}
            mappings=review['mappings']; covered={r['product_criterion_id'] for r in mappings}
            if not required<=covered or any(r['specification_criterion_id'] not in targets for r in mappings): reasons.append('product_criterion_mapping_incomplete')
            for mapping in mappings:
                if not mapping['reason'].strip():reasons.append('product_semantic_review_reason_required')
                target=next((r for r in spec['content']['criteria'] if r['id']==mapping['specification_criterion_id']),None)
                if mapping['product_criterion_id'] in required and target and not target['required']:reasons.append('product_required_criterion_weakened')
    if stage=='plan':
        plan=latest(change,'plan')
        review=next((r for r in reversed(product['plans']) if plan and r['change_id']==change['id'] and r['artifact_revision']==plan['revision']),None)
        if not review:reasons.append('product_plan_file_review_required')
        else:
            if {r['path'] for r in review['files']}!=set(plan['content']['files']):reasons.append('product_plan_file_scope_incomplete')
            try:
                from cc_product_documents import observe_files
                if observe_files(root.path,[r['path'] for r in review['files']])!=review['files']: reasons.append('product_plan_source_changed')
            except (OSError,ValueError):reasons.append('product_plan_source_unreadable')
    return reasons

def view(state,*,root=None,include_planning=True):
    product=state.get('product',empty()); row=current(product)
    drafts={}
    for stage,key in (('design','specifications'),('plan','plans')):
        for draft in product[key]:drafts[(draft['change_id'],stage)]={**draft,'stage':stage}
    from cc_product_evolution import view as evolution_view,active_selections
    return {'document':row['document'] if row else None,'current_revision':row['revision'] if row else 'absent','accepted_revision':accepted_revision(product),'responsible_person':row['actor'] if row else '', 'decisions':product['decisions'],'increments':active_selections(product),'drafts':list(drafts.values()),'adoption':__import__('cc_product_planning').domain(product)['adoptions'][-1] if __import__('cc_product_planning').domain(product)['adoptions'] else None,'blockers':__import__('cc_product_planning').adoption_reasons(product), 'evolution':evolution_view(state,root=root), 'planning':__import__('cc_product_planning').views(root,state) if include_planning else []}

def apply(root,state,action,timestamp):
    if action['kind'] in DETAIL_FIELDS:
        from cc_product_planning import apply as detail_apply
        return detail_apply(root,state,action,timestamp)
    if action['kind'] in EVOLUTION_FIELDS:
        from cc_product_evolution import apply as evolution_apply
        state.setdefault('product',empty())
        return evolution_apply(root,state,action,timestamp)
    if 'planning_review' in action:
        import cc_planning
        cc_planning.guard_action(root,action)
    from cc_development_lib import apply as development_apply, change_for
    product=state.setdefault('product',empty()); kind=action['kind']; row=current(product)
    event_result=None
    if kind=='product_save':
        require(action['base_revision']==(row['revision'] if row else 'absent'),'product_revision_conflict')
        if 'source_review' in action:
            from cc_evidence_inputs import Budget
            import hashlib
            source=action['source_review']; observed=root.read(source['path'],Budget(per_file=512*1024,total=512*1024),missing=True)
            require(observed is not None,'product_source_review_stale');raw,info=observed
            require(info.st_nlink==1 and hashlib.sha256(raw).hexdigest()==source['sha256'],'product_source_review_stale')
        doc=action['document']; require(not row or row['document']['id']==doc['id'],'product_identity_changed')
        if row and row['document']==doc:return state
        record={'previous':row['revision'] if row else 'absent','document':doc,'actor':action['actor'],'timestamp':timestamp}
        record['revision']=digest(record)
        product['revisions'].append(record)
        event_result=record
    elif kind=='product_decide':
        require(row and action['product_revision']==row['revision'],'product_revision_conflict')
        if action['decision']=='accepted':
            from cc_product_planning import adoption_reasons
            require(not adoption_reasons(product),'product_adoption_conflict_unresolved')
            complete(root,row['document'])
        event_result={k:action[k] for k in ('product_revision','decision','actor','reason')}|{'timestamp':timestamp}
        product['decisions'].append(event_result)
    elif kind=='product_increment':
        require(state['enabled'],'method_not_active')
        require(row and accepted_revision(product)==action['product_revision']==row['revision'],'accepted_product_revision_required')
        complete(root,row['document'])
        feature=next((f for f in row['document']['features'] if f['id']==action['feature_id']),None)
        require(feature is not None,'product_feature_not_found'); require(not feature['dependencies'],'product_dependencies_unsupported')
        from cc_product_evolution import active_selections,empty as evolution_empty
        require(not any(i['id']==action['increment_id'] or feature['id'] in {f['feature_id'] for f in i['features']} for i in active_selections(product)),'product_increment_scope_exists')
        require(not any(r['feature_id']==feature['id'] for r in product.get('evolution',evolution_empty())['retirements']),'product_feature_retired')
        require(not any(i['id']==action['increment_id'] or i['feature_id']==feature['id'] for i in product['increments']),'product_increment_scope_exists')
        increment={'id':action['increment_id'],'title':action['title'],'product_revision':row['revision'],'feature_id':feature['id'],'feature_revision':digest(feature),'feature_semantics':feature_semantics(feature),'change_id':action['change_id'],'criteria':feature['criteria'],'constraints':row['document']['constraints'],'non_goals':row['document']['non_goals'],'users':feature['users'] or row['document']['users'],'risks':feature['risks']}
        increment['selection_revision']=digest(increment)
        state=development_apply(root,state,{'kind':'create',**{k:action[k] for k in ('change_id','title','owner','risk')}},timestamp=timestamp)
        state=development_apply(root,state,{'kind':'artifact','change_id':action['change_id'],'stage':'intent','actor':action['actor'],'content':{'problem':feature['problem'],'outcome':feature['outcome'],'affected_users_and_systems':feature['users'] or row['document']['users'],'constraints':row['document']['constraints'],'non_goals':row['document']['non_goals'],'open_questions':feature['questions'],'success_measures':[r['text'] for r in feature['criteria']]}},timestamp=timestamp)
        product=state['product']; product['increments'].append(increment)
        event_result=increment
    else:
        increment=increment_for(product,action['change_id']); require(increment is not None,'product_linked_change_required')
        require(not scope_reasons(product,increment),'product_selected_scope_changed_review_unsupported')
        from cc_product_planning import applicability
        require(applicability(product,change_for(state,action['change_id']),root)=='legacy','planning_detail_required')
        stage='design' if kind=='product_specification' else 'plan'
        upstream_stage='intent' if stage=='design' else 'design'
        upstream_field='upstream_intent_revision' if stage=='design' else 'upstream_specification_revision'
        from cc_development_lib.evaluator import latest
        upstream_artifact=latest(change_for(state,action['change_id']),upstream_stage)
        require(upstream_artifact and action['content'][upstream_field]==upstream_artifact['revision'],'product_draft_upstream_stale')
        published=draft_published(stage,action['content'])
        if published:
            state=development_apply(root,state,{'kind':'artifact','stage':stage,'change_id':action['change_id'],'content':action['content'],'actor':action['actor']},timestamp=timestamp)
        product=state['product']; entry={'change_id':action['change_id'],'artifact_revision':digest(action['content']),'selection_revision':increment['selection_revision'],'actor':action['actor'],'timestamp':timestamp,'content':action['content'],'published':published}
        field='mappings' if stage=='design' else 'files'; entry[field]=action[field]
        product['specifications' if stage=='design' else 'plans'].append(entry)
        event_result=entry
    profiles=[]
    if kind=='product_increment':
        from cc_product_planning import make_profile,add_profile
        profile=make_profile(change_for(state,action['change_id']),increment['selection_revision'],kind,action['actor'],'New Product change',timestamp)
        add_profile(product,profile);profiles.append(profile)
    event={'action':action,'result':event_result,'previous':product['events'][-1]['digest'] if product['events'] else 'absent','timestamp':timestamp}
    if profiles:event['profiles']=profiles
    event['digest']=digest(event); product['events'].append(event)
    return state

def validate_state(product, *, creation_methods=None):
    require(type(product) is dict and set(empty()) <= set(product) <= set(empty())|{'evolution','detailed'},'invalid_product_state')
    for key in empty(): require(type(product[key]) is list and len(product[key])<=2048,'product_record_limit')
    if 'evolution' in product:
        from cc_product_evolution import empty as evolution_empty
        require(type(product['evolution']) is dict and set(product['evolution'])==set(evolution_empty()),'invalid_product_evolution')
        for rows in product['evolution'].values():require(type(rows) is list and len(rows)<=2048,'product_record_limit')
    if 'detailed' in product:
        require(type(product['detailed']) is dict and set(product['detailed'])=={'profiles','details','adoptions'},'invalid_planning_state')
        for values in product['detailed'].values():require(type(values) is list and len(values)<=2048,'product_record_limit')
    previous='absent'
    for row in product['revisions']:
        require(set(row)=={'revision','previous','document','actor','timestamp'} and row['previous']==previous and row['revision']==digest({k:v for k,v in row.items() if k!='revision'}),'invalid_product_revision')
        validate_document(row['document']); previous=row['revision']
    projected=empty(); previous='absent'; change_ids=set(); increment_ids=set();feature_ids=set()
    for event in product['events']:
        require(type(event) is dict and {'action','result','previous','timestamp','digest'}<=set(event)<= {'action','result','previous','timestamp','digest','profiles'} and event['previous']==previous and event['digest']==digest({k:v for k,v in event.items() if k!='digest'}),'invalid_product_history')
        action=validate_action(event['action']); result=event['result']; kind=action['kind']; row=current(projected)
        require(type(result) is dict,'invalid_product_history_result')
        if kind in DETAIL_FIELDS:
            from cc_product_planning import validate_history as detail_history
            detail_history(projected,event,creation_methods=creation_methods)
        elif kind in EVOLUTION_FIELDS:
            from cc_product_evolution import validate_history
            validate_history(projected,event)
        elif kind=='product_save':
            require(action['base_revision']==(row['revision'] if row else 'absent') and result['document']==action['document'] and result['actor']==action['actor'] and result['timestamp']==event['timestamp'],'invalid_product_history_revision')
            projected['revisions'].append(result)
        elif kind=='product_decide':
            if action['decision']=='accepted':require(not __import__('cc_product_planning').adoption_reasons(projected),'product_adoption_conflict_unresolved')
            require(row and action['product_revision']==row['revision'] and result=={k:action[k] for k in ('product_revision','decision','actor','reason')}|{'timestamp':event['timestamp']},'invalid_product_history_decision')
            projected['decisions'].append(result)
        elif kind=='product_increment':
            require(row and accepted_revision(projected)==action['product_revision']==row['revision'],'invalid_product_selection_origin')
            from cc_product_evolution import active_selections,empty as evolution_empty
            require(not any(s['id']==action['increment_id'] or action['feature_id'] in {f['feature_id'] for f in s['features']} for s in active_selections(projected)),'invalid_product_selection')
            require(not any(r['feature_id']==action['feature_id'] for r in projected.get('evolution',evolution_empty())['retirements']),'invalid_product_selection')
            feature=next((f for f in row['document']['features'] if f['id']==action['feature_id']),None)
            require(feature is not None and not feature['dependencies'],'invalid_product_selection_feature')
            expected={'id':action['increment_id'],'title':action['title'],'product_revision':row['revision'],'feature_id':feature['id'],'feature_revision':digest(feature),'feature_semantics':feature_semantics(feature),'change_id':action['change_id'],'criteria':feature['criteria'],'constraints':row['document']['constraints'],'non_goals':row['document']['non_goals'],'users':feature['users'] or row['document']['users'],'risks':feature['risks']}
            expected['selection_revision']=digest(expected)
            require(result==expected and result['id'] not in increment_ids and result['change_id'] not in change_ids and result['feature_id'] not in feature_ids,'invalid_product_selection')
            increment_ids.add(result['id']);change_ids.add(result['change_id']);feature_ids.add(result['feature_id']);projected['increments'].append(result)
        else:
            increment=increment_for(projected,action['change_id']);require(increment is not None,'invalid_product_artifact_origin')
            field='mappings' if kind=='product_specification' else 'files'
            stage='design' if field=='mappings' else 'plan';published=draft_published(stage,action['content'])
            require(result=={'change_id':action['change_id'],'artifact_revision':digest(action['content']),'selection_revision':increment['selection_revision'],'actor':action['actor'],'timestamp':event['timestamp'],'content':action['content'],'published':published,field:action[field]},'invalid_product_artifact_review')
            if field=='mappings':
                product_ids={r['id'] for r in increment['criteria']}; spec_ids={r['id'] for r in action['content']['criteria']}
                require(all(r['product_criterion_id'] in product_ids and r['specification_criterion_id'] in spec_ids for r in action[field]),'invalid_product_mapping_reference')
                require(len({r['product_criterion_id'] for r in action[field]})==len(action[field]),'duplicate_product_mapping')
            else:require(len({r['path'] for r in action[field]})==len(action[field]),'duplicate_product_file_observation')
            projected['specifications' if field=='mappings' else 'plans'].append(result)
        if 'profiles' in event:
            from cc_product_planning import validate_profiles
            validate_profiles(projected,event,creation_methods=creation_methods)
        projected['events'].append(event);previous=event['digest']
    require(projected==product,'product_history_projection_mismatch')

def template(root,change_id,stage,*,snapshot=None):
    from cc_development_lib import read
    snapshot=snapshot if snapshot is not None else read(root); change=next((c for c in snapshot['changes'] if c['id']==change_id),None)
    require(change is not None,'change_not_found')
    from cc_product_evolution import for_change
    projected={'increments':[], 'evolution':{**snapshot['product']['evolution'],'selections':snapshot['product']['increments']}}
    increment=for_change(projected,change_id)
    require(increment is not None,'product_linked_change_required')
    from cc_development_lib.evaluator import latest
    if stage=='design':
        intent=latest(change,'intent'); require(intent is not None,'artifact_missing')
        criteria=[{'id':r['id'],'requirement_id':'req-'+digest(r['id'])[:24],'text':r['text'],'required':r['required'],'proof':'human','checks':[]} for r in increment['criteria']]
        return {'content':{'upstream_intent_revision':intent['revision'],'requirements':[{'id':'req-'+digest(r['id'])[:24],'text':r['text']} for r in increment['criteria']],'criteria':criteria,'decisions':[],'risks':increment['risks'],'policy_concerns':[],'source_references':[increment['selection_revision']]},'mappings':[{'product_criterion_id':r['id'],'specification_criterion_id':r['id'],'decision':'equivalent','reason':''} for r in increment['criteria']],'files':[],'unresolved':['Review each condition and explain semantic equivalence.']}
    require(stage=='plan','invalid_stage'); spec=latest(change,'design'); require(spec is not None,'artifact_missing')
    return {'content':{'upstream_specification_revision':spec['revision'],'files':[],'ownership_scopes':[],'tasks':[],'dependencies':[],'criterion_check_mapping':{r['id']:[] for r in spec['content']['criteria']},'verification_strategy':'','rollback_assessment':''},'mappings':[],'files':[],'unresolved':['Choose file scope, accountable owners, actionable tasks, checks and rollback.']}
