"""Opt-in controlled development service with source-bound local approvals."""
from datetime import datetime, timezone
import os
from pathlib import Path
from cc_evidence_inputs import SafeRoot, EvidenceError, snapshot_inputs
from cc_layout import LayoutError
from .method import METHOD, IDENTITY, STAGES, PROFILES, digest
from .schema import DevelopmentError, validate_action, revision, json_copy
from . import storage
from .evaluator import latest, upstream, accepted, capture_candidate, evaluate


def now():
    return datetime.now(timezone.utc).isoformat()


def snapshot(root, state, state_revision, *, locked=False, evaluation_state=None):
    with snapshot_inputs(root):
        return _snapshot(root, state, state_revision, locked=locked, evaluation_state=evaluation_state)


def _snapshot(root, state, state_revision, *, locked=False, evaluation_state=None):
    evaluation_state=state if evaluation_state is None else evaluation_state
    changes = json_copy(state['changes'])
    for change in changes:
        if 'history_base' in change:
            change['history_retention'] = {'partial': True, 'retained_records': change['history_base']['count'] + len(change['history']),
                                          'checkpoint': change['history_base']['ref'], 'inspection': 'development history'}
        evaluated=change if evaluation_state is state else json_copy(next(row for row in evaluation_state['changes'] if row['id']==change['id']))
        change['gates'] = evaluate(root, evaluated, product=evaluation_state.get('product', {}),state=evaluation_state)
        if evaluated is not change and change.get('candidate') is not None:
            change['candidate']['current']=evaluated['candidate'].get('current',False)
        from .policy import job_assessments, execution_reports
        change.update(job_assessments(root.path, change))
        change['execution_reports'] = execution_reports(root.path, change)
        from cc_development_release import packet_reports
        change['release_packets'] = packet_reports(root.path, change)
        release = change['releases'][-1] if change['releases'] else None
        release_assessment = {'state': 'unknown', 'reasons': ['release_observation_missing']}
        if release and release['status'] == 'observed':
            from cc_development_adapters import assess_release
            release_assessment = assess_release(root.path, change, release)
        change['release_assessment'] = release_assessment
        from cc_development_operations import reports as incident_reports
        from cc_development_diagnosis import reports as diagnosis_reports
        change['operational_incidents'] = incident_reports(change, allow_preview=True)
        for incident in change['operational_incidents']:
            incident['linked_changes'] = [{'id': child['id'], 'title': child['title'],
                                           'status': 'closed' if child['closed'] else 'intent_draft' if not child['decisions'] else 'in_progress'}
                                          for child in state['changes'] if child['id'] in incident['linked_changes']]
        change['diagnosis_reports'] = diagnosis_reports(root.path, change, locked=locked)
        if change['closed']:
            change['stage'], change['status'] = 'closed', 'closed'
        else:
            change['stage'] = next((stage for stage in STAGES if not change['gates'][stage]['accepted'] or change['gates'][stage]['outcome'] != 'allowed'), 'maintain')
            gate = change['gates'][change['stage']]
            decisions = [d for d in change['decisions'] if d['stage'] == change['stage']]
            change['status'] = ('stale' if decisions and decisions[-1]['decision'] == 'accepted' else
                                decisions[-1]['decision'] if decisions and decisions[-1]['decision'] != 'accepted' else
                                'awaiting_review' if gate['outcome'] == 'allowed' else 'blocked')
        current_candidate = bool(change.get('candidate')) and change['candidate'].get('current', False)
        change['outcomes'] = {'implemented': bool(change.get('candidate')), 'candidate_current': current_candidate, 'verified': change['gates']['verify']['accepted'] and change['gates']['verify']['outcome'] == 'allowed',
                              'approved': change['gates']['review_release']['accepted'] and change['gates']['review_release']['outcome'] == 'allowed',
                              'released': any(item['status'] == 'observed' for item in change['releases']),
                              'released_current_candidate': current_candidate and bool(release) and release['status'] == 'observed' and release['candidate_identity'] == change['candidate']['digest'] and release_assessment['state'] == 'allowed',
                              'release_status': change['releases'][-1]['status'] if change['releases'] else 'unprepared'}
        change['next_action'] = 'Closed' if change['closed'] else {'intent': 'Review intent', 'design': 'Review specification', 'plan': 'Approve plan', 'build': 'Prepare work', 'verify': 'Run checks', 'review_release': 'Review result', 'maintain': 'Review outcome'}[change['stage']]
    observed_adapters = False
    try:
        from cc_development_jobs import manifest
        config, _, _ = manifest(root.path)
        observed_adapters = any(item['kind'] in ('release_status', 'deploy', 'rollback') for item in config['adapters'])
    except (OSError, ValueError, EvidenceError):
        pass
    from cc_product import view as product_view
    return {'schema_version': 1, 'enabled': state['enabled'], 'revision': state_revision, 'product': product_view(evaluation_state,root=root),
            'profile': state['profile'], 'method': {**IDENTITY, 'artifact_fields': METHOD['artifact_fields'], 'profiles': METHOD['profiles'], 'stages': METHOD['stages']},
            'project_identity': state['project_identity'], 'changes': changes,
            'capabilities': {'local_revision': True, 'authenticated_approval': False, 'external_write_enforcement': False,
                             'deployment': 'configured_observation_adapter' if observed_adapters else 'manual_prepared_or_self_reported', 'autonomous_execution': False,
                             'agent_evaluations': 'versioned_tasks_independent_file_grading',
                             'controlled_execution': 'qualified_proposals_reviewed_scoped_apply',
                             'operational_feedback': 'canonical_incidents_linked_intent_drafts',
                             'memory_required': False, 'planner': 'read_only_reference', 'features': 'read_only_reference'}}


