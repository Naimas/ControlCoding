"""Current artifact/candidate gates; projections never establish approval."""
from cc_evidence_inputs import Budget, EvidenceError
from .method import STAGES, IDENTITY, digest


def latest(change, stage):
    records = change['artifacts'].get(stage, [])
    return records[-1] if records else None


def upstream(change, stage):
    return {key: value['digest'] for key in STAGES[:STAGES.index(stage)]
            if (value := latest(change, key)) is not None}


def candidate_identity(change, files):
    return digest({'files': files, 'method': change['method']['digest'], 'upstream': upstream(change, 'build')})


def capture_candidate(root, change, paths):
    budget = Budget(total=64 * 1024 * 1024, per_file=8 * 1024 * 1024, entries=256)
    import hashlib
    files = []
    for path in sorted(paths):
        budget.entry()
        data, info = root.read(path, budget)
        if info.st_nlink != 1:
            from .schema import DevelopmentError
            raise DevelopmentError('unsafe_candidate_file')
        files.append({'path': path, 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)})
    root.validate()
    return {'digest': candidate_identity(change, files), 'files': files, 'upstream': upstream(change, 'build')}


def accepted(change, stage, product=None, root=None):
    artifact = latest(change, stage)
    if artifact is None or artifact['upstream'] != upstream(change, stage):
        return False
    decisions = [item for item in change['decisions'] if item['stage'] == stage]
    if not decisions:
        return False
    decision = decisions[-1]
    if stage in STAGES[3:] and decision.get('method', change['method']) != IDENTITY:
        return False
    if product is not None and stage in ('intent','design','plan'):
        from cc_product import increment_for,decision_binding
        if increment_for(product,change['id']):
            if stage in ('design','plan'):
                from cc_product_planning import applicability,profile,latest_detail
                contract=applicability(product,change,root)
                if contract=='corrupt':return False
                if contract=='detailed':
                    detail=latest_detail(product,change['id'],stage);current_profile=profile(product,change['id'])
                    if not detail or detail['profile_revision']!=current_profile['revision'] or detail['artifact_revision']!=artifact['revision']:return False
            from cc_product_evolution import selection_applicable
            current=decision_binding(product,change,stage);bound=decision.get('product_binding',{})
            if not selection_applicable(product,change['id'],bound.get('selection_revision')) or bound.get('review_revision')!=current['review_revision']:return False
    return (decision['decision'] == 'accepted' and decision['artifact_revision'] == artifact['revision']
            and decision['upstream'] == artifact['upstream']
            and (stage not in STAGES[3:] or decision['candidate_identity'] == (change.get('candidate') or {}).get('digest')))


def unresolved(items):
    if not isinstance(items, list):
        return True
    return any(isinstance(item, dict) and item.get('blocking', False) and item.get('disposition') not in ('resolved', 'accepted') for item in items)


def assess_receipts(root, change):
    from cc_product_progress_observation import current
    observer = current()
    key = ('receipts', change['id'], digest(change))
    if observer is not None and key in observer.memo:
        return observer.memo[key]
    try:
        from cc_development_adapters import assess_evidence
    except ImportError:
        return {'state': 'unknown', 'reasons': ['evidence_adapter_unavailable']}
    records = [record for batch in change['evidence'] for record in batch['records']
               if batch['candidate_identity'] == (change.get('candidate') or {}).get('digest')]
    result = assess_evidence(root.path, change, records)
    if observer is not None:
        observer.memo[key] = result
    return result


def evaluate(root, change, *, through_stage=None, product=None,state=None,dependency_stack=()):
    from cc_product_progress_observation import current
    observer = current()
    if observer is None:
        return _evaluate(root, change, through_stage=through_stage, product=product,
                         state=state, dependency_stack=dependency_stack)
    observer.check()
    key = ('gates', change['id'], through_stage, dependency_stack, id(state))
    if key not in observer.memo:
        result = _evaluate(root, change, through_stage=through_stage, product=product,
                           state=state, dependency_stack=dependency_stack)
        observer.memo[key] = (result, (change.get('candidate') or {}).get('current', False))
    result, candidate_current = observer.memo[key]
    if change.get('candidate') is not None:
        change['candidate']['current'] = candidate_current
    return result


