"""Read-only critical policy and canonical adapter observation assessment."""
from cc_layout import managed_relative
from cc_evidence_inputs import EvidenceError
from .schema import DevelopmentError, text
from .method import digest

ROLES = ('technical', 'policy', 'environment')


def critical_policy(root):
    from cc_development_adapters import read_json
    try:
        value, source_digest = read_json(root, managed_relative(root, '.controlcoding/development-policy.json'))
        if type(value) is not dict or set(value) != {'schema_version', 'critical'} or type(value['schema_version']) is not int or value['schema_version'] != 1:
            raise DevelopmentError('critical_policy_invalid')
        critical = value['critical']
        if type(critical) is not dict or set(critical) != {'approvers', 'security_checks', 'recovery_receipt'}:
            raise DevelopmentError('critical_policy_invalid')
        approvers = critical['approvers']
        if type(approvers) is not dict or set(approvers) != set(ROLES):
            raise DevelopmentError('critical_policy_invalid')
        for actor in approvers.values():
            text(actor, 'approver', limit=160)
        if len(set(approvers.values())) != len(ROLES):
            raise DevelopmentError('critical_role_separation_required')
        checks = critical['security_checks']
        if type(checks) is not list or not checks or len(checks) > 128:
            raise DevelopmentError('critical_security_checks_missing')
        for check in checks:
            if type(check) is not dict or set(check) != {'kind', 'id'} or check['kind'] not in ('verification', 'invariants'):
                raise DevelopmentError('critical_security_check_invalid')
            from .schema import identifier
            identifier(check['id'])
        receipt = critical['recovery_receipt']
        if type(receipt) is not dict or set(receipt) != {'receiptId', 'observation'} or type(receipt['observation']) is not dict:
            raise DevelopmentError('critical_recovery_receipt_invalid')
        return {'state': 'allowed', 'reasons': [], 'digest': source_digest, **critical}
    except (OSError, ValueError, KeyError, TypeError, EvidenceError) as error:
        return {'state': 'unknown', 'reasons': [getattr(error, 'code', 'critical_policy_unavailable')], 'digest': None}


def critical_assessment(root, change, evidence):
    policy = critical_policy(root)
    reasons = list(policy['reasons'])
    if policy['state'] != 'allowed':
        return policy
    candidate = change.get('candidate') or {}
    if candidate.get('actor') in policy['approvers'].values():
        reasons.append('critical_approver_is_implementer')
    from .evaluator import latest
    artifact = latest(change, 'review_release') or {}
    for role, actor in policy['approvers'].items():
        decisions = [decision for decision in change['decisions'] if decision['stage'] == 'review_release' and decision['actor'] == actor]
        if not decisions or not (decisions[-1]['decision'] == 'accepted'
                and decisions[-1]['artifact_revision'] == artifact.get('revision')
                and decisions[-1]['candidate_identity'] == candidate.get('digest')
                and decisions[-1].get('policy_digest') == policy['digest']):
            reasons.append('critical_approval_missing:' + role)
    for check in policy['security_checks']:
        assessed = (evidence or {}).get('checks', {}).get(check['kind'], {})
        if check['id'] not in assessed.get('required', []) or not assessed.get('assessment', {}).get('currentRequiredPass'):
            reasons.append('critical_security_check_unproved:' + check['id'])
    from cc_development_jobs import assess_job_observation
    recovery = assess_job_observation(root, policy['recovery_receipt'], change, 'rollback')
    if recovery['state'] != 'allowed':
        reasons.extend('critical_recovery:' + reason for reason in recovery['reasons'])
    else:
        observation = recovery['observation']
        configuration = recovery['configuration']
        content = artifact.get('content', {})
        if (configuration.get('mode') != 'rehearsal' or observation.get('mode') != 'rehearsal'
                or configuration.get('environment') != content.get('environment')
                or observation.get('environment') != content.get('environment')
                or observation.get('restoredDigest') != configuration.get('knownArtifactDigest')
                or observation.get('status') != 'rolled_back' or observation.get('candidateDigest') != candidate.get('digest')):
            reasons.append('critical_recovery_not_observed')
    return {**policy, 'state': 'blocked' if reasons else 'allowed', 'reasons': reasons, 'provenance': 'local_named_roles_not_authenticated'}


