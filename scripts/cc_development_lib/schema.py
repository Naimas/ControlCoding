"""Strict bounded action validation; external claims remain claims."""
import re
from cc_evidence_inputs import parts, EvidenceError
from .method import FIELDS, STAGES, PROFILES, canonical


class DevelopmentError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


ACTION_FIELDS = {
    'activate': ('profile',), 'create': ('change_id', 'title', 'owner', 'risk'),
    'artifact': ('change_id', 'stage', 'content', 'actor'),
    'decide': ('change_id', 'stage', 'decision', 'actor', 'reason'),
    'candidate': ('change_id', 'files', 'actor'), 'evidence': ('change_id', 'records', 'actor'),
    'review': ('change_id', 'findings', 'actor', 'reason'),
    'release': ('change_id', 'environment', 'artifact_digest', 'status', 'actor', 'reason'),
    'observation': ('change_id', 'signals', 'actor'), 'close': ('change_id', 'actor', 'reason'),
    'incident_capture': ('change_id', 'receipt_id', 'signal_id', 'actor', 'reason'),
    'incident_decide': ('change_id', 'incident_id', 'decision', 'owner', 'actor', 'reason'),
    'incident_followup': ('change_id', 'incident_id', 'child_id', 'title', 'owner', 'risk', 'actor', 'reason'),
    'recover': ('actor', 'reason', 'lock_fingerprint'),
}


def text(value, name, *, limit=4096):
    if type(value) is not str or not value.strip() or len(value) > limit or '\x00' in value:
        raise DevelopmentError('invalid_' + name)
    return value


def identifier(value):
    if type(value) is not str or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,79}', value):
        raise DevelopmentError('invalid_id')
    return value


def revision(value):
    if value != 'absent' and (type(value) is not str or not re.fullmatch('[0-9a-f]{64}', value)):
        raise DevelopmentError('invalid_revision')


def source_path(value):
    if type(value) is not str or '\\' in value:
        raise DevelopmentError('unsafe_source_path')
    try:
        components = parts(value)
    except (EvidenceError, TypeError, ValueError):
        raise DevelopmentError('unsafe_source_path') from None
    if any(p.casefold() in {'.git', '.controlcoding', '.controlwork', '_work', 'cc'} for p in components):
        raise DevelopmentError('managed_source_path')
    return value


def array(value, name):
    if type(value) is not list or len(value) > 256:
        raise DevelopmentError('invalid_' + name)


def stable_records(value, name):
    array(value, name)
    seen = set()
    for item in value:
        if type(item) is not dict or 'id' not in item:
            raise DevelopmentError('invalid_' + name)
        key = identifier(item['id'])
        if key in seen:
            raise DevelopmentError('duplicate_id')
        seen.add(key)