def translate(error):
    if isinstance(error, DevelopmentError):
        return error
    from cc_development_store import StoreError
    if isinstance(error, (EvidenceError, LayoutError, StoreError)):
        return DevelopmentError(error.code)
    return DevelopmentError('development_io_failed')


def read(root):
    try:
        check_root(root)
        with SafeRoot(root) as safe:
            state, state_revision = storage.load(safe)
            view = snapshot(safe, state, state_revision)
            safe.validate()
            return view
    except (OSError, ValueError, TypeError, RecursionError, EvidenceError) as error:
        raise translate(error) from None


def change_for(state, change_id):
    for change in state['changes']:
        if change['id'] == change_id:
            return change
    raise DevelopmentError('change_not_found')


def check_root(root):
    raw = os.fspath(root)
    if '\x00' in raw or raw.startswith(('\\\\', '//')) or '..' in Path(raw).parts:
        raise DevelopmentError('unsafe_project_path')
    from .method import implementation_identity, IMPLEMENTATION_DIGEST
    if implementation_identity() != IMPLEMENTATION_DIGEST:
        raise DevelopmentError('method_implementation_changed')


def apply(root, state, action, *, timestamp):
    state = json_copy(state)
    kind = action['kind']
    if kind.startswith('product_'):
        from cc_product import apply as product_apply
        return product_apply(root,state,action,timestamp)
    operational_result = None
    if kind == 'activate':
        if state['enabled']:
            raise DevelopmentError('method_already_active')
        state['enabled'], state['profile'] = True, action['profile']
        return state
    if not state['enabled']:
        raise DevelopmentError('method_not_active')
    if kind == 'create':
        if any(change['id'] == action['change_id'] for change in state['changes']):
            raise DevelopmentError('change_id_exists')
        if len(state['changes']) >= 256:
            raise DevelopmentError('change_limit')
        profile = PROFILES[max(PROFILES.index(state['profile']), PROFILES.index(action['risk']))]
        state['changes'].append({'id': action['change_id'], 'title': action['title'], 'owner': action['owner'], 'risk': action['risk'],
                                 'profile': profile, 'method': dict(IDENTITY), 'policy_revision': digest([IDENTITY, profile]),
                                 'artifacts': {}, 'decisions': [], 'criterionDecisions': [], 'candidate': None, 'evidence': [], 'reviews': [],
                                 'releases': [], 'observations': [], 'closed': False, 'history': [], 'created_at': timestamp})
        change = state['changes'][-1]
        change.update(incidents=[], incident_captures=[], incident_decisions=[], origin=None)
    else:
        change = change_for(state, action['change_id'])
        if kind not in ('incident_capture','incident_decide','incident_followup'):
            from cc_product import gate_reasons
            gate_stage=action.get('stage', {'candidate':'build','evidence':'verify','review':'review_release','release':'review_release','observation':'maintain','close':'maintain'}.get(kind,'intent'))
            product_reasons=gate_reasons(root,change,gate_stage,state.get('product',{}),state=state)
            # Publishing a revised artifact is permitted after adoption, while
            # actual candidate, approval, evidence and release paths recheck scope.
            if kind not in ('artifact','decide') and product_reasons:
                raise DevelopmentError('gate_blocked:' + ','.join(product_reasons))
        if change['closed'] and kind not in ('incident_capture', 'incident_decide', 'incident_followup'):
            raise DevelopmentError('change_closed')
        if kind in ('incident_capture', 'incident_decide', 'incident_followup'):
            from cc_development_operations import capture, incident_view, proposed_intent
            try:
                if kind == 'incident_capture':
                    captured = capture(root.path, change, action['receipt_id'], action['signal_id'], timestamp)
                    observation = captured['observation']
                    if not captured['replay']:
                        if len(change['incident_captures']) >= 256:
                            raise DevelopmentError('incident_capture_limit')
                        change['incident_captures'].append(observation)
                        upserts = {item['id']: item for item in captured['incidents']}
                        change['incidents'] = [upserts.pop(item['id'], item) for item in change['incidents']] + list(upserts.values())
                        change['observations'].append({'signals': [{'id': action['signal_id'], 'kind': 'monitor',
                            'receiptId': action['receipt_id'], 'observation': observation['receipt']['observation']}],
                            'actor': action['actor'], 'timestamp': timestamp,
                            'candidate_identity': observation['group']['candidateDigest'],
                            'release_identity': observation['group']['releaseId'],
                            'release_artifact_identity': observation['group']['deployedDigest'], 'provenance': 'authoritative_adapter'})
                    operational_result = {'capture': observation, 'replayed': captured['replay'],
                                          'observation': None if captured['replay'] else change['observations'][-1]}
                else:
                    incident = incident_view(change, action['incident_id'])
                    incident = next(item for item in change['incidents'] if item['id'] == incident['id'])
                    if len(change['incident_decisions']) >= 256:
                        raise DevelopmentError('incident_decision_limit')
                    decision = {'incident_id': incident['id'], 'incident_revision': incident['revision'],
                                'decision': 'followup' if kind == 'incident_followup' else action['decision'],
                                'owner': action['owner'], 'actor': action['actor'], 'reason': action['reason'],
                                'timestamp': timestamp, 'child_id': action.get('child_id')}
                    origin = None
                    if kind == 'incident_followup':
                        if any(item['incident_id'] == incident['id'] and item['child_id'] for item in change['incident_decisions']):
                            raise DevelopmentError('incident_followup_exists')
                        origin = {'kind': 'incident_followup', 'change_id': change['id'],
                                  'incident_id': incident['id'], 'incident_revision': incident['revision']}
                        risk = PROFILES[max(PROFILES.index(change['profile']), PROFILES.index(action['risk']))]
                        state = apply(root, state, {'kind': 'create', 'change_id': action['child_id'],
                                      'title': action['title'], 'owner': action['owner'], 'risk': risk}, timestamp=timestamp)
                        child = change_for(state, action['child_id'])
                        child['origin'] = origin
                        child['history'][0]['result']['origin'] = origin
                        child['history'][0]['digest'] = digest({key: value for key, value in child['history'][0].items() if key != 'digest'})
                        state = apply(root, state, {'kind': 'artifact', 'change_id': action['child_id'], 'stage': 'intent',
                                      'content': proposed_intent(incident), 'actor': action['actor']}, timestamp=timestamp)
                        change = change_for(state, action['change_id'])
                    change['incident_decisions'].append(decision)
                    operational_result = {'decision': decision, 'origin': origin}
            except EvidenceError as error:
                raise DevelopmentError(error.code) from None
        elif kind == 'artifact':
            stage = action['stage']
            content = action['content']
            value_digest = digest(content)
            versions = change['artifacts'].setdefault(stage, [])
            versions.append({'revision': value_digest, 'digest': value_digest, 'content': content,
                             'upstream': upstream(change, stage), 'actor': action['actor'], 'timestamp': timestamp,
                             'owner': 'controlled_development', 'provenance': 'local_revision'})
        elif kind == 'candidate':
            plan = latest(change, 'plan')
            if not plan or not accepted(change, 'plan'):
                raise DevelopmentError('approved_plan_required')
            if not set(action['files']).issubset(set(plan['content']['files'])):
                raise DevelopmentError('candidate_outside_plan_scope')
            captured = capture_candidate(root, change, action['files'])
            change['candidate'] = {**captured, 'actor': action['actor'], 'timestamp': timestamp}
        elif kind == 'decide':
            stage = action['stage']
            artifact = latest(change, stage)
            if artifact is None:
                raise DevelopmentError('artifact_missing')
            from cc_product import decision_binding
            product_binding=decision_binding(state.get('product'),change,stage)
            if action['decision'] == 'accepted':
                critical_decision = stage == 'review_release' and change['profile'] == 'critical'
                policy_digest = None
                if critical_decision:
                    from .policy import critical_policy
                    policy = critical_policy(root.path)
                    if policy['state'] != 'allowed' or action['actor'] not in policy['approvers'].values():
                        raise DevelopmentError('critical_named_approver_required')
                    policy_digest = policy['digest']
                    provisional = {'stage': stage, 'decision': action['decision'], 'artifact_revision': artifact['revision'],
                                   'upstream': artifact['upstream'], 'candidate_identity': (change.get('candidate') or {}).get('digest'),
                                   'actor': action['actor'], 'reason': action['reason'], 'timestamp': timestamp,
                                   'provenance': 'local_actor_label_not_authenticated', 'policy_digest': policy_digest,
                                   'method': dict(IDENTITY)}
                    change['decisions'].append(provisional)
                if stage == 'verify' and change.get('candidate'):
                    # This explicit decision accepts named human observations;
                    # the imported record's own passed/accepted claims are ignored.
                    for batch in change['evidence']:
                        if batch['candidate_identity'] != change['candidate']['digest']:
                            continue
                        for record in batch['records']:
                            if record.get('kind') == 'human':
                                change['criterionDecisions'].append({'criterionId': record.get('criterionId'),
                                    'candidateDigest': change['candidate']['digest'], 'evidenceDigest': digest(record),
                                    'actor': action['actor'], 'decision': 'accept', 'timestamp': timestamp})
                # Reacceptance evaluates the newly reviewed binding, while the
                # retained older decision continues to describe history only.
                gate_change=json_copy(change)
                if product_binding and gate_change['decisions']:
                    for prior in gate_change['decisions']:
                        if prior['stage']==stage:prior['product_binding']=product_binding
                gate = evaluate(root, gate_change, product=state.get('product',{}),state=state)[stage]
                from cc_product import gate_reasons
                product_blockers = gate_reasons(root,gate_change,stage,state.get('product'),state=state)
                if product_blockers:
                    raise DevelopmentError('gate_blocked:' + ','.join(product_blockers))
                if gate['outcome'] != 'allowed' and not (critical_decision and gate['reasons'] and all(reason.startswith('critical_approval_missing:') for reason in gate['reasons'])):
                    raise DevelopmentError('gate_blocked:' + ','.join(gate['reasons']))
                if stage in ('verify', 'review_release') and action['actor'] == (change.get('candidate') or {}).get('actor'):
                    raise DevelopmentError('independent_actor_required')
                if critical_decision:
                    change['decisions'].pop()
            change['decisions'].append({'stage': stage, 'decision': action['decision'], 'artifact_revision': artifact['revision'],
                                        'upstream': artifact['upstream'], 'candidate_identity': (change.get('candidate') or {}).get('digest'),
                                        'actor': action['actor'], 'reason': action['reason'], 'timestamp': timestamp,
                                        'provenance': 'local_actor_label_not_authenticated',
                                        'method': dict(IDENTITY),
                                        'policy_digest': policy_digest if action['decision'] == 'accepted' and stage == 'review_release' and change['profile'] == 'critical' else None})
            if product_binding:change['decisions'][-1]['product_binding']=product_binding
        elif kind == 'evidence':
            if not change.get('candidate'):
                raise DevelopmentError('candidate_missing')
            change['evidence'].append({'records': action['records'], 'actor': action['actor'], 'candidate_identity': change['candidate']['digest'], 'timestamp': timestamp})
        elif kind == 'review':
            if not change.get('candidate'):
                raise DevelopmentError('candidate_missing')
            if action['actor'] == change['candidate']['actor']:
                raise DevelopmentError('independent_actor_required')
            change['reviews'].append({'findings': action['findings'], 'actor': action['actor'], 'reason': action['reason'],
                                      'candidate_identity': change['candidate']['digest'], 'timestamp': timestamp,
                                      'provenance': 'local_actor_label_not_authenticated'})
        elif kind == 'release':
            gate = evaluate(root, change,product=state.get('product',{}),state=state)['review_release']
            if not accepted(change, 'review_release') or gate['outcome'] != 'allowed':
                raise DevelopmentError('release_gate_blocked')
            artifact = latest(change, 'review_release')['content']
            if action['artifact_digest'] != artifact['release_artifact_identity'] or action['environment'] != artifact['environment']:
                raise DevelopmentError('release_authorization_mismatch')
            if action['status'] == 'observed':
                try:
                    from cc_development_adapters import assess_release
                except ImportError:
                    raise DevelopmentError('release_observation_adapter_unavailable') from None
                assessment = assess_release(root.path, change, action)
                if assessment.get('state') != 'allowed':
                    raise DevelopmentError('release_observation_unverified')
            change['releases'].append({**{key: value for key, value in action.items() if key not in ('kind', 'change_id')},
                                       'candidate_identity': change['candidate']['digest'], 'timestamp': timestamp,
                                       'provenance': 'authoritative_adapter' if action['status'] == 'observed' else 'local_self_report'})
        elif kind == 'observation':
            candidate = change.get('candidate')
            releases = [record for record in change['releases'] if candidate and record['candidate_identity'] == candidate['digest']]
            if not candidate or not releases:
                raise DevelopmentError('observation_release_required')
            change['observations'].append({'signals': action['signals'], 'actor': action['actor'], 'timestamp': timestamp,
                                          'candidate_identity': candidate['digest'], 'release_identity': digest(releases[-1]),
                                          'release_artifact_identity': releases[-1]['artifact_digest'], 'provenance': 'self_reported'})
        elif kind == 'close':
            if not accepted(change, 'maintain') or evaluate(root, change)['maintain']['outcome'] != 'allowed':
                raise DevelopmentError('outcome_gate_blocked')
            change['closed'] = True
    if change.get('candidate'):
        change['candidate'].pop('current', None)
    result = operational_result
    if kind == 'create':
        result = {key: value for key, value in change.items() if key not in ('history', 'artifacts', 'decisions', 'criterionDecisions', 'candidate', 'evidence', 'reviews', 'releases', 'observations', 'closed', 'incidents', 'incident_captures', 'incident_decisions')}
    elif kind == 'artifact':
        result = latest(change, action['stage'])
    elif kind == 'candidate':
        result = change['candidate']
    elif kind == 'decide':
        result = {'decision': change['decisions'][-1], 'criterion_decisions': change['criterionDecisions']}
    elif kind in ('evidence', 'review', 'release', 'observation'):
        result = change[{'evidence': 'evidence', 'review': 'reviews', 'release': 'releases', 'observation': 'observations'}[kind]][-1]
    elif kind == 'close':
        result = True
    event = {'action': action, 'result': json_copy(result), 'timestamp': timestamp,
             'previous': change['history'][-1]['digest'] if change['history'] else change.get('history_base', {}).get('head', 'absent')}
    event['digest'] = digest(event)
    change['history'].append(event)
    return state


