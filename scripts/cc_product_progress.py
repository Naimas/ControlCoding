"""Canonical bounded Product progress. Existing owners retain every decision."""
from datetime import datetime, timezone
import json
import time

from cc_product_progress_contract import (SCHEMA_VERSION, MAX_ROWS, MAX_RESPONSE_BYTES,
    ProgressError, validate_query, validate_response, decode_cursor, encode_cursor, serialized)
from cc_product_progress_observation import observing, ObservationError, stamp


AUTHORING = {'artifact_missing','candidate_missing','independent_review_missing','release_record_missing',
             'operational_observation_missing','planning_detail_missing','criterion_mapping_incomplete',
             'criteria_missing','product_specification_missing','product_plan_missing','product_progress_mapping_missing',
             'product_criterion_mapping_review_required','product_criterion_mapping_incomplete','product_plan_file_review_required'}
BLOCKED = {'blocking_questions_unresolved','blocking_concerns_unresolved','external_dependencies_unresolved',
           'verification_failures_unresolved','review_findings_unresolved','operational_findings_unresolved',
           'plan_amendment_required','product_final_integration_proof_required','product_integration_contribution_unverified',
           'product_integration_current_proof_required','product_integration_check_unproved',
           'product_integration_candidate_not_covered','human_criterion_acceptance_missing',
           'product_scope_cancel','product_scope_defer'}
ABSENT_REFERENCES={'intent_reference_stale','specification_reference_stale','plan_reference_stale',
                   'candidate_reference_stale','release_artifact_reference_stale','candidate_inventory_mismatch'}
PENDING_PROOF={'criterion_check_unproved','human_criterion_acceptance_missing'}
NO_RECEIPT={'verification:no_receipt','invariants:no_receipt'}


def attention(code, *, future=False):
    """Closed explicit classes with a visible conservative unknown fallback."""
    base = code.split(':', 1)[0]
    if future and (base in ABSENT_REFERENCES|PENDING_PROOF or code in NO_RECEIPT):
        return 'none'
    if base in AUTHORING or base.startswith('upstream_') and base.endswith('_not_accepted'):
        return 'none' if future else 'blocked'
    if (base in BLOCKED or base.startswith('product_dependency_')
            or base in ('criterion_check_unproved','criterion_checks_missing','criterion_check_not_required',
                        'monitor_incident_disposition_missing','operational_incident_disposition_missing')):
        return 'none' if future and base.startswith('product_final_integration_') else 'blocked'
    if base.startswith(('verification','invariants')):
        tail = code.split(':',1)[-1]
        if any(word in tail for word in ('unreadable','unknown','incomplete','malformed','limit','timeout')):
            return 'not_established'
        return 'blocked'
    if (base.endswith(('_stale','_changed','_mismatch','_not_current'))
            or base in ('product_requirements_changed_review_required','product_adoption_conflict_unresolved',
                        'planning_scope_amendment_required','product_selected_feature_retired','decision_requires_review',
                        'product_selected_scope_changed_review_unsupported','product_design_draft_pending','product_plan_draft_pending')):
        return 'needs_review'
    return 'not_established'


def bucket(total=0):
    return {'total':total,'mapped':0,'implemented':0,'verified':0,'released':0,'missing':0,'unknown':0}


def empty_coverage(total=0):
    return {'required':bucket(total),'optional':bucket(total),'duplicate_contributions':0}


def _semantic_reason(code, assessed_criteria):
    # Match the complete owner-assessed ID and reason, rather than guessing
    # a namespace from an arbitrary suffix or delimiter position.
    return next((inner for row in assessed_criteria for inner in row['reasons']
                 if code==row['id']+':'+inner),code)


def reason(code, *, gate=None, change_id=None, criterion_id=None, future=False, assessed_criteria=()):
    semantic=_semantic_reason(code,assessed_criteria)
    return {'code':code,'attention':attention(semantic,future=future),'gate':gate,
            'change_id':change_id,'criterion_id':criterion_id}


def summarize(coverage=None, *, planned=False, in_progress=False, reasons=(), actions=(), currentness='current'):
    coverage = coverage if coverage is not None else empty_coverage()
    reasons = list({json.dumps(row, sort_keys=True):row for row in reasons}.values())
    if len(reasons)>128 or len(actions)>8:
        raise ProgressError('progress_record_too_large')
    required = coverage['required']; total = required['total']
    outcome = 'in_progress' if in_progress else 'planned' if planned else 'not_established'
    if total and required['unknown']==0 and required['missing']==0:
        for flag in ('implemented','verified','released'):
            if required[flag]==total:
                outcome=flag
    rank = {'none':0,'needs_review':1,'blocked':2,'not_established':3}
    state = max((row['attention'] for row in reasons), key=lambda item:rank[item], default='none')
    if required['unknown'] and rank[state]<3:
        state='not_established'
    if currentness=='current':
        if state=='not_established':
            currentness='unknown'
        elif any(row['attention']=='needs_review' for row in reasons):
            currentness='stale'
    return {'outcome':outcome,'attention':state,'currentness':currentness,'coverage':coverage,
            'reasons':reasons,'next_actions':list(actions)}