def artifact(stage, content):
    if type(content) is not dict or set(content) != set(FIELDS[stage]):
        raise DevelopmentError('invalid_artifact_fields')
    for field, value in content.items():
        if not isinstance(value, (str, list, dict, bool)) or value is None:
            raise DevelopmentError('invalid_artifact_content')
        if isinstance(value, str):
            text(value, field)
        if isinstance(value, list):
            array(value, field)
    list_fields = {
        'intent': ('affected_users_and_systems', 'constraints', 'non_goals', 'open_questions', 'success_measures'),
        'design': ('requirements', 'criteria', 'decisions', 'risks', 'policy_concerns', 'source_references'),
        'plan': ('files', 'ownership_scopes', 'tasks', 'dependencies'),
        'build': ('deviations', 'changed_files'), 'verify': ('unresolved_failures',),
        'review_release': ('review_findings', 'finding_resolutions', 'required_approvals'),
        'maintain': ('signals', 'findings', 'dispositions', 'followup_owners')}
    for field in list_fields[stage]:
        array(content[field], field)
    hash_fields = {'design': ('upstream_intent_revision',), 'plan': ('upstream_specification_revision',),
                   'build': ('approved_plan_revision', 'source_identity'), 'verify': ('candidate_identity',),
                   'review_release': ('candidate_identity', 'release_artifact_identity'), 'maintain': ('deployed_artifact_identity',)}
    for field in hash_fields.get(stage, ()):
        revision(content[field])
        if content[field] == 'absent':
            raise DevelopmentError('invalid_artifact_identity')
    if stage == 'intent':
        for field in ('problem', 'outcome'):
            text(content[field], field)
        if not content['success_measures']:
            raise DevelopmentError('missing_success_measures')
    if stage == 'design':
        for field in ('requirements', 'criteria'):
            stable_records(content[field], field)
            if not content[field]:
                raise DevelopmentError('missing_' + field)
        requirement_ids = {item['id'] for item in content['requirements']}
        for item in content['requirements']:
            if set(item) != {'id', 'text'}:
                raise DevelopmentError('invalid_requirement')
            text(item['text'], 'requirement')
        for item in content['criteria']:
            if set(item) != {'id', 'requirement_id', 'text', 'required', 'proof', 'checks'}:
                raise DevelopmentError('invalid_criterion')
            if item['requirement_id'] not in requirement_ids or item['proof'] not in ('automated', 'human', 'both'):
                raise DevelopmentError('invalid_criterion')
            text(item['text'], 'criterion')
            if type(item['required']) is not bool:
                raise DevelopmentError('invalid_criterion')
            array(item['checks'], 'checks')
            if item['proof'] != 'human' and not item['checks']:
                raise DevelopmentError('missing_check_ids')
            seen_checks = set()
            for check in item['checks']:
                if type(check) is not dict or set(check) != {'kind', 'id'} or check['kind'] not in ('verification', 'invariants'):
                    raise DevelopmentError('invalid_check')
                identifier(check['id'])
                key = (check['kind'], check['id'])
                if key in seen_checks:
                    raise DevelopmentError('duplicate_check')
                seen_checks.add(key)
        covered = {item['requirement_id'] for item in content['criteria']}
        if requirement_ids - covered:
            raise DevelopmentError('requirement_criteria_missing')
    if stage == 'plan':
        array(content['files'], 'files')
        if not content['files']:
            raise DevelopmentError('missing_file_scope')
        for path in content['files']:
            source_path(path)
        if len(set(content['files'])) != len(content['files']):
            raise DevelopmentError('duplicate_file_scope')
        if not content['ownership_scopes']:
            raise DevelopmentError('missing_ownership_scopes')
        owners = {}
        for scope in content['ownership_scopes']:
            if isinstance(scope, str):
                text(scope, 'scope_owner', limit=160)
            elif type(scope) is dict and set(scope) == {'owner', 'files'}:
                text(scope['owner'], 'scope_owner', limit=160)
                array(scope['files'], 'scope_files')
                if not scope['files']:
                    raise DevelopmentError('missing_scope_files')
                for path in scope['files']:
                    source_path(path)
                    if path not in content['files']:
                        raise DevelopmentError('owner_scope_outside_plan')
                    if path in owners and owners[path] != scope['owner']:
                        raise DevelopmentError('conflicting_file_owners')
                    owners[path] = scope['owner']
            else:
                raise DevelopmentError('invalid_ownership_scope')
        if type(content['criterion_check_mapping']) is not dict:
            raise DevelopmentError('invalid_criterion_mapping')
        for criterion, checks in content['criterion_check_mapping'].items():
            identifier(criterion)
            array(checks, 'criterion_mapping_checks')
            if checks and any(isinstance(check, str) for check in checks) and not all(isinstance(check, str) for check in checks):
                raise DevelopmentError('invalid_criterion_mapping_check')
            for check in checks:
                if isinstance(check, str):
                    identifier(check)
                elif type(check) is dict and set(check) == {'kind', 'id'} and check['kind'] in ('verification', 'invariants'):
                    identifier(check['id'])
                else:
                    raise DevelopmentError('invalid_criterion_mapping_check')
        stable_records(content['tasks'], 'tasks')
        if not content['tasks']:
            raise DevelopmentError('missing_tasks')
        for task in content['tasks']:
            if set(task) != {'id', 'title', 'owner', 'depends_on'}:
                raise DevelopmentError('invalid_task')
            text(task['title'], 'task_title')
            text(task['owner'], 'task_owner', limit=160)
            array(task['depends_on'], 'task_dependencies')
        graph = {task['id']: task['depends_on'] for task in content['tasks']}
        def visit(key, active, done):
            if key not in graph or key in active:
                raise DevelopmentError('invalid_task_dependencies')
            if key in done:
                return
            for child in graph[key]:
                visit(child, active | {key}, done)
            done.add(key)
        done = set()
        for key in graph:
            visit(key, set(), done)
    if stage == 'maintain':
        for disposition in content['dispositions']:
            if not isinstance(disposition, dict) or 'incident_id' not in disposition:
                continue
            if set(disposition) - {'incident_id', 'decision', 'owner', 'reason', 'followup_change_id'} or {'incident_id', 'decision', 'owner', 'reason'} - set(disposition):
                raise DevelopmentError('invalid_incident_disposition')
            revision(disposition['incident_id'])
            if disposition['incident_id'] == 'absent':
                raise DevelopmentError('invalid_incident_disposition')
            if disposition['decision'] not in ('resolved', 'assigned', 'deferred'):
                raise DevelopmentError('invalid_incident_disposition')
            text(disposition['owner'], 'incident_owner', limit=160)
            text(disposition['reason'], 'incident_reason')
            if 'followup_change_id' in disposition:
                identifier(disposition['followup_change_id'])