def prepared(root, state, state_revision, action, *, locked=False):
    view = snapshot(root, state, state_revision, locked=locked)
    blockers = []
    try:
        proposed = apply(root, state, action, timestamp='preview')
    except DevelopmentError as error:
        proposed = state
        blockers.append(error.code)
    proposed_view = snapshot(root, proposed, state_revision, locked=locked)
    approval_id = digest({'project_identity': state['project_identity'], 'revision': state_revision,
                          'action': action, 'current_view': view, 'proposed_view': proposed_view})
    return {'schema_version': 1, 'revision': state_revision, 'approval_id': approval_id, 'action': action,
            'effects': ['Record ' + action['kind'] + ' in controlled development history'], 'blockers': blockers,
            'view': proposed_view, 'project_identity': state['project_identity']}


def recovery_prepared(root, state, state_revision, action):
    fingerprint, process, _ = storage.recovery_lock(root)
    blockers = []
    if action['lock_fingerprint'] != fingerprint:
        blockers.append('recovery_lock_changed')
    if process != 'inactive':
        blockers.append('recovery_writer_' + process)
    view = snapshot(root, state, state_revision)
    approval_id = digest({'project_identity': state['project_identity'], 'revision': state_revision,
                          'action': action, 'current_view': view, 'lock_fingerprint': fingerprint})
    return {'schema_version': 1, 'revision': state_revision, 'approval_id': approval_id, 'action': action,
            'effects': ['Remove the observed abandoned development lock and record recovery'],
            'blockers': blockers, 'view': view, 'project_identity': state['project_identity']}


