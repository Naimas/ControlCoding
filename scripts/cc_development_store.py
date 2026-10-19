"""Bounded immutable storage; callers serialize publication with the Core lock.

Objects are durable before the manifest commit. Unreferenced objects are retained;
this module deliberately provides no garbage collection or session mutation.
"""
import base64
import hashlib
import json
import os
import re

from cc_evidence_inputs import Budget
from cc_layout import managed_relative

MANIFEST = '.controlcoding/development.json'
OBJECTS = '.controlcoding/development/objects/'
PLANNING_LIMIT = 64 * 1024 * 1024
MANIFEST_LIMIT = 2 * 1024 * 1024
PLANNING_RESERVE = 512 * 1024
OBJECT_LIMIT = 256 * 1024
MAX_OBJECTS = 32768
MAX_BYTES = 128 * 1024 * 1024
MAX_DEPTH = 96
PLANNING_REPLAY_BYTES = 32 * 1024 * 1024
PLANNING_REPLAY_OBJECTS = 16384
HASH = re.compile(r'[a-f0-9]{64}\Z')


class StoreError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _encoded(value):
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                      allow_nan=False).encode() + b'\n'


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise StoreError('duplicate_json_key')
        result[key] = value
    return result


def _decode(raw):
    try:
        return json.loads(raw, object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(StoreError('invalid_json')))
    except (UnicodeError, ValueError, RecursionError) as error:
        if isinstance(error, StoreError):
            raise
        raise StoreError('invalid_json') from None


def _ref(value):
    if type(value) is not str or not HASH.fullmatch(value):
        raise StoreError('invalid_object_reference')
    return value


def _read(safe, path, limit, missing=False):
    item = safe.read(managed_relative(safe.path, path),
                     Budget(per_file=limit, total=limit), missing=missing)
    if item is not None and item[1].st_nlink != 1:
        raise StoreError('unsafe_state_file')
    return None if item is None else item[0]


def _durable(safe, path):
    safe.validate()
    if os.name != 'nt':
        with safe.directory(managed_relative(safe.path, path).rsplit('/', 1)[0]) as parent:
            os.fsync(parent)


def _envelope(value, domain):
    if domain == 'operational':
        domain = 'control'
    if domain not in ('planning', 'control'):
        raise StoreError('invalid_capacity_class')
    return {'schema_version': 1, 'domain': domain, 'payload': value}


def object_storage_bytes(value, domain='control'):
    return len(_encoded(_envelope(value, domain)))


def ensure_planning_capacity(safe, extra_bytes=0, *, reservation=None):
    """Conservative retained-object accounting; caller owns publication barrier."""
    if type(extra_bytes) is not int or extra_bytes < 0:
        raise StoreError('invalid_planning_budget')
    total = 0
    inspected_bytes = 0
    try:
        entries = safe.listing(managed_relative(safe.path, OBJECTS.rstrip('/')),
                               Budget(entries=MAX_OBJECTS, total=MAX_BYTES, per_file=OBJECT_LIMIT))
    except FileNotFoundError:
        entries = []
    for name, info in entries:
        if not name.endswith('.json'):
            raise StoreError('invalid_planning_index')
        ref = _ref(name[:-5])
        raw = _read(safe, OBJECTS + ref + '.json', OBJECT_LIMIT)
        inspected_bytes += len(raw)
        if inspected_bytes > MAX_BYTES:
            raise StoreError('object_traversal_limit')
        if hashlib.sha256(raw).hexdigest() != ref:
            raise StoreError('object_digest_mismatch')
        envelope = _decode(raw)
        _payload(envelope)
        if envelope['domain'] == 'planning':
            total += len(raw)
    sessions = '.controlcoding/development/sessions'
    try:
        heads = safe.listing(managed_relative(safe.path, sessions), Budget(entries=256))
    except FileNotFoundError:
        heads = []
    for name, info in heads:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.json', name):
            raise StoreError('invalid_session_head')
        raw_head = _read(safe, sessions + '/' + name, OBJECT_LIMIT)
        total += len(raw_head)
        head = _decode(raw_head)
        if head.get('schemaVersion') == 2:
            import cc_planning
            verified = cc_planning._head(safe, name[:-5])
            cc_planning.history(safe, verified)
            total += verified['reservedBytes']
    credit = 0
    if reservation is not None:
        import cc_planning
        if type(reservation) is not tuple or len(reservation) != 2:
            raise StoreError('planning_reservation_invalid')
        head = cc_planning._head(safe, reservation[0])
        if head is None or head.get('schemaVersion') != 2 or head['object'] != reservation[1]:
            raise StoreError('planning_reservation_invalid')
        cc_planning.history(safe, head)
        credit = head['reservedBytes']
    if total + extra_bytes - credit > PLANNING_LIMIT:
        raise StoreError('planning_quota_exceeded')
    return total