class Projection:
    def __init__(self, safe, state, revision, observer):
        from cc_product import current, accepted_revision, empty
        import cc_product_evolution as evolution
        self.safe,self.state,self.revision,self.observer=safe,state,revision,observer
        self.product=state.get('product',empty())
        self.document_row=current(self.product)
        self.document=self.document_row['document'] if self.document_row else None
        self.accepted=accepted_revision(self.product)
        self.selections=evolution.selections(self.product)
        self.active=evolution.active_selections(self.product)
        self.changes={c['id']:c for c in state['changes']}
        self.facts={}
        self.criteria_cache={}
        self.feature_cache={}
        self.original_scopes={}

    def target(self, destination='Product', **fields):
        result={key:None for key in ('feature_id','feature_revision','increment_id','change_id','criterion_id','incident_id','stage','selection_revision','artifact_revision')}
        result.update(destination=destination,project_identity=self.state['project_identity'])
        result.update(fields)
        if result['feature_id'] is not None and result['feature_revision'] is None:
            from cc_development_lib.method import digest
            selected=next((s for s in self.selections if s['selection_revision']==result['selection_revision']),None)
            feature=next((f for f in selected['features'] if f['feature_id']==result['feature_id']),None) if selected else None
            current=next((f for f in (self.document or {}).get('features',[]) if f['id']==result['feature_id']),None)
            result['feature_revision']=feature['feature_revision'] if feature else digest(current) if current else None
        return result

    def decision(self, selection):
        rows=[r for r in self.product.get('evolution',{}).get('scope_decisions',[]) if r['increment_id']==selection['id']]
        return rows[-1]['decision'] if rows else 'selected'

    def selection_role(self, selection):
        from cc_product_evolution import active_feature_selections
        retired={r['feature_id'] for r in self.product.get('evolution',{}).get('retirements',[])}
        features=[f['feature_id'] for f in selection['features']]
        if all(f in retired for f in features):
            return 'retired'
        heads=active_feature_selections(self.product,features)
        return 'active' if any(s['selection_revision']==selection['selection_revision'] for s in heads) else 'superseded'

    def scope(self, feature=None, selection=None, *, role=None):
        retired={r['feature_id'] for r in self.product.get('evolution',{}).get('retirements',[])}
        feature_id=(feature.get('id') or feature.get('feature_id')) if feature else None
        if selection is not None:
            selected=[selection]
        elif feature_id is not None:
            from cc_product_evolution import active_feature_selections
            selected=active_feature_selections(self.product,[feature_id])
        else:
            selected=self.active
        binding=selection or (selected[0] if feature_id is not None and len(selected)==1 else None)
        if role is None and selection is not None:
            role='retired' if feature_id in retired else self.selection_role(selection)
        if role is None and feature_id:
            current_ids={f['id'] for f in (self.document or {}).get('features',[])}
            role='retired' if feature_id in retired else 'superseded' if feature_id not in current_ids else 'active' if selected else 'unselected'
        if role is None and selection:
            role=self.selection_role(selection)
        from cc_development_lib.method import digest
        return {'document_title':self.document['title'] if self.document else '',
                'document_state':'none' if not self.document_row else 'accepted' if self.accepted==self.document_row['revision'] else 'draft',
                'feature_id':feature_id,'feature_revision':(feature.get('feature_revision') or digest(feature)) if feature else None,
                'selected_feature_revisions':[{'feature_id':f['feature_id'],'feature_revision':f['feature_revision'],'selection_revision':s['selection_revision']}
                                              for s in selected for f in s['features'] if feature_id is None or f['feature_id']==feature_id],
                'increment_id':binding['id'] if binding else None,
                'selection_revision':binding['selection_revision'] if binding else None,
                'role':role,'decision':self.decision(binding) if binding else None}

    def row(self, kind, row_id, title, data, *, feature=None, selection=None, summary=None, target=None, role=None):
        return {'kind':kind,'id':row_id,'title':title,'scope':self.scope(feature,selection,role=role),
                'summary':summary,'target':target,'data':data}

    def change_facts(self, change_id):
        if change_id in self.facts:
            return self.facts[change_id]
        from cc_development_lib.evaluator import evaluate,latest
        from cc_development_lib.schema import json_copy
        from cc_development_adapters import assess_release
        change=json_copy(self.changes[change_id])
        gates=evaluate(self.safe,change,through_stage='review_release',product=self.product,state=self.state)
        def passed(stage):
            return bool(gates[stage]['accepted'] and gates[stage]['outcome']=='allowed')
        current=bool(change.get('candidate') and change['candidate'].get('current'))
        release=change['releases'][-1] if change['releases'] else None
        assessment={'state':'unknown','reasons':['release_observation_missing']}
        if release and release['status']=='observed':
            assessment=assess_release(self.safe.path,change,release)
        released=(passed('verify') and passed('review_release') and current and bool(release)
                  and release['status']=='observed' and release['candidate_identity']==change['candidate']['digest']
                  and assessment['state']=='allowed')
        stage=next((s for s in gates if not passed(s)),'maintain')
        established=passed('intent')
        reasons=[]
        # Current candidate drift remains visible even when an earlier gate is
        # the next authoring step. Future absent stages do not become blockers.
        for s,gate in gates.items():
            future=not gates[s]['accepted'] and s!=stage
            if s==stage or gate['accepted'] or current or change.get('candidate') and s=='build':
                absent=latest(change,s) is None
                criteria=(gate.get('evidence') or {}).get('criteria',[])
                for code in gate['reasons']:
                    semantic=_semantic_reason(code,criteria)
                    pending=(semantic in AUTHORING|ABSENT_REFERENCES|NO_RECEIPT
                             or semantic.split(':',1)[0] in PENDING_PROOF)
                    reasons.append(reason(code,gate=s,change_id=change_id,assessed_criteria=criteria,
                                          future=future or absent and pending or not established and semantic in AUTHORING))
        if release and release['status']=='observed' and not released:
            reasons.extend(reason(code,gate='review_release',change_id=change_id) for code in assessment['reasons'])
        result={'change':change,'gates':gates,'in_progress':established,'implemented':passed('build') and current,
                'verified':passed('verify') and current,'released':bool(released),'stage':stage,
                'release_assessment':assessment,'reasons':reasons}
        self.facts[change_id]=result
        return result

    def selection_matches(self, selection, selected_feature, feature, condition):
        import cc_product_evolution as evolution
        if selected_feature is None:
            return False
        if feature.get('id') is None:  # Explicit selection-scope detail.
            return True
        if evolution.common_semantics(feature)!=selected_feature['common_semantics']:
            return False
        if self.document and (selection['constraints']!=self.document['constraints'] or selection['non_goals']!=self.document['non_goals']
                              or selected_feature['users']!=(feature['users'] or self.document['users'])):
            return False
        return next((c for c in selected_feature['criteria'] if c['id']==condition['id']),None)==condition

    def criterion(self, condition, feature, selections, *, only_change=None):
        from cc_development_lib.method import digest
        from cc_product_evolution import integration_assessment,selection_applicable
        from cc_development_lib.evaluator import latest
        key=(digest(condition),digest(feature),tuple(s['selection_revision'] for s in selections),only_change)
        if key in self.criteria_cache:
            return self.criteria_cache[key]
        feature_id=feature.get('id') or feature.get('feature_id')
        own_rows=[];contributions=[];reasons=[];integrations=[];seen=set();duplicates=0
        for selection in selections:
            selected_feature=next((f for f in selection['features'] if f['feature_id']==feature_id),None)
            if not self.selection_matches(selection,selected_feature,feature,condition):
                if selected_feature is not None:
                    reasons.append(reason('product_requirements_changed_review_required',criterion_id=condition['id']))
                continue
            own=next((r for r in selection['ownership'] if r['criterion_id']==condition['id']),None)
            if own is None:
                continue
            own_rows.append(own)
            for change_id in own['contributor_change_ids']:
                if only_change is not None and change_id!=only_change:
                    continue
                if change_id in seen:
                    duplicates+=1;continue
                seen.add(change_id)
                contribution={'change_id':change_id,'specification_criterion_ids':[],'mapped':False,
                              'candidate_identity':None,'implemented':False,'verified':False,'released':False,'reasons':[]}
                if change_id not in self.changes:
                    contribution['reasons'].append('product_progress_contributor_missing')
                else:
                    facts=self.change_facts(change_id);change=facts['change'];design=latest(change,'design')
                    reasons.extend({**r,'criterion_id':condition['id']} for r in facts['reasons'])
                    review=next((r for r in reversed(self.product.get('specifications',[])) if r['change_id']==change_id),None)
                    mapping=[r['specification_criterion_id'] for r in (review or {}).get('mappings',[]) if r['product_criterion_id']==condition['id']]
                    mapped=bool(review and review.get('published') and design and review['artifact_revision']==design['revision']
                                and selection_applicable(self.product,change_id,review['selection_revision']) and mapping)
                    assessment=facts['gates']['verify'].get('evidence',{})
                    assessed={r['id']:r for r in assessment.get('criteria',[])}
                    condition_verified=mapped and all(c in assessed and assessed[c]['state']=='allowed' for c in mapping)
                    contribution.update(specification_criterion_ids=mapping,mapped=bool(mapped),candidate_identity=(change.get('candidate') or {}).get('digest'),
                                        implemented=bool(mapped and facts['implemented']),verified=bool(condition_verified and facts['verified']),
                                        released=bool(condition_verified and facts['released']))
                    if not mapped:
                        contribution['reasons'].append('product_progress_mapping_missing')
                    if mapped and not condition_verified and facts['verified']:
                        contribution['reasons'].append('product_progress_criterion_proof_missing')
                    # Gate reasons stay on change rows; per-condition reasons
                    # retain direct coverage issues and unavailable owner facts.
                    if mapped and not facts['verified']:
                        contribution['reasons'].extend(facts['gates']['verify']['reasons'])
                contributions.append(contribution)
            if own['integration_check'] is not None:
                integrations.append(integration_assessment(self.safe,selection,own,self.product,self.state))
        if duplicates:
            reasons.append(reason('product_progress_duplicate_contribution',criterion_id=condition['id']))
        if not own_rows or not contributions:
            reasons.append(reason('product_progress_mapping_missing',criterion_id=condition['id'],future=True))
        for row in contributions:
            for code in row['reasons']:
                retained=self.change_facts(row['change_id'])['reasons'] if row['change_id'] in self.changes else []
                if any(r['code']==code and r['gate']=='verify' for r in retained):
                    # Only remove an actually materialized contextual copy;
                    # data.reasons retains the original owner code.
                    continue
                future=not row['implemented'] and (code.split(':',1)[0] in AUTHORING or code.startswith('upstream_') and code.endswith('_not_accepted'))
                reasons.append(reason(code,change_id=row['change_id'],criterion_id=condition['id'],future=future or not row['mapped']))
        combined={'required':bool(integrations),'state':'not_required','reasons':[]}
        if integrations:
            codes=list(dict.fromkeys(code for row in integrations for code in row['reasons']))
            combined.update(state='allowed' if all(row['state']=='allowed' for row in integrations) else 'blocked',reasons=codes)
            ready=bool(contributions) and all(row['verified'] for row in contributions)
            reasons.extend(reason(code,gate='review_release',criterion_id=condition['id'],future=not ready) for code in codes)
        complete=bool(contributions) and duplicates==0 and all(row['mapped'] for row in contributions)
        flags={flag:complete and all(row[flag] for row in contributions) and (flag=='implemented' or combined['state'] in ('allowed','not_required'))
               for flag in ('implemented','verified','released')}
        unknown=duplicates>0 or any(r['attention']=='not_established' for r in reasons)
        count=bucket(1);count.update(mapped=int(complete),missing=int(not complete),unknown=int(unknown),**{flag:int(value) for flag,value in flags.items()})
        coverage=empty_coverage();coverage['required' if condition['required'] else 'optional']=count;coverage['duplicate_contributions']=duplicates
        in_progress=any(self.change_facts(r['change_id'])['in_progress'] for r in contributions if r['change_id'] in self.changes)
        target_fields={'feature_id':feature_id,'criterion_id':condition['id']}
        if feature.get('id') is None and len(selections)==1:
            target_fields.update(feature_revision=feature['feature_revision'],selection_revision=selections[0]['selection_revision'],
                                 increment_id=selections[0]['id'],change_id=only_change)
        target=self.target(**target_fields)
        summary=summarize(coverage,planned=True,in_progress=in_progress,reasons=reasons,actions=[target])
        result=self.row('criterion',condition['id'],condition['text'],
                        {'required':condition['required'],'text':condition['text'],'owner_change_id':own_rows[0]['owner_change_id'] if own_rows else None,
                         'contributor_change_ids':list(dict.fromkeys(c for o in own_rows for c in o['contributor_change_ids'])),
                         'contributions':contributions,'integration':combined},feature=feature,
                        selection=selections[0] if len(selections)==1 else None,summary=summary,
                        target=target)
        self.criteria_cache[key]=result
        return result

    def aggregate(self, rows, *, planned=True, actions=()):
        coverage=empty_coverage();reasons=[];in_progress=False
        for row in rows:
            summary=row['summary']
            if summary is None:
                continue
            for key in ('required','optional'):
                for name,value in summary['coverage'][key].items():
                    if name=='total' and (value is None or coverage[key][name] is None):
                        coverage[key][name]=None
                    elif coverage[key][name] is not None:
                        coverage[key][name]+=value
            coverage['duplicate_contributions']+=summary['coverage']['duplicate_contributions']
            reasons.extend(summary['reasons'])
            in_progress |= summary['outcome'] in ('in_progress','implemented','verified','released')
        return summarize(coverage,planned=planned,in_progress=in_progress,reasons=reasons,actions=actions)

    def feature(self, feature):
        from cc_product_evolution import active_feature_selections
        if feature['id'] in self.feature_cache:
            return self.feature_cache[feature['id']]
        selections=active_feature_selections(self.product,[feature['id']])
        criteria=[self.criterion(c,feature,selections) for c in feature['criteria']]
        summary=self.aggregate(criteria,actions=[self.target(feature_id=feature['id'])])
        if not criteria:
            summary['outcome']='planned'
        result=self.row('feature',feature['id'],feature['title'],{'description':feature['outcome'],'selected_scope_count':len(selections)},
                        feature=feature,summary=summary,target=self.target(feature_id=feature['id']))
        if result['scope']['role'] in ('retired','superseded'):
            result['summary']['currentness']='not_applicable'
        self.feature_cache[feature['id']]=(result,criteria,selections)
        return self.feature_cache[feature['id']]

    def increment(self, selection):
        criteria=[self.criterion(c,f,[selection]) for f in selection['features'] for c in f['criteria']]
        summary=self.aggregate(criteria,actions=[self.target(increment_id=selection['id'],selection_revision=selection['selection_revision'])])
        if self.selection_role(selection)!='active':
            summary['currentness']='not_applicable'
        return self.row('increment',selection['id'],selection['title'],
                        {'feature_ids':[f['feature_id'] for f in selection['features']],
                         'change_ids':[c['change_id'] for c in selection['changes']],'priority':'',
                         'document_order':self.active.index(selection) if selection in self.active else 0},
                        selection=selection,summary=summary,target=self.target(increment_id=selection['id'],selection_revision=selection['selection_revision']))

    def change_row(self, change_id, selection):
        from cc_development_lib.evaluator import latest
        from cc_product_planning import profile,latest_detail
        facts=self.change_facts(change_id);change=facts['change']
        criteria=self.change_criteria(change_id,selection)
        summary=self.aggregate(criteria,actions=[self.target('Development',change_id=change_id,stage=facts['stage'],selection_revision=selection['selection_revision'])])
        if facts['in_progress'] and summary['outcome'] in ('not_established','planned'):
            summary['outcome']='in_progress'
        if not criteria:
            summary=summarize(planned=True,in_progress=facts['in_progress'],reasons=facts['reasons'],actions=summary['next_actions'])
        else:
            # Surface current change owner blockers alongside condition counts.
            summary=summarize(summary['coverage'],planned=True,in_progress=facts['in_progress'],reasons=summary['reasons']+facts['reasons'],actions=summary['next_actions'])
        p=profile(self.product,change_id)
        details={stage:latest_detail(self.product,change_id,stage) for stage in ('design','plan')}
        return self.row('change',change_id,change['title'],
                        {'owner':change['owner'],'stage':'closed' if change['closed'] else facts['stage'],
                         'candidate_identity':(change.get('candidate') or {}).get('digest'),'candidate_current':bool((change.get('candidate') or {}).get('current')),
                         'gates':facts['gates'],'artifact_revisions':{s:latest(change,s)['revision'] for s in change['artifacts'] if latest(change,s)},
                         'detail_revisions':{s:r['revision'] if r else None for s,r in details.items()},
                         'profile_revision':p['revision'] if p else None,'history_partial':'history_base' in change},
                        selection=selection,summary=summary,target=summary['next_actions'][0])

    def change_criteria(self, change_id, selection):
        wanted={o['criterion_id'] for o in selection['ownership'] if change_id in o['contributor_change_ids']}
        return [self.criterion(c,f,[selection],only_change=change_id) for f in selection['features'] for c in f['criteria'] if c['id'] in wanted]

    def tasks(self, change_id, selection):
        from cc_development_lib.evaluator import latest
        from cc_development_lib.method import digest
        from cc_product_planning import latest_detail,profile
        change=self.changes[change_id];plan=latest(change,'plan')
        if plan is None:
            return []
        detail=latest_detail(self.product,change_id,'plan');p=profile(self.product,change_id)
        details={r['task_id']:r for r in detail['details']['task_details']} if detail else {}
        tasks=plan['content']['tasks'];by_id={r['id']:r for r in tasks};positions={r['id']:i for i,r in enumerate(tasks)}
        ids={r['id']:digest([self.state['project_identity'],change_id,plan['revision'],r['id']]) for r in tasks}
        done=[];pending=list(by_id)
        while pending:
            ready=sorted((key for key in pending if all(dep in done for dep in by_id[key]['depends_on'])),key=lambda key:(positions[key],key))
            if not ready:
                raise ProgressError('progress_evidence_unreadable')
            for key in ready:
                pending.remove(key);done.append(key)
        outgoing={key:sum(key in t['depends_on'] for t in tasks) for key in by_id}
        result=[]
        for order,key in enumerate(done):
            task=by_id[key];companion=details.get(key,{})
            result.append(self.row('task',ids[key],task.get('title',key),
                {'change_id':change_id,'plan_revision':plan['revision'],'detail_revision':detail['revision'] if detail else None,
                 'profile_revision':p['revision'] if p else None,'task_id':key,'owner':task['owner'],
                 'deliverable':companion.get('deliverable',task.get('title',key)),'criterion_ids':companion.get('criterion_ids',[]),
                 'files':companion.get('files',[]),'depends_on':[ids[d] for d in task['depends_on']],
                 'supports_task_ids':[ids[d] for d in companion.get('supports_task_ids',[])],
                 'declaration_order':positions[key],'topological_order':order,'branch':outgoing[key]>1,'join':len(task['depends_on'])>1,
                 'completion':'not_tracked'},selection=selection,
                target=self.target('Product',change_id=change_id,stage='plan',selection_revision=selection['selection_revision'],artifact_revision=plan['revision'])))
        return result

    def dependencies(self, selection):
        from cc_product_evolution import dependency_assessment
        from cc_development_lib.method import digest
        result=[]
        for dep in selection['dependencies']:
            change=self.changes.get(dep['consumer_change_id'])
            assessed=dependency_assessment(self.safe,change,dep['gate'],self.product,self.state,selection,dep) if change else {'state':'blocked','reasons':['product_dependency_target_missing']}
            data={**dep,'target_selection_revision':selection['selection_revision'] if dep['target_selection_revision']=='same_increment' else dep['target_selection_revision'],**assessed,'precision':'edge'}
            result.append(self.row('dependency',digest([selection['selection_revision'],dep]),dep['target_feature_id'],data,selection=selection,
                                   target=self.target('Development',change_id=dep['consumer_change_id'],stage=dep['gate'],selection_revision=selection['selection_revision'])))
        return result

    def original_scope(self, change_id, kind, identity):
        """Bind retained facts through original ordered plan/candidate history."""
        from cc_development_history import full_history
        from cc_development_lib.method import digest
        if change_id not in self.original_scopes:
            plan=None; candidates={}; origins={'release':{},'evidence':{}}
            for event in full_history(self.safe,self.changes[change_id]):
                action,result=event['action'],event['result']
                if action['kind']=='decide' and action['stage']=='plan':
                    decision=result['decision']
                    plan=(decision.get('product_binding') or {}).get('selection_revision') if decision['decision']=='accepted' else None
                elif action['kind']=='candidate':
                    candidates[result['digest']]=plan
                elif action['kind'] in origins:
                    origins[action['kind']][digest(result)]=candidates.get(result['candidate_identity'])
            self.original_scopes[change_id]=origins
        revision=self.original_scopes[change_id][kind].get(identity)
        return next((s for s in self.selections if s['selection_revision']==revision
                     and any(c['change_id']==change_id for c in s['changes'])),None)

    def original_row(self, kind, row_id, title, data, change_id, selection, stage, **target_fields):
        assigned=next((c['feature_ids'] for c in selection['changes'] if c['change_id']==change_id),[]) if selection else []
        features=[f for f in selection['features'] if f['feature_id'] in assigned] if selection else []
        feature=features[0] if len(features)==1 else None
        target=self.target('Development',change_id=change_id,stage=stage,
                           selection_revision=selection['selection_revision'] if selection else None,
                           increment_id=selection['id'] if selection else None,
                           feature_id=feature['feature_id'] if feature else None,
                           feature_revision=feature['feature_revision'] if feature else None,**target_fields)
        row=self.row(kind,row_id,title,data,feature=feature,selection=selection,target=target)
        if selection:
            row['scope']['selected_feature_revisions']=[r for r in row['scope']['selected_feature_revisions'] if r['feature_id'] in assigned]
            # Later decisions describe the current lineage head, not this fact.
            adopted=any((r.get('adopted_selection') or {}).get('selection_revision')==selection['selection_revision']
                        for r in self.product.get('evolution',{}).get('scope_decisions',[]))
            row['scope']['decision']='adopt' if adopted else 'selected'
        else:
            row['scope'].update(feature_id=None,feature_revision=None,selected_feature_revisions=[],
                                increment_id=None,selection_revision=None,role=None,decision=None)
        return row

    def releases(self, change_id, selection):
        from cc_development_lib.method import digest
        change=self.changes[change_id];facts=self.change_facts(change_id);result=[]
        for index,release in enumerate(change['releases']):
            original=self.original_scope(change_id,'release',digest(release))
            current=(index==len(change['releases'])-1 and facts['released'] and original is not None)
            assessment=facts['release_assessment'] if index==len(change['releases'])-1 else {'state':'unknown','reasons':['historical_release_not_current']}
            if original is None:
                assessment={'state':'unknown','reasons':['historical_scope_not_established']}
            result.append(self.original_row('release',digest(release),release.get('environment','Release'),
                {'change_id':change_id,'release_identity':digest(release),'candidate_identity':release.get('candidate_identity'),
                 'artifact_identity':release.get('artifact_digest'),'environment':release.get('environment',''),
                 'status':release['status'],'historical':not current,'assessment':{'state':assessment['state'],'reasons':assessment['reasons']}},
                change_id,original,'review_release'))
        return result

    def project(self, query):
        view,entity,collection=query['view'],query['entity_id'],query['collection']
        if view=='overview':
            features=list(self.document['features']) if self.document else []
            # Retired/removed scope remains inspectable under its original ID.
            known={f['id'] for f in features}
            for revision in reversed(self.product.get('revisions',[])):
                for feature in revision['document']['features']:
                    if feature['id'] not in known:
                        features.append(feature);known.add(feature['id'])
            rows=[self.feature(f)[0] for f in features]
            current_rows=[r for r in rows if r['scope']['role'] not in ('retired','superseded')]
            summary=self.aggregate(current_rows,planned=bool(current_rows),actions=[self.target()])
            return self.scope(),summary,rows
        if view=='feature':
            feature=next((f for rev in reversed(self.product.get('revisions',[])) for f in rev['document']['features'] if f['id']==entity),None)
            if feature is None:
                raise ProgressError('progress_entity_missing')
            row,criteria,selections=self.feature(feature)
            rows=criteria if collection=='criteria' else [self.increment(s) for s in selections] if collection=='increments' else []
            if collection=='history_refs':
                for change_id in dict.fromkeys(c['change_id'] for s in self.selections if any(f['feature_id']==entity for f in s['features']) for c in s['changes']):
                    change=self.changes[change_id];base=change.get('history_base',{})
                    rows.append(self.row('history_ref',change_id,change['title'],{'change_id':change_id,'partial':bool(base),
                        'retained_records':base.get('count',0)+len(change['history'])},feature=feature,
                        target=self.target('Development',feature_id=entity,change_id=change_id)))
            return row['scope'],row['summary'],rows
        if view=='increment':
            selection=next((s for s in reversed(self.selections) if s['id']==entity),None)
            if selection is None:
                raise ProgressError('progress_entity_missing')
            row=self.increment(selection)
            rows=[self.change_row(c['change_id'],selection) for c in selection['changes']] if collection=='changes' else self.dependencies(selection) if collection=='dependencies' else [self.criterion(c,f,[selection]) for f in selection['features'] for c in f['criteria']]
            return row['scope'],row['summary'],rows
        from cc_product_evolution import for_change
        if entity not in self.changes:
            raise ProgressError('progress_entity_missing')
        selection=for_change(self.product,entity)
        if selection is None:
            raise ProgressError('progress_entity_missing')
        row=self.change_row(entity,selection)
        if collection=='tasks':
            rows=self.tasks(entity,selection)
        elif collection=='criteria':
            rows=self.change_criteria(entity,selection)
        elif collection=='releases':
            rows=self.releases(entity,selection)
        elif collection=='incidents':
            from cc_development_operations import reports
            rows=[]
            for incident in reports(self.changes[entity],allow_preview=True):
                original=self.original_scope(entity,'release',incident['group']['releaseId'])
                rows.append(self.original_row('incident',incident['id'],incident.get('title',incident['id']),
                    {'change_id':entity,'incident_id':incident['id'],'state':'decided' if incident.get('current_decision') else 'needs_review',
                     'linked_change_ids':incident['linked_changes']},entity,original,'maintain',incident_id=incident['id']))
        else:
            from cc_development_lib.method import digest
            facts=self.change_facts(entity);rows=[]
            for batch in self.changes[entity]['evidence']:
                original=self.original_scope(entity,'evidence',digest(batch))
                for record in batch['records']:
                    current=original is not None and batch['candidate_identity']==(facts['change'].get('candidate') or {}).get('digest')
                    rows.append(self.original_row('evidence',digest([batch['candidate_identity'],record]),record.get('kind','Evidence'),
                        {'change_id':entity,'candidate_identity':batch['candidate_identity'],'kind':record['kind'],
                         'reference':record.get('receiptId',record.get('id',digest(record))),
                         'state':'allowed' if current and facts['verified'] else 'unknown',
                         'reasons':[] if current and facts['verified'] else ['evidence_not_currently_qualified']},entity,original,'verify'))
        return row['scope'],row['summary'],rows


