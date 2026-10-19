"""Pure bounded observation of legacy Planner JSON; never imports legacy code."""
from __future__ import annotations
import datetime
import hashlib
import json
import math
import re
from cc_evidence_inputs import SafeRoot, Budget, EvidenceError
from cc_development_lib.schema import DevelopmentError, identifier

LIMIT = 128 * 1024
FORMAT = 'legacy_planner_json_v1'
FIELDS = {'schema_version','format','source_id','kind','owner','path','sha256','observed_at','content','items','warnings'}
ITEM_FIELDS = {'id','pointer','entity_id','category','title','text','selected','disposition','targets','reason'}

def fail(code):
    raise DevelopmentError(code)

def parse(content):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result: fail('source_json_duplicate_key')
            result[key] = value
        return result
    def nonfinite(value): fail('source_json_nonfinite')
    try:
        data = json.loads(content, object_pairs_hook=pairs, parse_constant=nonfinite)
    except (json.JSONDecodeError, RecursionError):
        fail('source_json_invalid')
    count = 0
    def walk(value, depth):
        nonlocal count
        count += 1
        if type(value) is float and not math.isfinite(value): fail('source_json_nonfinite')
        if count > 4096: fail('source_json_node_limit')
        if depth > 16: fail('source_json_depth_limit')
        if isinstance(value, dict):
            for child in value.values(): walk(child, depth + 1)
        elif isinstance(value, list):
            for child in value: walk(child, depth + 1)
    walk(data, 0)
    if type(data) is not dict or not {'scope','phases','architecture','expansion_state','invariants_global'} <= set(data):
        fail('unsupported_source_format')
    if (type(data['scope']) is not str or type(data['phases']) is not list
            or type(data['architecture']) is not dict or type(data['expansion_state']) is not dict
            or type(data['invariants_global']) is not list):
        fail('unsupported_source_format')
    for phase in data['phases']:
        if type(phase) is not dict or type(phase.get('features')) is not list:
            fail('unsupported_source_format')
        for feature in phase['features']:
            if type(feature) is not dict: fail('unsupported_source_format')
            for field in ('acceptance_criteria','verification_steps','invariants','dependencies'):
                if field in feature and type(feature[field]) is not list: fail('unsupported_source_format')
    return data