def put_object(safe, value, *, domain='control', reservation=None):
    envelope = _envelope(value, domain)
    raw = _encoded(envelope)
    if len(raw) > OBJECT_LIMIT:
        raise StoreError('object_size_limit')
    reference = hashlib.sha256(raw).hexdigest()
    path = OBJECTS + reference + '.json'
    existing = _read(safe, path, OBJECT_LIMIT, missing=True)
    if existing is None:
        if envelope['domain'] == 'planning':
            ensure_planning_capacity(safe, len(raw), reservation=reservation)
        try:
            safe.write_atomic(managed_relative(safe.path, path), envelope, replace_existing=False)
        except FileExistsError:
            existing = _read(safe, path, OBJECT_LIMIT)
        _durable(safe, path)
    if existing is not None and existing != raw:
        raise StoreError('object_digest_mismatch')
    return reference


def _payload(envelope):
    if type(envelope) is not dict or set(envelope) != {'schema_version', 'domain', 'payload'} or type(envelope['schema_version']) is not int or envelope['schema_version'] != 1 or envelope['domain'] not in ('planning', 'control'):
        raise StoreError('invalid_object_envelope')
    return envelope['payload']


def get_object(safe, reference):
    return _payload(get_raw_object(safe, reference))


def validate_raw_object(reference, envelope):
    if hashlib.sha256(_encoded(envelope)).hexdigest() != _ref(reference):
        raise StoreError('object_digest_mismatch')
    if len(_encoded(envelope)) > OBJECT_LIMIT:
        raise StoreError('object_size_limit')
    return _payload(envelope)


def get_raw_object(safe, reference):
    reference = _ref(reference)
    try:
        raw = _read(safe, OBJECTS + reference + '.json', OBJECT_LIMIT)
    except FileNotFoundError:
        raise StoreError('object_missing') from None
    if hashlib.sha256(raw).hexdigest() != reference:
        raise StoreError('object_digest_mismatch')
    envelope = _decode(raw)
    _payload(envelope)
    return envelope


def _planning_path(path, value):
    return (path == ('product',) or
            (len(path)==3 and path[0]=='operations' and type(path[1]) is int and path[2]=='action'
             and type(value) is dict and str(value.get('kind','')).startswith('product_')))


def _contains_planning(path, value):
    if _planning_path(path,value): return True
    if type(value) is dict:
        return any(_contains_planning(path+(k,),v) for k,v in value.items())
    if type(value) is list:
        return any(_contains_planning(path+(i,),v) for i,v in enumerate(value))
    return False


def _charge_pack(budget,payload,domain):
    while len(budget)<4:budget.append(0)
    size=len(_encoded(payload));budget[0]+=1;budget[1]+=size
    if domain=='planning':
        budget[2]+=1;budget[3]+=size
        if budget[2]>PLANNING_REPLAY_OBJECTS or budget[3]>PLANNING_REPLAY_BYTES:
            raise StoreError('planning_replay_limit')
    if budget[0]>MAX_OBJECTS or budget[1]>MAX_BYTES:raise StoreError('object_traversal_limit')


def _pack(safe, value, depth=0, budget=None, domain='control', path=()):
    budget = budget if budget is not None else [0, 0]
    if depth > MAX_DEPTH:
        raise StoreError('object_depth_limit')
    if domain=='control' and _planning_path(path,value):
        child=_pack(safe,value,depth+1,budget,'planning',path)
        node={'type':'planning','ref':child};_charge_pack(budget,node,'control')
        return put_object(safe,node,domain='control')
    split_planning = domain=='planning' and (path and path[-1] in ('revisions','events','document','features','chapters') or
                     type(value) is dict and ('document' in value or type(value.get('action')) is dict and value['action'].get('kind')=='product_save') or
                     type(value) is dict and set(value)=={'revisions','decisions','increments','specifications','plans','events'})
    if len(_encoded(value)) <= 32 * 1024 and not split_planning and (domain=='planning' or not _contains_planning(path,value)):
        _validate_value(value, depth)
        node={'type':'value','value':value};_charge_pack(budget,node,domain)
        return put_object(safe,node,domain=domain)
    def part(payload):
        _charge_pack(budget,payload,domain)
        return put_object(safe,payload,domain=domain)
    if type(value) is dict:
        if any(type(k) is not str for k in value):
            raise StoreError('invalid_state')
        children = [[k, _pack(safe, v, depth + 1, budget, domain,path+(k,))] for k, v in sorted(value.items())]
        node = {'type': 'dict', 'parts': [part(children[i:i+256]) for i in range(0, len(children), 256)]}
    elif type(value) is list:
        children = [_pack(safe, v, depth + 1, budget, domain,path+(i,)) for i,v in enumerate(value)]
        node = {'type': 'list', 'parts': [part(children[i:i+256]) for i in range(0, len(children), 256)]}
    elif type(value) is str and len(_encoded(value)) > OBJECT_LIMIT // 2:
        node = {'type': 'text', 'parts': [part(value[i:i+16384]) for i in range(0, len(value), 16384)]}
    elif value is None or type(value) in (str, bool, int, float):
        node = {'type': 'scalar', 'value': value}
    else:
        raise StoreError('invalid_state')
    _charge_pack(budget,node,domain)
    return put_object(safe, node, domain=domain)