def _evaluate(root, change, *, through_stage=None, product=None,state=None,dependency_stack=()):
    if product is None:
        from cc_development_store import read_state
        state,_,_=read_state(root)
        product=(state or {}).get('product',{})
    candidate = change.get('candidate')
    candidate_current = False
    candidate_reasons = []
    if candidate:
        try:
            current = capture_candidate(root, change, [item['path'] for item in candidate['files']])
            candidate_current = current['digest'] == candidate['digest'] and candidate['upstream'] == upstream(change, 'build')
        except (OSError, ValueError, EvidenceError) as error:
            candidate_reasons.append(getattr(error, 'code', 'candidate_unreadable'))
        if not candidate_current:
            candidate_reasons.append('candidate_stale')
        candidate['current'] = candidate_current
    gates = {}
    evidence_assessment = None
    evaluated_stages = STAGES if through_stage is None else STAGES[:STAGES.index(through_stage) + 1]
    for index, stage in enumerate(evaluated_stages):
        from cc_product import gate_reasons
        reasons = gate_reasons(root,change,stage,product,state=state,dependency_stack=dependency_stack)
        monitoring_unknown = False
        artifact = latest(change, stage)
        if not artifact:
            reasons.append('artifact_missing')
        elif artifact['upstream'] != upstream(change, stage):
            reasons.append('upstream_stale')
        for before in STAGES[:index]:
            if not accepted(change, before, product, root):
                reasons.append('upstream_' + before + '_not_accepted')
            elif gates[before]['outcome'] != 'allowed':
                reasons.append('upstream_' + before + '_not_current')
        content = artifact['content'] if artifact else {}
        if stage in STAGES[2:]:
            from cc_development_adapters import assess_owner_bindings
            bindings = assess_owner_bindings(root.path, change)
            if bindings['state'] != 'allowed':
                reasons.extend(bindings['reasons'])
        if stage == 'intent' and unresolved(content.get('open_questions', [])):
            reasons.append('blocking_questions_unresolved')
        if stage == 'design':
            intent = latest(change, 'intent')
            if intent and content.get('upstream_intent_revision') != intent['revision']:
                reasons.append('intent_reference_stale')
            if unresolved(content.get('policy_concerns', [])) or unresolved(content.get('risks', [])):
                reasons.append('blocking_concerns_unresolved')
        if stage == 'plan':
            design = latest(change, 'design')
            if design and content.get('upstream_specification_revision') != design['revision']:
                reasons.append('specification_reference_stale')
            criteria = design['content']['criteria'] if design else []
            mapping = content.get('criterion_check_mapping', {})
            if type(mapping) is not dict or any(item['required'] and item['id'] not in mapping for item in criteria):
                reasons.append('criterion_mapping_incomplete')
            elif artifact:
                declared = {item['id']: item for item in criteria}
                for key, checks in mapping.items():
                    if key not in declared:
                        reasons.append('criterion_mapping_unknown')
                        continue
                    expected = declared[key]['checks']
                    if all(isinstance(check, str) for check in checks):
                        matches = sorted(checks) == sorted(check['id'] for check in expected)
                    else:
                        matches = sorted((check['kind'], check['id']) for check in checks) == sorted((check['kind'], check['id']) for check in expected)
                    if not matches:
                        reasons.append('criterion_mapping_mismatch:' + key)
            if content.get('dependencies'):
                reasons.append('external_dependencies_unresolved')
        if stage in STAGES[3:]:
            if not candidate:
                reasons.append('candidate_missing')
            elif not candidate_current:
                reasons.extend(candidate_reasons)
            if stage == 'build':
                plan = latest(change, 'plan')
                if plan and content.get('approved_plan_revision') != plan['revision']:
                    reasons.append('plan_reference_stale')
                if candidate and content.get('source_identity') != candidate['digest']:
                    reasons.append('candidate_reference_stale')
                if content.get('deviations'):
                    reasons.append('plan_amendment_required')
                if candidate and sorted(content.get('changed_files', [])) != sorted(item['path'] for item in candidate['files']):
                    reasons.append('candidate_inventory_mismatch')
                provenance = content.get('execution_provenance')
                if isinstance(provenance, dict) and provenance.get('mode', 'manual') != 'manual':
                    if provenance.get('mode') == 'controlled_proposal':
                        from .policy import applied_execution
                        observed_execution = applied_execution(root.path, change, provenance)
                        reasons.extend(observed_execution['reasons'])
                    else:
                        from .policy import automation_qualification
                        qualification = automation_qualification(root.path, change, provenance)
                        reasons.extend(qualification['reasons'])
                        reasons.append('coding_host_adapter_unavailable')
            elif stage in ('verify', 'review_release') and candidate and content.get('candidate_identity') != candidate['digest']:
                reasons.append('candidate_reference_stale')
        assessment = None
        if stage in ('verify', 'review_release', 'maintain') and candidate_current:
            if evidence_assessment is None:
                evidence_assessment = assess_receipts(root, change)
            assessment = evidence_assessment
            if assessment.get('state') != 'allowed':
                reasons.extend(assessment.get('reasons') or ['evidence_unknown'])
        if stage == 'verify' and content.get('unresolved_failures'):
            reasons.append('verification_failures_unresolved')
        if stage in ('review_release', 'maintain'):
            reviews = [review for review in change['reviews'] if candidate and review['candidate_identity'] == candidate['digest']]
            if not reviews:
                reasons.append('independent_review_missing')
            elif any(finding['severity'] in ('major', 'critical') and finding['disposition'] != 'resolved' for finding in reviews[-1]['findings']):
                reasons.append('review_findings_unresolved')
        pull_request = None
        if stage == 'review_release':
            from cc_development_release import assessment as assess_pull_request
            pull_request = assess_pull_request(root.path, change)
            if pull_request['state'] != 'allowed':
                reasons.extend(pull_request['reasons'])
        critical = None
        if stage == 'review_release' and change['profile'] == 'critical':
            from .policy import critical_assessment
            critical = critical_assessment(root.path, change, assessment)
            reasons.extend(critical['reasons'])
        if stage == 'maintain':
            releases = [release for release in change['releases'] if candidate and release['candidate_identity'] == candidate['digest']]
            if not releases:
                reasons.append('release_record_missing')
            elif content.get('deployed_artifact_identity') != releases[-1]['artifact_digest']:
                reasons.append('release_artifact_reference_stale')
            release_identity = digest(releases[-1]) if releases else None
            observations = [batch for batch in change['observations']
                            if candidate and batch.get('candidate_identity') == candidate['digest']
                            and batch.get('release_identity') == release_identity and batch['signals']]
            if not observations:
                reasons.append('operational_observation_missing')
            from .policy import job_assessments
            monitoring = job_assessments(root.path, change)['monitoring']
            current_monitors = {item['id']: item for item in monitoring if item['current']}
            declared_monitors = {record['id'] for batch in observations for record in batch['signals'] if record.get('kind') == 'monitor'}
            declared_monitors.update(record['id'] for record in content.get('signals', [])
                                     if isinstance(record, dict) and record.get('kind') == 'monitor' and 'id' in record)
            for monitor_id in declared_monitors:
                monitor = current_monitors.get(monitor_id)
                if monitor is None:
                    monitoring_unknown = True
                    reasons.append('monitor_observation_missing:' + monitor_id)
                elif monitor['state'] != 'allowed':
                    monitoring_unknown = monitoring_unknown or monitor['state'] == 'unknown'
                    reasons.extend('monitor:' + reason for reason in monitor.get('reasons') or ['observation_unknown'])
            incident_ids = {proposal['id'] for item in monitoring for proposal in item.get('proposals', [])}
            dispositions = {item.get('incident_id') for item in content.get('dispositions', [])
                            if isinstance(item, dict) and item.get('decision') in ('resolved', 'assigned', 'deferred')
                            and isinstance(item.get('owner'), str) and item['owner'].strip()
                            and isinstance(item.get('reason'), str) and item['reason'].strip()}
            from cc_development_operations import reports as incident_reports
            operational_incidents = incident_reports(change, allow_preview=True)
            for incident in operational_incidents:
                if incident['current_decision']:
                    group = incident['group']
                    dispositions.add(digest({'deployedDigest': group['deployedDigest'], 'window': group['window'], 'metric': group['metric']}))
                else:
                    reasons.append('operational_incident_disposition_missing:' + incident['id'])
            if incident_ids - dispositions:
                reasons.append('monitor_incident_disposition_missing')
            if unresolved(content.get('findings', [])):
                reasons.append('operational_findings_unresolved')
        outcome = 'blocked' if reasons else 'allowed'
        if reasons and (monitoring_unknown or assessment and assessment.get('state') == 'unknown'):
            outcome = 'unknown'
        gates[stage] = {'outcome': outcome, 'reasons': list(dict.fromkeys(reasons)), 'accepted': accepted(change, stage, product, root)}
        if assessment:
            gates[stage]['evidence'] = assessment
        if critical:
            gates[stage]['policy'] = critical
        if pull_request:
            gates[stage]['pull_request'] = pull_request
    return gates