def inventory(content):
    data = parse(content)
    result = []
    known = {'version','status','status_hash','scope','created_at','approved_at','phases','invariants_global','architecture','expansion_state'}
    categories = {'scope':'scope','phases':'feature','invariants_global':'invariant','architecture':'architecture','expansion_state':'expansion','status':'status','version':'status','status_hash':'status','created_at':'status','approved_at':'status'}
    def walk(value, pointer, top, entity=''):
        if isinstance(value, dict) and value:
            local = str(value.get('id',entity))
            for key, child in value.items():
                escaped = key.replace('~','~0').replace('/','~1')
                walk(child, pointer + '/' + escaped, top, local)
            return
        if isinstance(value, list) and value:
            for index, child in enumerate(value): walk(child,pointer + '/' + str(index),top,entity)
            return
        if len(result) >= 128: fail('source_inventory_limit')
        category = categories.get(top,'unsupported')
        if top == 'phases':
            allowed={'id','name','description','features','status','owner','priority','depends_on','title','implementation_notes','acceptance_criteria','verification_steps','invariants','dependencies','files','completed_at','acceptance_criterion','verification_method','type','test_sketch','text','zone_mapping','phase'}
            if any(segment not in allowed and not segment.isdigit() for segment in pointer.split('/')[2:]):category='unsupported'
            else:category = 'condition' if any(s in pointer for s in ('acceptance','verification','invariants')) else 'phase' if '/features/' not in pointer else 'feature'
        if top=='architecture' and len(pointer.split('/'))>2 and pointer.split('/')[2] not in ('zones','dependency_direction','deny_zones','warn_zones'):category='unsupported'
        if top=='expansion_state' and len(pointer.split('/'))>2 and pointer.split('/')[2] not in ('completed_rounds','current_round','responses'):category='unsupported'
        if top=='invariants_global' and any(segment not in ('id','text','type','test_sketch') and not segment.isdigit() for segment in pointer.split('/')[2:]):category='unsupported'
        result.append({'id':'item-' + hashlib.sha256(pointer.encode('utf-8')).hexdigest()[:24],
                       'pointer':pointer,'entity_id':entity,'category':category,'title':pointer,
                       'text':value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,sort_keys=True),
                       'selected':False,'disposition':'unresolved','targets':[],'reason':''})
    for key,value in data.items(): walk(value,'/' + key.replace('~','~0').replace('/','~1'),key)
    original = dict(data); observed = original.pop('status_hash',None)
    expected = hashlib.sha256(json.dumps(original,sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest()
    warnings = []
    if not observed: warnings.append('legacy_integrity_missing')
    elif observed != expected: warnings.append('legacy_integrity_mismatch')
    if set(data) - known or any(r['category']=='unsupported' for r in result): warnings.append('legacy_unsupported_material')
    warnings.append('legacy_status_is_historical')
    return result,warnings

def observe(root,path):
    if type(path) is not str or not path.lower().endswith('.json'): fail('unsupported_source_format')
    try:
        with SafeRoot(root.path if hasattr(root,'path') else root) as safe:
            raw, info = safe.read(path,Budget(per_file=LIMIT,total=LIMIT))
            if info.st_nlink != 1: fail('unsafe_source_file')
        content = raw.decode('utf-8-sig')
    except (EvidenceError,UnicodeError,OSError) as error:
        code=getattr(error,'code','source_unreadable')
        fail('source_size_or_content_limit' if code in ('byte_limit','file_size_limit','input_size_limit','byte_budget_exceeded','budget_exceeded') else code)
    if len(raw)>LIMIT or not content.strip() or '\x00' in content: fail('source_size_or_content_limit')
    items,warnings = inventory(content)
    sha = hashlib.sha256(raw).hexdigest()
    return {'schema_version':2,'format':FORMAT,'source_id':'src-'+sha[:16],
            'kind':'file','owner':'project','path':path,'sha256':sha,
            'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'content':content,'items':items,'warnings':warnings}

def validate(root,source,*,current=True,reviewed=False):
    if type(source) is not dict or set(source)!=FIELDS or type(source['schema_version']) is not int or source['schema_version']!=2 or source['format']!=FORMAT: fail('invalid_source_observation')
    if source['kind']!='file' or source['owner']!='project': fail('invalid_source_owner')
    if any(type(source[k]) is not str for k in ('source_id','path','sha256','observed_at','content')): fail('invalid_source_observation')
    from cc_evidence_inputs import parts
    parts(source['path'])
    if not source['path'].lower().endswith('.json') or not re.fullmatch('[a-f0-9]{64}',source['sha256']) or source['source_id']!='src-'+source['sha256'][:16]: fail('invalid_source_observation')
    if len(source['content'].encode('utf-8'))>LIMIT or '\x00' in source['content']: fail('source_size_or_content_limit')
    try: datetime.datetime.fromisoformat(source['observed_at'])
    except ValueError: fail('invalid_source_observation')
    expected,warnings = inventory(source['content'])
    if source['warnings']!=warnings or type(source['items']) is not list or len(source['items'])!=len(expected): fail('invalid_source_inventory')
    for actual,original in zip(source['items'],expected):
        if type(actual) is not dict or set(actual)!=ITEM_FIELDS or any(actual[k]!=original[k] for k in ('id','pointer','entity_id','category','title','text')): fail('invalid_source_inventory')
        if type(actual['selected']) is not bool or actual['disposition'] not in ('mapped','reference-only','excluded','unresolved') or type(actual['reason']) is not str or len(actual['reason'])>4000 or type(actual['targets']) is not list or len(actual['targets'])>128: fail('invalid_source_disposition')
        for target in actual['targets']: identifier(target)
        if len(set(actual['targets']))!=len(actual['targets']): fail('invalid_source_disposition')
        if actual['selected'] and ((actual['disposition']=='mapped' and not actual['targets']) or (actual['disposition']=='excluded' and not actual['reason'].strip())): fail('invalid_source_disposition')
        if reviewed and actual['selected'] and actual['disposition']=='unresolved': fail('selected_source_unresolved')
    if current:
        observed=observe(root,source['path'])
        if observed['sha256']!=source['sha256'] or observed['content']!=source['content']: fail('source_observation_stale')
    return source

def preview(root,path):
    if path.lower().endswith('.json'): source=observe(root,path)
    else:
        from cc_product_documents import observe_source
        source=observe_source(root.path if hasattr(root,'path') else root,path)
    items=source.get('items',source.get('sections',[]))
    return {'schema_version':1,'source':source,'inventory':{'selected':sum(r['selected'] for r in items),'total':len(items),'unresolved':sum(r['selected'] and r['disposition']=='unresolved' for r in items)},'warnings':source.get('warnings',[]),'blockers':[]}