def _validate_action(action):
    if type(action) is dict and str(action.get('kind','')).startswith('product_'):
        from cc_product import validate_action as product_action, FIELDS as PRODUCT_FIELDS
        if action['kind'] not in PRODUCT_FIELDS:
            raise DevelopmentError('invalid_action')
        if len(canonical(action)) > 512 * 1024:
            raise DevelopmentError('action_size_limit')
        return product_action(action)
    if type(action) is not dict or action.get('kind') not in ACTION_FIELDS:
        raise DevelopmentError('invalid_action')
    kind = action['kind']
    allowed = {'kind', *ACTION_FIELDS[kind]}
    if kind == 'release':
        allowed.add('observation')
        allowed.add('adapter_kind')
    if set(action) - allowed or set(ACTION_FIELDS[kind]) - set(action):
        raise DevelopmentError('invalid_action_fields')
    try:
        if len(canonical(action)) > 128 * 1024:
            raise DevelopmentError('action_size_limit')
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise DevelopmentError('invalid_action_json') from None
    if 'change_id' in action:
        identifier(action['change_id'])
    for field in ('title', 'owner', 'actor', 'reason', 'environment'):
        if field in action:
            text(action[field], field, limit=160 if field in ('owner', 'actor', 'environment') else 4096)
    if kind == 'activate' and action['profile'] not in PROFILES:
        raise DevelopmentError('invalid_profile')
    if kind == 'create' and action['risk'] not in PROFILES:
        raise DevelopmentError('invalid_risk')
    if kind.startswith('incident_'):
        if kind == 'incident_capture':
            if type(action['receipt_id']) is not str or not re.fullmatch(r'development_job_[0-9a-f]{32}', action['receipt_id']):
                raise DevelopmentError('invalid_incident_receipt')
            identifier(action['signal_id'])
        else:
            revision(action['incident_id'])
            if action['incident_id'] == 'absent':
                raise DevelopmentError('invalid_incident_id')
        if kind == 'incident_decide' and action['decision'] not in ('resolved', 'rejected', 'deferred'):
            raise DevelopmentError('invalid_incident_decision')
        if kind == 'incident_followup':
            identifier(action['child_id'])
            if action['risk'] not in PROFILES:
                raise DevelopmentError('invalid_risk')
    if 'stage' in action and action['stage'] not in STAGES:
        raise DevelopmentError('invalid_stage')
    if kind == 'artifact':
        artifact(action['stage'], action['content'])
    if kind == 'decide' and action['decision'] not in ('accepted', 'rejected', 'deferred'):
        raise DevelopmentError('invalid_decision')
    if kind == 'candidate':
        array(action['files'], 'files')
        if not action['files'] or len(set(action['files'])) != len(action['files']):
            raise DevelopmentError('invalid_files')
        if len({p.casefold() for p in action['files'] if isinstance(p, str)}) != len(action['files']):
            raise DevelopmentError('case_collision')
        for path in action['files']:
            source_path(path)
    for field in ('records', 'findings', 'signals'):
        if field in action:
            stable_records(action[field], field)
    if kind == 'review':
        for finding in action['findings']:
            if set(finding) != {'id', 'severity', 'text', 'disposition'} or finding['severity'] not in ('info', 'minor', 'major', 'critical') or finding['disposition'] not in ('open', 'resolved', 'deferred'):
                raise DevelopmentError('invalid_finding')
            text(finding['text'], 'finding')
    if kind == 'release':
        revision(action['artifact_digest'])
        if action['artifact_digest'] == 'absent' or action['status'] not in ('prepared', 'self_reported', 'observed'):
            raise DevelopmentError('invalid_release')
        if 'observation' in action and type(action['observation']) is not dict:
            raise DevelopmentError('invalid_observation')
        if 'adapter_kind' in action and action['adapter_kind'] not in ('release_status', 'deploy', 'rollback'):
            raise DevelopmentError('invalid_adapter_kind')
    if kind == 'recover':
        revision(action['lock_fingerprint'])
        if action['lock_fingerprint'] == 'absent':
            raise DevelopmentError('invalid_lock_fingerprint')
    return json_copy(action)


def validate_action(action):
    try:
        return _validate_action(action)
    except DevelopmentError:
        raise
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
        raise DevelopmentError('invalid_action_json') from None


def json_copy(value):
    import json
    return json.loads(canonical(value))