def job_assessments(root, change):
    from cc_development_jobs import assess_job_observation
    from cc_development_adapters import assess_agent_eval, assess_monitor
    evaluations, monitors, incident_ids = [], [], set()
    candidate_digest = (change.get('candidate') or {}).get('digest')
    releases = [release for release in change['releases'] if release['candidate_identity'] == candidate_digest]
    release_digest = digest(releases[-1]) if releases else None
    evaluation_records = [(batch, record) for batch in change['evidence'] for record in batch['records']
                          if record.get('kind') == 'agent_eval']
    latest_records = {record['id']: index for index, (_, record) in enumerate(evaluation_records)}
    for index, (batch, record) in enumerate(evaluation_records):
        current = latest_records[record['id']] == index and batch.get('candidate_identity') == candidate_digest
        observed = assess_job_observation(root, record, change, 'agent_eval')
        result = observed
        if observed['state'] == 'allowed' and observed.get('configuration', {}).get('evaluationVersion') != 2:
            result = {**observed, **assess_agent_eval(change, observed['configuration'], observed['observation'])}
        evaluations.append({'id': record['id'], **result, 'current': current,
                            'qualification': current and result.get('qualification') is True and result['state'] == 'allowed'})
    for batch in change['observations']:
        current = batch.get('candidate_identity') == candidate_digest and batch.get('release_identity') == release_digest
        for record in batch['signals']:
            if record.get('kind') != 'monitor':
                continue
            result = assess_monitor(root, change, record, incident_ids)
            monitors.append({'id': record['id'], 'current': current, **result})
            incident_ids.update(proposal['id'] for proposal in result.get('proposals', []))
    return {'agent_evaluations': evaluations, 'monitoring': monitors}


def automation_qualification(root, change, provenance):
    """Require the exact current evaluated configuration chosen for execution."""
    if type(provenance) is not dict:
        return {'state': 'blocked', 'reasons': ['agent_evaluation_selection_missing']}
    evaluation_id = provenance.get('evaluation_id')
    configuration_digest = provenance.get('configuration_digest')
    if not evaluation_id or not configuration_digest:
        return {'state': 'blocked', 'reasons': ['agent_evaluation_selection_missing']}
    evaluations = job_assessments(root, change)['agent_evaluations']
    matching = [item for item in evaluations if item['id'] == evaluation_id and item.get('current')]
    if not matching or matching[-1].get('qualification') is not True:
        return {'state': 'blocked', 'reasons': ['agent_configuration_not_evaluated']}
    result = matching[-1]
    if result.get('configurationDigest') != configuration_digest:
        return {'state': 'blocked', 'reasons': ['agent_evaluation_configuration_mismatch']}
    return {'state': 'allowed', 'reasons': [], 'evaluation': result}


def applied_execution(root, change, provenance):
    """Build accepts effects only through the canonical scoped apply owner."""
    execution_id = provenance.get('execution_id') if isinstance(provenance, dict) else None
    if not execution_id or provenance.get('apply_id') != execution_id:
        return {'state': 'blocked', 'reasons': ['execution_application_selection_missing']}
    from cc_development_gateway import assess_applied
    result = assess_applied(root, change, execution_id)
    if result['state'] != 'allowed' and not result.get('reasons'):
        result = {**result, 'reasons': ['execution_application_unproved']}
    return result


def execution_reports(root, change):
    """Read linked canonical attempts without treating worker success as apply."""
    from cc_development_execution import inspect_receipt
    records = [record for batch in change['evidence'] for record in batch['records']
               if record.get('kind') == 'execution']
    latest_records = {record['id']: index for index, record in enumerate(records)}
    reports = []
    for index, record in enumerate(records):
        try:
            execution_id = record.get('receiptId')
            receipt = inspect_receipt(root, execution_id, change)
            application = applied_execution(root, change, {'execution_id': execution_id, 'apply_id': execution_id})
            reports.append({'id': record['id'], 'executionId': execution_id,
                            'current': latest_records[record['id']] == index,
                            'state': receipt['state'], 'reasons': receipt.get('reasons', []),
                            'application': application, 'receipt': receipt})
        except (OSError, ValueError, KeyError, TypeError, EvidenceError) as error:
            reports.append({'id': record['id'], 'current': latest_records[record['id']] == index,
                            'state': 'unknown', 'reasons': [getattr(error, 'code', 'execution_receipt_unavailable')],
                            'application': {'state': 'unknown', 'reasons': ['execution_application_unproved']}})
    return reports
