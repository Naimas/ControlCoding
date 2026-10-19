"""Strict, versioned read-only progress wire contract."""
import base64
from datetime import datetime
import json
import re

SCHEMA_VERSION = 1
MAX_ROWS = 32
MAX_RESPONSE_BYTES = 524288
VIEWS = {'overview': ('features',), 'feature': ('criteria', 'increments', 'history_refs'),
         'increment': ('changes', 'criteria', 'dependencies'),
         'change': ('tasks', 'criteria', 'evidence', 'incidents', 'releases'), 'history': ('events',)}
STAGES = ('intent', 'design', 'plan', 'build', 'verify', 'review_release', 'maintain')
KINDS = {'features':'feature','increments':'increment','changes':'change','criteria':'criterion',
         'dependencies':'dependency','tasks':'task','evidence':'evidence','incidents':'incident',
         'releases':'release','history_refs':'history_ref','events':'event'}
DATA = {
    'feature':'description selected_scope_count',
    'increment':'feature_ids change_ids priority document_order',
    'change':'owner stage candidate_identity candidate_current gates artifact_revisions detail_revisions profile_revision history_partial',
    'criterion':'required text owner_change_id contributor_change_ids contributions integration',
    'dependency':'consumer_change_id target_feature_id target_feature_revision target_selection_revision target_change_id required_result gate state reasons precision',
    'task':'change_id plan_revision detail_revision profile_revision task_id owner deliverable criterion_ids files depends_on supports_task_ids declaration_order topological_order branch join completion',
    'evidence':'change_id candidate_identity kind reference state reasons',
    'incident':'change_id incident_id state linked_change_ids',
    'release':'change_id release_identity candidate_identity artifact_identity environment status historical assessment',
    'history_ref':'change_id partial retained_records', 'event':'change_id event'}
HASH = re.compile('[0-9a-f]{64}\\Z')
ID = re.compile('[A-Za-z][A-Za-z0-9_.-]{0,79}\\Z')


class ProgressError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(value, code='progress_invalid_response'):
    if not value:
        raise ProgressError(code)


def exact(value, keys, code='progress_invalid_response'):
    require(type(value) is dict and set(value) == set(keys.split() if isinstance(keys, str) else keys), code)


def string(value):
    require(type(value) is str and '\x00' not in value)


def reference(value, nullable=True):
    require(value is None and nullable or type(value) is str and HASH.fullmatch(value) is not None)


def integer(value, nullable=False):
    require(value is None and nullable or type(value) is int and value >= 0)


def strings(value):
    require(type(value) is list)
    for item in value:
        string(item)


def boolean(value):
    require(type(value) is bool)


def enum(value, choices):
    require(value in choices and (value is None or type(value) is str))


def timestamp(value, nullable=False):
    if value is None and nullable:
        return
    string(value)
    try:
        require(datetime.fromisoformat(value.replace('Z', '+00:00')).utcoffset().total_seconds() == 0)
    except (ValueError, AttributeError, OverflowError):
        raise ProgressError('progress_invalid_response') from None


def serialized(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ProgressError('progress_invalid_response') from None


def validate_query(query):
    code = 'progress_invalid_selector'
    exact(query, 'view entity_id collection cursor observation_id', code)
    view = query['view']
    require(type(view) is str and view in VIEWS, code)
    entity = query['entity_id']
    require(entity is None if view == 'overview' else type(entity) is str and ID.fullmatch(entity) is not None, code)
    collection = query['collection'] or VIEWS[view][0] if query['collection'] is None else query['collection']
    require(type(collection) is str and collection in VIEWS[view], code)
    observation = query['observation_id']
    require(observation is None or type(observation) is str and HASH.fullmatch(observation) is not None, code)
    cursor = query['cursor']
    require(cursor is None or type(cursor) is str and 0 < len(cursor) <= 2048, code)
    require(cursor is None or observation is not None, 'progress_invalid_cursor')
    return {**query, 'collection': collection}


def decode_cursor(value):
    def pairs(items):
        result = {}
        for key, item in items:
            require(key not in result, 'progress_invalid_cursor')
            result[key] = item
        return result
    try:
        require(type(value) is str and len(value) <= 2048 and re.fullmatch('[A-Za-z0-9_-]+', value) is not None)
        raw = base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
        require(len(raw) <= 1536)
        result = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: require(False))
        exact(result, 'schema_version project_identity method_digest control_revision observation_id input_digest view entity_id collection order offset expires_at')
        require(type(result['schema_version']) is int and result['schema_version'] == 1)
        for key in ('method_digest','observation_id','input_digest'):
            reference(result[key], False)
        reference(result['control_revision'])
        string(result['project_identity'])
        validate_query({k: result[k] for k in ('view','entity_id','collection','observation_id')} | {'cursor': None})
        require(result['order'] == 'declaration_then_id_v1')
        integer(result['offset']); integer(result['expires_at'])
        require(encode_cursor(result) == value)
        return result
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ProgressError('progress_invalid_cursor') from None