def _unpack(safe, reference, depth=0, budget=None, object_reader=None):
    budget = budget if budget is not None else [0, 0]
    if depth > MAX_DEPTH:
        raise StoreError('object_depth_limit')
    def fetch(ref):
        budget[0] += 1
        obj = object_reader(_ref(ref)) if object_reader is not None else get_object(safe, ref)
        budget[1] += len(_encoded(obj))
        if budget[0] > MAX_OBJECTS or budget[1] > MAX_BYTES:
            raise StoreError('object_traversal_limit')
        return obj
    node = fetch(reference)
    if type(node) is not dict:
        raise StoreError('invalid_object')
    kind = node.get('type')
    if kind == 'planning' and set(node)=={'type','ref'}:
        return _unpack(safe,node['ref'],depth+1,budget,object_reader)
    if kind == 'value' and set(node) == {'type', 'value'}:
        _validate_value(node['value'], depth)
        # Immutable object readers may cache shared leaves. Each decoded path
        # owns its mutable logical value; projections must never alias arrays.
        import copy
        return copy.deepcopy(node['value'])
    if kind == 'scalar' and set(node) == {'type', 'value'} and (node['value'] is None or type(node['value']) in (str, bool, int, float)):
        return node['value']
    if kind not in ('dict', 'list', 'text') or set(node) != {'type', 'parts'} or type(node['parts']) is not list:
        raise StoreError('invalid_object')
    result = {} if kind == 'dict' else []
    for part in node['parts']:
        values = fetch(part)
        if kind == 'text':
            if type(values) is not str:
                raise StoreError('invalid_object')
            result.append(values)
            continue
        if type(values) is not list or len(values) > 256:
            raise StoreError('invalid_object')
        for value in values:
            if kind == 'dict':
                if type(value) is not list or len(value) != 2 or type(value[0]) is not str or value[0] in result:
                    raise StoreError('invalid_object')
                result[value[0]] = _unpack(safe, value[1], depth + 1, budget, object_reader)
            else:
                result.append(_unpack(safe, value, depth + 1, budget, object_reader))
    return ''.join(result) if kind == 'text' else result


def unpack_reference(reference, object_reader):
    """Decode a detached graph through a caller's hash-validated payload reader."""
    return _unpack(None, reference, object_reader=object_reader)


def packed_domains(reference, fetch, *, domain='control', owner_paths=True, root_path=()):
    """Validate envelope domains through explicit typed owner paths, not text."""
    domains={};active=set();total=0;visits=0
    def value(ref,expected):
        nonlocal total,visits
        envelope=fetch(_ref(ref));payload=validate_raw_object(ref,envelope)
        visits+=1;total+=len(_encoded(payload))
        if visits>MAX_OBJECTS or total>MAX_BYTES:raise StoreError('object_traversal_limit')
        if envelope['domain']!=expected or ref in domains and domains[ref]!=expected:raise StoreError('object_domain_invalid')
        if ref not in domains:
            domains[ref]=expected
        return payload
    def walk(ref,path,expected,depth=0):
        if depth>MAX_DEPTH or ref in active:raise StoreError('object_depth_limit')
        node=value(ref,expected)
        if type(node) is not dict:raise StoreError('invalid_object')
        active.add(ref);kind=node.get('type')
        if kind=='planning':
            if expected!='control' or set(node)!={'type','ref'}:raise StoreError('object_domain_invalid')
            payload=unpack_reference(node['ref'],lambda r:validate_raw_object(r,fetch(r)))
            if owner_paths and not _planning_path(path,payload):raise StoreError('planning_root_invalid')
            walk(node['ref'],path,'planning',depth+1)
        elif kind in ('value','scalar'):
            if set(node)!={'type','value'}:raise StoreError('invalid_object')
            if expected=='control' and owner_paths and _contains_planning(path,node['value']):raise StoreError('planning_root_inline')
        elif kind in ('dict','list','text'):
            if set(node)!={'type','parts'} or type(node['parts']) is not list:raise StoreError('invalid_object')
            index=0
            for part in node['parts']:
                values=value(part,expected)
                if kind=='text':
                    if type(values) is not str:raise StoreError('invalid_object')
                    continue
                if type(values) is not list or len(values)>256:raise StoreError('invalid_object')
                for item in values:
                    if kind=='dict':
                        if type(item) is not list or len(item)!=2 or type(item[0]) is not str:raise StoreError('invalid_object')
                        key,child=item
                    else:key,child=index,item;index+=1
                    walk(child,path+(key,),expected,depth+1)
        else:raise StoreError('invalid_object')
        active.remove(ref)
    walk(reference,root_path,domain)
    return domains