def _boundary(safe):
    from cc_development_bundle import check_pending
    from cc_layout import managed_relative
    check_pending(safe)
    try:
        with safe.directory(managed_relative(safe.path,'.controlcoding/development.lock')):
            raise ProgressError('progress_recovery_required')
    except FileNotFoundError:
        pass
    safe.validate()


def read(project_root, *, view='overview', entity_id=None, collection=None, cursor=None, observation_id=None):
    query_raw={'view':view,'entity_id':entity_id,'collection':collection,'cursor':cursor,'observation_id':observation_id}
    with observing() as observer:
        query=validate_query(query_raw)
        supplied=decode_cursor(cursor) if cursor is not None else None
        if supplied and supplied['expires_at']<=time.time():
            raise ProgressError('progress_cursor_expired')
        from cc_evidence_inputs import SafeRoot,EvidenceError
        from cc_development_lib import storage,check_root
        from cc_development_lib.method import IDENTITY,digest
        last_error=None
        for attempt in range(2):
            if attempt:
                observer.reset_attempt()
            observer.attempts=attempt+1
            try:
                observer.check()
                check_root(project_root)
                with SafeRoot(project_root) as safe:
                    state,revision=storage.load(safe)
                    observer.state,observer.revision,observer.project_path=state,revision,safe.path
                    projection=Projection(safe,state,revision,observer)
                    offset=supplied['offset'] if supplied else 0
                    history_total=None
                    if view=='history':
                        from cc_development_history import page_state
                        if entity_id not in projection.changes:
                            raise ProgressError('progress_entity_missing')
                        scope=projection.scope();summary=summarize(currentness='not_applicable')
                        # History pages bind their authenticated graph root. The
                        # page owner validates each fetched content-addressed
                        # original object without loading a complete archive.
                        history_binding=observer.input_digest()
                        page=page_state(safe,state,revision,entity_id,{'revision':revision,'offset':offset},MAX_ROWS,revision)
                        history_total=page['total']
                        rows=[projection.row('event',event['digest'],event['action']['kind'],{'change_id':entity_id,'event':event}) for event in page['records']]
                    else:
                        scope,summary,rows=projection.project(query)
                        history_binding=None
                    partial_history=(view=='change' and collection in ('releases','evidence','incidents')
                                     and 'history_base' in projection.changes[entity_id])
                    _boundary(safe)
                    if not observer.recheck():
                        last_error='progress_observation_changed'
                        continue
                    observer.check()
                    input_digest=history_binding or observer.input_digest()
                    identity=digest({'project':state['project_identity'],'method':IDENTITY,'control':None if revision=='absent' else revision,
                                     'query':{k:query[k] for k in ('view','entity_id','collection')},'input':input_digest,
                                     'projection':{'scope':scope,'summary':summary,'rows':rows} if view!='history' else {'total':history_total},
                                     'proof_deadline':observer.valid_until})
                    if observation_id is not None and observation_id!=identity:
                        raise ProgressError('progress_observation_changed')
                    bindings={'schema_version':1,'project_identity':state['project_identity'],'method_digest':IDENTITY['digest'],
                              'control_revision':None if revision=='absent' else revision,'observation_id':identity,'input_digest':input_digest,
                              'view':view,'entity_id':entity_id,'collection':query['collection'],'order':'declaration_then_id_v1'}
                    if supplied and any(supplied[key]!=value for key,value in bindings.items()):
                        raise ProgressError('progress_observation_changed')
                    total=history_total if view=='history' else len(rows)
                    if offset>total:
                        raise ProgressError('progress_invalid_cursor')
                    selected=rows if view=='history' else rows[offset:offset+MAX_ROWS]
                    expires=supplied['expires_at'] if supplied else int(datetime.fromisoformat(observer.observed_at).timestamp()+60)
                    if observer.valid_until:
                        expires=min(expires,int(datetime.fromisoformat(observer.valid_until).timestamp()))
                    result={'schema_version':1,'view':view,'entity_id':entity_id,'collection':query['collection'],
                            'project_identity':state['project_identity'],'method':dict(IDENTITY),'control_revision':bindings['control_revision'],
                            'current_product_revision':projection.document_row['revision'] if projection.document_row else None,
                            'accepted_product_revision':projection.accepted if projection.accepted!='absent' else None,
                            'enabled':state['enabled'],'status':'available' if projection.document_row or state['enabled'] else 'empty',
                            'observation':{'observation_id':identity,'observed_at':observer.observed_at,'observation_finished_at':stamp(),
                                           'valid_until':observer.valid_until,'coherence':'coherent','reasons':[],
                                           'completeness_scope':{k:query[k] for k in ('view','entity_id','collection')},
                                           'input_digest':input_digest,'accounting':observer.accounting()},
                            'scope':scope,'summary':summary,'rows':selected,'pagination':{}}
                    while True:
                        end=offset+len(selected)
                        result['pagination']={'returned':len(selected),'total':None if partial_history else total,
                            'total_complete':not partial_history,
                            'next_cursor':encode_cursor({**bindings,'offset':end,'expires_at':expires}) if end<total else None}
                        if len(serialized(result))<=MAX_RESPONSE_BYTES:
                            break
                        if not selected:
                            raise ProgressError('progress_record_too_large')
                        selected.pop()
                        if not selected:
                            raise ProgressError('progress_record_too_large')
                    observer.check()
                    result=validate_response(result)
                    observer.check()
                    if observer.valid_until and datetime.now(timezone.utc)>=datetime.fromisoformat(observer.valid_until):
                        last_error='progress_observation_changed'
                        continue
                    return result
            except ObservationError as error:
                last_error=error.code;break
            except EvidenceError as error:
                if error.code in ('file_changed','root_changed'):
                    last_error='progress_observation_changed';continue
                raise ProgressError(error.code) from None
            except ValueError as error:
                if isinstance(error,ProgressError):
                    raise
                code=getattr(error,'code',str(error))
                if code in ('unsupported_method','method_implementation_changed'):
                    raise ProgressError('progress_unsupported_method') from None
                if code in ('recovery_required','pending_restore_recovery_required'):
                    raise ProgressError('progress_recovery_required') from None
                raise ProgressError('progress_evidence_unreadable') from None
        # A caller accumulating pages must discard its previous page set.
        if observation_id is not None:
            raise ProgressError(last_error or 'progress_observation_changed')
        raise ProgressError(last_error or 'progress_observation_changed')