def encode_cursor(value):
    raw = serialized(value)
    require(len(raw) <= 1536, 'progress_invalid_cursor')
    result = base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')
    require(len(result) <= 2048, 'progress_invalid_cursor')
    return result


def validate_target(value):
    exact(value, 'destination project_identity feature_id feature_revision increment_id change_id criterion_id incident_id stage selection_revision artifact_revision')
    enum(value['destination'], ('Product','Development','Status','Project Map'))
    string(value['project_identity'])
    for key in ('feature_id','increment_id','change_id','criterion_id'):
        require(value[key] is None or type(value[key]) is str and ID.fullmatch(value[key]) is not None)
    reference(value['incident_id'])
    enum(value['stage'], (*STAGES, None))
    reference(value['feature_revision']); reference(value['selection_revision']); reference(value['artifact_revision'])


def validate_scope(value):
    exact(value, 'document_title document_state feature_id feature_revision selected_feature_revisions increment_id selection_revision role decision')
    string(value['document_title']); enum(value['document_state'], ('none','draft','accepted'))
    for key in ('feature_id','increment_id'):
        require(value[key] is None or type(value[key]) is str and ID.fullmatch(value[key]) is not None)
    reference(value['feature_revision']); reference(value['selection_revision'])
    enum(value['role'], ('active','superseded','retired','unselected',None))
    enum(value['decision'], ('selected','keep','adopt','defer','cancel',None))
    require(type(value['selected_feature_revisions']) is list)
    for row in value['selected_feature_revisions']:
        exact(row, 'feature_id feature_revision selection_revision')
        string(row['feature_id']); reference(row['feature_revision'], False); reference(row['selection_revision'], False)


def validate_summary(value):
    exact(value, 'outcome attention currentness coverage reasons next_actions')
    enum(value['outcome'], ('not_established','planned','in_progress','implemented','verified','released'))
    enum(value['attention'], ('none','needs_review','blocked','not_established'))
    enum(value['currentness'], ('current','stale','unknown','not_applicable'))
    exact(value['coverage'], 'required optional duplicate_contributions')
    integer(value['coverage']['duplicate_contributions'])
    for key in ('required','optional'):
        bucket = value['coverage'][key]
        exact(bucket, 'total mapped implemented verified released missing unknown')
        for name, count in bucket.items():
            integer(count, nullable=name == 'total')
            if bucket['total'] is not None and name != 'total':
                require(count <= bucket['total'])
    require(type(value['reasons']) is list and len(value['reasons']) <= 128)
    for reason in value['reasons']:
        exact(reason, 'code attention gate change_id criterion_id')
        string(reason['code']); enum(reason['attention'], ('none','needs_review','blocked','not_established'))
        enum(reason['gate'], (*STAGES,None))
        for key in ('change_id','criterion_id'):
            require(reason[key] is None or type(reason[key]) is str and ID.fullmatch(reason[key]) is not None)
    require(type(value['next_actions']) is list and len(value['next_actions']) <= 8)
    for target in value['next_actions']:
        validate_target(target)