def recovery_prepare(root, actor, reason):
    """Inspect recovery without weakening the ordinary read lock contract."""
    try:
        check_root(root)
        with SafeRoot(root) as safe:
            state, state_revision = storage.load(safe, locked=True)
            fingerprint, _, _ = storage.recovery_lock(safe)
            action = validate_action({'kind': 'recover', 'actor': actor, 'reason': reason, 'lock_fingerprint': fingerprint})
            return recovery_prepared(safe, state, state_revision, action)
    except (OSError, ValueError, TypeError, EvidenceError) as error:
        raise translate(error) from None


def prepare(root, action, expected_revision):
    action = validate_action(action)
    revision(expected_revision)
    try:
        check_root(root)
        with SafeRoot(root) as safe:
            state, state_revision = storage.load(safe, locked=action['kind'] == 'recover')
            if state_revision != expected_revision:
                raise DevelopmentError('revision_conflict')
            result = recovery_prepared(safe, state, state_revision, action) if action['kind'] == 'recover' else prepared(safe, state, state_revision, action)
            safe.validate()
            return result
    except (OSError, ValueError, TypeError, RecursionError, EvidenceError) as error:
        raise translate(error) from None


def commit(root, action, expected_revision, approval_id):
    action = validate_action(action)
    revision(expected_revision)
    revision(approval_id)
    if approval_id == 'absent':
        raise DevelopmentError('invalid_approval_id')
    try:
        check_root(root)
        with SafeRoot(root) as safe:
            # A successful retry is read-only, including when another operation
            # subsequently advanced the registry. The original action is bound.
            state, state_revision = storage.load(safe, locked=action['kind'] == 'recover')
            previous = next((item for item in state['operations'] if item['approval_id'] == approval_id), None)
            if previous is None and state.get('retention'):
                from cc_development_history import lookup_operation
                from cc_development_store import read_state
                previous = lookup_operation(safe, read_state(safe)[2], approval_id)
            if previous:
                if previous['action'] != action or previous['expected_revision'] != expected_revision:
                    raise DevelopmentError('idempotency_conflict')
                return {'schema_version': 1, 'view': snapshot(safe, state, state_revision),
                        'receipt': {**previous, 'revision': state_revision, 'idempotent': True}}
            if action['kind'] == 'recover':
                if state_revision != expected_revision:
                    raise DevelopmentError('revision_conflict')
                preview = recovery_prepared(safe, state, state_revision, action)
                if preview['approval_id'] != approval_id:
                    raise DevelopmentError('approval_conflict')
                if preview['blockers']:
                    raise DevelopmentError(preview['blockers'][0])
                fingerprint, process, lock_info = storage.recovery_lock(safe)
                if fingerprint != action['lock_fingerprint'] or process != 'inactive':
                    raise DevelopmentError('recovery_lock_changed')
                storage.release_lock(safe, expected=lock_info)
            with storage.transaction(safe, approval_id):
                state, state_revision = storage.load(safe, locked=True)
                if state_revision != expected_revision:
                    raise DevelopmentError('revision_conflict')
                if action['kind'] != 'recover':
                    preview = prepared(safe, state, state_revision, action, locked=True)
                    if preview['approval_id'] != approval_id:
                        raise DevelopmentError('approval_conflict')
                    if preview['blockers']:
                        raise DevelopmentError(preview['blockers'][0])
                if len(state['operations']) >= 64 or any(len(c['history']) >= 64 for c in state['changes']):
                    from cc_development_history import _compact
                    from cc_development_store import read_state
                    state = _compact(safe, state, read_state(safe)[2])
                updated = json_copy(state) if action['kind'] == 'recover' else apply(safe, state, action, timestamp=now())
                receipt = {'operation_id': approval_id, 'approval_id': approval_id, 'expected_revision': expected_revision,
                           'action': action, 'timestamp': now(), 'provenance': 'local_revision'}
                updated['operations'].append(receipt)
                if len(updated['operations']) > 2048:
                    raise DevelopmentError('checkpoint_required')
                storage.validate_state(safe, updated, compatibility=storage.compatibility_for(safe))
                new_revision = storage.save(safe, updated, state_revision, capacity_class='planning' if action['kind'].startswith('product_') else 'operational')
                return {'schema_version': 1, 'view': snapshot(safe, updated, new_revision, locked=True),
                        'receipt': {**receipt, 'revision': new_revision, 'idempotent': False}}
    except (OSError, ValueError, TypeError, RecursionError, EvidenceError) as error:
        raise translate(error) from None