def path_value(reference, path, object_reader):
    """Read one typed DAG path without materializing unrelated history arrays."""
    node = object_reader(_ref(reference))
    if node.get('type')=='planning': return path_value(node['ref'],path,object_reader)
    if not path:
        return unpack_reference(reference, object_reader)
    if node.get('type') == 'value':
        value = node['value']
        for part in path:
            value = value[part]
        return value
    part, rest = path[0], path[1:]
    if node.get('type') == 'dict' and type(part) is str:
        for page_ref in node['parts']:
            for key, child in object_reader(_ref(page_ref)):
                if key == part:
                    return path_value(child, rest, object_reader)
        raise StoreError('object_path_missing')
    if node.get('type') == 'list' and type(part) is int:
        if part < 0:
            page = object_reader(_ref(node['parts'][-1]))
            if -part > len(page):
                raise StoreError('object_path_budget')
            child = page[part]
        else:
            page = object_reader(_ref(node['parts'][part // 256]))
            child = page[part % 256]
        return path_value(child, rest, object_reader)
    raise StoreError('object_path_invalid')


def path_keys(reference, path, object_reader):
    node = object_reader(_ref(reference))
    if node.get('type')=='planning': return path_keys(node['ref'],path,object_reader)
    if node.get('type') == 'value':
        value = node['value']
        for part in path: value = value[part]
        if type(value) is not dict: raise StoreError('object_path_invalid')
        return list(value)
    if not path and node.get('type') == 'dict':
        return [pair[0] for ref in node['parts'] for pair in object_reader(_ref(ref))]
    part, rest = path[0], path[1:]
    if node.get('type') == 'dict':
        for ref in node['parts']:
            for key, child in object_reader(_ref(ref)):
                if key == part: return path_keys(child, rest, object_reader)
    elif node.get('type') == 'list' and type(part) is int and part >= 0:
        child = object_reader(_ref(node['parts'][part // 256]))[part % 256]
        return path_keys(child, rest, object_reader)
    raise StoreError('object_path_invalid')


def path_length(reference, path, object_reader):
    node = object_reader(_ref(reference))
    if node.get('type')=='planning': return path_length(node['ref'],path,object_reader)
    if node.get('type') == 'value':
        value = node['value']
        for part in path: value = value[part]
        if type(value) is not list: raise StoreError('object_path_invalid')
        return len(value)
    if not path and node.get('type') == 'list':
        return 0 if not node['parts'] else (len(node['parts']) - 1) * 256 + len(object_reader(_ref(node['parts'][-1])))
    part, rest = path[0], path[1:]
    if node.get('type') == 'dict':
        for ref in node['parts']:
            for key, child in object_reader(_ref(ref)):
                if key == part: return path_length(child, rest, object_reader)
    elif node.get('type') == 'list' and type(part) is int and part >= 0:
        child = object_reader(_ref(node['parts'][part // 256]))[part % 256]
        return path_length(child, rest, object_reader)
    raise StoreError('object_path_invalid')


def _validate_value(value, depth=0):
    if depth > MAX_DEPTH:
        raise StoreError('object_depth_limit')
    if type(value) is dict:
        if any(type(k) is not str for k in value):
            raise StoreError('invalid_state')
        for item in value.values():
            _validate_value(item, depth + 1)
    elif type(value) is list:
        for item in value:
            _validate_value(item, depth + 1)
    elif value is not None and type(value) not in (str, bool, int, float):
        raise StoreError('invalid_state')


def read_state(safe):
    raw = _read(safe, MANIFEST, MANIFEST_LIMIT, missing=True)
    if raw is None:
        return None, 'absent', None
    manifest = _decode(raw)
    fields = {'schema_version', 'kind', 'project_identity', 'method', 'state_ref', 'archives', 'compatibility'}
    if type(manifest) is not dict or set(manifest) not in (fields, fields | {'retention'}) or manifest['schema_version'] != 2 or manifest['kind'] != 'development_control':
        raise StoreError('unsupported_manifest')
    if type(manifest['archives']) is not list or type(manifest['compatibility']) is not list:
        raise StoreError('invalid_manifest')
    objects={}
    def fetch(ref):
        if ref not in objects:objects[ref]=get_raw_object(safe,ref)
        return objects[ref]
    packed_domains(manifest['state_ref'],fetch)
    state = _unpack(safe, manifest['state_ref'],object_reader=lambda ref:validate_raw_object(ref,fetch(ref)))
    if type(state) is not dict or state.get('project_identity') != manifest['project_identity'] or state.get('method') != manifest['method']:
        raise StoreError('manifest_identity_mismatch')
    if state.get('retention') != manifest.get('retention'):
        raise StoreError('retention_manifest_mismatch')
    return state, hashlib.sha256(raw).hexdigest(), manifest


def write_state(safe, state, expected_revision, metadata=None, *, capacity_class='operational'):
    if capacity_class not in ('planning', 'operational'):
        raise StoreError('invalid_capacity_class')
    raw = _read(safe, MANIFEST, MANIFEST_LIMIT, missing=True)
    current = 'absent' if raw is None else hashlib.sha256(raw).hexdigest()
    if current != expected_revision:
        raise StoreError('revision_conflict')
    previous = _decode(raw) if raw else {}
    if metadata is not None and type(metadata) is not dict:
        raise StoreError('invalid_manifest')
    metadata = {**previous, **(metadata or {})}
    if type(metadata) is not dict or type(metadata.get('archives', [])) is not list or type(metadata.get('compatibility', [])) is not list:
        raise StoreError('invalid_manifest')
    manifest = {'schema_version': 2, 'kind': 'development_control',
                'project_identity': state['project_identity'], 'method': state['method'],
                'state_ref': _pack(safe, state), 'archives': metadata.get('archives', []),
                'compatibility': metadata.get('compatibility', [])}
    if 'retention' in state:
        manifest['retention'] = state['retention']
    encoded = _encoded(manifest)
    limit = MANIFEST_LIMIT - PLANNING_RESERVE if capacity_class == 'planning' else MANIFEST_LIMIT
    if len(encoded) > limit:
        raise StoreError('manifest_size_limit')
    # Verify every referenced state object before publication and compare raw CAS
    # again after staging; the caller's owner lock excludes competing writers.
    if _unpack(safe, manifest['state_ref']) != state:
        raise StoreError('state_roundtrip_mismatch')
    latest = _read(safe, MANIFEST, MANIFEST_LIMIT, missing=True)
    if ('absent' if latest is None else hashlib.sha256(latest).hexdigest()) != expected_revision:
        raise StoreError('revision_conflict')
    safe.write_atomic(managed_relative(safe.path, MANIFEST), manifest, replace_existing=expected_revision != 'absent')
    _durable(safe, MANIFEST)
    return hashlib.sha256(encoded).hexdigest()


def archive_bytes(safe, raw):
    if type(raw) is not bytes or len(raw) > MAX_BYTES:
        raise StoreError('archive_size_limit')
    parts = [put_object(safe, base64.b64encode(raw[i:i+65536]).decode('ascii')) for i in range(0, len(raw), 65536)]
    return put_object(safe, {'kind': 'exact_bytes', 'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw), 'parts': parts})


def read_archive_bytes(safe, reference, object_reader=None):
    reader = object_reader if object_reader is not None else lambda ref: get_object(safe, ref)
    node = reader(_ref(reference))
    if type(node) is not dict or set(node) != {'kind', 'sha256', 'size', 'parts'} or node['kind'] != 'exact_bytes' or type(node['size']) is not int or not 0 <= node['size'] <= MAX_BYTES or type(node['parts']) is not list or len(node['parts']) > MAX_BYTES // 65536:
        raise StoreError('invalid_archive')
    chunks = []
    for part in node['parts']:
        value = reader(_ref(part))
        if type(value) is not str or len(value) > 87384:
            raise StoreError('invalid_archive')
        try:
            chunks.append(base64.b64decode(value, validate=True))
        except ValueError:
            raise StoreError('invalid_archive') from None
    raw = b''.join(chunks)
    if len(raw) != node['size'] or hashlib.sha256(raw).hexdigest() != node['sha256']:
        raise StoreError('archive_digest_mismatch')
    return raw