def validate_row(value):
    exact(value, 'kind id title scope summary target data')
    kind = value['kind']; require(type(kind) is str and kind in DATA)
    string(value['id']); string(value['title']); validate_scope(value['scope'])
    if value['summary'] is None:
        require(kind in ('dependency','task','evidence','incident','release','history_ref','event'))
    else:
        validate_summary(value['summary'])
    if value['target'] is not None:
        validate_target(value['target'])
    data = value['data']; exact(data, DATA[kind])
    for key, item in data.items():
        if key in ('required','mapped','candidate_current','history_partial','branch','join','historical','partial'):
            boolean(item)
        elif key in ('selected_scope_count','document_order','declaration_order','topological_order','retained_records'):
            integer(item)
        elif key.endswith('_ids') or key in ('files','depends_on','reasons'):
            strings(item)
        elif key.endswith('_revision') or key in ('candidate_identity','release_identity','artifact_identity'):
            reference(item, nullable=key not in ('plan_revision','release_identity','target_feature_revision','target_selection_revision'))
        elif key not in ('gates','artifact_revisions','detail_revisions','contributions','integration','assessment','event','owner_change_id'):
            string(item)
    if kind == 'criterion':
        require(data['owner_change_id'] is None or type(data['owner_change_id']) is str)
        require(type(data['contributions']) is list)
        for row in data['contributions']:
            exact(row, 'change_id specification_criterion_ids mapped candidate_identity implemented verified released reasons')
            string(row['change_id']); strings(row['specification_criterion_ids']); strings(row['reasons']); reference(row['candidate_identity'])
            for key in ('mapped','implemented','verified','released'):
                boolean(row[key])
        exact(data['integration'], 'required state reasons'); boolean(data['integration']['required'])
        enum(data['integration']['state'], ('allowed','blocked','unknown','not_required')); strings(data['integration']['reasons'])
    if kind == 'change':
        require(type(data['gates']) is dict and set(data['gates']) <= set(STAGES))
        for gate in data['gates'].values():
            require(type(gate) is dict and {'outcome','reasons','accepted'} <= set(gate) <= {'outcome','reasons','accepted','evidence','policy','pull_request'})
            enum(gate['outcome'], ('allowed','blocked','unknown')); strings(gate['reasons']); boolean(gate['accepted'])
        for key in ('artifact_revisions','detail_revisions'):
            require(type(data[key]) is dict and set(data[key]) <= set(STAGES))
            for revision in data[key].values():
                reference(revision)
    if kind in ('dependency','evidence'):
        enum(data['state'], ('allowed','blocked','unknown'))
    if kind == 'dependency':
        enum(data['precision'], ('edge','aggregate'))
        enum(data['required_result'], ('specification_accepted','verified','released'))
        enum(data['gate'], ('design','plan','build','verify','review_release','maintain'))
    if kind == 'task':
        require(data['completion'] == 'not_tracked')
    if kind == 'release':
        exact(data['assessment'], 'state reasons'); enum(data['assessment']['state'], ('allowed','blocked','unknown')); strings(data['assessment']['reasons'])
        enum(data['status'], ('prepared','self_reported','observed'))
    if kind == 'event':
        exact(data['event'], 'action result timestamp previous digest')
        from cc_development_history import _event
        _event(data['event'])


def validate_response(value):
    exact(value, 'schema_version view entity_id collection project_identity method control_revision current_product_revision accepted_product_revision enabled status observation scope summary rows pagination')
    require(type(value['schema_version']) is int and value['schema_version'] == 1)
    validate_query({k: value[k] for k in ('view','entity_id','collection')} | {'cursor':None,'observation_id':None})
    string(value['project_identity']); boolean(value['enabled']); enum(value['status'], ('empty','available','unavailable'))
    exact(value['method'], 'id version digest evaluator_version implementation_digest')
    for item in value['method'].values():
        string(item)
    for key in ('control_revision','current_product_revision','accepted_product_revision'):
        reference(value[key])
    observation = value['observation']
    exact(observation, 'observation_id observed_at observation_finished_at valid_until coherence reasons completeness_scope input_digest accounting')
    for key in ('observation_id','input_digest'):
        reference(observation[key])
    for key in ('observed_at','observation_finished_at','valid_until'):
        timestamp(observation[key], nullable=key == 'valid_until')
    enum(observation['coherence'], ('coherent','incomplete')); strings(observation['reasons'])
    exact(observation['completeness_scope'], 'view entity_id collection')
    require(observation['completeness_scope'] == {k:value[k] for k in ('view','entity_id','collection')})
    exact(observation['accounting'], 'read_entries input_bytes attempts')
    for item in observation['accounting'].values():
        integer(item)
    require(observation['accounting']['attempts'] <= 2)
    validate_scope(value['scope']); validate_summary(value['summary'])
    require(type(value['rows']) is list and len(value['rows']) <= MAX_ROWS)
    for row in value['rows']:
        validate_row(row); require(row['kind'] == KINDS[value['collection']])
    pagination = value['pagination']; exact(pagination, 'returned total total_complete next_cursor')
    integer(pagination['returned']); integer(pagination['total'], nullable=True); boolean(pagination['total_complete'])
    require(pagination['returned'] == len(value['rows']))
    require(pagination['total_complete'] == (pagination['total'] is not None))
    if pagination['next_cursor'] is not None:
        decode_cursor(pagination['next_cursor'])
    if observation['coherence'] == 'incomplete':
        require(value['summary']['outcome'] == 'not_established' and not value['rows'] and pagination['total'] is None)
    else:
        reference(observation['observation_id'], False); reference(observation['input_digest'], False)
    require(len(serialized(value)) <= MAX_RESPONSE_BYTES, 'progress_record_too_large')
    return value
